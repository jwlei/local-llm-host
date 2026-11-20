"""
llama.cpp's llama-server, one model at a time: started when a message needs it, kept loaded for WARM_SECONDS after a
reply so the next one starts at once, then closed to give the GPU back. It listens only on 127.0.0.1, on a port of
its own; the app is what devices talk to.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time

import requests

ALIAS = "local"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_job = None
_job_lock = threading.Lock()


def _tie_to_this_process(process):
    """
    On Windows, the server ends with this app however it ends (a crash, Task Manager), never left holding the GPU:
    it goes in a job object that kills what is in it once the app's handle to it closes.
    """
    global _job
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class Counters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("Reads", "Writes", "Others", "ReadBytes", "WriteBytes", "OtherBytes")]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", Counters), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        with _job_lock:
            if _job is None:
                job = kernel32.CreateJobObjectW(None, None)
                if not job:
                    return
                info = Extended()
                info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))  # Extended limits
                _job = job
        handle = kernel32.OpenProcess(0x0001 | 0x0100, False, process.pid)  # PROCESS_TERMINATE | PROCESS_SET_QUOTA
        if handle:
            kernel32.AssignProcessToJobObject(_job, handle)
            kernel32.CloseHandle(handle)
    except (OSError, AttributeError):
        pass  # Best effort: stop() and the app's exit still close it


def context_of(config, model):
    return model.options.get("context", config.context)


class LlamaServer:
    def __init__(self, config, model, vision=False):
        self.config = config
        self.model = model
        # The projector only when a message has images: it takes GPU memory the model could use.
        self.projector = model.projector if vision else None
        self.process = None
        self.url = None
        self.log = None
        self.log_path = os.path.join(config.data_dir, "logs", "llama-server.log")

    def command(self, port):
        server = self.config.llama_server
        options = self.model.options
        # A Python script as the server: the tests' stand-in for llama-server.
        args = [sys.executable, server] if server.lower().endswith(".py") else [server]
        args += ["--model", self.model.path]
        if self.projector:
            args += ["--mmproj", self.projector]
        args += ["--host", "127.0.0.1", "--port", str(port), "--ctx-size", str(context_of(self.config, self.model)),
                 "--parallel", "1", "--gpu-layers", str(options.get("gpu_layers", self.config.gpu_layers)),
                 "--alias", ALIAS]
        threads = options.get("threads", self.config.threads)
        if threads:
            args += ["--threads", str(threads)]
        cache = options.get("cache_type", self.config.cache_type)
        if cache:
            # A quantised V cache needs flash attention.
            args += ["--flash-attn", "on", "--cache-type-k", cache, "--cache-type-v", cache]
        return args + list(self.config.extra_args) + list(options.get("args", []))

    def start(self):
        server = self.config.llama_server
        if not server:
            raise RuntimeError("Set LLAMA_SERVER in .env to the llama-server program from llama.cpp.")
        if not os.path.isfile(server):
            raise RuntimeError(f"llama-server isn't at {server}: check LLAMA_SERVER in .env.")
        if not os.path.isfile(self.model.path):
            raise RuntimeError(f"The model file is gone: {self.model.path}")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/v1"
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        self.log = open(self.log_path, "w+", encoding="utf-8", errors="replace")
        print(f"Loading {self.model.label}{' with vision' if self.projector else ''}...", flush=True)
        try:
            self.process = subprocess.Popen(self.command(port), stdin=subprocess.DEVNULL, stdout=self.log,
                                            stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
            _tie_to_this_process(self.process)
            deadline = time.monotonic() + self.config.start_timeout
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError(f"llama-server stopped while loading the model:\n{self._log_tail()}")
                try:
                    response = requests.get(f"{self.url}/models", timeout=2)
                    if response.ok and any(m.get("id") == ALIAS for m in response.json().get("data", [])):
                        print(f"{self.model.label} is ready.", flush=True)
                        return self
                except (requests.RequestException, ValueError):
                    pass
                time.sleep(0.5)
            raise RuntimeError(f"The model didn't load within {self.config.start_timeout} seconds.")
        except BaseException:
            self.stop()
            raise

    def _log_tail(self, chars=2000):
        try:
            self.log.flush()
            self.log.seek(0)
            return self.log.read()[-chars:].strip()
        except (OSError, ValueError):
            return ""

    def alive(self):
        return self.process is not None and self.process.poll() is None

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process = None
        if self.log:
            self.log.close()
            self.log = None

    def chat(self, messages, max_tokens):
        """
        The reply as it is written: ("text", piece) for each piece, ("speed", tokens a second) as the server times
        its writing, then ("usage", {"prompt": n, "completion": n}) when it counted them, and last ("finish", "stop"
        or "length" when it ran out of room).
        """
        temperature = self.model.options.get("temperature", self.config.temperature)
        try:
            with requests.post(f"{self.url}/chat/completions", json={
                "model": ALIAS, "messages": messages, "max_tokens": max_tokens, "temperature": temperature,
                "stream": True, "stream_options": {"include_usage": True},
                # The server's timing with every piece: the speed shows as soon as it writes.
                "timings_per_token": True,
                # The answer, not reasoning first, for models that think (Qwen3): a reply starts at once.
                "chat_template_kwargs": {"enable_thinking": False},
            }, stream=True, timeout=(10, 900)) as response:
                if not response.ok:
                    detail = response.text[:500]
                    raise RuntimeError(f"llama-server answered {response.status_code}: {detail}")
                finish, usage, timings = None, None, None
                for line in response.iter_lines(decode_unicode=True):
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    usage, timings = chunk.get("usage") or usage, chunk.get("timings") or timings
                    choice = (chunk.get("choices") or [{}])[0]
                    piece = (choice.get("delta") or {}).get("content")
                    if piece:
                        yield "text", piece
                    speed = (chunk.get("timings") or {}).get("predicted_per_second")
                    if piece and isinstance(speed, (int, float)) and speed > 0:
                        yield "speed", speed
                    finish = choice.get("finish_reason") or finish
                if usage and usage.get("prompt_tokens") is not None:
                    yield "usage", {"prompt": usage["prompt_tokens"], "completion": usage.get("completion_tokens") or 0}
                elif timings and timings.get("prompt_n") is not None:
                    yield "usage", {"prompt": timings["prompt_n"] + (timings.get("cache_n") or 0),
                                    "completion": timings.get("predicted_n") or 0}
                yield "finish", finish or "stop"
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
            raise RuntimeError(f"Could not get a reply: {e}") from e


class Runner:
    """The one model kept loaded between messages, and its timer."""

    def __init__(self, config):
        self.config = config
        self._lock = threading.Lock()
        self._warm = None
        self._timer = None

    def take(self, model_id, vision):
        """The loaded server, now the caller's, if it runs this model (with vision, when asked); otherwise None."""
        with self._lock:
            held, self._warm = self._warm, None
            if self._timer:
                self._timer.cancel()
                self._timer = None
        if held and held.alive() and held.model.id == model_id and (held.projector or not vision):
            return held
        if held:
            held.stop()
        return None

    def keep(self, server):
        """Kept loaded for the next message, and closed after WARM_SECONDS without one."""
        if not server.alive():
            server.stop()
            return
        if self.config.warm_seconds <= 0:
            server.stop()
            return
        with self._lock:
            old, self._warm = self._warm, server
            self._timer = threading.Timer(self.config.warm_seconds, self._expire, args=(server,))
            self._timer.daemon = True
            self._timer.start()
        if old and old is not server:
            old.stop()

    def _expire(self, server):
        with self._lock:
            if self._warm is not server:
                return
            self._warm, self._timer = None, None
        print(f"Unloading {server.model.label} after {self.config.warm_seconds} s idle.", flush=True)
        server.stop()

    def loaded(self):
        with self._lock:
            return self._warm.model if self._warm and self._warm.alive() else None

    def unload(self):
        with self._lock:
            held, self._warm = self._warm, None
            if self._timer:
                self._timer.cancel()
                self._timer = None
        if held:
            held.stop()
        return held is not None
