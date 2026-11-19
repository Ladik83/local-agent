"""Telegram: отправка сообщений ботом (токен из DPAPI или файла)."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

from . import config, diag, keystore

TOKEN_FILE = config.DATA_DIR / "telegram_token.txt"
API = "https://api.telegram.org/bot"


def token() -> str:
    try:
        return keystore.get("telegram_bot") or ""
    except Exception:
        return ""


def token_from_file() -> str:
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _call(method: str, params: dict) -> dict:
    key = token() or token_from_file()
    if not key:
        return {"ok": False, "error": "нет токена бота"}
    url = API + key + "/" + method
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        diag.warn("telegram/" + method, exc)
        return {"ok": False, "error": str(exc)}


def get_me() -> dict:
    return _call("getMe", {})


def find_chat_id() -> str:
    data = _call("getUpdates", {"limit": 10})
    for item in reversed(data.get("result") or []):
        msg = item.get("message") or item.get("edited_message") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            return str(chat["id"])
    return ""


def send(text: str, chat_id: str = "") -> dict:
    target = chat_id or find_chat_id()
    if not target:
        return {"ok": False, "error": "не нашёл chat_id: напишите боту любое сообщение"}
    return _call("sendMessage", {"chat_id": target, "text": text[:4000]})
