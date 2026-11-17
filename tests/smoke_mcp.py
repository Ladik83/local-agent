"""Проверка MCP-клиента на локальном тестовом сервере (без интернета)."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import config, mcp  # noqa: E402

fake = ROOT / "tests" / "fake_mcp.py"
servers = [{"id": "echo", "command": sys.executable, "args": [str(fake)], "enabled": True}]
config.save({"mcp": servers})
loaded = config.load()["mcp"]

tools = mcp.tools_for(loaded)
print("1. инструменты MCP:", [item["function"]["name"] for item in tools])
print("2. схема аргументов:", tools[0]["function"]["parameters"] if tools else "нет")
print("3. вызов:", mcp.call("mcp__echo__say", {"text": "привет"}, loaded))
print("4. повторный вызов:", mcp.call("mcp__echo__say", {"text": "ещё раз"}, loaded))
print("5. неизвестный инструмент:", mcp.call("mcp__nope__x", {}, loaded))
mcp.stop_all()
print("6. сессии закрыты")
