"""База знаний: индексируем папки и документы, ищем по смыслу (без интернета и внешних библиотек).

Векторы — локальные хэшированные мешки слов (TF с нормировкой), поэтому поиск работает
на любой машине без скачивания эмбеддинг-модели. Хранилище — SQLite рядом с настройками.
"""
from __future__ import annotations

from . import diag

import json
import math
import os
import pathlib
import re
import sqlite3
import time
import zipfile
import zlib

from . import config

DIM = 768
MAX_FILES = 3000
MAX_BYTES = 4 * 1024 * 1024
CHUNK = 900
OVERLAP = 160

TEXT_EXT = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml",
    ".ini", ".cfg", ".log", ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".cs", ".cpp", ".c",
    ".h", ".hpp", ".go", ".rs", ".rb", ".php", ".sql", ".sh", ".ps1", ".bat", ".html", ".htm",
    ".css", ".scss", ".xml", ".toml", ".env", ".gradle", ".kt", ".swift", ".m", ".lua",
}
SKIP_DIRS = {
    "node_modules", ".git", ".venv", "venv", "__pycache__", ".idea", ".vs", "dist", "build",
    "target", ".next", "coverage", ".cache", "obj", "bin",
}
STOP = set(
    "и в во на не что как это по для из к с от до же ли бы или а но так то все еще уже "
    "the a an of to in on for is are was were be be to and or not with as at by it this that "
    "я ты он она они мы вы меня мне тебя его её их был была было были есть нет да".split()
)


