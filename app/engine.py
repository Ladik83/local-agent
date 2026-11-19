"""Движок llama.cpp: сборки, загрузка, распаковка, запуск сервера модели."""
from __future__ import annotations

from . import diag

import ctypes
import ctypes.wintypes
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
import zipfile

from . import config

GH = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=8"
CACHE = config.DATA_DIR / "engine-cache.json"
UA = {"User-Agent": "LocalAgent/0.1"}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
EXE = "llama-server.exe" if os.name == "nt" else "llama-server"

JOB = {"active": False, "kind": "", "pct": 0.0, "text": "", "error": None, "result": None}
SERVER = {"proc": None, "port": None, "model": None, "started": 0.0, "error": None}
LOCK = threading.Lock()


def _json(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def builds(force: bool = False):
    """Последние сборки: [{'tag': 'b12421', 'assets': {'vulkan': url, 'cpu': url}}]"""
    if not force:
        try:
            cached = json.loads(CACHE.read_text(encoding="utf-8"))
            if time.time() - cached.get("ts", 0) < 6 * 3600:
                return cached["builds"]
        except (OSError, ValueError, KeyError) as exc:
            diag.warn("engine.py:44", exc)
    out = []
    for rel in _json(GH):
        tag = rel.get("tag_name") or ""
        if not re.fullmatch(r"b\d+", tag):
            continue
        assets = {}
        for a in rel.get("assets", []):
            name = a.get("name", "")
            if name.endswith("-bin-win-vulkan-x64.zip"):
                assets["vulkan"] = a["browser_download_url"]
            elif name.endswith("-bin-win-cpu-x64.zip"):
                assets["cpu"] = a["browser_download_url"]
        if assets:
            out.append({"tag": tag, "assets": assets, "published": rel.get("published_at", "")})
    if out:
        try:
            CACHE.write_text(json.dumps({"ts": time.time(), "builds": out}), encoding="utf-8")
        except OSError as exc:
            diag.warn("engine.py:63", exc)
    return out


def _fetch(url: str, dest: pathlib.Path, what: str) -> None:
    JOB.update(active=True, kind="engine", pct=0.0, text="Скачиваем " + what, error=None, result=None)
    part = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as resp, open(part, "wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if total:
                JOB["pct"] = round(done * 100 / total, 1)
                JOB["text"] = "%s: %.0f из %.0f МБ" % (what, done / 1048576, total / 1048576)
    part.replace(dest)


def install(tag: str, variant: str = "cpu") -> dict:
    """Скачать и распаковать сборку."""
    with LOCK:
        try:
            picked = None
            for b in builds():
                if b["tag"] == tag:
                    picked = b
                    break
            if picked is None:
                return {"ok": False, "error": "сборка %s не найдена" % tag}
            url = picked["assets"].get(variant) or picked["assets"].get("cpu")
            if not url:
                return {"ok": False, "error": "нет архива для варианта %s" % variant}
            zip_path = config.DOWNLOAD_DIR / ("llama-%s-%s.zip" % (tag, variant))
            if not zip_path.exists():
                _fetch(url, zip_path, "llama.cpp %s (%s)" % (tag, variant))
            JOB.update(active=True, pct=100.0, text="Распаковываем…")
            target = config.ENGINES_DIR / ("llamacpp-%s-%s" % (tag, variant))
            staging = config.ENGINES_DIR / (".new-%s-%s" % (tag, variant))
            shutil.rmtree(str(staging), ignore_errors=True)
            staging.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(str(zip_path)) as zf:
                zf.extractall(str(staging))
            exe = next(staging.glob("**/" + EXE), None)
            if exe is None:
                return {"ok": False, "error": "в архиве нет llama-server"}
            if target.exists():
                shutil.rmtree(str(target), ignore_errors=True)
            shutil.move(str(exe.parent), str(target))
            shutil.rmtree(str(staging), ignore_errors=True)
            try:
                zip_path.unlink()
            except OSError as exc:
                diag.warn("engine.py:120", exc)
            config.save({"engine_tag": tag, "engine_variant": variant})
            JOB.update(active=False, pct=100.0, text="llama.cpp %s готов" % tag, result=str(target))
            return {"ok": True, "path": str(target), "tag": tag}
        except Exception as exc:
            JOB.update(active=False, error=str(exc), text="Ошибка установки движка")
            return {"ok": False, "error": str(exc)}


def local_builds():
    out = []
    root = config.ENGINES_DIR
    if not root.exists():
        return out
    for d in sorted(root.iterdir()):
        if d.is_dir() and not d.name.startswith("."):
            exe = d / EXE
            if exe.exists():
                out.append({"name": d.name, "exe": str(exe)})
    return out


def find_exe():
    cfg = config.load()
    tag, variant = cfg.get("engine_tag"), cfg.get("engine_variant")
    if tag and variant:
        cand = config.ENGINES_DIR / ("llamacpp-%s-%s" % (tag, variant)) / EXE
        if cand.exists():
            return str(cand)
    found = local_builds()
    if found:
        return found[-1]["exe"]
    for folder in (pathlib.Path.home() / ".lmstudio" / "extensions" / "backends",
                   pathlib.Path.home() / ".chipdeil" / "engines" / "llamacpp"):
        if not folder.is_dir():
            continue
        if (folder / EXE).exists():
            return str(folder / EXE)
        candidates = sorted(folder.glob("*/" + EXE), key=lambda p: ("vulkan" not in str(p).lower(), str(p)))
        if candidates:
            return str(candidates[0])
    return None


def status() -> dict:
    cfg = config.load()
    exe = find_exe()
    latest = None
    try:
        found = builds()
        if found:
            latest = found[0]["tag"]
    except Exception:
        latest = None
    running = bool(SERVER["proc"] and SERVER["proc"].poll() is None)
    return {
        "installed": bool(exe),
        "exe": exe,
        "tag": cfg.get("engine_tag"),
        "variant": cfg.get("engine_variant"),
        "latest": latest,
        "update_available": bool(latest and cfg.get("engine_tag") and latest != cfg.get("engine_tag")),
        "running": running,
        "port": SERVER["port"],
        "model": SERVER["model"],
        "error": SERVER["error"],
        "used_mb": memory_mb(SERVER.get("proc")),
        "job": dict(JOB),
        "local": local_builds(),
    }


def free_port(start: int = 8090) -> int:
    for port in range(start, start + 60):
        sock = socket.socket()
        try:
            sock.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
        finally:
            sock.close()
    return start


def stop() -> None:
    proc = SERVER.get("proc")
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except OSError as exc:
                diag.warn("engine.py:215", exc)
    SERVER.update({"proc": None, "port": None, "model": None, "started": 0.0})


def tail(path: pathlib.Path, limit: int = 2500) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError:
        return ""


def _healthy(port: int, timeout: float) -> bool:
    end = time.time() + timeout
    url = "http://127.0.0.1:%d/health" % port
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status == 200:
                    return True
        except Exception as exc:
            diag.warn("engine.py:235", exc)
        proc = SERVER.get("proc")
        if proc is not None and proc.poll() is not None:
            return False
        time.sleep(0.7)
    return False


def start(model_path: str, ctx=None, ngl=None) -> dict:
    """Поднять llama-server под модель."""
    cfg = config.load()
    if SERVER.get("model") == model_path and SERVER.get("proc") and SERVER["proc"].poll() is None:
        return {"ok": True, "port": SERVER["port"], "reused": True}
    exe = find_exe()
    if not exe:
        return {"ok": False, "error": "движок llama.cpp не установлен"}
    if not pathlib.Path(model_path).exists():
        return {"ok": False, "error": "файл модели не найден"}
    stop()
    port = free_port()
    ctx = int(ctx or cfg.get("context") or 8192)
    ngl = int(ngl if ngl is not None else (cfg.get("ngl") or 99))
    log_path = config.LOGS_DIR / "llama-server.log"
    cmd = [exe, "-m", model_path, "--host", "127.0.0.1", "--port", str(port),
           "-c", str(ctx), "-ngl", str(ngl), "--no-warmup", "--embeddings", "--pooling", "mean"]
    last_log = ""
    for extra in (["--jinja"], []):
        handle = open(str(log_path), "w", encoding="utf-8", errors="replace")
        try:
            proc = subprocess.Popen(cmd + extra, stdout=handle, stderr=subprocess.STDOUT,
                                    cwd=str(pathlib.Path(model_path).parent),
                                    creationflags=NO_WINDOW)
        except OSError as exc:
            return {"ok": False, "error": "не удалось запустить llama-server: %s" % exc}
        SERVER.update({"proc": proc, "port": port, "model": model_path,
                       "started": time.time(), "error": None})
        if _healthy(port, 300):
            return {"ok": True, "port": port, "log": str(log_path)}
        last_log = tail(log_path)
        SERVER["error"] = "модель не запустилась"
        stop()
    return {"ok": False, "error": "llama-server не поднялся", "log": last_log}


class _PMC(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.wintypes.DWORD), ("PageFaultCount", ctypes.wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def memory_mb(proc) -> int:
    """Сколько памяти занимает процесс модели (для кнопки «Выгрузить»)."""
    if proc is None:
        return 0
    try:
        counters = _PMC()
        counters.cb = ctypes.sizeof(_PMC)
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, proc.pid)
        if not handle:
            return 0
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        ctypes.windll.kernel32.CloseHandle(handle)
        return int(counters.WorkingSetSize // (1024 * 1024)) if ok else 0
    except Exception:
        return 0


def base_url():
    proc = SERVER.get("proc")
    if SERVER.get("port") and proc is not None and proc.poll() is None:
        return "http://127.0.0.1:%d" % SERVER["port"]
    return None
