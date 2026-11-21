"""Вложения в чат: текст из документов и картинки с диска или загрузкой из браузера."""
from __future__ import annotations

import base64
import binascii
import pathlib
import re
import shutil
import subprocess
import zipfile

from . import config, diag

TEXT_EXT = {".txt", ".md", ".csv", ".tsv", ".json", ".py", ".js", ".ts", ".html", ".css",
            ".log", ".ini", ".cfg", ".yml", ".yaml", ".bat", ".sh", ".sql", ".xml"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
MAX_TEXT = 60000
MAX_UPLOAD = 20 * 1024 * 1024
UPLOAD_DIR = config.DATA_DIR / "attachments"


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _read_text(path: pathlib.Path) -> str:
    for encoding in ("utf-8", "utf-16", "cp1251"):
        try:
            return path.read_text(encoding=encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return path.read_bytes().decode("utf-8", "replace")


def _docx_text(path: pathlib.Path) -> str:
    with zipfile.ZipFile(str(path)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8", "replace")
    xml = xml.replace("</w:p>", "\n")
    return _clean(re.sub(r"<[^>]+>", " ", xml))


def _xlsx_text(path: pathlib.Path) -> str:
    rows = []
    with zipfile.ZipFile(str(path)) as zf:
        shared = []
        if "xl/sharedStrings.xml" in zf.namelist():
            data = zf.read("xl/sharedStrings.xml").decode("utf-8", "replace")
            shared = [re.sub(r"<[^>]+>", "", item) for item in re.findall(r"<t[^>]*>(.*?)</t>", data, re.S)]
        for name in sorted(n for n in zf.namelist() if n.startswith("xl/worksheets/sheet")):
            data = zf.read(name).decode("utf-8", "replace")
            for row in re.findall(r"<row[^>]*>(.*?)</row>", data, re.S):
                cells = []
                for cell in re.findall(r"<c[^>]*?(?:t=\"s\")?[^>]*>(.*?)</c>", row, re.S):
                    value = re.sub(r"<[^>]+>", "", cell)
                    if value.strip().isdigit() and shared and int(value) < len(shared):
                        value = shared[int(value)]
                    if value.strip():
                        cells.append(value.strip())
                if cells:
                    rows.append(" | ".join(cells))
    return _clean("\n".join(rows))


def _pdf_text(path: pathlib.Path) -> str:
    exe = shutil.which("pdftotext")
    if not exe:
        return ""
    try:
        done = subprocess.run([exe, "-layout", str(path), "-"], capture_output=True, timeout=60)
        if done.returncode == 0:
            return _clean(done.stdout.decode("utf-8", "replace"))
    except Exception as exc:
        diag.warn("attach pdf", exc)
    return ""


def extract(raw_path: str) -> dict:
    """Текст из файла на диске. Свои вложения читаем без allow-list."""
    from . import tools
    try:
        candidate = pathlib.Path(str(raw_path or "")).resolve()
        own = UPLOAD_DIR.resolve() in candidate.parents
        path = candidate if own else tools._resolve(str(raw_path or ""), config.load())
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if not path.is_file():
        return {"ok": False, "error": "Файл не найден: %s" % path}

    suffix = path.suffix.lower()
    size = path.stat().st_size
    out = {"ok": True, "name": path.name, "path": str(path), "size": size, "kind": "text", "text": "", "truncated": False}
    try:
        if suffix in IMAGE_EXT:
            out["kind"] = "image"
            out["text"] = "(изображение %s, %d КБ)" % (path.name, size // 1024)
            return out
        if suffix == ".docx":
            text = _docx_text(path)
        elif suffix in (".xlsx", ".xlsm"):
            text = _xlsx_text(path)
        elif suffix == ".pdf":
            text = _pdf_text(path)
            if not text:
                out["ok"] = False
                out["error"] = "PDF без текстового слоя: сохраните как DOCX или включите pdftotext"
                return out
        elif suffix in TEXT_EXT or size < 200000:
            text = _clean(_read_text(path))
        else:
            out["ok"] = False
            out["error"] = "Формат не поддержан: %s" % suffix
            return out
    except Exception as exc:
        diag.warn("attach extract", exc)
        return {"ok": False, "error": "Не удалось прочитать файл: %s" % exc}

    if len(text) > MAX_TEXT:
        out["truncated"] = True
        text = text[:MAX_TEXT]
    out["text"] = text
    out["chars"] = len(text)
    return out


def save_upload(name: str, data_b64: str) -> dict:
    """Файл из браузера (drag&drop): сохраняем во вложения и разбираем."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if not str(name or "").strip():
        return {"ok": False, "error": "Пустое имя файла"}
    safe = re.sub(r"[^0-9A-Za-zА-Яа-я._-]", "_", str(name or "file"))[-80:]
    if not safe:
        return {"ok": False, "error": "Пустое имя файла"}
    try:
        raw = base64.b64decode(data_b64 or "")
    except (binascii.Error, ValueError):
        return {"ok": False, "error": "Битые данные файла"}
    if not raw:
        return {"ok": False, "error": "Пустой файл"}
    if len(raw) > MAX_UPLOAD:
        return {"ok": False, "error": "Файл больше 20 МБ"}
    target = UPLOAD_DIR / safe
    try:
        target.write_bytes(raw)
    except OSError as exc:
        return {"ok": False, "error": "Не удалось сохранить: %s" % exc}

    suffix = target.suffix.lower()
    if suffix in IMAGE_EXT:
        return {"ok": True, "name": safe, "path": str(target), "size": len(raw), "kind": "image",
                "text": "(изображение %s, %d КБ)" % (safe, len(raw) // 1024)}
    return extract(str(target))
