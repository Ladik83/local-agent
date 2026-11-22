"""Проверка опции обновления движка llama.cpp.

Запуск: python tests/check_engine.py
Живая часть обращается к GitHub; без интернета проверка помечается как пропуск, а не провал.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, engine, main

problems = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + ((" | " + detail) if detail else ""))
    if not ok:
        problems.append(name)


app_js = (ROOT / "app" / "ui" / "app.js").read_text(encoding="utf-8")
main_py = pathlib.Path(main.__file__).read_text(encoding="utf-8")

check("функция сверки версий", hasattr(engine, "check_latest") and callable(engine.check_latest))
check("маршрут проверки", '/api/engine/check' in main_py)
check("авто-проверка при старте", 'auto_update_engine' in main_py and 'check_latest(force=True)' in main_py)
check("ключ в настройках", "auto_update_engine" in config.DEFAULTS)
check("кнопка «Проверить сейчас»", 'id="engineCheck"' in app_js and 'function checkEngineUpdate' in app_js)
check("галочка авто-проверки", 'id="autoUpdateEngine"' in app_js)
check("строка про обновление", "Обновление движка" in app_js)

html = (ROOT / "app" / "ui" / "index.html").read_text(encoding="utf-8")
check("поле адреса манифеста", 'id="updateUrl"' in html)
check("кнопка проверки приложения", 'id="updateCheck"' in html and 'function checkAppUpdate' in app_js)
check("вывод версий", 'id="updateState"' in html and "доступна" in app_js)
check("кнопка установки", 'id="updateApply"' in html and 'function applyAppUpdate' in app_js)
check("маршрут проверки приложения", '/api/update/check' in main_py)
check("адрес сохраняется", "update_url" in app_js and "update_url" in config.DEFAULTS)

result = engine.check_latest(force=True)
if result.get("ok"):
    print("     установлено:", result.get("tag") or "не задано", "| последняя:", result.get("latest"))
    check("ответ проверки", bool(result.get("latest")), str(result.get("latest")))
    check("флаг обновления", isinstance(result.get("update_available"), bool))
else:
    print("     пропуск живой проверки:", result.get("error"))

print()
if problems:
    print("ПРОБЛЕМЫ:", len(problems))
    sys.exit(1)
print("обновление движка: все проверки пройдены")
