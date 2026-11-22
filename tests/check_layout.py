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
check("10 блоков", len(blocks) == 10, ",".join(blocks))
check("5 зон", zones == ["left", "right", "drawer1", "drawer2", "drawer3"], ",".join(zones))
check("нет дублей блоков", len(blocks) == len(set(blocks)))
check("зоны на теле панелей", 'id="railLeftBody" data-zone="left"' in html and 'id="railRightBody" data-zone="right"' in html)
check("теги сбалансированы", len(re.findall(r"<div\b", html)) == html.count("</div>"))
check("нет маркеров-заглушек", not re.findall(r"\[[A-Z][A-Z0-9_]{2,}\]", html + css + app))

check("patchBox объявлен", "function patchBox(box, html)" in app)
check("patchBox в глобальной области", "window.patchBox = patchBox" in app)
check("renderStats не перерисовывает ресурсы", "monRes" not in app.split("function renderStats()")[1].split("async function pollStats()")[0])
check("панели рисуют через patchBox", panels.count("window.patchBox(box, html)") >= 5,
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

for name in ("app.js", "panels.js"):
    res = subprocess.run(["node", "--check", str(UI / name)], capture_output=True, text=True)
    check("node --check " + name, res.returncode == 0, res.stderr.strip()[:120])

print()
if problems:
    print("ПРОБЛЕМЫ:", len(problems))
    sys.exit(1)
print("раскладка и стабильность мониторов: все проверки пройдены")
