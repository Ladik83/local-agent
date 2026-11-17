"""Поиск .gguf  на диске и разбор параметров из имени файла."""
from __future__ import annotations

import pathlib
import re

SKIP = ("mmproj", "embed", "clip", "vae", "encoder", "tts", "audio", "voice", "flux", "krea", "image")
QUANT = re.compile(r"((?:UD-|IQ|Q)\d+[_A-Z0-9]*)", re.IGNORECASE)
PARAMS = re.compile(r"(\d+(?:[.,]\d+)?)\s*[Bb](?![a-zA-Z])")
MOE = re.compile(r"[Aa](\d+(?:[.,]\d+)?)\s*[Bb](?![a-zA-Z])")
VISION = ("-vl", "vision", "llava", "gemma-3", "qwen2.5-vl")


def _num(text: str) -> float:
    return float(text.replace(",", "."))


def describe(path: pathlib.Path) -> dict:
    name = path.name
    lower = name.lower()
    try:
        size_gb = round(path.stat().st_size / 1024 ** 3, 2)
    except OSError:
        size_gb = 0.0

    quant = ""
    found = QUANT.search(name)
    if found:
        quant = found.group(1).upper()

    flat = name.replace("-", " ").replace("_", " ")
    params = 0.0
    for match in PARAMS.finditer(flat):
        value = _num(match.group(1))
        if 0.3 <= value <= 1000:
            params = max(params, value)

    active = 0.0
    moe_match = MOE.search(flat)
    if moe_match:
        active = _num(moe_match.group(1))

    return {
        "id": str(path),
        "name": path.stem,
        "file": name,
        "folder": str(path.parent),
        "size_gb": size_gb,
        "quant": quant,
        "params_b": params,
        "active_b": active,
        "moe": active > 0,
        "vision": any(tag in lower for tag in VISION),
    }


def scan(models_dir, limit: int = 400) -> list[dict]:
    """Ищем .gguf в одной или нескольких папках."""
    if isinstance(models_dir, str):
        roots = [models_dir]
    else:
        roots = [str(path) for path in (models_dir or [])]
    result = []
    seen = set()
    for raw in roots:
        root = pathlib.Path(raw)
        if not root.is_dir():
            continue
        for item in root.rglob("*.gguf"):
            key = str(item).lower()
            if key in seen:
                continue
            try:
                if any(marker in item.name.lower() for marker in SKIP):
                    continue
                if item.stat().st_size < 50 * 1024 * 1024:
                    continue
                result.append(describe(item))
                seen.add(key)
            except OSError:
                continue
            if len(result) >= limit:
                return result
    return result


def score(model: dict, budget_gb: float) -> float:
    """Пригодность модели: влезает ли в память и насколько быстро пойдёт.

    У MoE активно мало параметров — на встроенной графике это в разы быстрее,
    поэтому такие модели получают бонус.
    """
    need = model["size_gb"] * 1.12 + 1.5
    if need > budget_gb:
        return -1.0
    headroom = budget_gb - need
    weight = model["params_b"] or 7.0
    bonus = 0.0
    if model["moe"] and model["active_b"]:
        bonus = min(6.0, model["params_b"] / max(model["active_b"], 0.5))
    return round(weight * 0.6 + min(headroom, 12) * 0.12 + bonus, 3)


def rank(models: list[dict], budget_gb: float) -> list[dict]:
    scored = []
    for model in models:
        value = score(model, budget_gb)
        item = dict(model)
        item["score"] = value
        item["fits"] = value >= 0
        scored.append(item)
    scored.sort(key=lambda m: (m["fits"], m["score"]), reverse=True)
    return scored
