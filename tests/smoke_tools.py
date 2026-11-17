"""Проверка инструментов через провайдер (локальный режим) + отдача UI."""
import json
import os
import urllib.request

BASE = "http://localhost:7867"


def call(path, payload=None, timeout=600):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


state = call("/api/state")
model = next(m for m in state["models"] if "4B-Instruct-2507" in m["file"])
print("load:", call("/api/model/load", {"path": model["id"]}))

downloads = os.path.expanduser("~") + "/Downloads"
body = json.dumps({"messages": [{"role": "user", "content":
        "Сколько файлов в папке " + downloads + "? Используй инструмент list_files и назови число."}]}).encode()
req = urllib.request.Request(BASE + "/api/chat", data=body, headers={"Content-Type": "application/json"})
answer, events = [], []
with urllib.request.urlopen(req, timeout=600) as resp:
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data: "):
            continue
        event = json.loads(line[6:])
        kind = event.get("type")
        if kind == "delta":
            answer.append(event["text"])
        elif kind == "tool":
            events.append("tool:" + str(event.get("name")))
        elif kind == "tool_result":
            events.append("result:" + str(event.get("text"))[:60])
        elif kind == "error":
            answer.append("[ОШИБКА] " + str(event.get("text")))
        elif kind == "done":
            break
print("events:", events)
print("answer:", "".join(answer).strip()[:200])

for asset in ("/", "/ui/app.js", "/ui/style.css"):
    with urllib.request.urlopen(BASE + asset, timeout=30) as resp:
        print("asset", asset, resp.status, len(resp.read()))

print("unload:", call("/api/model/stop", {}))
