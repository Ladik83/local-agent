"""Статическая проверка зрения: код, маршруты, интерфейс.

Запуск: python tests/check_vision.py
Модели не запускает — только проверяет, что всё на месте.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = ROOT / "app"
UI = APP / "ui"

problems = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + ((" | " + detail) if detail else ""))
    if not ok:
        problems.append(name)


def read(path):
    return path.read_text(encoding="utf-8")


vision = read(APP / "vision.py")
engine = read(APP / "engine.py")
main = read(APP / "main.py")
agent = read(APP / "agent.py")
config = read(APP / "config.py")
app_js = read(UI / "app.js")
html = read(UI / "index.html")
css = read(UI / "style.css")

check("модуль зрения", all(token in vision for token in ("def candidates(", "def pick(", "def start(", "def ask(", "def stop(")))
check("фильтр черновиков", "dflash" in vision and "MIN_GB" in vision)
check("проектор ищется универсально", "def find_projector(" in engine and '"--mmproj"' in engine)
check("слот зрения с проектором", '"--mmproj", projector' in vision and "-c" in vision)
check("картинки в запросе", "def _with_images(" in agent and "image_url" in agent)
check("ответ без инструментов", "def chat_plain(" in agent)
check("маршруты зрения", all(route in main for route in ('/api/vision/models', '/api/vision/start', '/api/vision/stop')))
check("картинки до загрузки чат-модели", "main_sees" in main and "vision.start(" in main)
check("статус в /api/state", '"vision": vision.status()' in main)
check("настройки в DEFAULTS", all(key in config for key in ('"vision_model"', '"vision_enabled"', '"vision_context"')))
check("выгрузка при старте", "vision.cleanup()" in main)
check("поля в настройках", all(x in html for x in ('id="visionEnabled"', 'id="visionModel"', 'id="visionCheck"', 'id="visionStop"')))
check("вложения-картинки", "kind === 'image'" in app_js and "images: images" in app_js)
check("служебные строки видны", "event.type === 'note'" in app_js)
check("статус в строке состояния", 'id="sbVision"' in html and "stats.vision" in app_js)
check("чип картинки в CSS", ".attachChip.image" in css)

print()
if problems:
    print("ПРОБЛЕМЫ:", len(problems))
    sys.exit(1)
print("зрение: все проверки пройдены")
