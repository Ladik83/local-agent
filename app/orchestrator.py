"""Оркестратор: план по ролям и последовательное исполнение сабагентами."""
from __future__ import annotations

import json
import re

from . import agent, roles, stats

MAX_ROLES = 5


def _json_from(text: str):
    found = re.search(r"\{.*\}", text or "", re.S)
    if not found:
        return None
    try:
        return json.loads(found.group(0))
    except ValueError:
        return None


def _last_text(convo: list) -> str:
    for item in reversed(convo):
        if item.get("role") == "assistant" and item.get("content"):
            return str(item["content"])
    return ""


def _quiet(_event: dict) -> None:
    return


def build_plan(task: str, cfg: dict, target: dict) -> dict:
    """Просим модель-оркестратор разложить задачу по ролям."""
    planner_cfg = dict(cfg)
    catalogue = "; ".join("%s — %s" % (item["id"], item["title"]) for item in roles.merge(cfg))
    planner_cfg["system_prompt"] = roles.ORCHESTRATOR_PROMPT + "\n\nРоли: " + catalogue
    planner_cfg["_only_tools"] = []
    planner_cfg["temperature"] = 0.2
    convo = [{"role": "user", "content": "Задача: " + task}]
    try:
        text, _calls = agent._call(convo, planner_cfg, _quiet, target)
    except Exception:
        text = ""
    data = _json_from(text) or {}
    known = {item["id"] for item in roles.merge(cfg)}
    steps = []
    for raw in (data.get("steps") or []):
        if not isinstance(raw, dict):
            continue
        role_id = str(raw.get("role") or "").strip()
        if role_id not in known:
            continue
        steps.append({"role": role_id, "task": str(raw.get("task") or task).strip()})
    if not steps:
        steps = [{"role": "analytics", "task": task}, {"role": "reviewer", "task": "Проверь вывод аналитика по задаче: " + task}]
    return {"mode": str(data.get("mode") or "pipeline"), "steps": steps[:MAX_ROLES]}


def _cfg_for_role(cfg: dict, role: dict) -> dict:
    role_cfg = dict(cfg)
    role_cfg["system_prompt"] = roles.prompt_for(cfg, role)
    allowed = [item for item in roles.tools_of(role) if item not in ("write_file", "run_command", "web_search")]
    role_cfg["_only_tools"] = allowed
    role_cfg["allow_write"] = bool(cfg.get("allow_write")) and "write_file" in allowed
    role_cfg["allow_shell"] = bool(cfg.get("allow_shell")) and "run_command" in allowed
    role_cfg["allow_web"] = bool(cfg.get("allow_web")) and "web_search" in allowed
    return role_cfg


def _tag(emit, role: dict, step: int, total: int):
    def send(event: dict) -> None:
        payload = dict(event)
        payload["role"] = role["title"]
        payload["role_id"] = role["id"]
        payload["step"] = step
        payload["total"] = total
        emit(payload)
    return send


def run(task: str, cfg: dict, emit, target: dict) -> list:
    """План → роли по очереди → сводка. Возвращает финальную диалог."""
    emit({"type": "plan_start", "text": "Составляю план по ролям…"})
    plan = build_plan(task, cfg, target)
    emit({"type": "plan", "mode": plan["mode"], "steps": plan["steps"]})
    notes = []
    total = len(plan["steps"])
    for index, step in enumerate(plan["steps"], 1):
        role = roles.get(cfg, step["role"])
        if role is None:
            continue
        emit({"type": "role_start", "role": role["title"], "role_id": role["id"], "step": index, "total": total})
        stats.mark(role=role["title"], step="%d/%d" % (index, total), phase="роль")
        prompt = step["task"] or task
        if notes:
            prompt += "\n\nЧто уже сделали коллеги:\n" + "\n\n".join(notes)[-2500:]
        convo = [{"role": "user", "content": prompt}]
        role_target = dict(target or {})
        if role.get("model"):
            role_target["model"] = role["model"]
        answer = agent.chat(convo, _cfg_for_role(cfg, role), _tag(emit, role, index, total), role_target)
        notes.append("[%s] %s" % (role["title"], _last_text(answer)))
        stats.mark(role="", step="", phase="ответ")
        emit({"type": "role_done", "role": role["title"], "role_id": role["id"], "step": index, "total": total})

    stats.mark(role="", step="", phase="сводка")
    emit({"type": "summary_start", "text": "Свожу итог…"})
    summary_cfg = dict(cfg)
    summary_cfg["system_prompt"] = (
        "Ты оркестратор команды. Собери для пользователя короткий итог: что сделано, "
        "какие выводы и что делать дальше. Без воды и без пересказа переписки."
    )
    summary_cfg["_only_tools"] = []
    convo = [{"role": "user", "content": "Задача: %s\n\nЧто сделали роли:\n%s" % (task, "\n\n".join(notes)[-6000:])}]
    final = agent.chat(convo, summary_cfg, emit, target)
    emit({"type": "done", "messages": final, "notes": notes})
    return final
