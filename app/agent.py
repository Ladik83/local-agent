"""Цикл агента: стриминг ответа модели и вызов инструментов."""
from __future__ import annotations

from . import diag

import base64
import json
import pathlib
import time
import urllib.error
import urllib.request

from . import engine, stats, tools, vision

MAX_STEPS = 8
STOP = {"flag": False}
API_CHAT = "/chat" + "/completions"


def _open(payload: dict, target: dict):
	body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
	headers = {"Content-Type": "application/json"}
	key = (target or {}).get("api_key") or ""
	if key:
		headers["Authorization"] = "Bearer " + key
	req = urllib.request.Request(
		target["base_url"] + API_CHAT,
		data=body,
		headers=headers,
		method="POST",
	)
	return urllib.request.urlopen(req, timeout=900)


def _image_parts(paths) -> list:
    """Локальные файлы картинок → части сообщения в формате OpenAI (data URL)."""
    parts = []
    for raw in paths or []:
        item = pathlib.Path(str(raw))
        if not item.is_file():
            continue
        try:
            data = item.read_bytes()
        except OSError:
            continue
        mime = vision.MIME.get(item.suffix.lower(), "image/png")
        parts.append({"type": "image_url",
                      "image_url": {"url": "data:%s;base64,%s" % (mime, base64.b64encode(data).decode("ascii"))}})
    return parts


def _clean(messages: list) -> list:
    """Сообщения без служебного поля images — для истории и события done."""
    return [{key: value for key, value in item.items() if key != "images"} for item in messages]


def _with_images(messages: list) -> list:
    """Сообщения с полем images превращаем в мультимодальные части контента."""
    out = []
    for item in messages:
        images = item.get("images") or []
        if not images:
            out.append({key: value for key, value in item.items() if key != "images"})
            continue
        parts = [{"type": "text", "text": str(item.get("content") or "")}]
        parts.extend(_image_parts(images))
        clean = {key: value for key, value in item.items() if key not in ("images", "content")}
        clean["content"] = parts
        out.append(clean)
    return out


def _call(convo: list, cfg: dict, emit, target: dict, allow_tools: bool = True):
    payload = {
        "messages": [{"role": "system", "content": cfg["system_prompt"]}] + _with_images(list(convo)),
        "stream": True,
        "temperature": float(cfg.get("temperature") or 0.7),
        "tool_choice": "auto",
    }
    if (target or {}).get("model"):
        payload["model"] = target["model"]
    payload["stream_options"] = {"include_usage": True}
    allowed = tools.schemas(cfg) if allow_tools else []
    if allowed:
        payload["tools"] = allowed
    if not allow_tools:
        payload.pop("tool_choice", None)
    text = ""
    calls = {}
    usage = {}
    started = time.time()
    with _open(payload, target) as resp:
        for raw in resp:
            if STOP["flag"]:
                raise RuntimeError("остановлено пользователем")
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if not chunk or chunk[0] == "[":
                continue
            try:
                event = json.loads(chunk)
            except ValueError:
                continue
            if event.get("usage"):
                usage = event["usage"] or {}
            choice = (event.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            think = delta.get("reasoning_content") or delta.get("reasoning")
            if think:
                emit({"type": "think", "text": think})
            piece = delta.get("content")
            if piece:
                text += piece
                emit({"type": "delta", "text": piece})
            for part in delta.get("tool_calls") or []:
                slot = calls.setdefault(part.get("index", 0), {"id": "", "name": "", "args": ""})
                if part.get("id"):
                    slot["id"] = part["id"]
                fn = part.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["args"] += fn["arguments"]
    elapsed = max(0.001, time.time() - started)
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    if not prompt_tokens:
        prompt_tokens = stats.estimate_tokens("".join(str(item.get("content") or "") for item in payload["messages"]))
    completion_tokens = int(usage.get("completion_tokens") or 0) or stats.estimate_tokens(text)
    stats.add_tokens(prompt_tokens, completion_tokens, elapsed)
    ordered = []
    for index, item in calls.items():
        if not item["name"]:
            continue
        ordered.append({
            "id": item["id"] or "call_%d" % index,
            "type": "function",
            "function": {"name": item["name"], "arguments": item["args"] or "{}"},
        })
    return text, ordered


def chat_plain(convo: list, cfg: dict, emit, target: dict) -> list:
    """Один ответ без инструментов — для моделей зрения и простых задач."""
    convo = list(convo)
    text, _calls = _call(convo, cfg, emit, target, allow_tools=False)
    convo.append({"role": "assistant", "content": text})
    emit({"type": "done", "messages": _clean(convo)})
    return convo


def chat(convo: list, cfg: dict, emit, target: dict = None) -> list:
    """Полный цикл: ответ, инструменты, ответ. emit получает события для UI."""
    convo = _compact(list(convo), cfg, emit, target)
    for _step in range(MAX_STEPS):
        if STOP["flag"]:
            emit({"type": "cancelled", "text": "Остановлено пользователем"})
            break
        text, calls = _call(convo, cfg, emit, target)
        if not calls:
            convo.append({"role": "assistant", "content": text})
            break
        convo.append({"role": "assistant", "content": text, "tool_calls": calls})
        for item in calls:
            name = item["function"]["name"]
            try:
                args = json.loads(item["function"]["arguments"] or "{}")
            except ValueError:
                args = {}
            emit({"type": "tool", "name": name, "args": args})
            stats.mark(tool=name, phase="инструмент")
            if tools.approval_gate(name, args, cfg, emit):
                result = tools.call(name, args, cfg)
            else:
                result = "Пользователь не разрешил этот вызов — сделай иначе или спроси."
            stats.mark(tool="", phase="ответ")
            emit({"type": "tool_result", "name": name, "text": result[:800]})
            convo.append({"role": "tool", "tool_call_id": item["id"], "content": result})
        convo = _compact(convo, cfg, emit, target)
    emit({"type": "done", "messages": _clean(convo)})
    return convo


STOP = {"flag": False}


def _chars(convo: list) -> int:
    return sum(len(str(item.get("content") or "")) for item in convo)


def _quiet(_event: dict) -> None:
    return


def _compact(convo: list, cfg: dict, emit, target: dict) -> list:
    """Сжимаем старую часть диалога, чтобы не упираться в контекст модели."""
    limit = int(cfg.get("context") or 8192) * 3
    if _chars(convo) <= limit * 0.7 or len(convo) < 10:
        return convo
    head, tail = convo[:-6], convo[-6:]
    parts = []
    for item in head:
        text = str(item.get("content") or "")
        if text:
            parts.append(item.get("role") + ": " + text[:400])
    raw = " | ".join(parts)
    summary = ""
    try:
        quiet = dict(cfg)
        quiet["system_prompt"] = "Ты сжимаешь диалог. Ответь пятью короткими пунктами, без воды."
        quiet["_only_tools"] = []
        question = "Сожми предыдущий диалог: " + raw[-6000:]
        text, _calls = _call([{"role": "user", "content": question}], quiet, _quiet, target)
        summary = str(text or "").strip()[:800]
    except Exception:
        summary = ""
    note = {"role": "user", "content": "Кратко о предыдущем: " + (summary or "детали опущены")}
    try:
        emit({"type": "compact", "text": "Контекст сжат, продолжаю"})
    except Exception as exc:
        diag.warn("agent.py:168", exc)
    return [note] + tail
