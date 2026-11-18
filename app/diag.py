"""Тихие сбои: пишем в logs/app.log вместо глухого pass."""
import pathlib
import time

from . import config

MAX_BYTES = 1000000


def _path() -> pathlib.Path:
    return config.LOGS_DIR / "app.log"


def warn(where: str, exc=None) -> None:
    try:
        path = _path()
        if path.exists() and path.stat().st_size > MAX_BYTES:
            path.write_text("", encoding="utf-8")
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        detail = "" if exc is None else str(exc)
        f = open(str(path), "a", encoding="utf-8")
        try:
            f.write("%s %s: %s\n" % (stamp, where, detail))
        finally:
            f.close()
    except Exception:
        return


def tail(lines: int = 20) -> str:
    try:
        text = _path().read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return "\n".join(text.strip().splitlines()[-lines:])
