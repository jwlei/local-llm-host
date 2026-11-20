"""
Settings, read from the environment (and a .env file in the project folder). Everything has a default except where
llama-server is: without LLAMA_SERVER, the chat opens but can't start a model.
"""
import os
import secrets
import shlex
from dataclasses import dataclass, field

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant. Answer clearly and directly, and use Markdown where it helps. "
    "Always reply in the language the user writes in.")


def _int(name, default, minimum=1):
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        number = int(value)
    except ValueError:
        raise ValueError(f"{name} must be a whole number, not {value!r}.") from None
    if number < minimum:
        raise ValueError(f"{name} must be at least {minimum}.")
    return number


def _path(name, default):
    value = os.getenv(name) or default
    return value if os.path.isabs(value) else os.path.join(ROOT, value)


@dataclass
class Config:
    llama_server: str = ""
    models_dir: str = os.path.join(ROOT, "models")
    data_dir: str = os.path.join(ROOT, "data")
    # The context every model starts with, unless its model.json says otherwise.
    context: int = 16384
    # Room kept for the reply: what is sent of the conversation is the context less this.
    reply_tokens: int = 4096
    gpu_layers: str = "all"
    threads: int = 0  # 0: llama.cpp chooses
    # A quantised KV cache (q8_0, q4_0) halves or quarters its memory: worth it for a model that only just fits.
    cache_type: str = ""
    extra_args: list = field(default_factory=list)
    temperature: float = 0.8
    # How long a model stays loaded after its last reply, so the next message doesn't wait for it to load.
    warm_seconds: int = 600
    start_timeout: int = 180
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    password: str = ""
    secret_key: str = ""

    @classmethod
    def from_env(cls):
        load_dotenv(os.path.join(ROOT, ".env"))
        gpu_layers = (os.getenv("GPU_LAYERS") or "all").strip()
        if gpu_layers not in ("all", "auto") and not gpu_layers.isdigit():
            raise ValueError("GPU_LAYERS must be all, auto or a number of layers.")
        try:
            temperature = float(os.getenv("TEMPERATURE") or 0.8)
        except ValueError:
            raise ValueError("TEMPERATURE must be a number, such as 0.8.") from None
        server = os.getenv("LLAMA_SERVER") or ""
        return cls(
            llama_server=_path("LLAMA_SERVER", server) if server else "",
            models_dir=_path("MODELS_DIR", "models"),
            data_dir=_path("DATA_DIR", "data"),
            context=_int("CONTEXT", 16384, 512),
            reply_tokens=_int("REPLY_TOKENS", 4096, 16),
            gpu_layers=gpu_layers,
            threads=_int("THREADS", 0, 0),
            cache_type=(os.getenv("CACHE_TYPE") or "").strip(),
            extra_args=shlex.split(os.getenv("LLAMA_ARGS") or "", posix=os.name != "nt"),
            temperature=temperature,
            warm_seconds=_int("WARM_SECONDS", 600, 0),
            start_timeout=_int("START_TIMEOUT", 180, 5),
            system_prompt=(os.getenv("SYSTEM_PROMPT") or DEFAULT_SYSTEM_PROMPT).strip(),
            password=os.getenv("APP_PASSWORD") or "",
            secret_key=os.getenv("FLASK_SECRET_KEY") or "",
        )

    def session_key(self):
        """The key that signs sign-in cookies. Without FLASK_SECRET_KEY, a random one: sign-ins end on a restart."""
        if not self.secret_key:
            self.secret_key = secrets.token_hex(32)
        return self.secret_key
