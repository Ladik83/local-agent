"""Сводка для панелей UI: железо, нагрузка, модель, активность, токены. Только stdlib."""
from __future__ import annotations

from . import diag

import copy
import ctypes
import pathlib
import threading
import time

try:
    from . import config, engine, hardware, scan, stats
except ImportError:  # запуск файла напрямую, без пакета
    import config  # type: ignore
    import engine  # type: ignore
    import hardware  # type: ignore
    import scan  # type: ignore
    import stats  # type: ignore

GB = 1024 ** 3
TTL = 1.0          # кэш агрегатора, сек
MODELS_TTL = 15.0  # список .gguf ходит по диску — обновляем реже
CPU_SAMPLE = 0.05  # окно замера CPU, сек

_CACHE = {"ts": 0.0, "value": None}
_MODELS = {"ts": 0.0, "value": None}


def reset_cache() -> None:
    """Сбросить кэш агрегатора вместе со списком моделей."""
    _CACHE["value"] = None
    _CACHE["ts"] = 0.0
    _MODELS["value"] = None
    _MODELS["ts"] = 0.0


def _blank() -> dict:
    return {
        "computer": {
            "cpu": "", "cores": 0, "ram_total_gb": 0.0, "ram_used_gb": 0.0,
            "ram_free_gb": 0.0, "gpus": [], "disk_free_gb": 0.0,
            "models_count": 0, "models_gb": 0.0,
        },
        "resources": {
            "cpu_percent": 0.0, "ram_percent": 0.0,
            "process_ram_mb": 0, "threads": 0,
        },
        "model": {
            "path": "", "name": "", "size_gb": 0.0,
            "loaded": False, "port": 0, "ctx": 0,
        },
        "now": {"phase": "", "tool": "", "role": "", "step": "", "seconds": 0.0},
        "tokens": {"in": 0, "out": 0, "in_speed": 0.0, "out_speed": 0.0, "requests": 0},
    }


def _cfg() -> dict:
    try:
        return config.load() or {}
    except Exception:
        return {}


def _models() -> tuple[int, float]:
    """(количество, суммарный размер ГБ) — из scan, с отдельным кэшем."""
    now = time.time()
    cached = _MODELS["value"]
    if cached is not None and now - _MODELS["ts"] < MODELS_TTL:
        return cached
    cfg = _cfg()
    dirs: list[str] = []
    primary = cfg.get("models_dir")
    if primary:
        dirs.append(str(primary))
    for item in cfg.get("extra_dirs") or []:
        if item and str(item) not in dirs:
            dirs.append(str(item))
    value: tuple[int, float] = (0, 0.0)
    try:
        found = scan.scan(dirs) or []
        total = sum(float(m.get("size_gb") or 0.0) for m in found)
        value = (len(found), round(total, 1))
    except Exception:
        value = (0, 0.0)
    _MODELS["value"] = value
    _MODELS["ts"] = now
    return value


