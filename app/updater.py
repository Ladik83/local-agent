"""Обновление приложения: проверка манифеста, скачивание и установка.

Только стандартная библиотека Python.
Сетевых вызовов при импорте модуля нет.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

try:  # обычный импорт пакетом
    from . import config
except ImportError:  # запасной путь: импорт из корня проекта
    from app import config  # type: ignore

FALLBACK_VERSION = "0.5.0"
USER_AGENT = "LocalAgent-Updater/1.0"
MAX_PACKAGE_BYTES = 200 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024

# Модули, без которых приложение не запустится.
REQUIRED_FILES = ("__init__.py", "config.py", "main.py")

ROOT_DIR = config.ROOT_DIR
DATA_DIR = config.DATA_DIR
DOWNLOAD_DIR = config.DOWNLOAD_DIR
LOGS_DIR = config.LOGS_DIR
APP_DIR = ROOT_DIR / "app"
BACKUP_DIR = DATA_DIR / "backup"
PENDING_FILE = DATA_DIR / "pending_update.json"
VERSION_FILE = ROOT_DIR / "VERSION"

LAST_ERROR = ""


# ---------------------------------------------------------------- служебное

def _log(message: str) -> None:
    """Тихая запись в data/logs/updater.log, ошибки записи не мешают работе."""
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOGS_DIR / "updater.log", "a", encoding="utf-8") as fh:
            fh.write("%s  %s\n" % (stamp, message))
    except OSError:
        pass


def _safe_name(value: str) -> str:
    """Имя файла без мусорных символов."""
    text = re.sub(r"[^0-9A-Za-z._-]+", "_", str(value or "").strip())
    return text.strip("._") or "unknown"


def _version_tuple(value) -> tuple:
    """Версия в кортеж чисел: '0.10.2' -> (0, 10, 2)."""
    parts = re.findall(r"\d+", str(value or ""))
    numbers = [int(item) for item in parts[:4]]
    while len(numbers) < 3:
        numbers.append(0)
    return tuple(numbers)


def current_version() -> str:
    """Версия из файла VERSION в корне проекта, иначе запасная."""
    try:
        text = VERSION_FILE.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return FALLBACK_VERSION
    if not text:
        return FALLBACK_VERSION
    text = text.split()[0]
    if text[:1] in ("v", "V"):
        text = text[1:]
    return text or FALLBACK_VERSION


# ---------------------------------------------------------------- проверка

def _fetch_manifest(url: str, timeout: float) -> dict:
    """Читаем манифест. Ошибки связи и разбора поднимаем наружу."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(MAX_MANIFEST_BYTES)
    return json.loads(raw.decode("utf-8"))


def check(url: str, timeout: float = 10) -> dict:
    """Сравниваем текущую версию с манифестом обновления.

    Манифест: {"version": "0.6.0", "url": "https://...zip", "notes": "..."}
    """
    current = current_version()
    result = {
        "ok": False,
        "current": current,
        "latest": "",
        "update": False,
        "url": "",
        "notes": "",
        "error": "",
    }
    if not isinstance(url, str) or not url.strip():
        result["error"] = "Не задан адрес манифеста"
        return result

    try:
        data = _fetch_manifest(url.strip(), timeout)
    except urllib.error.HTTPError as exc:
        result["error"] = "Сервер ответил кодом %s" % exc.code
        return result
    except urllib.error.URLError as exc:
        result["error"] = "Нет связи с сервером: %s" % (exc.reason,)
        return result
    except (TimeoutError, OSError) as exc:
        result["error"] = "Сеть недоступна: %s" % exc
        return result
    except (ValueError, UnicodeDecodeError):
        result["error"] = "Ответ сервера не похож на JSON"
        return result

    if not isinstance(data, dict):
        result["error"] = "Манифест должен быть объектом JSON"
        return result

    latest = str(data.get("version") or "").strip()
    if not latest:
        result["error"] = "В манифесте нет поля version"
        return result

    result["latest"] = latest
    result["url"] = str(data.get("url") or "").strip()
    result["notes"] = str(data.get("notes") or "")
    result["update"] = _version_tuple(latest) > _version_tuple(current)
    result["ok"] = True
    _log("проверка: текущая %s, в манифесте %s, обновление %s"
         % (current, latest, "нужно" if result["update"] else "не нужно"))
    return result


# ---------------------------------------------------------------- скачивание

