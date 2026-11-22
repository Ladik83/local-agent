"""Параллельные субагенты: несколько ролей отвечают на одну задачу сразу.

Каждая роль — отдельный запрос к модели без инструментов и без истории:
свой системный промпт роли плюс правило «отвечай по делу». Ответы собираются
в один список, затем главная модель (если она есть) сводит их в короткий итог;
если модели нет — итог склеивается детерминированно.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import diag, roles

MAX_ROLES = 4
WORKERS = 3
RULE = "Отвечай по делу: только суть и конкретные шаги, без вступлений и извинений."
TIMEOUT = 300
TEMPERATURE = 0.3
MAX_TOKENS = 700
ANSWER_CUT = 800
SUMMARY_CUT = 1200
SUMMARY_SYSTEM = "Ты сводишь ответы специалистов в короткий итог"

_LOCK = threading.Lock()


def _url(base_url: str) -> str:
    """Адрес чата: база может быть и с /v1, и без него."""
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def _post(messages: list, target: dict | None = None, temperature: float = TEMPERATURE,
          max_tokens: int = MAX_TOKENS, timeout: int = TIMEOUT) -> str:
    """Один не-стриминговый запрос к OpenAI-совместимому серверу."""
    target = target or {}
    url = _url(str(target.get("base_url") or ""))
    if not url:
        raise ValueError("не выбран провайдер модели")
    payload = {"messages": messages, "stream": False,
               "temperature": temperature, "max_tokens": max_tokens}
    if target.get("model"):
        payload["model"] = target["model"]
    headers = {"Content-Type": "application/json"}
    key = target.get("api_key") or ""
    if key:
        headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", "replace")
    data = json.loads(raw)
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("пустой ответ модели")
    message = choices[0].get("message") or {}
    return str(message.get("content") or "").strip()


def _system(role: dict) -> str:
    """Промпт роли + правило, не длиннее 400 символов."""
    prompt = str(role.get("prompt") or "").strip()
    text = (prompt + "\n\n" + RULE).strip() if prompt else RULE
    return text[:400]


def pick(cfg: dict, role_ids=None) -> list:
    """Выбранные роли; пустой список — первые три стандартные."""
    available = roles.merge(cfg or {})
    wanted = [str(item) for item in (role_ids or []) if str(item).strip()]
    by_id = {item["id"]: item for item in available}
    chosen = [by_id[item] for item in wanted if item in by_id]
    if not chosen:
        chosen = list(available[:3])
    return chosen[:MAX_ROLES]


def run(task, role_ids=None, cfg=None, target=None, emit=None) -> dict:
    """Спросить несколько ролей параллельно и свести ответы.

    Возвращает {'ok': True, 'answers': [{'role', 'text'}], 'summary': str}
    либо {'ok': False, 'error': ...}. Ошибка одной роли не роняет остальные.
    """
    cfg = cfg or {}
    notify = emit or (lambda event: None)

    def say(event: dict) -> None:
        try:
            with _LOCK:
                notify(event)
        except Exception as exc:
            diag.warn("subagents:emit", exc)

    text = str(task or "").strip()
    if not text:
        return {"ok": False, "error": "пустая задача"}
    picked = pick(cfg, role_ids)
    if not picked:
        return {"ok": False, "error": "не выбраны роли"}

    say({"type": "note", "text": "Субагенты: %d ролей параллельно" % len(picked)})
    answers: list = [None] * len(picked)

    def work(index: int, role: dict) -> None:
        title = str(role.get("title") or role.get("id") or "роль")
        say({"type": "subagent_start", "role": title})
        messages = [
            {"role": "system", "content": _system(role)},
            {"role": "user", "content": text},
        ]
        try:
            answer = _post(messages, target)
            if not answer:
                answer = "ошибка: пустой ответ модели"
        except Exception as exc:
            answer = "ошибка: " + str(exc)[:300]
        answers[index] = {"role": title, "text": answer}
        say({"type": "subagent_done", "role": title, "text": answer[:ANSWER_CUT]})

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(work, number, role) for number, role in enumerate(picked)]
        for future in futures:
            future.result()

    answers = [item for item in answers if item]
    summary = _summarize(text, answers, target)
    return {"ok": True, "answers": answers, "summary": summary}


def _summarize(task: str, answers: list, target: dict | None = None) -> str:
    """Итог от главной модели; при сбое — простая склейка «Роль: ответ»."""
    joined = "\n\n".join("%s: %s" % (item["role"], item["text"]) for item in answers)
    if (target or {}).get("base_url") and answers:
        block = "\n\n".join("%s: %s" % (item["role"], item["text"][:SUMMARY_CUT]) for item in answers)
        try:
            summary = _post([
                {"role": "system", "content": SUMMARY_SYSTEM},
                {"role": "user", "content": "Задача: %s\n\nОтветы специалистов:\n%s" % (task, block)},
            ], target)
            if summary:
                return summary
        except Exception as exc:
            diag.warn("subagents:summary", exc)
    return joined
