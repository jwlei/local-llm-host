"""
The web app: a chat page, its API, and sign-in. One reply is written at a time, as there is one GPU; a message from
another device meanwhile is refused until it is done.
"""
import ipaddress
import json
import os
import re
import secrets
import socket
import threading
import time
from datetime import timedelta
from functools import wraps

from flask import (Flask, Response, abort, redirect, render_template, request, send_file, session,
                   stream_with_context, url_for)

from local_llm_host import __version__, models as model_scan, tailscale
from local_llm_host.chats import MAX_IMAGES, MAX_MESSAGE, ChatStore, Context, ForeignScript, new_session, read_image, summary, title
from local_llm_host.config import ROOT, Config
from local_llm_host.runner import LlamaServer, Runner, context_of

def lan_ip():
    """This PC's address on the home network (no packet is sent: a UDP socket only picks the route)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("10.255.255.255", 1))
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def _this_pc(address):
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def create_app(config=None, shutdown=None):
    config = config or Config.from_env()
    app = Flask(__name__, template_folder=os.path.join(ROOT, "templates"), static_folder=os.path.join(ROOT, "static"))
    app.secret_key = config.session_key()
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      PERMANENT_SESSION_LIFETIME=timedelta(days=30), MAX_CONTENT_LENGTH=80 * 2 ** 20)
    store = ChatStore(config.data_dir)
    runner = Runner(config)
    busy = {"replying": False}
    busy_lock = threading.Lock()
    app.extensions["local_llm_host"] = {"config": config, "store": store, "runner": runner, "busy": busy}

    def stop_process():
        runner.unload()
        os._exit(0)

    shutdown = shutdown or stop_process

    # --- Sign-in ---

    @app.before_request
    def only_this_pc_without_a_password():
        """Without APP_PASSWORD, only this PC is answered, however the app was started."""
        if not config.password and not _this_pc(request.remote_addr or ""):
            abort(403, "Set APP_PASSWORD to use this from other devices.")

    def password_required(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not config.password or session.get("authed"):
                return view(*args, **kwargs)
            if request.path.startswith("/api/"):
                return {"ok": False, "error": "Sign in again."}, 401
            return redirect(url_for("login", next=request.path))
        return wrapper

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not config.password:
            return redirect(url_for("index"))
        error = None
        if request.method == "POST":
            if secrets.compare_digest(request.form.get("password", "").encode(), config.password.encode()):
                session["authed"] = True
                session.permanent = True
                target = request.args.get("next") or ""
                # Only back into this site: a link can't send you elsewhere after signing in.
                return redirect(target if re.fullmatch(r"/(?![/\\]).*", target) else url_for("index"))
            time.sleep(1)  # Slows down guessing
            error = "Wrong password."
        return render_template("login.html", error=error)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login") if config.password else url_for("index"))

    @app.route("/")
    @password_required
    def index():
        return render_template("index.html", protected=bool(config.password), version=__version__)

    # --- State ---

    def context_for(model):
        window = context_of(config, model) if model else config.context
        return Context(window, min(config.reply_tokens, window // 2), config.system_prompt)

    def available():
        return model_scan.scan(config.models_dir)

    def state(chats):
        current = store.session(chats)
        models = available()
        ids = {m.id for m in models}
        model_id = current.get("model") if current.get("model") in ids else (models[0].id if models else None)
        model = next((m for m in models if m.id == model_id), None)
        shown = [s for s in chats["sessions"] if s["messages"]]
        return {"ok": True, "id": current["id"], "messages": current["messages"], "model": model_id,
                "models": [m.summary() for m in models], "context": context_for(model).usage(current),
                "busy": "Replying…" if busy["replying"] else None,
                "sessions": [summary(s) for s in sorted(shown, key=lambda s: s.get("updated") or 0, reverse=True)]}

    def refuse_while_replying():
        return ({"ok": False, "error": "Wait for the reply to finish."}, 409) if busy["replying"] else None

    @app.route("/api/chat")
    @password_required
    def chat_state():
        with store.lock:
            return state(store.load())

    @app.route("/api/status")
    @password_required
    def status():
        port = request.host.rsplit(":", 1)[-1] if ":" in request.host else "80"
        on_network = app.config.get("BOUND_HOST", "127.0.0.1") not in ("127.0.0.1", "localhost", "::1")
        tailnet, problem = tailscale.reach(port) if config.password else (None, None)
        loaded = runner.loaded()
        return {"ok": True, "loaded": loaded.summary() if loaded else None, "replying": busy["replying"],
                "tailscale": tailnet, "tailscale_problem": problem,
                "lan": f"http://{lan_ip()}:{port}" if on_network else None,
                "server_set": bool(config.llama_server and os.path.isfile(config.llama_server)),
                "models_dir": config.models_dir, "protected": bool(config.password), "version": __version__}

    # --- Sessions ---

    @app.route("/api/chat/clear", methods=["POST"])
    @password_required
    def chat_clear():
        """The open conversation's messages, and so its context, go; the other conversations stay as they are."""
        with store.lock:
            refused = refuse_while_replying()
            if refused:
                return refused
            chats = store.load()
            store.session(chats).update(messages=[], usage=None, title="", updated=time.time())
            store.save(chats)
            return state(chats)

    @app.route("/api/chat/new", methods=["POST"])
    @password_required
    def chat_new():
        """A new, empty conversation. The last is kept under Sessions."""
        with store.lock:
            refused = refuse_while_replying()
            if refused:
                return refused
            chats = store.load()
            current = store.session(chats)
            if current["messages"]:
                fresh = new_session(current.get("model"))
                chats["sessions"].append(fresh)
                chats["current"] = fresh["id"]
            store.save(chats)
            return state(chats)

    @app.route("/api/chat/open/<session_id>", methods=["POST"])
    @password_required
    def chat_open(session_id):
        """Carries on an earlier conversation. An empty one left for it goes, as it holds nothing."""
        with store.lock:
            refused = refuse_while_replying()
            if refused:
                return refused
            chats = store.load()
            if not store.session(chats, session_id):
                return {"ok": False, "error": "That chat is no longer saved."}, 404
            chats["sessions"] = [s for s in chats["sessions"] if s["messages"] or s["id"] == session_id]
            chats["current"] = session_id
            store.save(chats)
            return state(chats)

    @app.route("/api/chat/delete/<session_id>", methods=["POST"])
    @password_required
    def chat_delete(session_id):
        """Deletes a saved conversation. The one open goes for a new, empty one."""
        with store.lock:
            refused = refuse_while_replying()
            if refused:
                return refused
            chats = store.load()
            gone = store.session(chats, session_id)
            if not gone:
                return {"ok": False, "error": "That chat is no longer saved."}, 404
            chats["sessions"].remove(gone)
            if chats["current"] == session_id or not chats["sessions"]:
                fresh = new_session(gone.get("model"))
                chats["sessions"].append(fresh)
                chats["current"] = fresh["id"]
            store.save(chats)
            return state(chats)

    @app.route("/api/chat/message/delete", methods=["POST"])
    @password_required
    def chat_delete_message():
        """
        Deletes one message of the open conversation. index: which; count: how many messages the page has, so a chat
        changed meanwhile (on another device) loses nothing it didn't mean.
        """
        data = request.get_json(silent=True) or {}
        index, count = data.get("index"), data.get("count")
        if type(index) is not int or type(count) is not int:
            return {"ok": False, "error": "Say which message to delete."}, 400
        with store.lock:
            refused = refuse_while_replying()
            if refused:
                return refused
            chats = store.load()
            current = store.session(chats)
            if count != len(current["messages"]) or not 0 <= index < count:
                return {"ok": False, "error": "The chat changed meanwhile: look again, then try."}, 409
            del current["messages"][index]
            current.update(usage=None, title=title(current["messages"]), updated=time.time())
            store.save(chats)
            return state(chats)

    @app.route("/api/chat/image/<name>")
    @password_required
    def chat_image(name):
        path = store.image_path(name)
        if not path:
            abort(404)
        return send_file(path, mimetype="image/jpeg", max_age=86400)

    # --- The model ---

    @app.route("/api/unload", methods=["POST"])
    @password_required
    def unload():
        """Closes the loaded model now, giving its memory back to the GPU."""
        if busy["replying"]:
            return {"ok": False, "error": "Wait for the reply to finish."}, 409
        return {"ok": True, "unloaded": runner.unload()}

    @app.route("/api/shutdown", methods=["POST"])
    @password_required
    def shut_down():
        """Stops the model and the app. Start it again from its shortcut."""
        threading.Timer(0.5, shutdown).start()
        return {"ok": True}

    @app.route("/api/chat/send", methods=["POST"])
    @password_required
    def chat_send():
        """
        Sends a message in the open conversation and streams the reply back, a JSON object a line: {"status":
        "loading" | "writing"}, {"speed": tokens a second}, {"text": piece} as it is written, and last {"done": true,
        "cut_off": ...} or {"error": ...}. from: the conversation goes on from this message instead of its end, what
        came from it on dropped: your message there, changed (Edit and resend), or with no text, its reply written again
        (Regenerate). count: how many messages the page has. images: up to MAX_IMAGES, as data: URLs.
        """
        data = request.get_json(silent=True) or {}
        text, model_id, start, images = data.get("text"), data.get("model"), data.get("from"), data.get("images") or []
        if not isinstance(images, list) or len(images) > MAX_IMAGES or not all(isinstance(i, str) for i in images):
            return {"ok": False, "error": f"Add up to {MAX_IMAGES} images."}, 400
        regenerate = start is not None and not text and not images
        if not regenerate and (not isinstance(text, str) or len(text) > MAX_MESSAGE or not (text.strip() or images)):
            return {"ok": False, "error": f"Write a message of up to {MAX_MESSAGE:,} characters."}, 400
        if start is not None and (type(start) is not int or type(data.get("count")) is not int):
            return {"ok": False, "error": "Say which message to go on from."}, 400
        model = model_scan.find(config.models_dir, model_id) if isinstance(model_id, str) else None
        if not model:
            return {"ok": False, "error": "Choose a model: put GGUF files in the models folder."}, 400
        if images and not model.vision:
            return {"ok": False, "error": "This model can't see images: choose one marked vision."}, 400
        try:
            images = [read_image(image) for image in images]
        except ValueError as e:
            return {"ok": False, "error": str(e)}, 400
        with busy_lock:
            if busy["replying"]:
                return {"ok": False, "error": "Another reply is being written: wait for it to finish."}, 409
            busy["replying"] = True
        context = context_for(model)
        try:
            with store.lock:
                chats = store.load()
                current = store.session(chats)
                history = current["messages"]
                edited_images = None
                if start is not None:
                    expected = "assistant" if regenerate else "user"
                    if (data["count"] != len(history) or not 0 <= start < len(history)
                            or history[start].get("role") != expected
                            or (regenerate and (start == 0 or history[start - 1].get("role") != "user"))):
                        finish()
                        return {"ok": False, "error": "The chat changed meanwhile: look again, then try."}, 409
                    # Your message changed keeps its images, unless it comes with others.
                    edited_images = history[start].get("images") if not regenerate else None
                    del history[start:]
                    current["usage"] = None
                if not regenerate:
                    message = {"role": "user", "content": (text or "").strip(), "at": time.time()}
                    names = [store.keep_image(image) for image in images] or edited_images
                    if names:
                        message["images"] = names
                    history.append(message)
                current.update(model=model.id, title=title(history), updated=time.time())
                store.save(chats)
                session_id = current["id"]
                messages = context.sent(history, store, vision=model.vision)
                written = [m["content"] for m in history if m["role"] == "user"]
            sees = any(isinstance(m["content"], list) for m in messages)
        except BaseException:
            finish()
            raise

        def line(**values):
            return json.dumps(values, ensure_ascii=False) + "\n"

        def reply():
            # However the reply ends, the model is either kept loaded for the next message (it answered, or the page
            # stopped it) or closed (it failed): never left running untracked.
            parts, outcome, server, usage = [], None, None, None
            try:
                server = runner.take(model.id, sees)
                if server is None:
                    yield line(status="loading")
                    server = LlamaServer(config, model, vision=sees)
                    server.start()
                yield line(status="writing")
                ended = "stop"
                script = ForeignScript(written)
                spoken = 0.0
                for kind, value in server.chat(messages, context.reply_tokens):
                    if kind == "text":
                        value = script.clean(value)
                        if value:
                            parts.append(value)
                            yield line(text=value)
                    elif kind == "speed":
                        if time.monotonic() - spoken >= 0.5:
                            spoken = time.monotonic()
                            yield line(speed=round(value, 1))
                    elif kind == "usage":
                        usage = value
                    else:
                        ended = value
                if script.dropped and not "".join(parts).strip():
                    raise RuntimeError("The model replied only in Chinese: try again, or choose another model.")
                outcome = "cut_off" if ended == "length" else "done"
                yield line(done=True, cut_off=ended == "length")
            except GeneratorExit:
                outcome = outcome or "stopped"  # The page stopped it: what it wrote so far is kept
                raise
            except (RuntimeError, OSError, ValueError) as e:
                outcome = "error"
                yield line(error=str(e))
            finally:
                if server is not None:
                    if outcome in ("done", "cut_off", "stopped"):
                        runner.keep(server)
                    else:
                        server.stop()
                finish(parts, outcome, session_id, usage, context)

        response = Response(stream_with_context(reply()), mimetype="application/x-ndjson")
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Accel-Buffering"] = "no"
        return response

    def finish(parts=(), outcome=None, session_id=None, usage=None, context=None):
        """The reply kept with its conversation (what there is of it), and the model's turn over."""
        try:
            if parts and session_id:
                with store.lock:
                    chats = store.load()
                    current = store.session(chats, session_id)
                    if current:  # Deleted meanwhile, it stays deleted
                        message = {"role": "assistant", "content": "".join(parts), "at": time.time()}
                        if outcome in ("stopped", "cut_off", "error"):
                            message["ended"] = outcome
                        current["messages"].append(message)
                        current["updated"] = time.time()
                        total = (usage or {}).get("prompt", 0) + (usage or {}).get("completion", 0)
                        current["usage"] = {"tokens": total, "messages": len(current["messages"]),
                                            "kept": context.kept(current["messages"])} if total else None
                        store.save(chats)
        finally:
            with busy_lock:
                busy["replying"] = False

    return app
