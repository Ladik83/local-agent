"""Тестовый MCP-сервер: минимальный JSON-RPC по stdio (для проверки клиента)."""
import json
import sys


def send(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake", "version": "1.0"},
        }})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [{
            "name": "say",
            "description": "Повторяет переданный текст",
            "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        }]}})
    elif method == "tools/call":
        params = msg.get("params") or {}
        args = params.get("arguments") or {}
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": "эхо: " + str(args.get("text"))}],
            "isError": False,
        }})
    elif mid is not None:
        send({"jsonrpc": "2.0", "id": mid, "result": {}})
