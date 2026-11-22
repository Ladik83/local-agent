"""Проверка субагентов и плана за чатом без настоящей модели.

Поднимает крошечный сервер, который отвечает и SSE, и обычным JSON, и смотрит:
параллельные роли вернули ответы, сводка их содержит, план чата лёг в файл
и читается обратно, пустая задача отвергается.
"""
import json
import pathlib
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, plan, roles, subagents

SEEN = {"paths": [], "bodies": []}
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
        SEEN["paths"].append(self.path)
        SEEN["bodies"].append(body)
        messages = body.get("messages") or []
        system = " ".join(str(item.get("content") or "") for item in messages
                          if item.get("role") == "system")
        user = "\n".join(str(item.get("content") or "") for item in messages
                         if item.get("role") == "user")
        if "сводишь" in system:
            text = "ИТОГ: " + user
        else:
            text = "ответ роли " + system[:24].strip()
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.end_headers()
            chunk = {"choices": [{"delta": {"content": text}}]}
            self.wfile.write(("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            raw = json.dumps({"choices": [{"message": {"content": text}}]},
                             ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        self.wfile.flush()


def main() -> int:
    plan.reset()
    server = ThreadingHTTPServer(("127.0.0.1", 8897), FakeModel)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    cfg = dict(config.load())
    cfg.pop("roles", None)
    target = {"kind": "server", "base_url": "http://127.0.0.1:8897", "model": ""}
    events = []
    try:
        result = subagents.run("починить сборку проекта", ["analytics", "dev"],
                               cfg, target, events.append)
    finally:
        server.shutdown()

    check("субагенты: ok", result.get("ok") is True, str(result)[:160])
    answers = result.get("answers") or []
    check("субагенты: два ответа", len(answers) == 2, "получено %d" % len(answers))
    check("субагенты: роли разные", len(answers) == 2 and answers[0]["text"] != answers[1]["text"])
    check("субагенты: в ответах нет ошибок",
          all(not item["text"].startswith("ошибка:") for item in answers),
          str([item["text"][:60] for item in answers]))
    summary = str(result.get("summary") or "")
    check("сводка содержит оба ответа",
          all(item["text"] in summary for item in answers), summary[:120])
    check("события старт/готово", sum(1 for e in events if e.get("type") == "subagent_start") == 2
          and sum(1 for e in events if e.get("type") == "subagent_done") == 2)
    check("заметка о параллельности",
          any(e.get("type") == "note" and "2 ролей параллельно" in str(e.get("text")) for e in events))
    check("адрес /v1/chat/completions",
          bool(SEEN["paths"]) and all(path.endswith("/v1/chat/completions") for path in SEEN["paths"]),
          ", ".join(sorted(set(SEEN["paths"])))[:120])
    calls = [body for body in SEEN["bodies"] if "сводишь" not in json.dumps(
        [m for m in body.get("messages") or [] if m.get("role") == "system"], ensure_ascii=False)]
    check("stream=False, temperature 0.3, max_tokens 700",
          bool(calls) and all(body.get("stream") is False and float(body.get("temperature") or 0) == 0.3
                              and int(body.get("max_tokens") or 0) == 700 for body in calls),
          str(calls[0].get("temperature") if calls else None))

    plain = subagents.run("проверить идею", ["analytics", "dev"], cfg, None, lambda event: None)
    plain_summary = str(plain.get("summary") or "")
    check("сводка без модели — склейка",
          plain.get("ok") and all(item["text"] in plain_summary for item in plain.get("answers") or []),
          plain_summary[:120])

    empty = subagents.run("   ", ["analytics"], cfg, target, lambda event: None)
    check("пустая задача отвергнута", empty.get("ok") is False and empty.get("error"),
          str(empty))

    too_many = subagents.pick(cfg, ["analytics", "architect", "dev", "devops", "tester"])
    check("не больше 4 ролей", len(too_many) <= subagents.MAX_ROLES, "выбрано %d" % len(too_many))

    saved = plan.set_plan("Тест плана", ["первый шаг", {"text": "второй шаг", "status": "done"}],
                          "test-plan")
    path = config.DATA_DIR / "chats" / "test-plan.plan.json"
    check("план чата: файл создан", path.is_file(), str(path))
    check("план чата: сохранён", saved.get("title") == "Тест плана" and saved.get("total") == 2,
          str(saved)[:120])
    loaded = plan.load("test-plan")
    check("план чата: читается обратно",
          loaded.get("title") == "Тест плана" and len(loaded.get("steps") or []) == 2
          and loaded["steps"][1]["status"] == "done", str(loaded)[:160])
    check("план чата: прогресс", loaded.get("done") == 1 and loaded.get("open") == 1)
    check("план чата не трогает память", plan.snapshot().get("title") != "Тест плана",
          str(plan.snapshot())[:80])

    plan.save("test-plan", {"title": "Перезаписан", "steps": ["один"]})
    check("план чата: save() перезаписал", plan.load("test-plan").get("title") == "Перезаписан")

    plan.reset("test-plan")
    check("план чата: файл удалён", not path.exists())

    check("роли для API", all(item.get("id") and item.get("title")
                              for item in roles.merge(cfg)),
          str([item["id"] for item in roles.merge(cfg)])[:100])

    plan.reset()
    print()
    if problems:
        print("ПРОБЛЕМЫ:", len(problems), "—", ", ".join(problems))
        return 1
    print("субагенты и план чата: все проверки пройдены")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
