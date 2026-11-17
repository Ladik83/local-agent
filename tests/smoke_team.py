"""Проверка команды ролей: план → роли → сводка."""
import json
import time
import urllib.request

BASE = "http://localhost:7867"


def call(path, payload=None, timeout=900):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def stream_chat(text, mode):
    body = json.dumps({"messages": [{"role": "user", "content": text}], "mode": mode}).encode()
    req = urllib.request.Request(BASE + "/api/chat", data=body, headers={"Content-Type": "application/json"})
    events, answer = [], []
    with urllib.request.urlopen(req, timeout=900) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            kind = ev.get("type")
            if kind in ("plan",):
                events.append("plan:" + ",".join(s["role"] for s in ev.get("steps") or []))
            elif kind in ("role_start", "role_done"):
                events.append(kind + ":" + str(ev.get("role")))
            elif kind == "delta":
                answer.append(ev["text"])
            elif kind == "error":
                events.append("ОШИБКА:" + str(ev.get("text")))
            elif kind == "done":
                break
    return events, "".join(answer)


state = call("/api/state")
print("1. ролей по умолчанию:", len(state["team"]["roles"]), "| команда включена:", state["team"]["enabled"])

rows = []
for role in state["team"]["roles"]:
    item = dict(role)
    if item["id"] == "analytics":
        item["thinking"] = "high"
    rows.append(item)
call("/api/roles", {"roles": rows})
state = call("/api/state")
saved = [r for r in state["team"]["roles"] if r["id"] == "analytics"][0]
print("2. правка роли сохранилась:", saved["thinking"] == "high")

model = None
for item in state["models"]:
    if "4B-Instruct-2507" in item["file"]:
        model = item
print("3. загрузка модели:", call("/api/model/load", {"path": model["id"]}).get("ok"))
print("4. включение команды:", call("/api/team", {"enabled": True})["enabled"])

started = time.time()
events, answer = stream_chat("Сколько .py файлов в папке C:/Users/volko/LocalAgent/app? Ответь числом и списком.", "team")
print("5. события (%.0f с):" % (time.time() - started))
for item in events:
    print("   ", item)
print("6. сводка:", answer.strip()[:400].replace("\n", " | "))

call("/api/team", {"enabled": False})
call("/api/model/stop", {})
print("7. выключил команду и выгрузил модель")
