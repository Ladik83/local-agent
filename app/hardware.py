"""Определение железа: RAM, CPU, GPU, место на диске. Только stdlib."""
from __future__ import annotations

import ctypes
import os
import pathlib
import shutil
import subprocess
import winreg

GB = 1024 ** 3


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def ram() -> tuple[float, float]:
    """(всего ГБ, свободно ГБ)."""
    st = _MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
        return 0.0, 0.0
    return st.ullTotalPhys / GB, st.ullAvailPhys / GB


def _reg(path: str, name: str) -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as k:
            return str(winreg.QueryValueEx(k, name)[0])
    except OSError:
        return ""


def cpu() -> dict:
    name = _reg(r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString").strip()
    return {
        "name": name or os.environ.get("PROCESSOR_IDENTIFIER", "CPU"),
        "cores": os.cpu_count() or 0,
        "arch": os.environ.get("PROCESSOR_ARCHITECTURE", "AMD64"),
    }


_GPU_CACHE = {"ts": 0.0, "value": None}
_PS_GPU = (
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    "Get-CimInstance Win32_VideoController | "
    "ForEach-Object { $_.Name + '|' + $_.AdapterRAM + '|' + $_.DriverVersion }"
)


def gpus() -> list[dict]:
    """Список видеокарт (кэш 60 с: PowerShell-опрос стоит ~200 мс)."""
    import time
    cached = _GPU_CACHE["value"]
    if cached is not None and time.time() - _GPU_CACHE["ts"] < 60:
        return cached
    out: list[dict] = []
    try:
        raw = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_GPU],
            capture_output=True, timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return out
    text = _decode(raw)
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if not parts or not parts[0]:
            continue
        name = parts[0]
        if any(skip in name.lower() for skip in ("basic display", "remote", "virtual", "idd")):
            continue
        try:
            vram_gb = round(int(parts[1]) / GB, 1)
        except (IndexError, ValueError):
            vram_gb = 0.0
        low = name.lower()
        vendor = "Intel" if "intel" in low else "NVIDIA" if ("nvidia" in low or "geforce" in low or "rtx" in low) else \
                 "AMD" if ("amd" in low or "radeon" in low) else "other"
        # AdapterRAM врёт для карт >4 ГБ и всегда 0 у встроек — помечаем как «общая память»
        integrated = vendor == "Intel" or "graphics" in low and vendor != "NVIDIA"
        out.append({"name": name, "vendor": vendor, "vram_gb": vram_gb,
                    "driver": parts[2] if len(parts) > 2 else "", "integrated": integrated})
    _GPU_CACHE["value"] = out
    _GPU_CACHE["ts"] = time.time()
    return out


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "cp866", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def disk_free(path: str) -> float:
    try:
        return shutil.disk_usage(path).free / GB
    except OSError:
        return 0.0


def budget_gb(total_ram: float, integrated_only: bool) -> float:
    """Сколько памяти реально можно отдать модели.

    Windows + фоновые приложения съедают ~5 ГБ; у встройки модель живёт в общей RAM,
    поэтому резерв больше (система + видеопамять браузера и т.п.).
    """
    reserve = 6.0 if not integrated_only else 9.0
    return max(0.0, total_ram - reserve)


def snapshot(models_dir: str) -> dict:
    total, avail = ram()
    gpu_list = gpus()
    cpu_info = cpu()
    integrated_only = not any(not g["integrated"] for g in gpu_list) if gpu_list else True
    bud = budget_gb(total, integrated_only)
    return {
        "cpu": cpu_info,
        "ram_total_gb": round(total, 1),
        "ram_free_gb": round(avail, 1),
        "gpu": gpu_list,
        "integrated_only": integrated_only,
        "budget_gb": round(bud, 1),
        "disk_free_gb": round(disk_free(models_dir), 1),
        "models_dir": models_dir,
        "exists": pathlib.Path(models_dir).exists(),
    }