def _own_ram_mb() -> int:
    """RSS своего процесса; структуру берём из engine, чтобы не дублировать."""
    pmc = getattr(engine, "_PMC", None)
    if pmc is None:
        return 0
    try:
        counters = pmc()
        counters.cb = ctypes.sizeof(pmc)
        # c_void_p обязателен: голый int -1 усекается до 32 бит и Windows его не принимает
        handle = ctypes.c_void_p(ctypes.windll.kernel32.GetCurrentProcess())
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), counters.cb
        )
        return int(counters.WorkingSetSize // (1024 * 1024)) if ok else 0
    except Exception:
        return 0


def _fill_computer(out: dict, total: float, free: float) -> None:
    cfg = _cfg()
    folder = str(cfg.get("models_dir") or "")
    try:
        info = hardware.cpu() or {}
        out["cpu"] = str(info.get("name") or "")
        out["cores"] = int(info.get("cores") or 0)
    except Exception as exc:
        diag.warn("panels.py:114", exc)
    if total > 0:
        out["ram_total_gb"] = round(total, 1)
        out["ram_free_gb"] = round(free, 1)
        out["ram_used_gb"] = round(max(0.0, total - free), 1)
    try:
        gpus = []
        for gpu in hardware.gpus() or []:
            vram_gb = float(gpu.get("vram_gb") or 0.0)
            gpus.append({"name": str(gpu.get("name") or ""),
                         "vram_mb": int(round(vram_gb * 1024))})
        out["gpus"] = gpus
    except Exception:
        out["gpus"] = []
    try:
        out["disk_free_gb"] = round(float(hardware.disk_free(folder)), 1)
        import shutil as _shutil
        _total, _used, _free = _shutil.disk_usage(str(folder))
        out["disk_total_gb"] = round(_total / (1024 ** 3), 1)
    except Exception:
        out["disk_free_gb"] = 0.0
        out["disk_total_gb"] = 0.0
    count, size = _models()
    out["models_count"] = int(count)
    out["models_gb"] = float(size)


def _fill_resources(out: dict, total: float, free: float) -> None:
    try:
        out["cpu_percent"] = float(stats.cpu_percent(CPU_SAMPLE))
    except Exception:
        out["cpu_percent"] = 0.0
    out["ram_percent"] = round(max(0.0, total - free) * 100.0 / total, 1) if total > 0 else 0.0
    out["process_ram_mb"] = _own_ram_mb()
    try:
        out["threads"] = threading.active_count()
    except Exception:
        out["threads"] = 0


def _fill_model(out: dict) -> None:
    cfg = _cfg()
    path = ""
    port = 0
    loaded = False
    try:
        proc = engine.SERVER.get("proc")
        loaded = bool(proc is not None and proc.poll() is None)
        path = str(engine.SERVER.get("model") or "")
        port = int(engine.SERVER.get("port") or 0)
    except Exception as exc:
        diag.warn("panels.py:165", exc)
    if not path:
        path = str(cfg.get("last_model") or "")
    out["loaded"] = loaded
    out["port"] = port if loaded else 0
    out["path"] = path
    if path:
        target = pathlib.Path(path)
        out["name"] = target.name
        try:
            out["size_gb"] = round(target.stat().st_size / GB, 2)
        except OSError:
            out["size_gb"] = 0.0
    try:
        out["ctx"] = int(cfg.get("context") or 0)
    except Exception:
        out["ctx"] = 0


def _fill_activity(now_out: dict, tokens_out: dict) -> None:
    try:
        act = dict(stats.ACTIVITY)
    except Exception:
        act = {}
    now_out["phase"] = str(act.get("phase") or "")
    now_out["tool"] = str(act.get("tool") or "")
    now_out["role"] = str(act.get("role") or "")
    now_out["step"] = str(act.get("step") or "")
    since = float(act.get("since") or 0.0)
    now_out["seconds"] = round(max(0.0, time.time() - since), 1) if since else 0.0
    tokens_out["in"] = int(act.get("tokens_in") or 0)
    tokens_out["out"] = int(act.get("tokens_out") or 0)
    tokens_out["in_speed"] = float(act.get("speed_in") or 0.0)
    tokens_out["out_speed"] = float(act.get("speed_out") or 0.0)
    tokens_out["requests"] = int(act.get("calls") or 0)


def snapshot() -> dict:
    """Один срез для всех панелей UI. Кэш 1 с, сбои секций не роняют остальные."""
    now = time.time()
    cached = _CACHE["value"]
    if cached is not None and now - _CACHE["ts"] < TTL:
        return copy.deepcopy(cached)

    total = free = 0.0
    try:
        pair = hardware.ram()
        total, free = float(pair[0]), float(pair[1])
    except Exception:
        total = free = 0.0

    data: dict = {"ok": True, "ts": now}
    data.update(_blank())
    for fill, args in (
        (_fill_computer, (data["computer"], total, free)),
        (_fill_resources, (data["resources"], total, free)),
        (_fill_model, (data["model"],)),
        (_fill_activity, (data["now"], data["tokens"])),
    ):
        try:
            fill(*args)
        except Exception as exc:
            diag.warn("panels.py:227", exc)

    _CACHE["value"] = data
    _CACHE["ts"] = now
    return copy.deepcopy(data)
