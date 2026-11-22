"""План задачи: шаги, статусы и прогресс.

Агент держит план сам (инструмент `todo_write`), интерфейс показывает его
над перепиской, а системное сообщение напоминает, на каком шаге он находится.
"""
from __future__ import annotations

import time

STATE = {"title": "", "steps": [], "updated": 0.0}
STATUSES = ("todo", "doing", "done", "skip")
MARK = {"todo": "○", "doing": "◐", "done": "●", "skip": "×"}


def _clean_status(value) -> str:
    text = str(value or "todo").strip().lower()
    return text if text in STATUSES else "todo"


def set_plan(title, steps) -> dict:
    """Заменить план целиком. steps: [{"text": "...", "status": "todo"}] или список строк."""
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
    STATE.update({"title": str(title or "").strip()[:120], "steps": clean[:20], "updated": time.time()})
    return snapshot()


def update_step(index, status) -> dict:
    try:
        position = int(index) - 1
    except (TypeError, ValueError):
        return snapshot()
    if 0 <= position < len(STATE["steps"]):
        STATE["steps"][position]["status"] = _clean_status(status)
        STATE["updated"] = time.time()
    return snapshot()


def reset() -> dict:
    STATE.update({"title": "", "steps": [], "updated": 0.0})
    return snapshot()


def progress() -> dict:
    total = len(STATE["steps"])
    done = sum(1 for step in STATE["steps"] if step["status"] in ("done", "skip"))
    return {"total": total, "done": done, "open": total - done}


def snapshot() -> dict:
    data = {"title": STATE["title"], "steps": list(STATE["steps"]), "updated": STATE["updated"]}
    data.update(progress())
    return data


def as_text() -> str:
    """Короткая версия плана для системного сообщения."""
    if not STATE["steps"]:
        return ""
    lines = ["Текущий план задачи:"]
    for number, step in enumerate(STATE["steps"], 1):
        lines.append("%d. %s %s" % (number, MARK.get(step["status"], "○"), step["text"]))
    left = progress()["open"]
    lines.append("Осталось шагов: %d. Отмечай выполнение через todo_write." % left if left else "Все шаги закрыты.")
    return "\n".join(lines)