def _download(url: str, timeout: float, filename: str = "") -> Path:
    """Качаем архив в папку загрузок, отдаём путь к файлу."""
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    name = filename or Path(urllib.parse.urlsplit(url).path).name or "update.zip"
    if not name.lower().endswith(".zip"):
        name += ".zip"
    target = DOWNLOAD_DIR / _safe_name(name)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    total = 0
    with urllib.request.urlopen(request, timeout=timeout) as response:
        with open(target, "wb") as handle:
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_PACKAGE_BYTES:
                    raise ValueError("архив больше %d МБ" % (MAX_PACKAGE_BYTES // (1024 * 1024)))
                handle.write(chunk)
    if total == 0:
        raise ValueError("сервер отдал пустой файл")
    _log("скачан архив %s, байт %d" % (target.name, total))
    return target


# ---------------------------------------------------------------- бэкап

def _backup_app(version: str) -> Path:
    """Копия текущей папки app в data/backup/app-<версия>.zip."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / ("app-%s.zip" % _safe_name(version))
    if target.exists():
        try:
            target.unlink()
        except OSError:
            target = BACKUP_DIR / ("app-%s-%d.zip" % (_safe_name(version), int(time.time())))
    with zipfile.ZipFile(str(target), "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(APP_DIR.rglob("*")):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
                continue
            try:
                zf.write(str(path), str(path.relative_to(ROOT_DIR)))
            except OSError:
                continue
    _log("бэкап папки app: %s" % target)
    return target


# ---------------------------------------------------------------- установка

def _find_payload(root: Path):
    """Ищем в распакованном архиве папку app с main.py."""
    direct = root / "app"
    if (direct / "main.py").is_file():
        return direct
    try:
        children = sorted(item for item in root.iterdir() if item.is_dir())
    except OSError:
        children = []
    for child in children:
        inner = child / "app"
        if (inner / "main.py").is_file():
            return inner
        if child.name == "app" and (child / "main.py").is_file():
            return child
        nested = list(child.glob("*/app/main.py"))
        if nested:
            return nested[0].parent
    if (root / "main.py").is_file():
        return root
    return None


def _verify_payload(app_dir: Path, expected: tuple = ()):
    """Проверяем набор модулей: (ошибки, предупреждения).

    Ошибки блокируют установку. Ошибки синтаксиса в [PERSON_NAME]
    только предупреждают: архив может нести уже сломанный модуль.
    """
    problems = []
    warnings = []
    if not (app_dir / "main.py").is_file():
        problems.append("нет файла app/main.py")
    for name in REQUIRED_FILES:
        if not (app_dir / name).is_file():
            problems.append("нет файла app/%s" % name)

    modules = [item for item in app_dir.rglob("*.py") if item.is_file()]
    if len(modules) < 3:
        problems.append("модулей меньше трёх")

    for name in expected:
        if not (app_dir / name).is_file():
            problems.append("нет файла app/%s" % name)

    for path in modules:
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            problems.append("не читается %s: %s" % (path.name, exc))
            continue
        try:
            compile(source, str(path), "exec")
        except SyntaxError as exc:
            message = "ошибка в %s: строка %s" % (path.name, exc.lineno)
            if path.name in REQUIRED_FILES or path.name == "main.py":
                problems.append(message)
            else:
                warnings.append(message)
        except ValueError as exc:
            message = "битый модуль %s: %s" % (path.name, exc)
            if path.name in REQUIRED_FILES:
                problems.append(message)
            else:
                warnings.append(message)
    return problems, warnings


def _swap_in(new_app: Path) -> None:
    """Подменяем папку app: старая -> app.old, новая -> app, затем уборка."""
    spare = ROOT_DIR / "app.old"
    if spare.exists() or spare.is_symlink():
        shutil.rmtree(spare, ignore_errors=True)
        if spare.exists():
            try:
                os.remove(spare)
            except OSError:
                pass

    moved = False
    try:
        if APP_DIR.exists() or APP_DIR.is_symlink():
            os.rename(str(APP_DIR), str(spare))
            moved = True
        shutil.move(str(new_app), str(APP_DIR))
    except OSError:
        # Откат: возвращаем старую папку на место.
        if moved and not APP_DIR.exists() and spare.exists():
            try:
                os.rename(str(spare), str(APP_DIR))
            except OSError:
                pass
        raise

    if spare.exists():
        shutil.rmtree(spare, ignore_errors=True)


def _mark_pending(zip_path: Path, version: str, reason: str) -> None:
    """Флаг отложенного обновления: применим после перезапуска."""
    payload = {
        "zip": str(zip_path),
        "version": str(version or ""),
        "root": str(ROOT_DIR),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "reason": str(reason or ""),
    }
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        PENDING_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _log("обновление отложено: %s" % reason)
    except OSError as exc:
        _log("не удалось записать флаг обновления: %s" % exc)


def _clear_pending() -> None:
    try:
        if PENDING_FILE.exists():
            PENDING_FILE.unlink()
    except OSError:
        pass


def pending_info():
    """Данные отложенного обновления или None."""
    try:
        raw = PENDING_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("zip"):
        return None
    return data


def _install_from_zip(zip_path: Path, version: str = "") -> dict:
    """Распаковать архив, проверить и подменить папку app."""
    out = {"ok": False, "applied": False, "pending": False, "files": 0, "error": "", "message": ""}
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        out["error"] = "Архив обновления не найден"
        return out
    if not zipfile.is_zipfile(str(zip_path)):
        out["error"] = "Файл не является zip-архивом"
        return out

    work = Path(tempfile.mkdtemp(prefix="update-", dir=str(DATA_DIR)))
    try:
        expected = ()
        try:
            with zipfile.ZipFile(str(zip_path)) as zf:
                broken = zf.testzip()
                if broken:
                    out["error"] = "В архиве повреждён файл: %s" % broken
                    return out
                names = []
                for name in zf.namelist():
                    if name.endswith("/"):
                        continue
                    norm = name.replace("\\", "/")
                    if norm.endswith(".py") and (norm.startswith("app/") or "/app/" in norm):
                        names.append(norm.split("app/", 1)[1])
                expected = tuple(sorted(set(names)))
                zf.extractall(str(work))
        except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
            out["error"] = "Не удалось распаковать архив: %s" % exc
            return out

        payload = _find_payload(work)
        if payload is None:
            out["error"] = "В архиве нет папки app с файлом main.py"
            return out

        problems, warnings = _verify_payload(payload, expected)
        out["warnings"] = warnings
        if problems:
            out["error"] = "Архив неполный: " + "; ".join(problems[:4])
            return out
        out["files"] = len([item for item in payload.rglob("*.py") if item.is_file()])

        try:
            _swap_in(payload)
        except OSError as exc:
            _mark_pending(zip_path, version, str(exc))
            out.update({
                "ok": True,
                "applied": False,
                "pending": True,
                "message": "Файлы заняты. Обновление применится после перезапуска.",
            })
            return out

        out.update({"ok": True, "applied": True, "pending": False,
                    "message": "Обновление установлено"})
        _log("установлено обновление %s, файлов %d" % (version or "?", out["files"]))
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


def download_and_apply(manifest_url: str, zip_url: str = "", timeout: float = 10) -> dict:
    """Скачать архив обновления, сделать бэкап и подменить папку app."""
    global LAST_ERROR
    current = current_version()
    out = {
        "ok": False,
        "applied": False,
        "pending": False,
        "current": current,
        "version": "",
        "backup": "",
        "files": 0,
        "error": "",
        "message": "",
    }

    latest = ""
    source = str(zip_url or "").strip()
    if not source:
        info = check(manifest_url, timeout=timeout)
        if not info.get("ok"):
            out["error"] = info.get("error") or "Не удалось прочитать манифест"
            LAST_ERROR = out["error"]
            return out
        latest = info.get("latest") or ""
        source = info.get("url") or ""
        if not source:
            out["error"] = "В манифесте нет ссылки на архив"
            LAST_ERROR = out["error"]
            return out
    elif manifest_url:
        info = check(manifest_url, timeout=timeout)
        if info.get("ok"):
            latest = info.get("latest") or ""

    out["version"] = latest or current

    try:
        package = _download(source, timeout=max(float(timeout), 30.0))
    except urllib.error.HTTPError as exc:
        out["error"] = "Сервер ответил кодом %s" % exc.code
    except urllib.error.URLError as exc:
        out["error"] = "Нет связи с сервером: %s" % (exc.reason,)
    except (TimeoutError, OSError, ValueError) as exc:
        out["error"] = "Не удалось скачать архив: %s" % exc
    else:
        if not zipfile.is_zipfile(str(package)):
            out["error"] = "Скачанный файл не является zip-архивом"
        else:
            try:
                backup = _backup_app(current)
                out["backup"] = str(backup)
            except OSError as exc:
                out["error"] = "Не удалось сделать бэкап: %s" % exc
            else:
                result = _install_from_zip(package, latest or current)
                out.update({
                    "ok": bool(result.get("ok")),
                    "applied": bool(result.get("applied")),
                    "pending": bool(result.get("pending")),
                    "files": int(result.get("files") or 0),
                    "error": result.get("error") or "",
                    "message": result.get("message") or "",
                })

    LAST_ERROR = out["error"]
    if out["error"]:
        _log("ошибка обновления: %s" % out["error"])
    return out


def apply_pending() -> bool:
    """Применить отложенное обновление. True — папка app уже заменена."""
    info = pending_info()
    if not info:
        return False

    spare = ROOT_DIR / "app.old"
    if not APP_DIR.exists() and spare.exists():
        # Прошлый запуск оборвался между двумя переименованиями.
        try:
            os.rename(str(spare), str(APP_DIR))
        except OSError as exc:
            _log("не удалось вернуть папку app: %s" % exc)
            return False

    package = Path(str(info.get("zip")))
    if not package.is_file():
        _log("архив отложенного обновления пропал: %s" % package)
        _clear_pending()
        return False

    result = _install_from_zip(package, str(info.get("version") or ""))
    if result.get("applied"):
        _clear_pending()
        _log("отложенное обновление применено: %s" % (info.get("version") or "?"))
        return True
    _log("отложенное обновление не применилось: %s" % (result.get("error") or result.get("message")))
    return False


def has_pending() -> bool:
    """Есть ли неприменённое обновление."""
    return pending_info() is not None
