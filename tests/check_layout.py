"""Статическая проверка раскладки и стабильности мониторов.

Запуск: python tests/check_layout.py
Проверяет разметку зон, перетаскивание, отсутствие пересборки блоков мониторов
и CSS-правила, которые держат размеры неизменными.
"""
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
UI = ROOT / "app" / "ui"

problems = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + ((" | " + detail) if detail else ""))
    if not ok:
        problems.append(name)


html = (UI / "index.html").read_text(encoding="utf-8")
css = (UI / "style.css").read_text(encoding="utf-8")
app = (UI / "app.js").read_text(encoding="utf-8")
panels = (UI / "panels.js").read_text(encoding="utf-8")

blocks = re.findall(r'class="kblock" data-block="([a-z]+)"', html)
zones = re.findall(r'data-zone="([a-z0-9]+)"', html)
check("9 блоков (параметры внутри модели)", len(blocks) == 9, ",".join(blocks))
check("5 зон", zones == ["left", "right", "drawer1", "drawer2", "drawer3"], ",".join(zones))
check("нет дублей блоков", len(blocks) == len(set(blocks)))
check("зоны на теле панелей", 'id="railLeftBody" data-zone="left"' in html and 'id="railRightBody" data-zone="right"' in html)
check("теги сбалансированы", len(re.findall(r"<div\b", html)) == html.count("</div>"))
check("нет маркеров-заглушек", not re.findall(r"\[[A-Z][A-Z0-9_]{2,}\]", html + css + app))

check("patchBox объявлен", "function patchBox(box, html)" in app)
check("patchBox в глобальной области", "window.patchBox = patchBox" in app)
check("renderStats не перерисовывает ресурсы", "monRes" not in app.split("function renderStats()")[1].split("async function pollStats()")[0])
check("панели рисуют через patchBox", panels.count("window.patchBox(box, html)") >= 4,
      "мест: %d" % panels.count("window.patchBox(box, html)"))

check("прокрутка не меняет ширину", "scrollbar-gutter: stable" in css)
check("цифры табличные", "font-variant-numeric: tabular-nums" in css)
check("метка не обрезается", ".metric span { flex: 0 1 auto; white-space: nowrap; }" in css)
check("длинное значение переносится", "flex-wrap: wrap" in css.split(".metric {")[1].split("}")[0])
check("полоска не сжимается", ".meter { flex: none;" in css)
check("панели не сжимаются во флексе", "flex: none" in css.split(".drawer {")[1].split("}")[0])

check("блок ролей: шестерёнка", 'id="rolesSettings"' in html)
check("блок ролей: список", 'id="rolesToggle"' in html and 'id="rolesMenu"' in html)
check("нет перехода по клику на блок", 'data-tab=&quot;agents&quot;]' not in html)
check("слой подсказок", 'id="tipLayer"' in html and ".tipLayer" in css)
check("подсказки ролей в коде", "function wireRolesBlock()" in app and "showRoleTip" in app)
check("список ролей рисуется", "function renderRolesMenu()" in app)

center_html = html.split('id="composer"')[0].split('<div id="messages"')[1] if '<div id="messages"' in html else ''
model_block = html.split('data-block="model"')[1].split('data-block="resources"')[0]
check("режимы в центре чата", 'class="modes"' in center_html)
check("короткие подписи режимов", ">Один<span" in html and ">Команда<span" in html)
check("режимы внутри карточки ввода", 'class="composerCard"' in html and html.index('class="modes"') < html.index('id="composer"'))
check("подпись «Режим работы»", "Режим работы" in html)
check("примеров не меньше восьми", html.count('data-template=') >= 8, "найдено %d" % html.count('data-template='))
check("примеры по центру", "justify-content: center" in css and ".templates {" in css)
check("кнопка режима агента", 'id="agentModeBtn"' in center_html)
check("выбор модели в правой панели", 'id="modelPick"' in model_block and 'id="modelLoadBtn"' in model_block)
check("параметры модели рядом", 'id="modelTuning"' in model_block)
check("отдельного блока настроек нет", 'data-block="tuning"' not in html)
check("адрес сервера и копирование", 'id="modelAddr"' in model_block and 'id="modelAddrCopy"' in model_block)
check("шаблоны компактные", "min-height: 24px" in css and ".templates { display: flex; gap: 4px" in css)
check("селект модели сжимается", "flex: 1 1 220px" in css)
check("шторка сворачивается в полосу", 'id="drawerHead"' in html and 'id="drawerSummary"' in html)
check("полоса подписана", 'drawerName' in html and "renderDrawerHead" in app)
check("свёрнутая прячет блоки", ".drawer:not(.open) .drawerGrid" in css)

for name in ("app.js", "panels.js"):
    res = subprocess.run(["node", "--check", str(UI / name)], capture_output=True, text=True)
    check("node --check " + name, res.returncode == 0, res.stderr.strip()[:120])

print()
if problems:
    print("ПРОБЛЕМЫ:", len(problems))
    sys.exit(1)
print("раскладка и стабильность мониторов: все проверки пройдены")
