"""Настройки приложения: простой JSON, без внешних зависимостей."""
from __future__ import annotations

import json
import os
import pathlib

APP_NAME = "Локальный агент"
ROOT_DIR = pathlib.Path(__file__).resolve().parent.parent
PORTABLE_MARKER = ROOT_DIR / "portable.txt"
PORTABLE = PORTABLE_MARKER.exists()


def _data_root() -> pathlib.Path:
    """Портативный режим: данные рядом [PERSON_NAME], иначе в профиле Windows."""
    env = os.environ.get("LOCALAGENT_HOME")
    if env:
        return pathlib.Path(env)
    if PORTABLE:
        return ROOT_DIR / "data"
    return pathlib.Path(os.environ.get("LOCALAPPDATA") or pathlib.Path.home()) / "LocalAgent"


DATA_DIR = _data_root()
DATA_DIR.mkdir(parents=True, exist_ok=True)

SETTINGS_PATH = DATA_DIR / "settings.json"
ENGINES_DIR = DATA_DIR / "engines"
DOWNLOAD_DIR = DATA_DIR / "downloads"
CHATS_DIR = DATA_DIR / "chats"
LOGS_DIR = DATA_DIR / "logs"
for _d in (ENGINES_DIR, DOWNLOAD_DIR, CHATS_DIR, LOGS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def guess_models_dir() -> str:
    """Ищем, где у пользователя уже лежат модели, чтобы не качать заново."""
    candidates = [
        pathlib.Path(r"F:\Models for LMstudio"),
        pathlib.Path.home() / ".lmstudio" / "models",
        pathlib.Path.home() / ".cache" / "lm-studio" / "models",
        pathlib.Path(r"D:\Models"),
        pathlib.Path(r"E:\Models"),
        DATA_DIR / "models",
    ]
    for p in candidates:
        try:
            if p.exists() and any(p.rglob("*.gguf")):
                return str(p)
        except OSError:
            continue
    return str(DATA_DIR / "models")


CANDIDATE_DIRS = [
    r"F:\Models for LMstudio",
    r"D:\Strata\cache\hub",
    r"D:\Strata\models",
    r"D:\LLM_wraper_progect\models",
    str(pathlib.Path.home() / ".lmstudio" / "models"),
]


def has_gguf(folder, depth: int = 4) -> bool:
    """Быстрая проверка: есть ли в папке .gguf (без полного обхода дерева)."""
    root = pathlib.Path(folder)
    if not root.is_dir():
        return False
    base = len(root.parts)
    for current, subdirs, files in os.walk(str(root)):
        if len(pathlib.Path(current).parts) - base >= depth:
            subdirs[:] = []
        for name in files:
            if name.lower().endswith(".gguf"):
                return True
    return False


def autodetect_dirs() -> tuple[str, list]:
    """Где на дисках уже лежат модели: первая папка — основная, остальные — дополнительные."""
    found = [pathlib.Path(item) for item in CANDIDATE_DIRS if has_gguf(item)]
    if not found:
        return str(DATA_DIR / "models"), []
    return str(found[0]), [str(item) for item in found[1:]]


DEFAULTS: dict = {
    "models_dir": None,          # заполняется при первом запуске
    "extra_dirs": [],            # дополнительные папки с .gguf
    "provider": {"kind": "local", "base_url": "", "model": ""},  # local | server | api
    "use_team": False,           # отвечает команда ролей, а не один агент
    "mcp": [],                   # подключённые внешние наборы инструментов
    "projects": [],              # рабочие папки пользователя
    "voice_tts": False,          # озвучивать ответы голосом
    "tray": False,               # иконка в области уведомлений
    "update_url": "",           # ссылка на манифест обновлений
    "approval_mode": "ask",      # ask | auto — подтверждать запись файлов и команды
    "voice_model": "turbo",      # base | small | turbo
    "voice_lang": "ru",          # ru | en | auto
    "roles": [],                 # правки ролей поверх стандартных
    "engine_tag": None,          # например b12421
    "engine_variant": None,      # vulkan | cpu
    "last_model": None,          # абсолютный путь к .gguf
    "context": 8192,             # размер контекста
    "ngl": 99,                   # слоёв на GPU
    "temperature": 0.7,
    "allow_write": False,        # агент может менять файлы
    "allow_shell": False,        # агент может запускать команды
    "allow_web": True,           # агент может искать в интернете
    "auto_update_engine": False,  # проверять обновления llama.cpp при старте
    "system_prompt": (
        "Ты — локальный ИИ-помощник на компьютере пользователя. "
        "Отвечай по-русски, кратко и по делу. "
        "Если для ответа нужен файл или команда — используй инструменты, "
        "но не выдумывай содержимое файлов. Файлы и папки называй полными путями."
    ),
}

_cfg: dict | None = None


def load() -> dict:
    global _cfg
    if _cfg is not None:
        return _cfg
    cfg = dict(DEFAULTS)
    try:
        if SETTINGS_PATH.exists():
            cfg.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    if not cfg.get("models_dir") or not cfg.get("extra_dirs"):
        primary, extras = autodetect_dirs()
        cfg["models_dir"] = cfg.get("models_dir") or primary
        if not cfg.get("extra_dirs"):
            cfg["extra_dirs"] = [item for item in extras if item != cfg["models_dir"]]
    _cfg = cfg
    return cfg


def save(patch: dict | None = None) -> dict:
    cfg = load()
    if patch:
        for k, v in patch.items():
            if k in DEFAULTS:
                cfg[k] = v
    try:
        SETTINGS_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return cfg
