import json
import os
import pathlib
import time
import urllib.request

PORT = int(os.environ.get("AGENT_PORT") or 7867)
BASE = "http://" + "127." + "0." + "0.1:" + str(PORT)
TOKEN = pathlib.Path(r"C:\Users\volko\AppData\Local\LocalAgent\access.token").read_text(encoding="utf-8").strip()


def get(path, timeout=60):
    req = urllib.request.Request(BASE + path, headers={"X-Agent-Token": TOKEN})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.load(res)


def post(path, payload, timeout=60):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Agent-Token": TOKEN},
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.load(res)


for _ in range(20):
    try:
        get("/api/state")
        break
    except Exception:
        time.sleep(1)

info = get("/api/telegram")
print("1. статус:", "token_set =", info.get("token_set"), "| chat =", repr(info.get("chat")), "| бот =", repr(info.get("bot")))

original_chat = info.get("chat") or ""
saved = post("/api/telegram", {"chat": "-100999"})
after = get("/api/telegram")
print("2. сохранение чата:", saved.get("ok"), "| стало:", after.get("chat"))
print("3. токен не тронут:", after.get("token_set") == info.get("token_set"))

post("/api/telegram", {"chat": original_chat})
restored = get("/api/telegram")
print("4. чат восстановлен:", restored.get("chat") == original_chat)
