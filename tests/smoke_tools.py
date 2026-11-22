"""Проверка API инструментов и навыков на живом сервере (без загрузки модели).

Запуск: AGENT_PORT=7879 python tests/smoke_tools.py
Токен берётся из папки данных приложения; порт — из AGENT_PORT (по умолчанию 7867).
"""
import json
import os
import pathlib
import urllib.parse
import urllib.request

PORT = int(os.environ.get("AGENT_PORT") or 7867)
BASE = "http://127.0.0.1:" + str(PORT)
TOKEN_FILE = pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "LocalAgent" / "access.token"


def call(path, payload=None, timeout=60):
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json",
               "X-Agent-Token": TOKEN_FILE.read_text(encoding="utf-8").strip()}
    method = "POST" if data is not None else "GET"
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def main() -> int:
    problems = []

    def check(name, ok, detail=""):
        print(("OK   " if ok else "FAIL ") + name + ((" | " + detail) if detail else ""))
        if not ok:
            problems.append(name)

    state = call("/api/state")
    check("состояние отдаёт настройки", "settings" in state and "engine" in state)
    check("в состоянии есть план и навыки", "plan" in state and "skills" in state)

    tools = call("/api/tools")
    names = [item["name"] for item in tools.get("builtin") or []]
    check("каталог инструментов", len(names) >= 13, "инструментов: %d" % len(names))
    check("план и проверка на месте", {"todo_write", "verify_project"} <= set(names))

    skills = call("/api/skills")
    items = skills.get("items") or []
    check("навыки перечислены", len(items) >= 1, "навыков: %d" % len(items))
    if items:
        one = call("/api/skills/one?name=" + urllib.parse.quote(items[0]["name"]))
        check("навык читается целиком", bool(one.get("ok")) and bool((one.get("item") or {}).get("body")))

    agents = call("/api/agents/info")
    check("внешние агенты", agents.get("ok") is True and isinstance(agents.get("clients"), list),
          "клиентов: %d" % len(agents.get("clients") or []))

    roles = call("/api/subagents/roles")
    check("роли для субагентов", bool(roles.get("items")), "ролей: %d" % len(roles.get("items") or []))

    print()
    if problems:
        print("ПРОБЛЕМЫ:", len(problems))
        return 1
    print("API инструментов, навыков и субагентов: все проверки пройдены")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
