"""Проверки вкладки «Команда»: две колонки, добавление и удаление ролей, генератор описаний."""
from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, roles  # noqa: E402

problems = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global problems
    if ok:
        print("OK  ", name, ("| " + extra) if extra else "")
    else:
        problems += 1
        print("FAIL", name, ("| " + extra) if extra else "")


html = (ROOT / "app" / "ui" / "index.html").read_text(encoding="utf-8")
js = (ROOT / "app" / "ui" / "app.js").read_text(encoding="utf-8")
css = (ROOT / "app" / "ui" / "style.css").read_text(encoding="utf-8")
main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
ast.parse(main)
ast.parse((ROOT / "app" / "roles.py").read_text(encoding="utf-8"))

team = html.split('id="view-agents"')[1].split("</section>")[0]
check("две колонки", 'class="teamCols"' in team and 'id="roleList"' in team and 'id="roleEditor"' in team)
check("разделитель колонок", ".teamList { border-right: 1px solid var(--border)" in css)
check("узкая колонка списка", "minmax(210px, 270px)" in css)
check("на узком экране одна колонка", "@media (max-width: 900px)" in css and "grid-template-columns: 1fr" in css)
check("кнопка добавления", 'id="addRole"' in team)
check("кнопка сброса", 'id="resetRoles"' in team)
check("сохранение на месте", 'id="saveRoles"' in team)
check("список и редактор раздельно", 'id="roleList"' in team and 'id="roleEditor"' in team)
check("счётчик ролей", 'id="rolesCount"' in team)

check("добавление роли в JS", "function addRole()" in js and "STATE.roleSel" in js)
check("удаление роли в JS", "function deleteRole()" in js and "rolesRemoved" in js)
check("сброс в JS", "function resetRoles()" in js)
check("генератор описания в JS", "function suggestRolePrompt()" in js and "/api/roles/suggest" in js)
check("пресеты инструментов", "ROLE_TOOLS_LABEL" in js and "только чтение" in js)
check("редактор собирается из полей", "renderRoleEditor" in js and 'id="rolePrompt"' in js)

check("маршрут подсказки на сервере", '"/api/roles/suggest"' in main and "def _model_once" in main)
check("маршрут ролей принимает removed", 'data.get("removed")' in main)
check("настройка хранит удалённые", '"roles_removed"' in (ROOT / "app" / "config.py").read_text(encoding="utf-8"))

cfg = {"roles": [], "roles_removed": ["tester"]}
merged = roles.merge(cfg)
check("удалённая стандартная роль скрыта", all(item["id"] != "tester" for item in merged), "осталось %d" % len(merged))
cfg2 = {"roles": [{"id": "role-x", "title": "Свой", "thinking": "low", "tools": "all", "prompt": "текст"}],
        "roles_removed": []}
merged2 = roles.merge(cfg2)
check("своя роль попадает в список", any(item["id"] == "role-x" for item in merged2), "всего %d" % len(merged2))

print()
if problems:
    print("ПРОБЛЕМЫ:", problems)
    sys.exit(1)
print("команда: все проверки пройдены")
