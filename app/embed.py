"""Эмбеддинги: пробуем llama-server /embeddings, иначе остаёмся на локальном хеше."""
import json
import urllib.request

from . import config
from . import diag
from . import providers

STATE = {"mode": None}


def remote_vectors(texts):
    """Векторы от сервера моделей или None, если он не умеет эмбеддинги."""
    target = providers.resolve(config.load())
    base = str(target.get("base_url") or "").rstrip("/")
    if not base:
        return None
    url = base + "/embeddings"
    body = json.dumps({"input": list(texts), "model": target.get("model") or ""}).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=40) as response:
        data = json.loads(response.read().decode("utf-8", "replace"))
    items = data.get("data") or []
    if len(items) != len(texts):
        return None
    vectors = []
    for item in items:
        vector = item.get("embedding") if isinstance(item, dict) else None
        if not vector:
            return None
        vectors.append([float(value) for value in vector])
    return vectors


def available() -> bool:
    """Один раз проверяем сервер и запоминаем режим."""
    if STATE["mode"] is None:
        try:
            probe = remote_vectors(["проверка"])
            STATE["mode"] = "remote" if probe else "hash"
        except Exception as exc:
            diag.warn("embed/probe", exc)
            STATE["mode"] = "hash"
    return STATE["mode"] == "remote"


def reset():
    STATE["mode"] = None
