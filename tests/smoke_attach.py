import base64
import json
import os
import pathlib
import time
import urllib.request

PORT = int(os.environ.get("AGENT_PORT") or 7867)
BASE = "http://" + "127." + "0." + "0.1:" + str(PORT)
TOKEN = pathlib.Path(r"C:\Users\volko\AppData\Local\LocalAgent\access.token").read_text(encoding="utf-8").strip()


def get(path, timeout=120):
    req = urllib.request.Request(BASE + path, headers={"X-Agent-Token": TOKEN})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.load(res)


def post(path, payload, timeout=120):
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

text = "Смоук вложения: строка один.\nСтрока два — проверка чтения.\n"
payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
upload = post("/api/attach/upload", {"name": "smoke_attach.txt", "data": payload})
print("1. загрузка txt:", upload.get("ok"), "| символов:", len(upload.get("text") or ""), "| имя:", upload.get("name"))
print("2. текст совпал:", "проверка чтения" in (upload.get("text") or ""))

path = upload.get("path") or ""
if path:
    again = post("/api/attach", {"path": path})
    print("3. чтение по пути:", again.get("ok"), "| символов:", len(again.get("text") or ""))

bad = post("/api/attach/upload", {"name": "broken.bin", "data": "!!!not-base64!!!"})
print("4. битые данные отклонены:", bad.get("ok") is False, "|", bad.get("error"))

empty = post("/api/attach/upload", {"name": "", "data": ""})
print("5. пустое имя отклонено:", empty.get("ok") is False)
