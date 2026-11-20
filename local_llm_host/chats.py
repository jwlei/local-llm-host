"""
The conversations, kept in data/chats.json so they are the same on every device: New starts another, Clear empties the
open one, and Sessions opens an earlier one to carry on. Images sent with messages are kept in data/chat_images while
a chat uses them.
"""
import base64
import io
import json
import math
import os
import re
import secrets
import threading
import time

from PIL import Image, ImageOps, UnidentifiedImageError

IMAGE_NAME = re.compile(r"[0-9a-f]{16}\.jpg")
MAX_IMAGES = 4
# What is kept of an image: at most this along its longer side, as JPEG. The model reads it smaller still.
IMAGE_EDGE = 1536
MAX_IMAGE_BYTES = 12 * 2 ** 20
# What an image takes of the model's context, about (Gemma reads one as 256 tokens, and its marks around it).
IMAGE_TOKENS = 280
MAX_MESSAGE = 16000
# What of the conversation is sent: as much of the latest as fits, at about 3 characters a token.
CHARS_PER_TOKEN = 3
# What the chat template adds around each message (its role and the marks between them), about.
TOKENS_PER_MESSAGE = 6
TITLE_CHARS = 60
# Chinese, Japanese and Korean script (and its punctuation and full-width forms). Qwen, trained mostly on Chinese, can
# start a reply in it, or drift into it, when asked in another language.
CJK = re.compile(r"[⺀-⿟　-〿぀-ヿ㄀-ㇿ㐀-䶿一-鿿가-힯豈-﫿︰-﹏＀-￯]+")


class ForeignScript:
    """
    A reply's Chinese (or other CJK script) taken out as it streams, when the conversation itself has none: what is
    left of a line that was all of it goes too, and the reply starts at its first real word.
    """

    def __init__(self, texts):
        """texts: what the user wrote in the conversation."""
        self.active = not any(CJK.search(text) for text in texts)
        self.started = False
        self.dropped = False

    def clean(self, piece):
        if not self.active:
            return piece
        cleaned = CJK.sub("", piece)
        self.dropped = self.dropped or cleaned != piece
        if not self.started:
            cleaned = cleaned.lstrip()
            self.started = bool(cleaned)
        return cleaned


def new_session(model=None):
    now = time.time()
    return {"id": f"{int(now)}_{secrets.token_hex(3)}", "title": "", "created": now, "updated": now, "model": model,
            "messages": [], "usage": None}


def title(messages):
    message = next((m for m in messages if m.get("role") == "user"), None)
    first = " ".join(message["content"].split()) if message else ""
    if message and not first and message.get("images"):
        first = "Image"
    return first if len(first) <= TITLE_CHARS else first[:TITLE_CHARS - 1].rstrip() + "…"


def summary(session):
    return {"id": session["id"], "title": session.get("title") or "New chat", "updated": session.get("updated"),
            "messages": len(session["messages"])}


def read_image(data_url):
    """
    An image the page sent (a data: URL), as what is kept of it: checked to be one, turned upright, at most IMAGE_EDGE
    along its longer side, as JPEG. ValueError says why it can't be.
    """
    if len(data_url) > MAX_IMAGE_BYTES * 4 // 3 + 100:
        raise ValueError(f"Images of up to {MAX_IMAGE_BYTES // 2 ** 20} MB.")
    match = re.fullmatch(r"data:image/[\w.+-]+;base64,([A-Za-z0-9+/=]+)", data_url)
    if not match:
        raise ValueError("That isn't an image this can read.")
    try:
        with Image.open(io.BytesIO(base64.b64decode(match.group(1)))) as image:
            image.load()
            upright = ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise ValueError("That isn't an image this can read.") from None
    upright.thumbnail((IMAGE_EDGE, IMAGE_EDGE))
    kept = io.BytesIO()
    upright.save(kept, "JPEG", quality=90)
    return kept.getvalue()


