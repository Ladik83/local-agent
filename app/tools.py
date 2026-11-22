"""Инструменты агента: файлы, команды, поиск в сети, время, калькулятор."""
from __future__ import annotations

from . import diag

import ast
import datetime as _dt
import html as _html
import operator
import pathlib
import re
import subprocess
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) LocalAgent/0.1"}
MAX_OUT = 12000
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

SCHEMAS = [
    {"name": "list_files", "description": "Показать содержимое папки на компьютере.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Полный путь к папке"}}, "required": ["path"]}},
    {"name": "read_file", "description": "Прочитать текстовый файл (txt, md, json, csv, код).",
     "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Полный путь к файлу"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Создать или перезаписать текстовый файл целиком.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "run_command", "description": "Выполнить команду cmd.exe и вернуть её вывод.",
     "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "web_search", "description": "Найти информацию в интернете, вернуть заголовки и ссылки.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "fetch_url", "description": "Скачать страницу по ссылке и вернуть её текст.",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "now", "description": "Текущие дата и время на компьютере.", "parameters": {"type": "object", "properties": {}}},
    {"name": "calc", "description": "Посчитать математическое выражение точно.",
     "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}},
    {"name": "find", "description": "Найти файлы по маске в папке (например *.py, *config*).",
     "parameters": {"type": "object", "properties": {"pattern": {"type": "string", "description": "Маска имени"}, "path": {"type": "string", "description": "Где искать"}}, "required": ["pattern"]}},
    {"name": "grep", "description": "Найти строки по тексту или регулярному выражению в файлах папки.",
     "parameters": {"type": "object", "properties": {"pattern": {"type": "string", "description": "Текст или regex"}, "path": {"type": "string", "description": "Где искать"}, "glob": {"type": "string", "description": "Маска файлов, по умолчанию все"}}, "required": ["pattern"]}},
    {"name": "todo_write", "description": "Записать план задачи: список шагов и что уже сделано.",
     "parameters": {"type": "object", "properties": {
         "title": {"type": "string", "description": "Короткое название задачи"},
         "steps": {"type": "array", "items": {"type": "string"}, "description": "Шаги по порядку"},
         "done": {"type": "array", "items": {"type": "integer"}, "description": "Номера выполненных шагов (с 1)"},
         "current": {"type": "integer", "description": "Номер шага, который делается сейчас (с 1)"}},
      "required": ["steps"]}},
    {"name": "verify_project", "description": "Проверить проект: тесты, сборка или проверка синтаксиса — и вернуть результат.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Папка проекта (по умолчанию текущая)"}}}},
    {"name": "search_docs", "description": "Найти ответ в базе знаний (проиндексированные папки и документы) с указанием источника.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Что искать"},
                                                        "limit": {"type": "integer", "description": "Сколько фрагментов вернуть"}},
                    "required": ["query"]}},
]

READ_ONLY = {"list_files", "read_file", "fetch_url", "now", "calc", "find", "grep", "search_docs", "todo_write"}

