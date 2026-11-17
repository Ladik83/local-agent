import json
import urllib.request

B = "http://localhost:7867"


def get(path):
    with urllib.request.urlopen(B + path, timeout=300) as resp:
        return json.loads(resp.read().decode())


def post(path, payload=None):
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(B + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read().decode())


state = get("/api/state")
model = None
for item in state["models"]:
    if "4B-Instruct-2507" in item["file"]:
        model = item
print("load:", post("/api/model/load", {"path": model["id"]})["ok"])

body = json.dumps({"messages": [{"role": "user", "content": "Сколько будет 7*6? Используй инструмент calc."}]}).encode()
req = urllib.request.Request(B + "/api/chat", data=body, headers={"Content-Type": "application/json"})
tools = []
with urllib.request.urlopen(req, timeout=600) as resp:
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if line.startswith("data: "):
            event = json.loads(line[6:])
            if event.get("type") == "tool":
                tools.append(event.get("name"))
            if event.get("type") == "done":
                break
print("tools used:", tools)

stats = get("/api/stats")
act = stats["activity"]
print("tokens in/out:", act["tokens_in"], "/", act["tokens_out"])
print("speed in/out:", act["speed_in"], "/", act["speed_out"], "| calls:", act["calls"])
print("model rss:", stats["engine"]["rss_mb"], "МБ | cpu:", stats["cpu"], "%")

saved = post("/api/chats/save", {"messages": [{"role": "user", "content": "тест чата"}, {"role": "assistant", "content": "ответ"}]})
print("saved chat:", saved["title"])
print("chats list:", [item["title"] for item in get("/api/chats")["chats"]])
print("chat messages:", len(get("/api/chats/get?id=" + saved["id"])["messages"]))
post("/api/chats/delete", {"id": saved["id"]})
print("after delete:", len(get("/api/chats")["chats"]))
print("unload:", post("/api/model/stop")["ok"])
