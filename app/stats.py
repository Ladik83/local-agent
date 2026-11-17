"""Живые показатели: процессор, память, диск, движок, токены и что происходит сейчас."""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import time

from . import config, engine

GB = 1024 ** 3


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.wintypes.DWORD), ("dwHighDateTime", ctypes.wintypes.DWORD)]


def _as_int(value) -> int:
    return (value.dwHighDateTime << 32) + value.dwLowDateTime


def cpu_percent(sample: float = 0.12) -> float:
    idle_a, kern_a, user_a = _FILETIME(), _FILETIME(), _FILETIME()
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle_a), ctypes.byref(kern_a), ctypes.byref(user_a))
    time.sleep(sample)
    idle_b, kern_b, user_b = _FILETIME(), _FILETIME(), _FILETIME()
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle_b), ctypes.byref(kern_b), ctypes.byref(user_b))
    idle = _as_int(idle_b) - _as_int(idle_a)
    kernel = _as_int(kern_b) - _as_int(kern_a)
    user = _as_int(user_b) - _as_int(user_a)
    total = kernel + user
    if total <= 0:
        return 0.0
    return round(max(0.0, min(100.0, (total - idle) * 100.0 / total)), 1)


class _MEM(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def memory() -> dict:
    status = _MEM()
    status.dwLength = ctypes.sizeof(_MEM)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    total = status.ullTotalPhys / GB
    free = status.ullAvailPhys / GB
    return {"total": round(total, 1), "free": round(free, 1), "used": round(total - free, 1),
            "percent": round((total - free) * 100.0 / total, 1) if total else 0.0}


ACTIVITY = {
    "tokens_in": 0,
    "tokens_out": 0,
    "speed_in": 0.0,
    "speed_out": 0.0,
    "calls": 0,
    "tool": "",
    "role": "",
    "step": "",
    "phase": "ожидание",
    "since": 0.0,
}


def reset_tokens() -> None:
    ACTIVITY.update(tokens_in=0, tokens_out=0, speed_in=0.0, speed_out=0.0, calls=0)


def mark(**fields) -> None:
    for key, value in fields.items():
        if key in ACTIVITY:
            ACTIVITY[key] = value
    ACTIVITY["since"] = time.time()


def add_tokens(prompt: int, completion: int, seconds: float) -> None:
    ACTIVITY["calls"] += 1
    ACTIVITY["tokens_in"] += max(0, int(prompt))
    ACTIVITY["tokens_out"] += max(0, int(completion))
    if seconds > 0 and completion:
        ACTIVITY["speed_out"] = round(completion / seconds, 1)
    if seconds > 0 and prompt:
        ACTIVITY["speed_in"] = round(prompt / seconds, 1)


def estimate_tokens(text: str) -> int:
    """Грубая оценка, если сервер не прислал usage."""
    return max(1, int(len(text or "") / 3.6))


def snapshot() -> dict:
    mem = memory()
    proc = engine.SERVER.get("proc")
    running = bool(proc is not None and proc.poll() is None)
    started = float(engine.SERVER.get("started") or 0.0)
    rss = engine.memory_mb(proc) if running else 0
    model = engine.SERVER.get("model") or ""
    folder = config.load().get("models_dir") or ""
    import shutil
    try:
        disk_free = round(shutil.disk_usage(folder).free / GB, 1)
    except OSError:
        disk_free = 0.0
    phase = ACTIVITY["phase"]
    if running and not model:
        phase = "запуск модели"
    return {
        "cpu": cpu_percent(),
        "memory": mem,
        "disk_free": disk_free,
        "engine": {
            "running": running,
            "port": engine.SERVER.get("port"),
            "model": model.rsplit("\\", 1)[-1] if model else "",
            "rss_mb": rss,
            "uptime": round(time.time() - started) if running and started else 0,
        },
        "activity": dict(ACTIVITY, phase=phase),
    }