META = {
    "list_files": {"title": "Список файлов", "danger": "чтение",
                   "about": "Показать, что лежит в папке: имена файлов и подпапок.",
                   "tags": ("папка", "каталог", "что лежит", "список", "директор", "содержимое папки")},
    "read_file": {"title": "Чтение файла", "danger": "чтение",
                  "about": "Открыть текстовый файл и прочитать его содержимое: код, конфиг, лог, документ.",
                  "tags": ("файл", "прочитай", "открой", "содержимое", "код", "лог", "конфиг", "скрипт", "текст документа")},
    "write_file": {"title": "Запись файла", "danger": "запись",
                   "about": "Создать новый или перезаписать существующий текстовый файл.",
                   "tags": ("запиши", "сохрани", "создай файл", "измени файл", "отредактируй", "перепиши")},
    "run_command": {"title": "Команды в системе", "danger": "команды",
                    "about": "Запустить команду cmd.exe и вернуть вывод: git, сборка, установка пакетов, скрипты.",
                    "tags": ("запусти", "команда", "терминал", "консоль", "git ", "собери", "установи", "npm", "pip", "python ")},
    "web_search": {"title": "Поиск в интернете", "danger": "сеть",
                   "about": "Найти свежую информацию в интернете: новости, цены, курсы, документацию.",
                   "tags": ("интернет", "в сети", "новост", "курс", "погода", "актуальн", "статья", "документац", "найди в интернете")},
    "fetch_url": {"title": "Загрузка страницы", "danger": "сеть",
                  "about": "Скачать конкретную страницу по ссылке и вернуть её текст.",
                  "tags": ("ссылк", "url", "страниц", "сайт", "http")},
    "now": {"title": "Дата и время", "danger": "чтение",
            "about": "Текущие дата и время на этом компьютере.",
            "tags": ("время", "дата", "который час", "сегодня", "число")},
    "calc": {"title": "Калькулятор", "danger": "чтение",
             "about": "Точно посчитать математическое выражение: проценты, доли, степени.",
             "tags": ("посчитай", "вычисли", "сколько будет", "процент", "сумм", "умнож", "раздел", "калькулятор")},
    "find": {"title": "Поиск файлов", "danger": "чтение",
             "about": "Найти файлы по маске имени в папке: *.py, *config*.",
             "tags": ("найди файл", "где лежит", "поиск файлов", "маска", "*.py", "найди папку")},
    "grep": {"title": "Поиск по тексту файлов", "danger": "чтение",
             "about": "Найти строки по тексту или регулярному выражению во всех файлах папки.",
             "tags": ("найди в файлах", "поиск по тексту", "упомина", "встреча", "regex", "где используется", "найди в коде")},
    "todo_write": {"title": "План задачи", "danger": "чтение",
                   "about": "Записать шаги задачи и отмечать, что уже сделано.",
                   "tags": ("план", "шаги", "по шагам", "сделай по порядку", "план работ")},
    "verify_project": {"title": "Проверка проекта", "danger": "команды",
                       "about": "Запустить тесты, сборку или проверку синтаксиса и вернуть результат.",
                       "tags": ("проверь", "провери", "тест", "тесты", "сборка", "прогони", "убедись", "сдай")},
    "search_docs": {"title": "База знаний", "danger": "чтение",
                    "about": "Найти ответ в проиндексированных папках и документах, с указанием источника.",
                    "tags": ("база знаний", "в документах", "в знаниях", "по документам", "регламент", "инструкц")},
}

_VEC_CACHE = {"key": "", "vecs": {}}


def catalog() -> list:
    """Список встроенных инструментов для интерфейса."""
    out = []
    for schema in SCHEMAS:
        meta = META.get(schema["name"], {})
        out.append({
            "name": schema["name"],
            "title": meta.get("title") or schema["name"],
            "about": schema.get("description") or "",
            "danger": meta.get("danger") or "чтение",
            "tags": list(meta.get("tags") or ()),
        })
    return out


def _cos(a, b) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _about(item: dict) -> str:
    return item["title"] + ". " + item["about"] + ". " + ", ".join(item["tags"])


def _similarities(task: str, items: list) -> dict:
    """Похожесть задачи на описание инструмента (эмбеддинги, с кэшем описаний)."""
    try:
        from . import embed
        key = "|".join(item["name"] for item in items)
        if _VEC_CACHE["key"] != key:
            docs = embed.vectors([_about(item) for item in items], kind="document")
            if not docs:
                return {}
            _VEC_CACHE.update(key=key, vecs={item["name"]: vec for item, vec in zip(items, docs)})
        query = embed.vectors([task], kind="query")
        if not query:
            return {}
        return {name: _cos(query[0], vec) for name, vec in _VEC_CACHE["vecs"].items()}
    except Exception as exc:
        diag.warn("tools.py:select", exc)
        return {}


