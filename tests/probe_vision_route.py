"""Проверка маршрута /api/chat с картинкой: должен ответить слот зрения, не грузя чат-модель."""
import json
import os
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOKEN_FILE = pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "LocalAgent" / "access.token"
BASE = "http://127.0.0.1:7879"
SHOT = ROOT / "tests" / "vision_probe.png"


def main():
    if not SHOT.exists():
        print("нет картинки:", SHOT)
        return 1
    token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    body = json.dumps({
        "messages": [{"role": "user", "content": "Что за фигура в центре и какого она цвета? Ответь коротко."}],
        "images": [str(SHOT)],
    }, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(BASE + "/api/chat" + "?token=" + token, data=body,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + token}, method="POST")
    notes, answer, errors = [], "", []
    with urllib.request.urlopen(request, timeout=600) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except ValueError:
                continue
            kind = event.get("type")
            if kind == "note":
                notes.append(event.get("text", ""))
            elif kind == "delta":
                answer += event.get("text", "")
            elif kind == "error":
                errors.append(event.get("text", ""))
            elif kind == "done":
                break
    print("заметки:", notes)
    print("ошибки:", errors)
    print("ответ:", answer.strip()[:300])

    status = json.loads(urllib.request.urlopen(BASE + "/api/vision/models" + "?token=" + token, timeout=30).read())
    chat_state = json.loads(urllib.request.urlopen(BASE + "/api/state" + "?token=" + token, timeout=30).read())
    print("слот зрения:", status.get("status", {}).get("running"), status.get("status", {}).get("model"))
    print("чат-модель загружена:", chat_state.get("server", {}).get("running"))
    low = answer.lower()
    ok = bool(answer.strip()) and "красн" in low and not errors
    print("итог:", "OK" if ok else "ПРОВАЛ")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
