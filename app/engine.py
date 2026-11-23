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
SERVER = {"proc": None, "port": None, "model": None, "projector": "", "started": 0.0, "error": None}
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
            if variant == "vulkan" and not has_vulkan(exe.parent):
                return _vulkan_fallback(tag, staging)
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


VULKAN_DLL = "ggml-vulkan.dll"


def has_vulkan(folder) -> bool:
    """В папке сборки есть Vulkan-бэкенд? По имени папки верить нельзя."""
    path = pathlib.Path(str(folder or ""))
    if path.is_file():
        path = path.parent
    return (path / VULKAN_DLL).exists()


def _vulkan_fallback(tag: str, staging) -> dict:
    """В свежей сборке не оказалось Vulkan — включаем ближайшую рабочую на диске."""
    shutil.rmtree(str(staging), ignore_errors=True)
    best = None
    for item in local_builds():
        folder = pathlib.Path(item["exe"]).parent
        if not has_vulkan(folder):
            continue
        parts = folder.name.split("-")
        if len(parts) >= 3 and parts[2] == "vulkan":
            best = (parts[1], folder)
            break
    if best is None:
        JOB.update(active=False, error="нет сборки с Vulkan")
        return {"ok": False, "error": "в сборке %s нет Vulkan (ggml-vulkan.dll) и других "
                                      "Vulkan-сборок на диске нет" % tag}
    skip = [str(item) for item in (config.load().get("engine_skip") or []) if str(item) != tag]
    config.save({"engine_tag": best[0], "engine_variant": "vulkan", "engine_skip": skip + [tag]})
    JOB.update(active=False, pct=100.0, text="Vulkan: сборка %s" % best[0], result=str(best[1]))
    return {"ok": True, "path": str(best[1]), "tag": best[0], "fallback": True,
            "message": "в сборке %s нет Vulkan — включена сборка %s" % (tag, best[0])}


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
        if cand.exists() and (variant != "vulkan" or has_vulkan(cand)):
            return str(cand)
    found = local_builds()
    if found:
        def rank(item):
            folder = pathlib.Path(item["exe"]).parent
            return (0 if has_vulkan(folder) else 1, [-ord(ch) for ch in item["name"]])
        return sorted(found, key=rank)[0]["exe"]
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


DEVICES = {"exe": "", "checked": 0.0, "items": []}


def devices(exe=None, force: bool = False) -> list:
    """Какие устройства видит движок. Пусто — значит сборка без Vulkan."""
    exe = exe or find_exe()
    if not exe:
        return []
    if (not force and DEVICES["exe"] == exe and DEVICES["items"]
            and time.time() - DEVICES["checked"] < 300):
        return DEVICES["items"]
    items = []
    try:
        out = subprocess.run([exe, "--list-devices"], capture_output=True, timeout=90,
                             creationflags=NO_WINDOW).stdout.decode("utf-8", "replace")
        for line in out.splitlines():
            line = line.strip()
            if ":" in line and not line.lower().startswith("available"):
                name, _, rest = line.partition(":")
                if name.lower().startswith(("vulkan", "cuda", "metal", "sycl")):
                    items.append({"name": name.strip(), "text": rest.strip()})
    except (OSError, subprocess.SubprocessError) as exc:
        diag.warn("engine.py:devices", exc)
    DEVICES.update({"exe": exe, "checked": time.time(), "items": items})
    return items


def offload_info(log_path, gpu_name: str = "") -> dict:
    """Что движок написал про выгрузку: видеокарту и число слоёв."""
    try:
        text = pathlib.Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    info = {"gpu": "", "layers": "", "on_gpu": False}
    hits = re.findall(r"device (Vulkan\d|CUDA\d|Metal|SYCL\d)", text)
    bad = re.search(r"no usable GPU|failed to (?:initialize|find) Vulkan|not supported by device", text, re.I)
    info["on_gpu"] = bool(hits) and not bad
    layers = re.search(r"offloaded (\d+/\d+) layers to GPU", text)
    if layers:
        info["layers"] = layers.group(1)
    if info["on_gpu"]:
        info["gpu"] = gpu_name or (hits[0] if hits else "видеокарта")
    return info


STARTUP = {"tag": "", "latest": "", "checked": 0.0, "error": ""}


def check_latest(force: bool = True) -> dict:
    """Сверить установленный движок с последней сборкой на GitHub."""
    cfg = config.load()
    installed = cfg.get("engine_tag") or ""
    try:
        found = builds(force=force)
    except Exception as exc:
        STARTUP.update({"tag": installed, "error": str(exc), "checked": time.time()})
        return {"ok": False, "error": "не удалось проверить обновления: %s" % exc, "tag": installed}
    latest = found[0]["tag"] if found else ""
    STARTUP.update({"tag": installed, "latest": latest, "error": "", "checked": time.time()})
    return {"ok": True, "tag": installed, "latest": latest,
            "update_available": bool(installed and latest and installed != latest)}


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
        "update_available": bool(latest and cfg.get("engine_tag") and latest != cfg.get("engine_tag")
                                 and latest not in [str(item) for item in (cfg.get("engine_skip") or [])]),
        "skipped": [str(item) for item in (cfg.get("engine_skip") or [])],
        "running": running,
        "port": SERVER["port"],
        "model": SERVER["model"],
        "error": SERVER["error"],
        "used_mb": memory_mb(SERVER.get("proc")),
        "job": dict(JOB),
        "local": local_builds(),
        "devices": devices(exe),
        "vulkan": bool(exe and has_vulkan(exe)),
        "gpu_mode": cfg.get("gpu") or "auto",
        "offload": SERVER.get("offload") or {},
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


