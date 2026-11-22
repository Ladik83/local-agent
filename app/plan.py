"""План задачи: шаги, статусы и прогресс.

Агент держит план сам (инструмент `todo_write`), интерфейс показывает его
над перепиской, а системное сообщение напоминает, на каком шаге он находится.

План можно привязать к чату: тогда он лежит в
`DATA_DIR/chats/<chat_id>.plan.json` и переживает перезапуск. Пустой
`chat_id` — прежнее поведение, план живёт в памяти процесса.
"""
from __future__ import annotations

import json
import pathlib
import re
import threading
import time

from . import config, diag

STATE = {"title": "", "steps": [], "updated": 0.0}
STATUSES = ("todo", "doing", "done", "skip")
MARK = {"todo": "○", "doing": "◐", "done": "●", "skip": "×"}

_NAME_RE = re.compile(r"[^0-9A-Za-z_-]")
_LOCAL = threading.local()


def _safe(chat_id) -> str:
    """Имя файла плана: только латиница, цифры, дефис и подчёркивание."""
    return _NAME_RE.sub("", str(chat_id or ""))[:60]


def bind(chat_id) -> None:
    """Привязать текущий поток к плану чата.

    Нужно инструментам, которые зовут set_plan без chat_id (todo_write):
    пустой chat_id тогда означает «план того чата, который сейчас отвечает».
    """
    _LOCAL.chat = _safe(chat_id)


def unbind() -> None:
    _LOCAL.chat = ""


def _scope(chat_id="") -> str:
    """Явный chat_id важнее привязки потока."""
    if chat_id:
        return _safe(chat_id)
    return _safe(getattr(_LOCAL, "chat", ""))


def _path(chat_id: str) -> pathlib.Path:
    return config.DATA_DIR / "chats" / (_safe(chat_id) + ".plan.json")


def _clean_status(value) -> str:
    text = str(value or "todo").strip().lower()
    return text if text in STATUSES else "todo"


def _clean_steps(steps) -> list:
    clean = []
    for item in steps or []:
        if isinstance(item, str):
            text, status = item, "todo"
        elif isinstance(item, dict):
            text, status = str(item.get("text") or item.get("step") or ""), _clean_status(item.get("status"))
        else:
            continue
        text = text.strip()
        if not text:
            continue
        clean.append({"text": text[:200], "status": status})
    return clean[:20]


def _norm(data) -> dict:
    data = data if isinstance(data, dict) else {}
    try:
        updated = float(data.get("updated") or 0)
    except (TypeError, ValueError):
        updated = 0.0
    return {"title": str(data.get("title") or "").strip()[:120],
            "steps": _clean_steps(data.get("steps")),
            "updated": updated}


def _empty() -> dict:
    return {"title": "", "steps": [], "updated": 0.0}


def _view(data: dict) -> dict:
    steps = [dict(step) for step in data.get("steps") or []]
    total = len(steps)
    done = sum(1 for step in steps if step.get("status") in ("done", "skip"))
    return {"title": data.get("title") or "", "steps": steps,
            "updated": data.get("updated") or 0.0,
            "total": total, "done": done, "open": total - done}


def _read(chat_id: str) -> dict:
    path = _path(chat_id)
    try:
        return _norm(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return _empty()
    except (OSError, ValueError) as exc:
        diag.warn("plan.py:read", exc)
        return _empty()


def _write(chat_id: str, data: dict) -> None:
    path = _path(chat_id)
    temp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)
    except OSError as exc:
        diag.warn("plan.py:write", exc)


def load(chat_id="") -> dict:
    """Прочитать план чата с диска (пустой chat_id — план в памяти)."""
    key = _safe(chat_id)
    return _view(STATE if not key else _read(key))


def save(chat_id, data) -> dict:
    """Записать готовый план (dict с title/steps) за чатом."""
    key = _scope(chat_id)
    clean = _norm(data)
    if not clean["updated"]:
        clean["updated"] = time.time()
    if not key:
        STATE.update(clean)
        return _view(STATE)
    _write(key, clean)
    return _view(clean)


def set_plan(title, steps, chat_id="") -> dict:
    """Заменить план целиком. steps: [{"text": "...", "status": "todo"}] или список строк."""
    key = _scope(chat_id)
    data = {"title": str(title or "").strip()[:120],
            "steps": _clean_steps(steps),
            "updated": time.time()}
    if key:
        _write(key, data)
        return _view(data)
    STATE.update(data)
    return _view(STATE)


def update_step(index, status, chat_id="") -> dict:
    key = _scope(chat_id)
    data = STATE if not key else _read(key)
    try:
        position = int(index) - 1
    except (TypeError, ValueError):
        return _view(data)
    if 0 <= position < len(data["steps"]):
        data["steps"][position]["status"] = _clean_status(status)
        data["updated"] = time.time()
        if key:
            _write(key, data)
    return _view(data)


def reset(chat_id="") -> dict:
    """Очистить план: за чатом — удалить файл, без chat_id — память."""
    key = _scope(chat_id)
    if key:
        try:
            _path(key).unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            diag.warn("plan.py:reset", exc)
        return _view(_empty())
    STATE.update(_empty())
    return _view(STATE)


def progress(chat_id="") -> dict:
    key = _scope(chat_id)
    data = STATE if not key else _read(key)
    total = len(data["steps"])
    done = sum(1 for step in data["steps"] if step["status"] in ("done", "skip"))
    return {"total": total, "done": done, "open": total - done}


def snapshot(chat_id="") -> dict:
    key = _scope(chat_id)
    return _view(STATE if not key else _read(key))


def as_text(chat_id="") -> str:
    """Короткая версия плана для системного сообщения."""
    key = _scope(chat_id)
    data = STATE if not key else _read(key)
    if not data["steps"]:
        return ""
    lines = ["Текущий план задачи:"]
    for number, step in enumerate(data["steps"], 1):
        lines.append("%d. %s %s" % (number, MARK.get(step["status"], "○"), step["text"]))
    left = sum(1 for step in data["steps"] if step["status"] not in ("done", "skip"))
    lines.append("Осталось шагов: %d. Отмечай выполнение через todo_write." % left if left else "Все шаги закрыты.")
    return "\n".join(lines)
