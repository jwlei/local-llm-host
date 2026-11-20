"""
The models: every GGUF in the models folder. A folder of its own may hold a model with its vision projector (a GGUF
with "mmproj" in its name), which lets it see images, and a model.json with its own settings:

    models/
      gemma-3-12b/                 one model that sees images
        gemma-3-12b-Q4_K_M.gguf
        mmproj-F16.gguf
        model.json                 optional: {"label": "...", "context": 8192, "cache_type": "q8_0", ...}
      Qwen3-14B-Q4_K_M.gguf        one model, text only

A model split into parts (name-00001-of-00003.gguf) is listed once, by its first part, which llama.cpp loads the rest from.
"""
import json
import os
import re
from dataclasses import dataclass, field

SPLIT_PART = re.compile(r"-(\d{5})-of-\d{5}\.gguf$", re.I)
# What model.json may set, and what each must be.
OPTIONS = {
    "label": lambda v: isinstance(v, str) and v.strip(),
    "context": lambda v: type(v) is int and v >= 512,
    "gpu_layers": lambda v: (type(v) is int and v >= 0) or v in ("all", "auto"),
    "cache_type": lambda v: isinstance(v, str) and re.fullmatch(r"[a-z0-9_]+", v),
    "threads": lambda v: type(v) is int and v >= 1,
    "temperature": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 2,
    "args": lambda v: isinstance(v, list) and all(isinstance(a, str) for a in v),
}


@dataclass(frozen=True)
class Model:
    id: str
    label: str
    path: str
    projector: str | None = None
    options: dict = field(default_factory=dict, hash=False, compare=False)

    @property
    def vision(self):
        return self.projector is not None

    def summary(self):
        return {"id": self.id, "label": self.label, "vision": self.vision}


def _is_model_file(name):
    lower = name.lower()
    if not lower.endswith(".gguf") or "mmproj" in lower:
        return False
    part = SPLIT_PART.search(name)
    return not part or part.group(1) == "00001"


def _stem(name):
    return SPLIT_PART.sub("", name)[:-5] if SPLIT_PART.search(name) else name[:-5]


def _options(folder):
    """A folder's model.json, keeping only the settings it may set, each as it must be."""
    try:
        with open(os.path.join(folder, "model.json"), encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        print(f"Ignoring {os.path.join(folder, 'model.json')}: {e}", flush=True)
        return {}
    if not isinstance(raw, dict):
        return {}
    kept = {key: value for key, value in raw.items() if key in OPTIONS and OPTIONS[key](value)}
    for key in raw.keys() - kept.keys():
        print(f"Ignoring {key!r} in {os.path.join(folder, 'model.json')}: not a setting, or not one it can be.", flush=True)
    return kept


def _files(folder):
    try:
        with os.scandir(folder) as entries:
            return sorted((e.name for e in entries if e.is_file()), key=str.casefold)
    except OSError:
        return []


def scan(models_dir):
    """Every model in the folder, sorted by name."""
    models = []
    try:
        with os.scandir(models_dir) as entries:
            entries = sorted(entries, key=lambda e: e.name.casefold())
    except OSError:
        return []
    for entry in entries:
        if entry.is_file() and _is_model_file(entry.name):
            models.append(Model(id=_stem(entry.name), label=_stem(entry.name), path=entry.path))
        elif entry.is_dir(follow_symlinks=False):
            files = _files(entry.path)
            weights = [name for name in files if _is_model_file(name)]
            if not weights:
                continue
            projectors = [name for name in files if name.lower().endswith(".gguf") and "mmproj" in name.lower()]
            projector = os.path.join(entry.path, projectors[0]) if projectors else None
            options = _options(entry.path)
            for name in weights:
                # One model in a folder is known by the folder; several, each by its file.
                single = len(weights) == 1
                model_id = entry.name if single else f"{entry.name}/{_stem(name)}"
                label = options.get("label", entry.name) if single else _stem(name)
                models.append(Model(id=model_id, label=label.strip(), path=os.path.join(entry.path, name),
                                    projector=projector, options=options))
    return models


def find(models_dir, model_id):
    return next((model for model in scan(models_dir) if model.id == model_id), None)
