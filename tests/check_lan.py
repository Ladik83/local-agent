"""Доступ по локальной сети: режим «только чтение» и мобильная вёрстка."""
from __future__ import annotations

import ast
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402

problems = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global problems
    if ok:
        print("OK  ", name, ("| " + extra) if extra else "")
    else:
        problems += 1
        print("FAIL", name, ("| " + extra) if extra else "")


main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
css = (ROOT / "app" / "ui" / "style.css").read_text(encoding="utf-8")
html = (ROOT / "app" / "ui" / "index.html").read_text(encoding="utf-8")
app_js = (ROOT / "app" / "ui" / "app.js").read_text(encoding="utf-8")
ast.parse(main)

check("настройка по умолчанию — только чтение", config.DEFAULTS.get("lan_readonly") is True,
      str(config.DEFAULTS.get("lan_readonly")))
check("сервер знает про чужой адрес", "def _lan_readonly(self)" in main and "is_loopback" in main)
check("список разрешённых маршрутов", 'LAN_WRITE_OK = {"/api/chat"' in main)
check("403 на изменения", "С этого устройства доступно только чтение" in main)
check("в чате выключаются запись и команды", 'cfg["allow_write"] = False' in main and 'cfg["allow_shell"] = False' in main)
check("переключатель в интерфейсе", 'id="lanReadonly"' in html and "lanReadonly" in app_js)
check("ссылка для телефона копируется", 'data-copy=' in app_js)
check("мобильная вёрстка", "@media (max-width: 640px)" in css)
check("поле ввода на телефоне во всю ширину", ".composer textarea { flex: 1 1 100%" in css)
check("панели на телефоне скрыты", ".railLeft, .railRight { width: 86vw; }" in css)
check("нижняя шторка на телефоне скрыта", "  .drawer { display: none; }" in css)

port = os.environ.get("AGENT_PORT", "7879")
token_file = pathlib.Path(os.environ.get("LOCALAPPDATA", ""), "LocalAgent", "access.token")
base = "http://127.0.0.1:%s" % port
if token_file.exists():
    token = token_file.read_text(encoding="utf-8").strip()
    try:
        with urllib.request.urlopen(base + "/api/lan?token=" + token, timeout=10) as resp:
            info = json.loads(resp.read().decode("utf-8"))
        check("живой сервер отдаёт режим", "readonly" in info, str(info.get("readonly")))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print("---- сервер на %s не ответил: %s" % (port, exc))

print()
if problems:
    print("ПРОБЛЕМЫ:", problems)
    sys.exit(1)
print("доступ по сети: все проверки пройдены")
