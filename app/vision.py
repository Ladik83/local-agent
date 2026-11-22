"""Зрение: отдельный llama-server с моделью и проектором (mmproj).

Универсально: подходит любая VL-модель в формате GGUF, у которой рядом лежит
mmproj*.gguf. Модель берётся из настроек, а если там пусто — самая лёгкая из
найденных, чтобы не занимать память без нужды.
"""
from __future__ import annotations

from . import config
from . import diag
from . import engine

import base64
import json
import pathlib
import time
import urllib.error
import urllib.request

STATE = {"proc": None, "port": None, "model": "", "projector": "", "started": 0.0, "error": None}
PID_FILE = config.DATA_DIR / "vision.pid"
CACHE = {"ts": 0.0, "items": []}
CACHE_TTL = 60.0
IMAGE_MAX = 12 * 1024 * 1024
SKIP = ("dflash", "draft", "eagle", "speculative", "lora", "adapter", "encoder",
        "clip", "vae", "mmproj", "embed", "tts", "audio", "voice")
VISION_HINTS = ("-vl", "vl-", "vision", "llava", "gemma-3", "multimodal", "mllama",
                "pixtral", "minicpm-v", "internvl", "moondream")
MIN_GB = 1.5
MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
}


def projector_for(model_path) -> str:
    """Проектор к модели: mmproj*.gguf рядом с файлом модели (логика в engine)."""
    return engine.find_projector(model_path)


def candidates(cfg=None) -> list:
    """Модели на диске, к которым есть проектор: [{id, name, size_gb, projector, folder}]."""
    cfg = cfg or config.load()
    now = time.time()
    if CACHE["items"] and now - CACHE["ts"] < CACHE_TTL:
        return list(CACHE["items"])
    roots = [cfg.get("models_dir")] + list(cfg.get("extra_dirs") or [])
    found = []
    seen = set()
    for raw in roots:
        root = pathlib.Path(str(raw or ""))
        if not root.is_dir():
            continue
        try:
            for item in root.rglob("*.gguf"):
                name = item.name.lower()
                if name.startswith("mmproj") or any(token in name for token in SKIP):
                    continue
                try:
                    if item.stat().st_size < MIN_GB * 2 ** 30:
                        continue
                except OSError:
                    continue
                key = str(item).lower()
                if key in seen:
                    continue
                projector = projector_for(item)
                if not projector:
                    continue
                seen.add(key)
                try:
                    size_gb = round(item.stat().st_size / 2 ** 30, 2)
                except OSError:
                    size_gb = 0.0
                found.append({
                    "id": str(item),
                    "name": item.stem,
                    "folder": str(item.parent),
                    "size_gb": size_gb,
                    "projector": projector,
                    "vision_hint": any(hint in name for hint in VISION_HINTS),
                })
        except OSError as exc:
            diag.warn("vision/candidates", exc)
    # сначала модели, в имени которых есть признак зрения, затем самые лёгкие
    found.sort(key=lambda entry: (not entry["vision_hint"], entry["size_gb"], entry["name"].lower()))
    CACHE.update({"ts": now, "items": found})
    return list(found)


def pick(cfg=None) -> str:
    """Путь к модели зрения: из настроек, иначе самая лёгкая найденная."""
    cfg = cfg or config.load()
    explicit = str(cfg.get("vision_model") or "").strip()
    if explicit:
        if pathlib.Path(explicit).is_file():
            return explicit
        return ""
    items = candidates(cfg)
    return items[0]["id"] if items else ""


def base_url() -> str:
    proc = STATE.get("proc")
    if STATE.get("port") and proc is not None and proc.poll() is None:
        return "http://127.0.0.1:%d" % STATE["port"]
    return ""


def status() -> dict:
    proc = STATE.get("proc")
    alive = bool(proc is not None and proc.poll() is None)
    return {
        "running": alive and bool(base_url()),
        "model": pathlib.Path(STATE["model"]).name if STATE.get("model") else "",
        "model_path": STATE.get("model") or "",
        "projector": pathlib.Path(STATE["projector"]).name if STATE.get("projector") else "",
        "port": STATE.get("port") if alive else None,
        "memory_mb": engine.memory_mb(proc) if alive else 0,
        "error": STATE.get("error"),
    }


def stop() -> None:
    proc = STATE.get("proc")
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=12)
        except Exception:
            try:
                proc.kill()
            except Exception as exc:
                diag.warn("vision/kill", exc)
    STATE.update({"proc": None, "port": None, "model": "", "projector": ""})
    try:
        if PID_FILE.exists():
            PID_FILE.unlink()
    except OSError as exc:
        diag.warn("vision/pid_del", exc)


