"""Живая проверка каталога MCP: запуск сервера, список инструментов, вызов.

Запуск: python tests/smoke_mcp_catalog.py
Настройки не меняет: работает с отдельной копией записи сервера.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import main, mcp


def run() -> int:
    entry = next((item for item in main.MCP_CATALOG if item["id"] == "everything"), None)
    if not entry:
        print("в каталоге нет проверочного сервера")
        return 1
    args = main._expand_args(entry["args"])
    print("1. сервер:", entry["title"], "|", entry["command"], " ".join(args))
    session = mcp.session(entry["id"], entry["command"], args)
    print("2. инструментов:", len(session.tools), "| ошибка:", session.error or "нет")
    if not session.tools:
        print("ПРОВАЛ: сервер не отдал инструменты")
        return 1
    names = [tool["raw"] for tool in session.tools]
    print("   примеры:", ", ".join(names[:5]))

    servers = [{"id": entry["id"], "command": entry["command"], "args": args, "enabled": True}]
    echo = next((name for name in names if "echo" in name), "")
    ok = True
    if echo:
        answer = mcp.call("mcp__" + entry["id"] + "__" + echo, {"message": "проверка связи"}, servers)
        print("3. вызов echo:", answer[:120])
        ok = "проверка" in answer.lower()
    mcp.stop_all()
    print("4. серверы остановлены:", not mcp._SESSIONS)
    print("итог:", "OK" if ok else "ПРОВАЛ")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
