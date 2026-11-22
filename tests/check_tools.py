"""Проверка каталога инструментов и авто-выбора (без запуска моделей).

Запуск: python tests/check_tools.py
Смысловая часть использует модель эмбеддингов, если она доступна; иначе только слова.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import agent, config, main, tools

problems = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + ((" | " + detail) if detail else ""))
    if not ok:
        problems.append(name)


cfg = dict(config.load())
cfg.update({"tools_auto": True, "allow_write": True, "allow_shell": True, "allow_web": True,
            "tools_off": [], "mcp": []})

catalog = tools.catalog()
check("каталог встроенных", len(catalog) == len(tools.SCHEMAS) == 11, "записей: %d" % len(catalog))
check("у каждого описание и теги", all(item["about"] and item["tags"] for item in catalog))
check("опасные помечены", any(item["danger"] == "запись" for item in catalog))

cases = {
    "прочитай файл README.md": {"read_file"},
    "где используется patchBox в коде": {"grep"},
    "сколько будет 15% от 2400": {"calc"},
    "какие новости про llama.cpp": {"web_search"},
    "покажи что лежит в папке D:/Strata": {"list_files"},
    "запусти git status": {"run_command"},
    "что написано в регламенте про отпуск": {"search_docs"},
    "какой сегодня день": {"now"},
    "сохрани текст в файл notes.txt": {"write_file"},
}
for task, expected in cases.items():
    picked = set(tools.select(task, cfg))
    check("выбор: " + task[:38], expected <= picked, "-> " + ", ".join(sorted(picked)))

check("болтовня без инструментов", tools.select("привет, как дела?", cfg) == [])
check("разбор задачи без инструментов", tools.select("проанализируй архитектуру проекта и предложи план", cfg) == [])

off = dict(cfg)
off["tools_off"] = ["calc"]
check("выключенный не выбирается", "calc" not in tools.select("сколько будет 2+2", off))
check("выключенный не попадает в схемы", "calc" not in [s["function"]["name"] for s in tools.schemas(off)])

auto_cfg = dict(cfg)
events = []
prepared = agent._prepare_tools(auto_cfg, "прочитай файл README.md", events.append)
check("пометка в чате", bool(events) and "Инструменты под задачу" in events[0]["text"], events[0]["text"] if events else "")
check("ограничение набора", prepared.get("_only_tools") == ["read_file"], str(prepared.get("_only_tools")))

manual = agent._prepare_tools(dict(cfg, tools_auto=False), "прочитай файл", events.append)
check("без авто ничего не сужаем", "_only_tools" not in manual)

check("каталог MCP не пустой", len(main.MCP_CATALOG) >= 10, "серверов: %d" % len(main.MCP_CATALOG))
check("подстановка путей", "%HOME%" not in " ".join(main._expand_args(["%HOME%"])))
check("маршруты", all(route in pathlib.Path(main.__file__).read_text(encoding="utf-8")
                      for route in ("/api/tools", "/api/tools/toggle", "/api/tools/auto", "/api/mcp/add")))
ui = (ROOT / "app" / "ui" / "app.js").read_text(encoding="utf-8")
html = (ROOT / "app" / "ui" / "index.html").read_text(encoding="utf-8")
check("вкладка в интерфейсе", 'data-tab="tools"' in html and 'id="view-tools"' in html)
check("переключатели в интерфейсе", 'id="toolsAuto"' in html and "data-tool=" in ui and "data-mcp=" in ui)

print()
if problems:
    print("ПРОБЛЕМЫ:", len(problems))
    sys.exit(1)
print("инструменты и MCP: все проверки пройдены")