def cleanup() -> None:
    """Погасить процесс, оставшийся от прошлого запуска приложения."""
    try:
        if not PID_FILE.exists():
            return
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        import os
        os.kill(pid, 9)
    except (OSError, ValueError) as exc:
        diag.warn("vision/cleanup", exc)
    finally:
        try:
            if PID_FILE.exists():
                PID_FILE.unlink()
        except OSError:
            pass


def start(model_path: str = "", ctx=None) -> dict:
    """Поднять сервер зрения. Свой слот: чат-модель и эмбеддинги не трогаем."""
    cfg = config.load()
    path = str(model_path or pick(cfg) or "")
    if not path:
        return {"ok": False, "error": "не найдено ни одной модели с проектором mmproj"}
    projector = projector_for(path)
    if not projector:
        return {"ok": False, "error": "рядом с моделью нет проектора mmproj — зрение недоступно"}
    if STATE.get("model") == path and STATE.get("projector") == projector and base_url():
        return {"ok": True, "port": STATE["port"], "reused": True, "model": path}
    exe = engine.find_exe()
    if not exe:
        return {"ok": False, "error": "движок llama.cpp не установлен"}
    stop()
    port = engine.free_port(8300)
    ctx = int(ctx or cfg.get("vision_context") or min(int(cfg.get("context") or 8192), 8192))
    ngl = int(cfg.get("ngl") or 99)
    log_path = config.LOGS_DIR / "vision-server.log"
    cmd = [exe, "-m", path, "--mmproj", projector, "--host", "127.0.0.1", "--port", str(port),
           "-c", str(ctx), "-ngl", str(ngl), "--no-warmup", "--jinja"]
    handle = open(str(log_path), "w", encoding="utf-8", errors="replace")
    try:
        proc = engine.subprocess.Popen(cmd, stdout=handle, stderr=engine.subprocess.STDOUT,
                                       cwd=str(pathlib.Path(path).parent),
                                       creationflags=engine.NO_WINDOW)
    except OSError as exc:
        handle.close()
        return {"ok": False, "error": "не удалось запустить сервер зрения: %s" % exc}
    STATE.update({"proc": proc, "port": port, "model": path, "projector": projector,
                  "started": time.time(), "error": None})
    try:
        PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    except OSError as exc:
        diag.warn("vision/pid_write", exc)
    if engine._healthy(port, 300):
        return {"ok": True, "port": port, "model": path, "projector": projector, "log": str(log_path)}
    STATE["error"] = "сервер зрения не поднялся"
    stop()
    return {"ok": False, "error": "сервер зрения не поднялся", "log": str(log_path)}


def _data_url(path: str) -> str:
    item = pathlib.Path(path)
    mime = MIME.get(item.suffix.lower(), "image/png")
    raw = item.read_bytes()[:IMAGE_MAX]
    return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii"))


def ask(images, prompt: str, system: str = "", max_tokens: int = 700, timeout: int = 300) -> dict:
    """Ответ по картинкам: один запрос к серверу зрения, без инструментов."""
    base = base_url()
    if not base:
        started = start()
        if not started.get("ok"):
            return {"ok": False, "error": started.get("error") or "сервер зрения не поднялся"}
        base = base_url()
    paths = [str(item) for item in (images or []) if pathlib.Path(str(item)).is_file()]
    if not paths:
        return {"ok": False, "error": "нет картинок для анализа"}
    parts: list = [{"type": "text", "text": str(prompt or "Опиши, что на картинке")}]
    for path in paths:
        try:
            parts.append({"type": "image_url", "image_url": {"url": _data_url(path)}})
        except OSError as exc:
            return {"ok": False, "error": "не удалось прочитать картинку: %s" % exc}
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": parts})
    body = json.dumps({"messages": messages, "stream": False, "temperature": 0.2,
                       "max_tokens": int(max_tokens)}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(base + "/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        text = ""
        try:
            text = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            text = ""
        return {"ok": False, "error": "сервер зрения ответил %s: %s" % (exc.code, text)}
    except (urllib.error.URLError, OSError) as exc:
        return {"ok": False, "error": "нет связи с сервером зрения: %s" % exc}
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    text = str(message.get("content") or "").strip()
    if not text:
        return {"ok": False, "error": "модель зрения вернула пустой ответ"}
    usage = data.get("usage") or {}
    return {"ok": True, "text": text, "model": pathlib.Path(STATE["model"]).name,
            "tokens": int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)}
