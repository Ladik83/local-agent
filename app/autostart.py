"""Автозапуск при входе в Windows (HKCU\\...\\Run, без прав администратора)."""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "LocalAgent"


def _pythonw() -> str:
    root = pathlib.Path(__file__).resolve().parent.parent
    portable = root / "python" / "pythonw.exe"
    if portable.exists():
        return str(portable)
    candidate = pathlib.Path(sys.executable)
    programs = pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python"
    if programs.exists():
        for child in sorted(programs.glob("Python3*"), reverse=True):
            found = child / "pythonw.exe"
            if found.exists():
                return str(found)
    for direct in ("C:/Python313/pythonw.exe", "C:/Python312/pythonw.exe", "C:/Python311/pythonw.exe"):
        if pathlib.Path(direct).exists():
            return direct
    if "hermes" in str(candidate).lower():
        found = shutil.which("pythonw") or shutil.which("python")
        if found:
            return found
    for name in ("pythonw.exe", "python.exe"):
        alt = candidate.with_name(name)
        if alt.exists():
            return str(alt)
    return str(candidate)


def command() -> str:
    root = pathlib.Path(__file__).resolve().parent.parent
    return '"%s" "%s"' % (_pythonw(), root / "run.py")


def status() -> dict:
    try:
        out = subprocess.run(["reg", "query", KEY, "/v", NAME], capture_output=True, timeout=15)
        return {"enabled": out.returncode == 0, "command": command()}
    except Exception as exc:
        return {"enabled": False, "command": command(), "error": str(exc)}


def set_enabled(enabled: bool) -> dict:
    if enabled:
        args = ["reg", "add", KEY, "/v", NAME, "/t", "REG_SZ", "/d", command(), "/f"]
    else:
        args = ["reg", "delete", KEY, "/v", NAME, "/f"]
    try:
        out = subprocess.run(args, capture_output=True, timeout=15)
        ok = out.returncode == 0 or not enabled
        return {"ok": ok, "enabled": bool(enabled), "detail": (out.stdout or out.stderr).decode("cp866", "replace").strip()[:300]}
    except Exception as exc:
        return {"ok": False, "enabled": False, "error": str(exc)}
