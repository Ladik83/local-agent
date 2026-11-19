"""Hugging Face: поиск .gguf, список файлов, скачивание в папку моделей."""
from __future__ import annotations

from . import config, diag, keystore

import json
import pathlib
import re
import urllib.parse
import urllib.request

API = "https://huggingface.co/api/models"
TREE = "https://huggingface.co/api/models/%s/tree/main?recursive=false"
RESOLVE = "https://huggingface.co/%s/resolve/main/%s"
UA = {"User-Agent": "LocalAgent/0.1"}

REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
QUANT_RE = re.compile(r"(IQ\d+_[A-Z0-9_]+|Q\d+_[A-Z0-9_]+|Q\d+_\d+|Q\d+_K|F16|BF16|F32)", re.I)


def token() -> str:
    try:
        return str(keystore.get("hf_token") or "")
    except Exception as exc:
        diag.warn("hf.py:token", exc)
        return ""


def set_token(value: str) -> dict:
    value = (value or "").strip()
    if not value:
        return {"ok": False, "error": "пустой токен"}
    keystore.put("hf_token", value)
    return {"ok": True, "token_set": True}


def _headers() -> dict:
    headers = dict(UA)
    found = token()
    if found:
        headers["Authorization"] = "Bearer " + found
    return headers


def _get(url: str, timeout: int = 25):
    request = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def search(query: str, limit: int = 20) -> list[dict]:
    """Поиск моделей с файлами .gguf, самые скачиваемые сверху."""
    query = (query or "").strip()
    if not query:
        return []
    params = {
        "search": query,
        "filter": "gguf",
        "sort": "downloads",
        "direction": -1,
        "limit": str(max(1, min(50, int(limit or 20)))),
    }
    raw = _get(API + "?" + urllib.parse.urlencode(params))
    out = []
    for item in raw:
        repo = str(item.get("modelId") or item.get("id") or "")
        if not REPO_RE.match(repo):
            continue
        tags = item.get("tags") or []
        license_name = ""
        for tag in tags:
            if str(tag).startswith("license:"):
                license_name = str(tag).split(":", 1)[1]
                break
        out.append({
            "repo": repo,
            "downloads": int(item.get("downloads") or 0),
            "likes": int(item.get("likes") or 0),
            "updated": str(item.get("lastModified") or item.get("createdAt") or "")[:10],
            "license": license_name,
            "pipeline": str(item.get("pipeline_tag") or ""),
            "tags": [str(t) for t in tags if not str(t).startswith(("license:", "region:", "base_model:", "quantized:"))][:8],
        })
    return out


def files(repo: str) -> list[dict]:
    """Файлы .gguf в репозитории с размером и квантом."""
    if not REPO_RE.match(repo or ""):
        return []
    raw = _get(TREE % repo)
    out = []
    for item in raw:
        if item.get("type") != "file":
            continue
        path = str(item.get("path") or "")
        if not path.lower().endswith(".gguf"):
            continue
        size = int(item.get("size") or 0)
        quant = QUANT_RE.search(path)
        out.append({
            "file": path,
            "name": pathlib.PurePosixPath(path).name,
            "bytes": size,
            "size_gb": round(size / (1024 ** 3), 2),
            "quant": quant.group(1).upper() if quant else "",
            "lfs": bool(item.get("lfs")),
        })
    out.sort(key=lambda entry: entry["bytes"])
    return out


def target_dir(repo: str) -> pathlib.Path:
    base = pathlib.Path(config.load()["models_dir"]) / "LocalAgent" / repo.replace("/", "--")
    base.mkdir(parents=True, exist_ok=True)
    return base


def download(repo: str, file: str, on_progress=None) -> dict:
    """Скачать один файл в папку моделей. on_progress(done, total) — байты."""
    if not REPO_RE.match(repo or ""):
        return {"ok": False, "error": "некорректное имя модели"}
    name = pathlib.PurePosixPath(str(file or "")).name
    if not name.lower().endswith(".gguf"):
        return {"ok": False, "error": "нужен файл .gguf"}
    dest = target_dir(repo) / name
    if dest.exists() and dest.stat().st_size > 0:
        return {"ok": True, "path": str(dest), "skipped": True}
    part = dest.with_name(dest.name + ".part")
    url = RESOLVE % (repo, urllib.parse.quote(str(file)))
    request = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(request, timeout=180) as resp, open(part, "wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            block = resp.read(1 << 20)
            if not block:
                break
            fh.write(block)
            done += len(block)
            if on_progress is not None:
                on_progress(done, total)
    part.replace(dest)
    return {"ok": True, "path": str(dest), "bytes": dest.stat().st_size}
