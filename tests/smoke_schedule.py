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

task = "смоук расписания: собрать сводку"
saved = post("/api/schedule/save", {"time": "07:15", "days": "weekdays", "mode": "agent", "task": task})
print("1. сохранение:", saved.get("ok"), "| id:", (saved.get("item") or {}).get("id") or saved.get("id"))
item_id = (saved.get("item") or {}).get("id") or saved.get("id")

items = get("/api/schedule").get("items") or []
found = [item for item in items if item.get("task") == task]
print("2. в списке:", len(found), "| время:", (found[0] or {}).get("time"), "| дни:", (found[0] or {}).get("days"))

bad = post("/api/schedule/save", {"time": "7:5", "task": task})
print("3. отказ на кривое время:", bad.get("ok") is False, "|", bad.get("error"))

removed = post("/api/schedule/delete", {"id": item_id})
left = [item for item in (get("/api/schedule").get("items") or []) if item.get("task") == task]
print("4. удаление:", removed.get("ok"), "| осталось:", len(left))
