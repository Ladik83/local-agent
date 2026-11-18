"""История чатов: простые JSON-файлы в папке данных (без базы)."""
from __future__ import annotations

from . import diag

import json
import pathlib
import time
import uuid

from . import config

DIR = config.CHATS_DIR


def _path(chat_id: str) -> pathlib.Path:
    safe = "".join(ch for ch in str(chat_id) if ch.isalnum() or ch in "-_")[:64]
    return DIR / (safe + ".json")


def _title(messages: list) -> str:
    for item in messages or []:
        if item.get("role") == "user" and item.get("content"):
            return str(item["content"])[:70]
    return "Новый чат"


def list_all() -> list:
    items = []
    for file in DIR.glob("*.json"):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except Exception:
            continue
        items.append({
            "id": file.stem,
            "title": data.get("title") or "Без названия",
            "updated": data.get("updated") or 0,
            "count": len(data.get("messages") or []),
        })
    items.sort(key=lambda item: item["updated"], reverse=True)
    return items[:60]


def save(chat_id: str, messages: list, title: str = "") -> dict:
    cid = chat_id or uuid.uuid4().hex[:12]
    data = {
        "id": cid,
        "title": title or _title(messages),
        "updated": time.time(),
        "messages": messages or [],
    }
    try:
        _path(cid).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        diag.warn("chats.py:53", exc)
    return {"id": cid, "title": data["title"]}


def load(chat_id: str) -> dict:
    try:
        return json.loads(_path(chat_id).read_text(encoding="utf-8"))
    except Exception:
        return {"id": chat_id, "messages": []}


def delete(chat_id: str) -> None:
    try:
        _path(chat_id).unlink()
    except OSError as exc:
        diag.warn("chats.py:68", exc)