def list_strays() -> list:
    """Наши лишние llama-server: слушают 8090–8400, но не текущий чат и не эмбеддинги."""
    import subprocess as sp
    try:
        text = sp.run(["netstat", "-ano"], capture_output=True, timeout=15).stdout.decode("cp866", "replace")
    except (OSError, sp.SubprocessError) as exc:
        diag.warn("engine.py:strays", exc)
        return []
    known = set()
    for container in (SERVER, EMBED):
        proc = container.get("proc")
        if proc is not None and proc.poll() is None:
            known.add(int(proc.pid))
    pids = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5 or "LISTENING" not in parts:
            continue
        address = parts[1]
        port = 0
        try:
            port = int(address.rsplit(":", 1)[1])
        except (IndexError, ValueError):
            continue
        if not 8090 <= port <= 8400:
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if pid in known or pid in pids:
            continue
        pids.append(pid)
    return pids


def reap_strays() -> dict:
    """Остановить лишние серверы моделей (после тестов и падений)."""
    stopped = []
    for pid in list_strays():
        class _P:
            pass
        holder = _P()
        holder.pid = pid
        if memory_mb(holder) > 3000:  # чужую большую модель не трогаем
            continue
        try:
            os.kill(pid, 9)
            stopped.append(pid)
        except OSError as exc:
            diag.warn("engine.py:reap_strays", exc)
    return {"ok": True, "stopped": stopped, "count": len(stopped)}


def find_projector(model_path) -> str:
    """Проектор зрения к модели: mmproj*.gguf в той же папке (или пусто)."""
    path = pathlib.Path(str(model_path or ""))
    if not path.is_file():
        return ""
    words = {word for word in path.stem.lower().replace("-", " ").replace("_", " ").split() if len(word) > 2}
    best = ""
    best_score = -1.0
    for item in sorted(path.parent.glob("mmproj*.gguf")):
        name_words = {word for word in item.name.lower().replace("-", " ").replace("_", " ").replace(".", " ").split() if len(word) > 2}
        overlap = len(words & name_words)
        try:
            size = item.stat().st_size
        except OSError:
            size = 0
        score = overlap * 10 + (2 if ("f16" in item.name.lower() or "bf16" in item.name.lower()) else 0) + size / 2 ** 30
        if score > best_score:
            best, best_score = str(item), score
    return best


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
    want = str(cfg.get("gpu") or "auto")
    found = devices(exe)
    if ngl is None:
        ngl = 0 if want == "cpu" or not found else int(cfg.get("ngl") or 99)
    ngl = int(ngl)
    log_path = config.LOGS_DIR / "llama-server.log"
    projector = find_projector(model_path)
    cmd = [exe, "-m", model_path, "--host", "127.0.0.1", "--port", str(port),
           "-c", str(ctx), "-ngl", str(ngl), "--no-warmup"]
    # flash-attn + квантованный KV-кеш: без них длинный контекст съедает ОЗУ
    # и llama-server обрывает генерацию на потолке (n_tokens = ctx, truncated = 1).
    kv = str(cfg.get("kv_cache") or "q8_0").lower()
    if bool(cfg.get("flash_attn", True)):
        cmd += ["--flash-attn"]
    if kv in ("q8_0", "q4_0", "q4_1", "q5_0", "q5_1"):
        if not bool(cfg.get("flash_attn", True)):
            # квантованный V-кеш требует flash-attn — включаем принудительно
            cmd += ["--flash-attn"]
        cmd += ["--cache-type-k", kv, "--cache-type-v", kv]
    elif kv == "f16":
        cmd += ["--cache-type-k", "f16", "--cache-type-v", "f16"]
    if ngl > 0 and found:
        cmd += ["--device", found[0]["name"]]
    if projector:
        cmd += ["--mmproj", projector]
    last_log = ""
    for extra in (["--jinja"], []):
        handle = open(str(log_path), "w", encoding="utf-8", errors="replace")
        try:
            proc = subprocess.Popen(cmd + extra, stdout=handle, stderr=subprocess.STDOUT,
                                    cwd=str(pathlib.Path(model_path).parent),
                                    creationflags=NO_WINDOW)
        except OSError as exc:
            return {"ok": False, "error": "не удалось запустить llama-server: %s" % exc}
        SERVER.update({"proc": proc, "port": port, "model": model_path, "projector": projector,
                       "started": time.time(), "error": None})
        if _healthy(port, 300):
            gpu_name = re.sub(r"\s*\(\d.*$", "", found[0]["text"]).strip() if found else ""
            SERVER["offload"] = offload_info(log_path, gpu_name)
            return {"ok": True, "port": port, "log": str(log_path),
                    "offload": SERVER["offload"], "ngl": ngl}
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


