"""Диски: ищем папки с .gguf на всех локальных дисках."""
from __future__ import annotations

import ctypes
import os
import pathlib
import string
import time

SKIP_NAMES = {
    "windows", "windows.old", "program files", "program files (x86)", "programdata",
    "$recycle.bin", "system volume information", "node_modules", ".git", "__pycache__",
    "site-packages", "temp", "tmp", "recovery", "perflogs", "drivers", "winsxs",
    "installer", "assembly", "packages", "logs",
}
MAX_DEPTH = 6
MAX_DIRS = 120000
TIME_LIMIT = 150.0


def fixed_drives() -> list[str]:
    """Список локальных дисков (без флешек и Диски)."""
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except AttributeError:
        return []
    result = []
    for index, letter in enumerate(string.ascii_uppercase):
        if not (mask >> index) & 1:
            continue
        root = letter + ":\\"
        if ctypes.windll.kernel32.GetDriveTypeW(root) == 3:
            result.append(root)
    return result


def find_model_dirs(progress=None) -> list[str]:
    """Обходит диски и возвращает папки, где лежит хотя бы один .gguf."""
    found: list[str] = []
    visited = 0
    started = time.time()
    for drive in fixed_drives():
        stack = [(drive, 0)]
        while stack:
            path, depth = stack.pop()
            visited += 1
            if progress and visited % 400 == 0:
                progress(visited, path)
            if visited > MAX_DIRS or time.time() - started > TIME_LIMIT:
                return normalize(found)
            try:
                entries = list(os.scandir(path))
            except OSError:
                continue
            has_model = False
            subdirs = []
            for entry in entries:
                try:
                    if entry.is_symlink():
                        if entry.name.lower().endswith(".gguf"):
                            has_model = True
                    elif entry.is_file(follow_symlinks=False):
                        if entry.name.lower().endswith(".gguf"):
                            has_model = True
                    elif entry.is_dir(follow_symlinks=False):
                        name = entry.name.lower()
                        if name in SKIP_NAMES or name.startswith("."):
                            continue
                        is_junction = getattr(entry, "is_junction", None)
                        if callable(is_junction) and is_junction():
                            continue
                        if _is_reparse(entry):
                            continue
                        subdirs.append(entry.path)
                except OSError:
                    continue
            if has_model:
                found.append(path)
            if depth < MAX_DEPTH:
                for sub in subdirs:
                    stack.append((sub, depth + 1))
    return normalize(found)


def collapse(folders: list[str]) -> list[str]:
    """Убираем вложенные папки, если родитель уже найден."""
    ordered = sorted(set(folders), key=len)
    result: list[str] = []
    for path in ordered:
        lower = path.lower().rstrip("\\") + "\\"
        if any(lower.startswith(item.lower().rstrip("\\") + "\\") for item in result):
            continue
        result.append(path)
    return result


def _is_reparse(entry) -> bool:
    """Junction/симлинк на папку — чтобы не обходить одни и те же файлы дважды."""
    try:
        attrs = entry.stat(follow_symlinks=False).st_file_attributes
    except (OSError, AttributeError):
        return False
    return bool(attrs & 0x400)


def normalize(folders: list[str]) -> list[str]:
    """Для кэшей Диски (…/models--автор--имя/snapshots/хэш) берём корень кэша.

    Так новые модели в той же папке подхватываются автоматически.
    """
    result = []
    for path in folders:
        parts = pathlib.PurePath(path).parts
        low = [part.lower() for part in parts]
        if "snapshots" in low:
            cut = low.index("snapshots")
            parts = parts[:max(cut - 1, 1)]
        result.append(str(pathlib.PurePath(*parts)))
    return collapse(result)