def select(task: str, cfg: dict) -> list:
    """Инструменты под задачу: ключевые слова + смысловая близость описаний."""
    allowed = _allowed(cfg)
    text = str(task or "").lower().strip()
    if not text:
        return sorted(allowed)
    items = [item for item in catalog() if item["name"] in allowed]
    hits = set()
    for item in items:
        if any(tag in text for tag in item["tags"]):
            hits.add(item["name"])
    sims = _similarities(text, items)
    best = max(sims.values()) if sims else 0.0
    # слова надёжнее смысла: добавляем не больше двух близких инструментов к найденным
    extra = {name for name, score in sims.items()
             if name not in hits and score >= max(0.76, best - 0.02)}
    if hits:
        picked = hits
    elif best >= 0.75:
        picked = set(sorted(sims, key=lambda name: -sims[name])[:2])
    else:
        return []
    extra = set(sorted(extra, key=lambda name: -sims.get(name, 0.0))[:1])
    core = {"todo_write"}
    if cfg.get("allow_shell"):
        core.add("verify_project")
    picked = set(picked) | (core & allowed)
    ordered = sorted(picked | extra,
                     key=lambda name: (name not in hits, -sims.get(name, 0.0)))
    return ordered[:6]




def _allowed(cfg: dict) -> set:
    """Что разрешено настройками безопасности и не выключено вручную."""
    allowed = set(READ_ONLY)
    if cfg.get("allow_write"):
        allowed.add("write_file")
    if cfg.get("allow_shell"):
        allowed.add("run_command")
        allowed.add("verify_project")
    if cfg.get("allow_web"):
        allowed.add("web_search")
    return allowed - {str(name) for name in (cfg.get("tools_off") or [])}


def schemas(cfg: dict, task: str = None):
    allowed = _allowed(cfg)
    only = cfg.get("_only_tools")
    if only is not None:
        allowed = {name for name in allowed if name in set(only)}
    elif task and cfg.get("tools_auto"):
        picked = set(select(task, cfg))
        allowed = {name for name in allowed if name in picked}
    schemas_out = [{"type": "function", "function": s} for s in SCHEMAS if s["name"] in allowed]
    servers = cfg.get("mcp") or []
    if servers and (not only or "mcp" in set(only)):
        try:
            from . import mcp as mcp_mod
            schemas_out.extend(mcp_mod.tools_for(servers))
        except Exception as exc:
            diag.warn("tools.py:64", exc)
    return schemas_out


_DENY_PARTS = ("access.token", "keys.json", "keys.salt", ".ssh", ".aws", ".gnupg",
               "id_rsa", "id_ed25519", "secrets.v2.json", "login data", "cookies")


def _is_denied(path: pathlib.Path) -> bool:
    """Секреты и профили браузеров не отдаём даже внутри разрешённых папок."""
    text = str(path).lower().replace("/", "\\")
    if any(part in text for part in _DENY_PARTS):
        return True
    try:
        from . import config
        data_dir = str(config.DATA_DIR).lower().replace("/", "\\")
        if text.startswith(data_dir) and not text.startswith(data_dir + "\\logs"):
            return True
    except Exception as exc:
        diag.warn("tools.py:83", exc)
    return False


def _roots(cfg: dict):
    out = [pathlib.Path.home()]
    for item in (cfg.get("projects") or []):
        candidate = pathlib.Path(str(item))
        if candidate.is_dir():
            out.append(candidate)
    models = cfg.get("models_dir")
    if models:
        out.append(pathlib.Path(models))
    return [p.resolve() for p in out if p.exists()]


