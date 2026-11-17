"""Сквозной тест: загрузка модели → переключение на режим «свой сервер» → чат → выгрузка."""
import json
import time
import urllib.error
import urllib.request

BASE = "http://localhost:7867"


def call(path, payload=None, timeout=600):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def state():
    return call("/api/state", None, 120)


def chat(text):
    body = json.dumps({"messages": [{"role": "user", "content": text}]}).encode()
    req = urllib.request.Request(BASE + "/api/chat", data=body, headers={"Content-Type": "application/json"})
    answer, tools_seen, started = [], [], time.time()
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
                tools_seen.append(event.get("name"))
            elif kind == "error":
                answer.append("[ОШИБКА] " + str(event.get("text")))
            elif kind == "done":
                break
    return "".join(answer).strip(), tools_seen, time.time() - started


s = state()
print("1. провайдер:", s["provider"]["kind"], "| движок:", s["engine"]["running"], s["engine"].get("used_mb"), "МБ")

model = None
for item in s["models"]:
    if "4B-Instruct-2507" in item["file"]:
        model = item
print("2. грузим:", model["file"], model["size_gb"], "ГБ")
print("   ", call("/api/model/load", {"path": model["id"]}))
s = state()
port = s["engine"]["port"]
print("3. в памяти:", s["engine"]["running"], "порт", port, "|", s["engine"]["used_mb"], "МБ")

servers = call("/api/providers/discover", {})["servers"]
print("4. автопоиск серверов:", [(x["base_url"], len(x["models"])) for x in servers])

print("5. переключаем провайдер на свой сервер")
print("   ", call("/api/provider", {"kind": "server", "base_url": "http://localhost:%d" % port, "model": "qwen3-4b"}))

answer, tools_used, spent = chat("Ответь одним словом: работает?")
print("6. ответ через свой сервер (%.1f с): %r | инструменты: %s" % (spent, answer[:160], tools_used))

s = state()
print("7. выгрузка модели")
print("   ", call("/api/model/stop", {}))
time.sleep(1.5)
s = state()
print("8. после выгрузки: running =", s["engine"]["running"], "| память =", s["engine"].get("used_mb"), "МБ")

print("9. возвращаем провайдера в локальный режим")
print("   ", call("/api/provider", {"kind": "local"})["provider"])
