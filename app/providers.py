"""Провайдеры моделей: локальный llama-server, чужие OpenAI-серверы, внешние API."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import config, engine, keystore

UA = {"User-Agent": "LocalAgent/0.2"}
LOCAL_PORTS = [1234, 11434, 8080, 8000, 5000, 12345, 8090, 8091]
PRESETS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "mistral": "https://api.mistral.ai/v1",
    "custom": "",
}


def _headers(api_key: str) -> dict:
    headers = {"Content-Type": "application/json"}
    headers.update(UA)
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    return headers


def normalize_base(base: str) -> str:
    """Приводим адрес к виду http://host:port/v1."""
    base = (base or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/v1"):
        return base
    return base + "/v1"


def probe(base_url: str, api_key: str = "", timeout: float = 4.0) -> dict:
    """Спросить у сервера список моделей."""
    base = normalize_base(base_url)
    if not base:
        return {"ok": False, "models": [], "error": "пустой адрес"}
    try:
        request = urllib.request.Request(base + "/models", headers=_headers(api_key))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return {"ok": False, "models": [], "error": "HTTP %d" % exc.code}
    except Exception as exc:
        return {"ok": False, "models": [], "error": str(exc)[:120]}
    models = []
    rows = data.get("data") if isinstance(data, dict) else data
    for item in rows or []:
        if isinstance(item, dict) and item.get("id"):
            models.append(item["id"])
        elif isinstance(item, str):
            models.append(item)
    return {"ok": True, "models": models, "error": None}


def discover() -> list:
    """Найти запущенные OpenAI-совместимые серверы на этом компьютере."""
    ports = list(LOCAL_PORTS)
    own = engine.SERVER.get("port")
    if own:
        ports.insert(0, own)
    found = []
    seen = set()
    for port in ports:
        base = "http://localhost:%d/v1" % port
        if base in seen:
            continue
        seen.add(base)
        info = probe(base, timeout=1.5)
        if info["ok"] and info["models"]:
            found.append({"base_url": base, "port": port, "models": info["models"]})
    return found


def key_name(base_url: str) -> str:
    return "key:" + (base_url or "").rstrip("/").lower()


def resolve(cfg: dict) -> dict:
    """Куда стучаться за ответом при текущих настройках."""
    provider = cfg.get("provider") or {}
    kind = provider.get("kind") or "local"
    if kind == "local":
        base = engine.base_url()
        return {"kind": "local", "base_url": (base + "/v1") if base else "",
                "model": None, "api_key": ""}
    base = normalize_base(provider.get("base_url"))
    return {
        "kind": kind,
        "base_url": base,
        "model": provider.get("model") or "",
        "api_key": keystore.get(key_name(base)),
    }


def summary(cfg: dict) -> dict:
    provider = cfg.get("provider") or {}
    base = (provider.get("base_url") or "").rstrip("/")
    return {
        "kind": provider.get("kind") or "local",
        "base_url": base,
        "model": provider.get("model") or "",
        "has_key": bool(keystore.get(key_name(base))) if base else False,
        "presets": PRESETS,
    }
