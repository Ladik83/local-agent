"""Планировщик: задачи по расписанию, результат сохраняется в чаты."""
from __future__ import annotations

import json
import pathlib
import threading
import time
import uuid

from . import config, diag

STORE = config.DATA_DIR / "schedules.json"
LOCK = threading.Lock()
TICK = 30


def _load() -> list:
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _save(items: list) -> None:
    try:
        STORE.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as exc:
        diag.warn("schedule save", exc)


def list_all() -> list:
    return _load()


def upsert(item: dict) -> dict:
    task = str(item.get("task") or "").strip()
    when = str(item.get("time") or "").strip()
    if not task:
        return {"ok": False, "error": "Пустое задание"}
    if len(when) != 5 or when[2] != ":":
        return {"ok": False, "error": "Время в формате ЧЧ:ММ"}
    try:
        hour, minute = int(when[:2]), int(when[3:])
    except ValueError:
        return {"ok": False, "error": "Время в формате ЧЧ:ММ"}
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return {"ok": False, "error": "Недопустимое время"}
    with LOCK:
        items = _load()
        target = None
        for entry in items:
            if entry.get("id") == item.get("id"):
                target = entry
                break
        if target is None:
            target = {"id": uuid.uuid4().hex[:8], "created": time.time(), "last": 0}
            items.append(target)
        target.update({
            "time": when,
            "days": str(item.get("days") or "daily"),
            "task": task,
            "mode": str(item.get("mode") or "agent"),
            "enabled": bool(item.get("enabled", True)),
        })
        _save(items)
    return {"ok": True, "item": target, "items": items}


def get(task_id: str):
    """Одна задача по id или None."""
    for entry in _load():
        if str(entry.get("id")) == str(task_id):
            return entry
    return None


def remove(task_id: str) -> dict:
    with LOCK:
        items = [entry for entry in _load() if entry.get("id") != task_id]
        _save(items)
    return {"ok": True, "items": items}


def _day_ok(days: str, weekday: int) -> bool:
    if days == "daily":
        return True
    if days == "weekdays":
        return weekday < 5
    if days == "weekends":
        return weekday >= 5
    return True


def due(now: float | None = None) -> list:
    """Задачи, которым пора: время совпало и сегодня ещё не запускались."""
    now = time.time() if now is None else now
    stamp = time.localtime(now)
    today = "%04d-%02d-%02d" % (stamp.tm_year, stamp.tm_mon, stamp.tm_mday)
    out = []
    for entry in _load():
        if not entry.get("enabled"):
            continue
        if not _day_ok(str(entry.get("days") or "daily"), stamp.tm_wday):
            continue
        when = str(entry.get("time") or "")
        if when != "%02d:%02d" % (stamp.tm_hour, stamp.tm_min):
            continue
        if str(entry.get("last_day") or "") == today:
            continue
        out.append(entry)
    return out


def mark_ran(task_id: str, note: str = "") -> None:
    now = time.time()
    stamp = time.localtime(now)
    today = "%04d-%02d-%02d" % (stamp.tm_year, stamp.tm_mon, stamp.tm_mday)
    with LOCK:
        items = _load()
        for entry in items:
            if entry.get("id") == task_id:
                entry["last"] = now
                entry["last_day"] = today
                entry["last_note"] = note[:400]
        _save(items)