def db_path() -> pathlib.Path:
    return config.DATA_DIR / "rag.sqlite"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path()), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS docs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT UNIQUE, mtime REAL, size INTEGER, chunks INTEGER, added REAL
        );
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id INTEGER, ord INTEGER, text TEXT, vec TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='chunks', content_rowid='id', tokenize='unicode61');
        CREATE TRIGGER IF NOT EXISTS chunks_fts_ai AFTER INSERT ON chunks BEGIN
            INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
        END;
        CREATE TRIGGER IF NOT EXISTS chunks_fts_ad AFTER DELETE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
        END;
        """
    )
    try:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(chunks)").fetchall()]
        if "vec_kind" not in columns:
            conn.execute("ALTER TABLE chunks ADD COLUMN vec_kind TEXT DEFAULT ''")
    except sqlite3.Error as exc:
        diag.warn("rag.py:alter", exc)
    try:
        if conn.execute("SELECT count(*) FROM chunks_fts").fetchone()[0] == 0:
            have = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
            if have:
                conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
                conn.commit()
    except sqlite3.Error as exc:
        diag.warn("rag.py:76", exc)
    return conn


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[0-9a-zа-яё_]{2,}", (text or "").lower())
    return [word for word in words if word not in STOP]


def _hash_vector(text: str) -> list[float]:
    counts: dict[str, int] = {}
    for word in _tokens(text):
        counts[word] = counts.get(word, 0) + 1
    vec = [0.0] * DIM
    for word, count in counts.items():
        index = zlib.crc32(word.encode("utf-8")) % DIM
        vec[index] += 1.0 + 0.5 * math.log(count)
    norm = math.sqrt(sum(value * value for value in vec)) or 1.0
    return [round(value / norm, 5) for value in vec]


def embed(text: str, kind: str = "document"):
    from . import embed as _semantic
    for attempt in (1, 2):
        try:
            vectors = _semantic.vectors([text], kind=kind)
        except Exception as exc:
            diag.warn("rag/embed", exc)
            vectors = None
        if vectors and vectors[0]:
            return [round(float(value), 6) for value in vectors[0]], _semantic.vector_kind()
        if attempt == 1:
            time.sleep(0.6)
    return _hash_vector(text), "hash"


def _split(text: str) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    parts = []
    start = 0
    while start < len(text):
        parts.append(text[start:start + CHUNK])
        start += CHUNK - OVERLAP
    return [part.strip() for part in parts if len(part.strip()) > 40]


def _read_docx(path: pathlib.Path) -> str:
    with zipfile.ZipFile(str(path)) as archive:
        raw = archive.read("word/document.xml").decode("utf-8", "replace")
    raw = raw.replace("</w:p>", "\n")
    return re.sub(r"<[^>]+>", "", raw)


def read_text(path: pathlib.Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        try:
            return _read_docx(path)
        except Exception:
            return ""
    if suffix == ".pdf":
        try:
            import pdfplumber  # необязательная зависимость

            with pdfplumber.open(str(path)) as pdf:
                return "\n".join((page.extract_text() or "") for page in pdf.pages[:80])
        except Exception:
            return ""
    for encoding in ("utf-8", "cp1251", "utf-16"):
        try:
            return path.read_text(encoding=encoding, errors="strict")
        except (UnicodeDecodeError, OSError):
            continue
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _collect(target: pathlib.Path) -> list[pathlib.Path]:
    if target.is_file():
        return [target] if target.suffix.lower() in TEXT_EXT | {".docx", ".pdf"} else []
    found: list[pathlib.Path] = []
    for root, dirs, files in os.walk(str(target)):
        dirs[:] = [name for name in dirs if name.lower() not in SKIP_DIRS and not name.startswith(".")]
        for name in files:
            path = pathlib.Path(root) / name
            if path.suffix.lower() in TEXT_EXT | {".docx", ".pdf"}:
                found.append(path)
            if len(found) >= MAX_FILES:
                return found
    return found


def add_path(raw_path: str, emit=None) -> dict:
    """Индексируем файл или папку. Возвращает {ok, files, chunks, skipped}."""
    try:
        from . import config as _config, tools as _tools
        target = _tools._resolve(str(raw_path or ""), _config.load())
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    target = pathlib.Path(target)
    if not str(target):
        return {"ok": False, "error": "Путь не указан"}
    if not target.exists():
        return {"ok": False, "error": "Путь не найден: %s" % target}
    files = _collect(target)
    if not files:
        return {"ok": False, "error": "Подходящих файлов не нашлось (txt, md, код, csv, docx, pdf)"}
    conn = _conn()
    indexed = 0
    total_chunks = 0
    skipped = 0
    for path in files:
        try:
            size = path.stat().st_size
            mtime = path.stat().st_mtime
        except OSError:
            skipped += 1
            continue
        if size > MAX_BYTES or size == 0:
            skipped += 1
            continue
        row = conn.execute("SELECT id, mtime FROM docs WHERE path = ?", (str(path),)).fetchone()
        if row and abs(float(row[1]) - mtime) < 1:
            continue
        text = read_text(path)
        parts = _split(text)
        if not parts:
            skipped += 1
            continue
        if row:
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (row[0],))
            doc_id = row[0]
            conn.execute(
                "UPDATE docs SET mtime = ?, size = ?, chunks = ? WHERE id = ?",
                (mtime, size, len(parts), doc_id),
            )
        else:
            cursor = conn.execute(
                "INSERT INTO docs (path, mtime, size, chunks, added) VALUES (?, ?, ?, ?, ?)",
                (str(path), mtime, size, len(parts), time.time()),
            )
            doc_id = cursor.lastrowid
        rows = []
        for order, part in enumerate(parts):
            vec, vec_kind = embed(part)
            rows.append((doc_id, order, part, json.dumps(vec), vec_kind))
        conn.executemany(
            "INSERT INTO chunks (doc_id, ord, text, vec, vec_kind) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        indexed += 1
        total_chunks += len(parts)
        if emit is not None:
            try:
                emit({"type": "rag", "text": "Проиндексировано: %s" % path.name, "done": indexed, "total": len(files)})
            except Exception as exc:
                diag.warn("rag.py:218", exc)
    conn.commit()
    conn.close()
    return {"ok": True, "files": indexed, "chunks": total_chunks, "skipped": skipped, "found": len(files)}


def _fts_query(needle: str) -> str:
    words = _tokens(needle)[:12]
    return " OR ".join('"' + word + '"' for word in words)


def search(query: str, limit: int = 5) -> list[dict]:
    """Поиск по базе знаний: сначала полнотекстовый BM25, затем векторный запасной вариант."""
    needle = (query or "").strip()
    if not needle or not db_path().exists():
        return []
    conn = _conn()
    phrase = _fts_query(needle)
    RRF = 60.0
    found = {}

    def add(chunk_id, text, path, rank, source, cos=None, bm25=None):
        item = found.get(chunk_id)
        if item is None:
            item = {"path": path, "text": text, "rrf": 0.0, "cos": None, "bm25": None, "sources": []}
            found[chunk_id] = item
        item["rrf"] += 1.0 / (RRF + rank + 1)
        if source not in item["sources"]:
            item["sources"].append(source)
        if cos is not None:
            item["cos"] = cos
        if bm25 is not None:
            item["bm25"] = bm25

    if phrase:
        try:
            rows = conn.execute(
                "SELECT c.id, c.text, d.path, bm25(chunks_fts) AS rank"
                " FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid"
                " JOIN docs d ON d.id = c.doc_id"
                " WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
                (phrase, limit * 4),
            ).fetchall()
            for rank, row in enumerate(rows):
                add(row[0], row[1], row[2], rank, "fts", bm25=round(-float(row[3]), 3))
        except sqlite3.Error as exc:
            diag.warn("rag/fts", exc)

    vector, vec_kind = [], "hash"
    try:
        vector, vec_kind = embed(needle, kind="query")
    except Exception as exc:
        diag.warn("rag/vector", exc)
    if vector:
        try:
            rows = conn.execute(
                "SELECT c.id, c.text, c.vec, d.path FROM chunks c JOIN docs d ON d.id = c.doc_id"
                " WHERE COALESCE(c.vec_kind, '') = ?",
                (vec_kind,),
            ).fetchall()
        except sqlite3.Error as exc:
            diag.warn("rag/vector_rows", exc)
            rows = []
        scored = []
        for row in rows:
            try:
                vec = json.loads(row[2])
            except (ValueError, TypeError):
                continue
            if len(vec) != len(vector):
                continue
            score = sum(a * b for a, b in zip(vector, vec))
            if score > 0.2:
                scored.append((score, row[0], row[1], row[3]))
        scored.sort(reverse=True, key=lambda item: item[0])
        for rank, item in enumerate(scored[:limit * 4]):
            add(item[1], item[2], item[3], rank, "vec", cos=round(item[0], 3))

    conn.close()
    order = sorted(found.values(), key=lambda it: (-it["rrf"], -(it["cos"] or 0)))
    out = []
    for item in order[:limit]:
        score = item["cos"] if item["cos"] is not None else (item["bm25"] or 0.0)
        out.append({"score": score, "rank": round(item["rrf"], 4), "source": "+".join(item["sources"]),
                    "path": item["path"], "text": item["text"]})
    return out


def list_docs() -> list[dict]:
    if not db_path().exists():
        return []
    conn = _conn()
    rows = conn.execute(
        "SELECT path, chunks, size, datetime(added, 'unixepoch', 'localtime') FROM docs ORDER BY path"
    ).fetchall()
    conn.close()
    return [{"path": row[0], "chunks": row[1], "size": row[2], "added": row[3]} for row in rows]


def remove_doc(path: str) -> dict:
    if not db_path().exists():
        return {"ok": False, "error": "База знаний пуста"}
    conn = _conn()
    row = conn.execute("SELECT id FROM docs WHERE path = ?", (str(path),)).fetchone()
    if not row:
        conn.close()
        return {"ok": False, "error": "Источник не найден"}
    conn.execute("DELETE FROM chunks WHERE doc_id = ?", (row[0],))
    conn.execute("DELETE FROM docs WHERE id = ?", (row[0],))
    conn.commit()
    conn.close()
    return {"ok": True}


def clear() -> dict:
    if db_path().exists():
        conn = _conn()
        conn.execute("DELETE FROM chunks")
        conn.execute("DELETE FROM docs")
        conn.commit()
        conn.close()
    return {"ok": True}


def reindex(emit=None) -> dict:
    paths = [item["path"] for item in list_docs()]
    files = 0
    chunks = 0
    for path in paths:
        remove_doc(path)
        result = add_path(path, emit)
        if result.get("ok"):
            files += int(result.get("files") or 0)
            chunks += int(result.get("chunks") or 0)
    return {"ok": True, "docs": len(paths), "files": files, "chunks": chunks}


def stats() -> dict:
    docs = list_docs()
    return {
        "docs": len(docs),
        "chunks": sum(item["chunks"] or 0 for item in docs),
        "size_mb": round(sum(item["size"] or 0 for item in docs) / 1048576.0, 1),
        "items": docs,
    }


def as_tool_text(query: str, limit: int = 5) -> str:
    hits = search(query, limit)
    if not hits:
        return "В базе знаний ничего не нашлось по запросу: %s" % query
    lines = []
    for index, hit in enumerate(hits, 1):
        snippet = re.sub(r"\s+", " ", hit["text"])[:600]
        lines.append("[%d] %s (релевантность %.2f)\n%s" % (index, hit["path"], hit["score"], snippet))
    return "\n\n".join(lines)
