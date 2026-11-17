"""Инструменты агента: файлы, команды, поиск в сети, время, калькулятор."""
from __future__ import annotations

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
    {"name": "search_docs", "description": "Найти ответ в базе знаний (проиндексированные папки и документы) с указанием источника.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Что искать"},
                                                        "limit": {"type": "integer", "description": "Сколько фрагментов вернуть"}},
                    "required": ["query"]}},
]

READ_ONLY = {"list_files", "read_file", "fetch_url", "now", "calc", "find", "grep", "search_docs"}


def schemas(cfg: dict):
    allowed = set(READ_ONLY)
    if cfg.get("allow_write"):
        allowed.add("write_file")
    if cfg.get("allow_shell"):
        allowed.add("run_command")
    if cfg.get("allow_web"):
        allowed.add("web_search")
    only = cfg.get("_only_tools")
    if only:
        allowed = {name for name in allowed if name in set(only)}
    schemas_out = [{"type": "function", "function": s} for s in SCHEMAS if s["name"] in allowed]
    servers = cfg.get("mcp") or []
    if servers and (not only or "mcp" in set(only)):
        try:
            from . import mcp as mcp_mod
            schemas_out.extend(mcp_mod.tools_for(servers))
        except Exception:
            pass
    return schemas_out


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
    roots = _roots(cfg)
    if not any(text.startswith(str(root).lower()) for root in roots):
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
        done = subprocess.run(cmd, shell=True, capture_output=True, timeout=180,
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
    snippets = [SNIPPET.finditer(page)]
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


def _fetch_url(args, cfg):
    req = urllib.request.Request(args.get("url", ""), headers=UA)
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
    except Exception:
        pass
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
