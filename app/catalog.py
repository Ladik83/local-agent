"""Каталог рекомендованных моделей — на случай, если своих .gguf на диске нет."""
from __future__ import annotations

HF = "https://huggingface.co/%s/resolve/main/%s?download=true"

CATALOG = [
    {
        "id": "qwen3-4b", "title": "Qwen3 4B Instruct (2507)", "size_gb": 2.50,
        "min_ram_gb": 8, "params_b": 4.0, "active_b": 0, "vision": False,
        "repo": "unsloth/Qwen3-4B-Instruct-2507-GGUF",
        "file": "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        "note": "лёгкая и быстрая, хорошо держит инструменты",
    },
    {
        "id": "qwen3-8b", "title": "Qwen3 8B (2507)", "size_gb": 5.03,
        "min_ram_gb": 14, "params_b": 8.0, "active_b": 0, "vision": False,
        "repo": "unsloth/Qwen3-8B-GGUF", "file": "Qwen3-8B-Q4_K_M.gguf",
        "note": "золотая середина для 16 ГБ и больше",
    },
    {
        "id": "qwen3-vl-8b", "title": "Qwen3-VL 8B (видит картинки)", "size_gb": 5.03,
        "min_ram_gb": 16, "params_b": 8.0, "active_b": 0, "vision": True,
        "repo": "unsloth/Qwen3-VL-8B-Instruct-GGUF",
        "file": "Qwen3-VL-8B-Instruct-Q4_K_M.gguf",
        "note": "то же 8B, но умеет читать изображения и скриншоты",
    },
    {
        "id": "qwen3-30b-a3b", "title": "Qwen3 30B A3B (MoE)", "size_gb": 17.69,
        "min_ram_gb": 32, "params_b": 30.0, "active_b": 3.3, "vision": False,
        "repo": "unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF",
        "file": "Qwen3-30B-A3B-Instruct-2507-UD-Q4_K_XL.gguf",
        "note": "30B качество при скорости 3B — лучший выбор для 32 ГБ+",
    },
    {
        "id": "llama32-3b", "title": "Llama 3.2 3B Instruct", "size_gb": 2.02,
        "min_ram_gb": 6, "params_b": 3.0, "active_b": 0, "vision": False,
        "repo": "bartowski/Llama-3.2-3B-Instruct-GGUF",
        "file": "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        "note": "самый скромный вариант для слабых машин",
    },
]


def url(entry: dict) -> str:
    return HF % (entry["repo"], entry["file"])


def local_url(entry: dict) -> str:
    """Ссылка на файл в локальной папке моделей (если решим складывать туда)."""
    return entry["file"]


def recommend(budget_gb: float, local_ranked: list[dict]) -> dict:
    """Что запускать: сначала свои файлы, потом каталог."""
    for model in local_ranked:
        if model.get("fits"):
            why = "уже на диске"
            if model.get("moe") and model.get("active_b"):
                why += ", MoE: %gB активных параметров — быстро на встройке" % model["active_b"]
            return {"kind": "local", "model": model, "why": why}
    fits = [c for c in CATALOG if c["size_gb"] * 1.12 + 1.5 <= budget_gb]
    if not fits:
        fits = [CATALOG[0]]
    best = max(fits, key=lambda c: (c["active_b"] or c["params_b"]))
    return {"kind": "catalog", "model": best, "why": best["note"]}
