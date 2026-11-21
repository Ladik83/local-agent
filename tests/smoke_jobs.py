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


def wait_job(name, seconds=60):
    deadline = time.time() + seconds
    while time.time() < deadline:
        job = (get("/api/jobs").get("jobs") or {}).get(name) or {}
        if job.get("done"):
            return job.get("result") or {}
        time.sleep(0.5)
    return {}


for _ in range(20):
    try:
        get("/api/state")
        break
    except Exception:
        time.sleep(1)

started = time.time()
answer = post("/api/model/load", {"path": r"C:\nonexistent\model.gguf", "background": True})
spent = time.time() - started
print("1. загрузка модели в фоне: ответ за %.2f c | ok = %s | job = %s" % (spent, answer.get("ok"), answer.get("job")))
result = wait_job("model_load", 60)
print("2. задача завершилась:", bool(result), "| ошибка:", bool(result.get("error")), "|", str(result.get("error"))[:60])

started = time.time()
queued = post("/api/update/apply", {"url": "http://127.0.0.1:9/manifest.json", "zip": ""})
spent = time.time() - started
print("3. обновление в фоне: ответ за %.2f c | ok = %s | job = %s" % (spent, queued.get("ok"), queued.get("job")))
update_result = wait_job("update_apply", 90)
print("4. обновление завершилось:", bool(update_result), "| ошибка:", bool(update_result.get("error")))

direct = post("/api/update/apply", {"url": "http://127.0.0.1:9/manifest.json", "zip": "", "wait": True})
print("5. синхронный режим по флагу wait:", "error" in direct or direct.get("ok") is False)