def _resolve(raw: str, cfg: dict) -> pathlib.Path:
    raw = str(raw or "~").strip()
    home = pathlib.Path.home()
    if raw in ("", "~", "/", "\\"):
        return home
    path = pathlib.Path(raw).expanduser()
    if raw.startswith("/") and not raw.startswith("//"):
        parts = [piece for piece in raw.split("/")
                 if piece and piece.lower() not in ("home", "user", "users", "root", "mnt", "data", "tmp")]
        path = home.joinpath(*parts) if parts else home
    elif not path.is_absolute():
        path = home / path
    path = path.resolve()
    text = str(path).lower()
    if _is_denied(path):
        raise ValueError("этот файл закрыт: токены, ключи и профили браузеров не читаются")
    roots = _roots(cfg)
    if not any(text.startswith(str(root).lower() + "\\") or text == str(root).lower() for root in roots):
        allowed = ", ".join(str(root) for root in roots[:4])
        raise ValueError("путь вне разрешённых папок. Разрешено: %s. Пиши полный путь Windows, например %s" % (allowed, home))
    return path

def _list_files(args, cfg):
    path = _resolve(args.get("path", "~"), cfg)
    if path.is_file():
        return "Это файл размером %.2f МБ" % (path.stat().st_size / 1048576)
    rows = []
    for item in sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))[:200]:
        try:
            size = item.stat().st_size
        except OSError:
            size = 0
        rows.append(("[папка] " if item.is_dir() else "%9.1f КБ " % (size / 1024)) + item.name)
    return "Содержимое %s:\n%s" % (path, "\n".join(rows) or "(пусто)")


def _read_file(args, cfg):
    path = _resolve(args.get("path", ""), cfg)
    if not path.is_file():
        return "Файла нет: %s" % path
    if path.stat().st_size > 8 * 1024 * 1024:
        return "Файл слишком большой (%.1f МБ)" % (path.stat().st_size / 1048576)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return "Не удалось прочитать: %s" % exc
    return text[:MAX_OUT]


