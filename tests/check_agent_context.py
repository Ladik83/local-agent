"""Проверка, что навыки и план реально попадают в запрос к модели.

Поднимает крошечный сервер, который притворяется моделью, и смотрит,
что именно агент отправил: системные сообщения, набор инструментов, заметки.
Модель не нужна — тест детерминированный.
"""
import json
import pathlib
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import agent, config, plan, skills

CAPTURED = {"messages": [], "tools": []}
problems = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + ((" | " + detail) if detail else ""))
    if not ok:
        problems.append(name)


class FakeModel(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        CAPTURED["messages"] = body.get("messages") or []
        CAPTURED["tools"] = [item.get("function", {}).get("name") for item in body.get("tools") or []]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.end_headers()
        chunk = {"choices": [{"delta": {"content": "готово"}}]}
        self.wfile.write(("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def main() -> int:
    skills.seed()
    plan.set_plan("Проверка контекста", [
        {"text": "прочитать файл", "status": "done"},
        {"text": "исправить", "status": "doing"},
    ])

    server = ThreadingHTTPServer(("127.0.0.1", 8898), FakeModel)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    cfg = dict(config.load())
    cfg.update({"tools_auto": True, "allow_shell": True, "tools_off": [],
                "system_prompt": "ты тестовый помощник"})
    events = []
    convo = [{"role": "user", "content": "починить ошибку в коде и проверить"}]
    try:
        agent.chat(convo, cfg, events.append, {"kind": "local", "base_url": "http://127.0.0.1:8898/v1", "model": ""})
    finally:
        server.shutdown()

    system = [item for item in CAPTURED["messages"] if item.get("role") == "system"]
    texts = " ".join(str(item.get("content") or "") for item in system)
    check("системное сообщение агента", "тестовый помощник" in texts)
    check("навык подставлен", "Навык:" in texts, texts[:80])
    check("план подставлен", "Текущий план задачи" in texts)
    check("заметка о навыках", any("Навыки:" in str(event.get("text")) for event in events),
          str([event.get("text") for event in events if event.get("type") == "note"])[:120])
    check("инструменты переданы", "todo_write" in CAPTURED["tools"] and "verify_project" in CAPTURED["tools"],
          ", ".join(CAPTURED["tools"]))
    check("пользовательский текст на месте",
          any(item.get("role") == "user" and "починить" in str(item.get("content")) for item in CAPTURED["messages"]))
    check("ответ дошёл", any(event.get("type") == "done" for event in events))

    # --- регрессии: чат в обычном режиме и выключатель агента ---
    main_src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    chat_line = next((line for line in main_src.splitlines() if "agent.chat(convo, cfg, emit, target)" in line), "")
    if_line = next((line for line in main_src.splitlines() if 'payload.get("mode") == "team"' in line), "")
    check("agent.chat вне ветки team", bool(chat_line) and len(chat_line) - len(chat_line.lstrip()) == len(if_line) - len(if_line.lstrip()),
          "отступ чата %d, отступ if %d" % (len(chat_line) - len(chat_line.lstrip()), len(if_line) - len(if_line.lstrip())))

    off_cfg = dict(cfg)
    off_cfg["agent_mode"] = False
    off_events = []
    prepared_off = agent._prepare_tools(off_cfg, "прочитай файл", off_events.append)
    check("выключатель агента убирает инструменты", prepared_off.get("_only_tools") == [],
          str(off_events[-1]["text"] if off_events else ""))

    plan.reset()
    print()
    if problems:
        print("ПРОБЛЕМЫ:", len(problems))
        return 1
    print("контекст агента: все проверки пройдены")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
