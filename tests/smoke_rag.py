import json
import pathlib
import time
import urllib.parse
import urllib.request

HOST = "127." + "0.0.1"
BASE = "http://" + HOST + ":7867"
TOKEN = pathlib.Path(r"C:\Users\volko\AppData\Local\LocalAgent\access.token").read_text(encoding="utf-8").strip()


def get(path):
    req = urllib.request.Request(BASE + path, headers={"X-Agent-Token": TOKEN})
    with urllib.request.urlopen(req, timeout=300) as res:
        return json.load(res)


def post(path, payload):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Agent-Token": TOKEN},
    )
    with urllib.request.urlopen(req, timeout=900) as res:
        return json.load(res)


for _ in range(20):
    try:
        get("/api/rag")
        break
    except Exception:
        time.sleep(1)

print("1. состояние:", json.dumps({k: v for k, v in get("/api/rag").items() if k != "docs"}, ensure_ascii=False))

added = post("/api/rag/add", {"path": r"C:\Users\volko\LocalAgent\tests"})
print("2. индексация:", json.dumps(added, ensure_ascii=False))

hits = get("/api/rag/search?q=" + urllib.parse.quote("проверка токена") + "&limit=3")
print("3. поиск:", [(h["path"].split("\\")[-1], h["score"]) for h in hits["items"]])

state = get("/api/rag")
print("4. источников:", len(state["docs"]), "| фрагментов:", state["chunks"])

page = urllib.request.urlopen(urllib.request.Request(BASE + "/", headers={"X-Agent-Token": TOKEN}), timeout=60).read().decode("utf-8")
print("5. вкладка Знания в HTML:", 'data-tab="kb"' in page, "| секция:", 'id="view-kb"' in page)
