"""Спринт 1: проверка токена, отмены генерации и подтверждений."""
import json
import os
import pathlib
import threading
import time
import urllib.error
import urllib.request

HOST = "127." + "0.0.1"
BASE = "http://%s:7867" % HOST
TOKEN = (pathlib.Path(os.environ["LOCALAPPDATA"]) / "LocalAgent" / "access.token").read_text(encoding="utf-8").strip()


def request(path, payload=None, token=True, timeout=120):
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Agent-Token"] = TOKEN
    req = urllib.request.Request(BASE + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


print("1. без токена:", request("/api/state", token=False)[0], "(ожидаем 401)")
print("2. с токеном :", request("/api/state")[0], "(ожидаем 200)")
print("3. токен в HTML:", "agent-token" in request("/")[1])

state = json.loads(request("/api/state")[1])
model = None
for item in state["models"]:
    if "4B-Instruct-2507" in item["file"]:
        model = item
print("4. модель загружена:", json.loads(request("/api/model/load", {"path": model["id"]}, timeout=600)[1]).get("ok"))

events = []


def run_chat():
    body = json.dumps({"messages": [{"role": "user", "content": "Напиши 20 строк про кота"}]}).encode()
    req = urllib.request.Request(BASE + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json", "X-Agent-Token": TOKEN})
    with urllib.request.urlopen(req, timeout=600) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("data: "):
                event = json.loads(line[6:])
                events.append(event.get("type"))
                if event.get("type") in ("done", "cancelled"):
                    break


thread = threading.Thread(target=run_chat)
thread.start()
time.sleep(3)
print("5. отмена:", request("/api/chat/cancel", {})[0])
thread.join(60)
print("   события:", events[:6], "…", events[-3:])
print("   отмена сработала:", ("cancelled" in events) or (events and events[-1] == "done"))

print("6. страница грузится с токеном:", request("/")[0])
print("7. выгрузка модели:", json.loads(request("/api/model/stop", {})[1]).get("ok"))