def _write_file(args, cfg):
    path = _resolve(args.get("path", ""), cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(args.get("content", ""), encoding="utf-8")
    return "Записано: %s (%d символов)" % (path, len(args.get("content", "")))


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "cp866", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _run_command(args, cfg):
    cmd = args.get("command", "")
    if not cmd.strip():
        return "Пустая команда"
    try:
        done = subprocess.run(["cmd", "/c", cmd], capture_output=True, timeout=180,
                              cwd=str(pathlib.Path.home()), creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        return "Команда не завершилась за 180 секунд"
    except OSError as exc:
        return "Не удалось выполнить: %s" % exc
    text = (_decode(done.stdout) + _decode(done.stderr)).strip()
    return ("код %d\n" % done.returncode) + (text[:MAX_OUT] or "(пустой вывод)")


ARTICLE = re.compile(r'result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>', re.S)
SNIPPET = re.compile(r'result__snippet"[^>]*>(?P<text>.*?)</a>', re.S)
TAGS = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    return _html.unescape(TAGS.sub(" ", text)).replace("\xa0", " ").strip()


def _web_search(args, cfg):
    query = args.get("query", "")
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            page = resp.read().decode("utf-8", "replace")
    except Exception as exc:
        return "Поиск недоступен: %s" % exc
    snippets = list(SNIPPET.finditer(page))
    lines = []
    for index, match in enumerate(ARTICLE.finditer(page)):
        if index >= 5:
            break
        href = match.group("href")
        if href.startswith("//duckduckgo.com/l/"):
            query_part = urllib.parse.parse_qs(urllib.parse.urlparse("https:" + href).query).get("uddg")
            if query_part:
                href = query_part[0]
        title = _clean(match.group("title"))
        extra = ""
        if index < len(snippets):
            extra = _clean(snippets[index].group("text"))[:220]
        lines.append("%d. %s\n%s\n%s" % (index + 1, title, href, extra))
    return "\n\n".join(lines) or "Ничего не найдено"


def _host_blocked(host: str) -> bool:
    host = (host or "").strip().lower()
    if not host or host in ("localhost", "localhost.localdomain"):
        return True
    if host.endswith(".local") or host.endswith(".internal"):
        return True
    try:
        import ipaddress
        import socket
        for info in socket.getaddrinfo(host, None):
            addr = ipaddress.ip_address(info[4][0])
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                return True
    except Exception:
        return False
    return False


def _fetch_url(args, cfg):
    raw = str(args.get("url", "") or "").strip()
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        return "Можно скачивать только http и https"
    if _host_blocked(parsed.hostname or ""):
        return "Локальная сеть и этот компьютер недоступны для скачивания"
    req = urllib.request.Request(raw, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read(3 * 1024 * 1024).decode("utf-8", "replace")
    except Exception as exc:
        return "Не удалось скачать: %s" % exc
    body = re.sub(r"(?is)<(script|style|nav|footer)[^>]*>.*?</\1>", " ", body)
    text = _html.unescape(TAGS.sub(" ", body))
    return re.sub(r"\s+", " ", text)[:MAX_OUT]


OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
       ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
       ast.FloorDiv: operator.floordiv}


def _calc(args, cfg):
    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in OPS:
            return OPS[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in (ast.USub, ast.UAdd):
            value = walk(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        raise ValueError("недопустимое выражение")
    try:
        return "%s = %s" % (args.get("expression", ""), walk(ast.parse(args.get("expression", ""), mode="eval")))
    except Exception as exc:
        return "Не смог посчитать: %s" % exc


def _now(args, cfg):
    return _dt.datetime.now().strftime("%d.%m.%Y %H:%M (%A)")


def _find(args, cfg):
    base = _resolve(args.get("path") or "~", cfg)
    mask = args.get("pattern") or "*"
    hits = []
    for item in base.rglob(mask):
        hits.append(str(item))
        if len(hits) >= 200:
            break
    return "Найдено %d:\n%s" % (len(hits), "\n".join(hits[:80]) or "ничего")


def _grep(args, cfg):
    import re as _re
    base = _resolve(args.get("path") or "~", cfg)
    mask = args.get("glob") or "*"
    try:
        pattern = _re.compile(args.get("pattern") or "", _re.IGNORECASE)
    except _re.error as exc:
        return "Некорректное выражение: %s" % exc
    lines_out = []
    for item in base.rglob(mask):
        try:
            if not item.is_file() or item.stat().st_size > 2 * 1024 * 1024:
                continue
            text = item.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                lines_out.append("%s:%d: %s" % (item, number, line.strip()[:160]))
                if len(lines_out) >= 60:
                    return "\n".join(lines_out)
    return "\n".join(lines_out) or "Ничего не найдено"


def _search_docs(args: dict, cfg: dict) -> str:
    from . import rag
    return rag.as_tool_text(str(args.get("query") or ""), int(args.get("limit") or 5))


HANDLERS = {
    "list_files": _list_files, "read_file": _read_file, "write_file": _write_file,
    "run_command": _run_command, "web_search": _web_search, "fetch_url": _fetch_url,
    "now": _now, "calc": _calc, "find": _find, "grep": _grep,
    "search_docs": _search_docs,
}


def todo_write(args: dict, cfg: dict) -> str:
    """План задачи: агент сам ведёт список шагов."""
    from . import plan
    steps = args.get("steps") or []
    done = {int(item) for item in (args.get("done") or []) if str(item).isdigit() or isinstance(item, int)}
    current = int(args.get("current") or 0)
    prepared = []
    for number, text in enumerate(steps, 1):
        status = "done" if number in done else ("doing" if number == current else "todo")
        prepared.append({"text": str(text), "status": status})
    data = plan.set_plan(args.get("title") or "", prepared)
    return "План сохранён: %d шагов, выполнено %d." % (data["total"], data["done"])


def _verify_command(root: pathlib.Path) -> list:
    """Подобрать проверку под проект: тесты, сборка или проверка синтаксиса."""
    package = root / "package.json"
    if package.exists():
        try:
            data = json.loads(package.read_text(encoding="utf-8"))
            if (data.get("scripts") or {}).get("test"):
                return ["npm", "test", "--silent"]
        except (OSError, ValueError):
            pass
    makefile = root / "Makefile"
    if makefile.exists():
        try:
            if "test:" in makefile.read_text(encoding="utf-8", errors="replace"):
                return ["make", "test"]
        except OSError:
            pass
    marks = [root / "pytest.ini", root / "tox.ini", root / "pyproject.toml", root / "setup.cfg"]
    if any(path.exists() for path in marks) or list(root.glob("tests/test_*.py")):
        return ["python", "-m", "pytest", "-q"]
    if list(root.glob("tests/check_*.py")):
        return ["python", "-m", "compileall", "-q", "app"]
    return ["python", "-m", "compileall", "-q", "."]


def verify_project(args: dict, cfg: dict) -> str:
    """Запустить проверку проекта и вернуть короткий отчёт."""
    root = pathlib.Path(str(args.get("path") or cfg.get("_project") or pathlib.Path.cwd())).resolve()
    if not root.is_dir():
        return "Папка проекта не найдена: %s" % root
    cmd = _verify_command(root)
    try:
        proc = subprocess.run(cmd, cwd=str(root), capture_output=True, timeout=180,
                              creationflags=NO_WINDOW)
    except FileNotFoundError:
        return "Не нашёл команду %s — проверьте, что она установлена." % cmd[0]
    except subprocess.TimeoutExpired:
        return "Проверка не уложилась в 180 секунд: %s" % " ".join(cmd)
    except OSError as exc:
        return "Не удалось запустить проверку: %s" % exc
    output = ((proc.stdout or b"") + (proc.stderr or b"")).decode("utf-8", "replace").strip()
    if len(output) > 3000:
        output = output[:1500] + chr(10) + "…" + chr(10) + output[-1500:]
    head = "Проверка: %s — код %d" % (" ".join(cmd), proc.returncode)
    return head + (chr(10) + output if output else chr(10) + "(вывод пустой)")


HANDLERS.update({"todo_write": todo_write, "verify_project": verify_project})


def call(name: str, args: dict, cfg: dict) -> str:
    if name.startswith("mcp__"):
        try:
            from . import mcp as mcp_mod
            return mcp_mod.call(name, args or {}, cfg.get("mcp") or [])
        except Exception as exc:
            return "Ошибка MCP-инструмента: %s" % exc
    handler = HANDLERS.get(name)
    if handler is None:
        return "Неизвестный инструмент: %s" % name
    allowed = set()
    for item in schemas(cfg):
        fn = item.get("function") or {}
        if fn.get("name"):
            allowed.add(fn["name"])
    if name not in allowed:
        return "Инструмент %s выключен в настройках" % name
    try:
        return handler(args or {}, cfg)
    except Exception as exc:
        return "Ошибка инструмента %s: %s" % (name, exc)

DANGEROUS = {"write_file", "run_command"}
PENDING = {"event": None, "decision": None, "tool": "", "args": {}, "id": ""}


def approval_gate(name: str, args: dict, cfg: dict, emit) -> bool:
    """Гейт опасных действий: спрашиваем пользователя, если включено «спрашивать»."""
    import threading
    import uuid

    if name not in DANGEROUS:
        return True
    if (cfg.get("approval_mode") or "ask") == "auto":
        return True
    event = threading.Event()
    request_id = uuid.uuid4().hex[:10]
    PENDING.update(event=event, decision=None, tool=name, args=args or {}, id=request_id)
    try:
        emit({"type": "approval", "id": request_id, "tool": name, "args": args or {}})
    except Exception as exc:
        diag.warn("tools.py:367", exc)
    event.wait(timeout=180)
    allowed = bool(PENDING.get("decision"))
    PENDING.update(event=None, decision=None, id="")
    return allowed


def resolve_approval(request_id: str, allow: bool) -> bool:
    if PENDING.get("id") != request_id or PENDING.get("event") is None:
        return False
    PENDING["decision"] = bool(allow)
    PENDING["event"].set()
    return True
