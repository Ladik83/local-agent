"""Эмбеддинги: отдельный llama-server с моделью эмбеддингов, запасной вариант — локальный хеш."""
from __future__ import annotations

from . import config
from . import diag
from . import engine

import json
import pathlib
import time
import urllib.request

STATE = {"mode": None, "dim": 0, "model": "", "error": None}
NEEDLES = ("embed", "bge", "minilm")
SMALL = 8 * 1024 * 1024
CACHE = {"ts": 0.0, "path": ""}
PREFIX = {
    "nomic": ("search_document: ", "search_query: "),
    "e5": ("passage: ", "query: "),
}


def _prefix(path: str, kind: str) -> str:
    name = pathlib.Path(path).name.lower()
    for key, pair in PREFIX.items():
        if key in name:
            return pair[1] if kind == "query" else pair[0]
    return ""


def find_model(cfg=None) -> str:
    """Путь к модели эмбеддингов: из настроек или первый подходящий файл на диске."""
    cfg = cfg or config.load()
    explicit = str(cfg.get("embed_model") or "").strip()
    if explicit and pathlib.Path(explicit).exists():
        return explicit
    now = time.time()
    cached = CACHE.get("path") or ""
    if cached and now - CACHE.get("ts", 0.0) < 60 and pathlib.Path(cached).exists():
        return cached
    roots = [cfg.get("models_dir")] + list(cfg.get("extra_dirs") or [])
    for raw in roots:
        root = pathlib.Path(str(raw or ""))
        if not root.is_dir():
            continue
        try:
            for item in root.rglob("*.gguf"):
                name = item.name.lower()
                if "mmproj" in name:
                    continue
                if any(needle in name for needle in NEEDLES) and item.stat().st_size > SMALL:
                    CACHE.update({"ts": now, "path": str(item)})
                    return str(item)
        except OSError as exc:
            diag.warn("embed/scan", exc)
    return ""


def vectors(texts, kind: str = "document", timeout: int = 120):
    """Векторы от модели эмбеддингов. None — если модель недоступна."""
    items = [str(text) for text in texts]
    if not items:
        return []
    path = find_model()
    if not path:
        STATE.update(mode="hash", error="model_missing")
        return None
    base = engine.embed_base_url()
    if not base:
        started = engine.start_embed(path)
        if not started.get("ok"):
            STATE.update(mode="hash", error=started.get("error") or "start_failed")
            return None
        base = engine.embed_base_url()
    if not base:
        return None
    prefix = _prefix(path, kind)
    payload = [prefix + text for text in items] if prefix else items
    body = json.dumps({"input": payload, "model": "local"}).encode("utf-8")
    request = urllib.request.Request(base + "/v1/embeddings", data=body,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8", "replace"))
    out = []
    for item in data.get("data") or []:
        vector = item.get("embedding") if isinstance(item, dict) else None
        if not vector:
            return None
        out.append([float(value) for value in vector])
    if len(out) != len(items):
        return None
    STATE.update(mode="local", dim=len(out[0]), model=path, error=None)
    return out


def available() -> bool:
    """Проверяем модель один раз и запоминаем режим."""
    if STATE["mode"] is None:
        try:
            STATE["mode"] = "local" if vectors(["check"]) else "hash"
        except Exception as exc:
            diag.warn("embed/probe", exc)
            STATE.update(mode="hash", error=str(exc))
    return STATE["mode"] == "local"


def reset() -> None:
    STATE.update(mode=None, dim=0, error=None)


def status() -> dict:
    path = find_model()
    running = bool(engine.embed_base_url())
    cfg = config.load()
    models_dir = str(cfg.get("models_dir") or "")
    dir_ok = bool(models_dir) and pathlib.Path(models_dir).is_dir()
    return {
        "mode": STATE.get("mode") or ("ready" if running else "off"),
        "dim": STATE.get("dim") or 0,
        "model": path,
        "name": pathlib.Path(path).name if path else "",
        "running": running,
        "dir": models_dir,
        "dir_ok": dir_ok,
        "error": STATE.get("error"),
    }
