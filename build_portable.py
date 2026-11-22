"""Сборка портативной версии: python-embed + код + данные рядом с программой.

  python build_portable.py            — обычная сборка (как раньше)
  python build_portable.py --clean    — сборка для раздачи: без data/, dist/, .git/,
                                        __pycache__/, *.pyc, *.log, venv/, .env
"""
from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import sys
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent
PY_VERSION = "3.11.9"
PY_URL = "https://www.python.org/ftp/python/%s/python-%s-embed-amd64.zip" % (PY_VERSION, PY_VERSION)
CACHE = ROOT / ".cache"

# Базовые исключения: работали и до появления --clean.
SKIP = {"__pycache__", ".git", ".cache", "dist", "node_modules"}

# Дополнительные исключения режима --clean (пользовательские данные и мусор).
CLEAN_DIRS = {
    "data", "dist", ".git", "__pycache__", ".cache", "node_modules",
    "venv", ".venv", "env", ".env", "tests",
}
CLEAN_SUFFIXES = {".pyc", ".pyo", ".log"}
CLEAN_FILES = {".env"}

# Что берём из корня проекта (остальное в сборку не входит).
ROOT_FILES = ("run.py", "README.md", "AUDIT.md", "MEMORY.md", "VERSION")


def _excluded(rel: pathlib.Path, clean: bool) -> bool:
    """Файл не попадает в сборку. Путь rel — относительно корня проекта."""
    parts = rel.parts
    if any(part in SKIP for part in parts):
        return True
    if not clean:
        return False
    if any(part in CLEAN_DIRS for part in parts):
        return True
    if rel.suffix.lower() in CLEAN_SUFFIXES:
        return True
    if rel.name in CLEAN_FILES or rel.name.startswith(".env."):
        return True
    return False


def collect_local(root: pathlib.Path = ROOT, clean: bool = False) -> list:
    """Файлы верхнего уровня, которые попадают в сборку (пути от корня)."""
    root = pathlib.Path(root)
    picked = []
    for name in ROOT_FILES:
        path = root / name
        if not path.is_file():
            continue
        rel = pathlib.Path(name)
        if _excluded(rel, clean):
            continue
        picked.append(rel)
    return picked


def collect_include(root: pathlib.Path = ROOT, clean: bool = False) -> list:
    """Все файлы сборки: файлы корня + дерево app/ (пути от корня)."""
    root = pathlib.Path(root)
    picked = list(collect_local(root, clean=clean))
    app_dir = root / "app"
    if app_dir.is_dir():
        for path in sorted(app_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            if _excluded(rel, clean):
                continue
            picked.append(rel)
    return picked


def clean_target(target: pathlib.Path) -> None:
    """Убрать из папки сборки следы прошлых запусков: данные, движки, старый код."""
    target = pathlib.Path(target)
    for name in ("data", "models", "app.old", "app"):
        path = target / name
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            try:
                path.unlink()
            except OSError:
                pass


def download_python() -> pathlib.Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    target = CACHE / ("python-%s-embed-amd64.zip" % PY_VERSION)
    if target.exists() and target.stat().st_size > 5_000_000:
        print("python: из кэша")
        return target
    print("python: скачиваю", PY_URL)
    with urllib.request.urlopen(PY_URL, timeout=180) as resp, open(target, "wb") as fh:
        shutil.copyfileobj(resp, fh)
    print("python: скачано %.1f МБ" % (target.stat().st_size / 1048576))
    return target


def extract_python(archive: pathlib.Path, target: pathlib.Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(target)
    pth = target / ("python%d%d._pth" % (sys.version_info[0], sys.version_info[1]))
    if not pth.exists():
        pth = next(target.glob("python*._pth"))
    pth.write_text("python311.zip\n.\n..\n..\\app\nimport site\n", encoding="utf-8")
    print("python: распакован в", target)


def copy_code(target: pathlib.Path, clean: bool = False) -> None:
    target = pathlib.Path(target)
    target.mkdir(parents=True, exist_ok=True)
    if clean:
        clean_target(target)
    else:
        app_dir = target / "app"
        if app_dir.exists():
            shutil.rmtree(app_dir)
    files = collect_include(ROOT, clean=clean)
    for rel in files:
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dst)
    print("код: скопирован (%d файлов%s)" % (len(files), ", clean" if clean else ""))


def write_launchers(target: pathlib.Path, clean: bool = False) -> None:
    target = pathlib.Path(target)
    target.mkdir(parents=True, exist_ok=True)
    start = (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "cd /d \"%~dp0\"\r\n"
        "if not exist \"data\" mkdir \"data\"\r\n"
        "start \"\" \"%~dp0python\\pythonw.exe\" run.py\r\n"
    )
    stop = (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "powershell -NoProfile -Command \"$p=(Get-NetTCPConnection -LocalPort 7867 -State Listen -ErrorAction SilentlyContinue).OwningProcess; "
        "if ($p) { Stop-Process -Id $p -Force }\"\r\n"
        "echo stopped\r\n"
        "timeout /t 2 >nul\r\n"
    )
    (target / "start.bat").write_text(start, encoding="utf-8")
    (target / "stop.bat").write_text(stop, encoding="utf-8")
    shortcut = (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "powershell -NoProfile -Command \"$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\\LocalAgent.lnk');"
        "$s.TargetPath='%~dp0start.bat';$s.WorkingDirectory='%~dp0';$s.WindowStyle=7;$s.Save()\"\r\n"
        "echo shortcut created\r\n"
        "timeout /t 2 >nul\r\n"
    )
    (target / "shortcut.bat").write_text(shortcut, encoding="utf-8")
    (target / "portable.txt").write_text("portable", encoding="utf-8")
    if not clean:
        (target / "data").mkdir(exist_ok=True)
        (target / "models").mkdir(exist_ok=True)
    print("лаунчеры: start.bat / stop.bat")


def copy_engines(target: pathlib.Path) -> None:
    src = pathlib.Path(os.environ.get("LOCALAPPDATA") or pathlib.Path.home()) / "LocalAgent" / "engines"
    if not src.exists():
        print("движки: нет", src)
        return
    dst = target / "data" / "engines"
    dst.mkdir(parents=True, exist_ok=True)
    total = 0
    for path in src.rglob("*"):
        if path.is_file():
            rel = path.relative_to(src)
            out = dst / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, out)
            total += path.stat().st_size
    print("движки: скопировано %.0f МБ" % (total / 1048576))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="dist")
    parser.add_argument("--engines", action="store_true")
    parser.add_argument("--clean", action="store_true",
                        help="сборка для раздачи: без data/, dist/, .git/, __pycache__/, *.pyc, *.log, venv/, .env")
    args = parser.parse_args()

    target = ROOT / args.out / "LocalAgent"
    target.mkdir(parents=True, exist_ok=True)
    copy_code(target, clean=args.clean)
    extract_python(download_python(), target / "python")
    write_launchers(target, clean=args.clean)
    if args.engines:
        if args.clean:
            print("движки: пропущены (--clean не переносит data/engines)")
        else:
            copy_engines(target)
    size = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
    print("готово: %s (%.1f МБ)" % (target, size / 1048576))
    if args.clean:
        data_dir = target / "data"
        leftovers = sorted(p.name for p in data_dir.rglob("*")) if data_dir.exists() else []
        print("clean: data/ в сборке %s" % ("отсутствует" if not leftovers else "не пуста: " + ", ".join(leftovers)))


if __name__ == "__main__":
    main()