class ChatStore:
    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, "chats.json")
        self.images_dir = os.path.join(data_dir, "chat_images")
        self.lock = threading.Lock()

    # --- The sessions: {"current": id, "sessions": [{id, title, created, updated, model, messages, usage}]} ---

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                chats = json.load(f)
            if isinstance(chats, dict) and isinstance(chats.get("sessions"), list):
                sessions = [s for s in chats["sessions"]
                            if isinstance(s, dict) and s.get("id") and isinstance(s.get("messages"), list)]
                if sessions:
                    current = chats.get("current")
                    if not any(s["id"] == current for s in sessions):
                        current = sessions[0]["id"]
                    return {"current": current, "sessions": sessions}
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            # Kept aside rather than written over: what it held can still be recovered by hand.
            aside = f"{self.path}.unreadable-{int(time.time())}"
            print(f"chats.json could not be read ({e}); moved to {aside}.", flush=True)
            try:
                os.replace(self.path, aside)
            except OSError:
                pass
        session = new_session()
        return {"current": session["id"], "sessions": [session]}

    def save(self, chats):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        temporary = self.path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(chats, f, ensure_ascii=False, indent=1)
        os.replace(temporary, self.path)
        self._drop_images(chats)

    def _drop_images(self, chats):
        """The images no chat names any more (their message, or their chat, deleted or cleared): gone from the disk."""
        named = {name for s in chats["sessions"] for m in s["messages"] for name in m.get("images") or ()}
        try:
            files = os.listdir(self.images_dir)
        except FileNotFoundError:
            return
        for name in files:
            if IMAGE_NAME.fullmatch(name) and name not in named:
                try:
                    os.remove(os.path.join(self.images_dir, name))
                except OSError:
                    pass  # Open elsewhere just now: the next save takes it

    @staticmethod
    def session(chats, session_id=None):
        session_id = session_id or chats["current"]
        return next((s for s in chats["sessions"] if s["id"] == session_id), None)

    # --- Images ---

    def keep_image(self, data):
        os.makedirs(self.images_dir, exist_ok=True)
        name = secrets.token_hex(8) + ".jpg"
        with open(os.path.join(self.images_dir, name), "wb") as f:
            f.write(data)
        return name

    def image_path(self, name):
        path = os.path.join(self.images_dir, name)
        return path if IMAGE_NAME.fullmatch(name) and os.path.isfile(path) else None

    def image_part(self, name):
        path = self.image_path(name)
        if not path:
            return None  # Gone from the disk: the message goes without it
        with open(path, "rb") as f:
            data = f.read()
        return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")}}


class Context:
    """What the model reads of a conversation, and how much of its context that takes."""

    def __init__(self, window, reply_tokens, instruction):
        self.window = window
        self.reply_tokens = reply_tokens
        self.instruction = instruction
        self.history_chars = max(1, (window - reply_tokens - 512) * CHARS_PER_TOKEN)

    @staticmethod
    def size(message):
        """What a message takes, in characters: its text, and its images as the text they cost as much as."""
        return len(message["content"]) + len(message.get("images") or ()) * IMAGE_TOKENS * CHARS_PER_TOKEN

    def kept(self, messages):
        """How many of the latest messages fit what is sent."""
        size = 0
        for count, message in enumerate(reversed(messages)):
            size += self.size(message)
            if count and size > self.history_chars:
                return count
        return len(messages)

    def sent(self, messages, store, vision=False):
        """
        The conversation as the model reads it: the instruction, then as much of the latest as fits. vision: the
        model sees images, so a message's go with it, before its text; otherwise it is sent the text alone.
        """
        def content(message):
            parts = [p for p in map(store.image_part, message.get("images") or ()) if p] if vision else []
            return [*parts, {"type": "text", "text": message["content"]}] if parts else message["content"]

        kept = messages[len(messages) - self.kept(messages):]
        return [{"role": "system", "content": self.instruction}, *({"role": m["role"], "content": content(m)} for m in kept)]

    def estimate(self, messages):
        return sum(math.ceil(self.size(m) / CHARS_PER_TOKEN) + TOKENS_PER_MESSAGE for m in messages)

    def usage(self, session):
        """
        How the context is spent, in tokens: the instruction, the conversation it reads, the room kept for the reply,
        and what is free. Counted by the model after its last reply (measured), with what came since estimated.
        left_out: older messages it no longer reads.
        """
        messages = session["messages"]
        kept = self.kept(messages)
        instruction = self.estimate([{"content": self.instruction}])
        usage = session.get("usage") or {}
        counted = usage.get("messages") or 0
        # The model's own count holds while it still reads the same messages from the start (none more left out).
        if usage.get("tokens") and counted <= len(messages) and kept == (usage.get("kept") or 0) + len(messages) - counted:
            conversation = max(0, usage["tokens"] - instruction) + self.estimate(messages[counted:])
            measured = counted == len(messages)
        else:
            conversation, measured = self.estimate(messages[len(messages) - kept:]), False
        return {"window": self.window, "instruction": instruction, "conversation": conversation,
                "reply": self.reply_tokens, "measured": measured, "left_out": len(messages) - kept,
                "free": max(0, self.window - instruction - conversation - self.reply_tokens)}
