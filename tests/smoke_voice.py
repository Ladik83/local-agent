import base64
import json
import pathlib
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app import voice

B = "http://localhost:7867"


def get(path):
    with urllib.request.urlopen(B + path, timeout=120) as resp:
        return json.loads(resp.read().decode())


def post(path, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(B + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read().decode())


print("1. status:", {k: get("/api/voice")[k] for k in ("ready", "tts")})
wav = pathlib.Path(tempfile.gettempdir()) / "voice_api_test.wav"
voice.make_test_wav(str(wav))
print("2. тестовая запись:", wav.stat().st_size, "байт")
audio = base64.b64encode(wav.read_bytes()).decode()
t0 = time.time()
result = post("/api/voice/transcribe", {"audio": audio})
print("3. распознано за %.1f с:" % (time.time() - t0), repr(result.get("text"))[:120])
print("4. озвучка:", post("/api/voice/say", {"text": "проверка связи"})["ok"])
state = get("/api/state")
print("5. настройки: voice_tts =", state["settings"].get("voice_tts"), "| проекты:", state["settings"].get("projects"))
