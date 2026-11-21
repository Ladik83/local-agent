import json
import os
import pathlib
import time
import urllib.parse
import urllib.request

PORT = int(os.environ.get("AGENT_PORT") or 7867)
BASE = "http://" + "127." + "0." + "0.1:" + str(PORT)
TOKEN = pathlib.Path(r"C:\Users\volko\AppData\Local\LocalAgent\access.token").read_text(encoding="utf-8").strip()


def get(path, timeout=120):
    req = urllib.request.Request(BASE + path, headers={"X-Agent-Token": TOKEN})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.load(res)


def post(path, payload, timeout=300):
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

state = get("/api/state")
emb = state.get("embeddings") or {}
print("1. состояние: hf =", state.get("hf"), "| embeddings =", emb.get("mode"),
      "| папка моделей:", emb.get("dir"), "есть" if emb.get("dir_ok") else "НЕТ")

found = get("/api/hf/search?q=" + urllib.parse.quote("nomic-embed"))
items = found.get("items") or []
print("2. поиск на Hugging Face:", found.get("ok"), "| моделей:", len(items),
      "| первая:", (items[0]["repo"] if items else "-"), (items[0]["downloads"] if items else 0))

if items:
    files = get("/api/hf/files?repo=" + urllib.parse.quote(items[0]["repo"]))
    gguf = files.get("files") or []
    print("3. файлы модели:", files.get("ok"), "| .gguf:", len(gguf),
          "| самый лёгкий:", (gguf[0]["file"], gguf[0]["size_gb"]) if gguf else "-")

bad = get("/api/hf/files?repo=" + urllib.parse.quote("../etc/passwd"))
print("4. защита от чужого пути:", bad.get("ok") is False, "|", bad.get("error"))

check = post("/api/embeddings/check", {})
if check.get("ok"):
    print("5. проверка эмбеддингов: True | размер вектора:", check.get("dim"))
else:
    print("5. проверка эмбеддингов: пропущено —", str(check.get("error"))[:70])

hits = get("/api/rag/search?q=" + urllib.parse.quote("проверка токена") + "&limit=3").get("items") or []
sources = sorted({str(hit.get("source")) for hit in hits})
print("6. поиск по базе знаний:", len(hits), "| источники:", sources,
      "| лучший:", round((hits[0].get("score") or 0), 3) if hits else "-")

page = urllib.request.urlopen(urllib.request.Request(BASE + "/", headers={"X-Agent-Token": TOKEN}), timeout=60).read().decode("utf-8")
marks = {"подвкладки моделей": 'data-models="online"' in page,
         "поиск HF": 'id="hfQuery"' in page,
         "статус эмбеддингов": 'id="embState"' in page,
         "пересчёт базы": 'id="kbReindex"' in page,
         "токен HF": 'id="hfToken"' in page,
         "ключ обновлений": 'id="updatePubkey"' in page}
print("7. интерфейс:", marks)