EMBED = {"proc": None, "port": None, "model": None, "error": None, "started": 0.0}
EMBED_PID = config.DATA_DIR / "embed.pid"
EMBED_PIDS = config.DATA_DIR / "embed.pids.json"  # все серверы эмбеддингов, что мы запускали


def _embed_pids_read() -> list:
    try:
        return [int(item) for item in json.loads(EMBED_PIDS.read_text(encoding="utf-8"))]
    except (OSError, ValueError, TypeError):
        return []


def _embed_pids_write(pids) -> None:
    try:
        EMBED_PIDS.write_text(json.dumps(sorted({int(pid) for pid in pids})), encoding="utf-8")
    except OSError as exc:
        diag.warn("engine.py:embed_pids", exc)


def embed_reap() -> None:
    """Погасить серверы эмбеддингов, оставшиеся от предыдущих запусков."""
    left = []
    for pid in _embed_pids_read():
        try:
            os.kill(pid, 9)
            left.append(pid)
        except OSError:
            continue
    if left:
        diag.warn("engine.py:embed_reap", left)
    _embed_pids_write([])


def _embed_pid_write(pid) -> None:
    try:
        EMBED_PID.write_text(str(pid), encoding="utf-8")
        _embed_pids_write(_embed_pids_read() + [pid])
    except OSError as exc:
        diag.warn("engine.py:embed_pid", exc)


def embed_cleanup() -> None:
    """Гасим сервер эмбеддингов, оставшийся от прошлого запуска."""
    try:
        pid = int(EMBED_PID.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return
    try:
        os.kill(pid, 9)
    except OSError as exc:
        diag.warn("engine.py:embed_cleanup", exc)
    try:
        EMBED_PID.unlink()
    except OSError as exc:
        diag.warn("engine.py:embed_pid_del", exc)


def embed_stop() -> None:
    proc = EMBED.get("proc")
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except OSError as exc:
                diag.warn("engine.py:embed_kill", exc)
    EMBED.update({"proc": None, "port": None, "model": None})
    try:
        EMBED_PID.unlink()
    except OSError:
        pass


def embed_base_url():
    proc = EMBED.get("proc")
    if EMBED.get("port") and proc is not None and proc.poll() is None:
        return "http://127.0.0.1:%d" % EMBED["port"]
    return None


def _wait_embed(port: int, timeout: float) -> bool:
    end = time.time() + timeout
    url = "http://127.0.0.1:%d/health" % port
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status == 200:
                    return True
        except Exception as exc:
            diag.warn("engine.py:embed_health", exc)
        proc = EMBED.get("proc")
        if proc is not None and proc.poll() is not None:
            return False
        time.sleep(0.6)
    return False


def start_embed(model_path: str) -> dict:
    path = str(model_path or "")
    if not path or not pathlib.Path(path).exists():
        return {"ok": False, "error": "Модель эмбеддингов не найдена — скачайте её на вкладке «Модели»"}
    if EMBED.get("model") == path and embed_base_url():
        return {"ok": True, "port": EMBED["port"], "reused": True}
    exe = find_exe()
    if not exe:
        return {"ok": False, "error": "движок llama.cpp не установлен"}
    embed_stop()
    embed_cleanup()  # и сервер от прошлого запуска приложения
    embed_reap()     # и все, что остались от тестов и перезапусков
    port = free_port(8190)
    log_path = config.LOGS_DIR / "llama-embed.log"
    cmd = [exe, "-m", path, "--host", "127.0.0.1", "--port", str(port),
           "-c", "2048", "-b", "2048", "-ub", "2048",
           "-ngl", "99", "--no-warmup", "--embeddings", "--pooling", "mean"]
    handle = open(str(log_path), "w", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(cmd, stdout=handle, stderr=subprocess.STDOUT,
                                cwd=str(pathlib.Path(path).parent), creationflags=NO_WINDOW)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    EMBED.update({"proc": proc, "port": port, "model": path, "error": None, "started": time.time()})
    _embed_pid_write(proc.pid)
    if _wait_embed(port, 120):
        return {"ok": True, "port": port, "log": str(log_path)}
    EMBED["error"] = "модель эмбеддингов не запустилась"
    log = tail(log_path)
    embed_stop()
    return {"ok": False, "error": "llama-server не поднялся", "log": log}


def base_url():
    proc = SERVER.get("proc")
    if SERVER.get("port") and proc is not None and proc.poll() is None:
        return "http://127.0.0.1:%d" % SERVER["port"]
    return None
