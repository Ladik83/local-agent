"""Веб-сервер приложения: состояние, модели, движок, чат."""
from __future__ import annotations

from . import diag

import ipaddress
import json
import mimetypes
import os
import pathlib
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import agent, attach, autostart, catalog, chats as chats_mod, hf, schedule, chats, config, drives, embed, engine, hardware, keystore, mcp, orchestrator, panels, plan, providers, rag, roles, scan, skills, stats, subagents, tools, updater, vision, voice

UI_DIR = pathlib.Path(__file__).parent / "ui"
TOKEN_FILE = config.DATA_DIR / "access.token"
def _load_token() -> str:
    try:
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if len(token) >= 24:
            return token
    except OSError as exc:
        diag.warn("main.py:24", exc)
    import secrets as _secrets
    token = _secrets.token_hex(24)
    try:
        TOKEN_FILE.write_text(token, encoding="utf-8")
    except OSError as exc:
        diag.warn("main.py:30", exc)
    return token


ACCESS_TOKEN = _load_token()

HOST = "127.0.0.1"
MCP_CATALOG = [
    {"id": "filesystem", "title": "Файлы", "command": "npx", "needs": "node", "category": "Файлы",
     "about": "Чтение, запись и поиск файлов в выбранной папке. Даёт агенту доступ к документам и коду.",
     "args": ["-y", "@modelcontextprotocol/server-filesystem", "%HOME%"]},
    {"id": "memory", "title": "Память (граф знаний)", "command": "npx", "needs": "node", "category": "Память",
     "about": "Хранит сущности и связи между ними: люди, проекты, решения. Агент может записывать и вспоминать.",
     "args": ["-y", "@modelcontextprotocol/server-memory"]},
    {"id": "sequential-thinking", "title": "Пошаговые рассуждения", "command": "npx", "needs": "node", "category": "Мышление",
     "about": "Помогает разбивать сложную задачу на шаги и проверять себя по ходу решения.",
     "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"]},
    {"id": "fetch", "title": "Загрузка веб-страниц", "command": "uvx", "needs": "python", "category": "Сеть",
     "about": "Скачивает страницу и переводит её в читаемый текст: статьи, документация, справки.",
     "args": ["mcp-server-fetch"]},
    {"id": "git", "title": "Git", "command": "uvx", "needs": "python", "category": "Разработка",
     "about": "История, диффы, ветки и коммиты в репозитории без ручных команд в терминале.",
     "args": ["mcp-server-git", "--repository", "%CWD%"]},
    {"id": "sqlite", "title": "SQLite", "command": "uvx", "needs": "python", "category": "Данные",
     "about": "SQL-запросы к локальной базе: таблицы, выборки, отчёты.",
     "args": ["mcp-server-sqlite", "--db-path", "%DATA%\\mcp.sqlite"]},
    {"id": "time", "title": "Время и часовые пояса", "command": "uvx", "needs": "python", "category": "Утилиты",
     "about": "Текущее время в любом городе и перевод между часовыми поясами.",
     "args": ["mcp-server-time"]},
    {"id": "everything", "title": "Проверочный сервер", "command": "npx", "needs": "node", "category": "Утилиты",
     "about": "Маленький демонстрационный сервер: удобно проверить, что MCP вообще работает.",
     "args": ["-y", "@modelcontextprotocol/server-everything"]},
    {"id": "github", "title": "GitHub", "command": "npx", "needs": "node", "key": "GITHUB_TOKEN", "category": "Разработка",
     "about": "Issues, pull request'ы, файлы и поиск по репозиториям GitHub. Нужен токен.",
     "args": ["-y", "@modelcontextprotocol/server-github"]},
    {"id": "brave-search", "title": "Веб-поиск Brave", "command": "npx", "needs": "node", "key": "BRAVE_API_KEY", "category": "Сеть",
     "about": "Поиск в интернете через Brave Search — свежие новости и ссылки. Нужен ключ API.",
     "args": ["-y", "@modelcontextprotocol/server-brave-search"]},
    {"id": "puppeteer", "title": "Браузер (Puppeteer)", "command": "npx", "needs": "node", "category": "Сеть",
     "about": "Открывает сайты в настоящем браузере, кликает и снимает скриншоты.",
     "args": ["-y", "@modelcontextprotocol/server-puppeteer"]},
    {"id": "postgres", "title": "PostgreSQL", "command": "npx", "needs": "node", "key": "DATABASE_URL", "category": "Данные",
     "about": "Запросы к PostgreSQL: схемы, выборки, отчёты. Нужна строка подключения.",
     "args": ["-y", "@modelcontextprotocol/server-postgres", "postgresql://user:pass@localhost/db"]},
    {"id": "docker", "title": "Docker", "command": "uvx", "needs": "python", "category": "Разработка",
     "about": "Список контейнеров, логи, запуск и остановка — без ручных команд.",
     "args": ["mcp-server-docker"]},
]
def _agents_info() -> dict:
    """Как подключить внешние оболочки к нашему серверу моделей."""
    cfg = config.load()
    provider = providers.resolve(cfg)
    base = str(provider.get("base_url") or "").rstrip("/")
    model = str(provider.get("model") or "")
    running = False
    if provider.get("kind") == "local":
        base = (engine.base_url() or "").rstrip("/")
        model = pathlib.Path(str(engine.SERVER.get("model") or "")).name
        running = bool(base)
    else:
        running = bool(base)
    url = base if base.endswith("/v1") else (base + "/v1" if base else "")
    shown = url or "http://127.0.0.1:8090/v1"
    name = model or "имя файла модели"
    clients = [
        {"name": "Cline (VS Code)", "how": "OpenAI Compatible: Base URL " + shown + ", API Key — любая строка, Model — " + name},
        {"name": "Continue", "how": "provider: openai, apiBase: " + shown + ", model: " + name},
        {"name": "opencode", "how": "OPENAI_BASE_URL=" + shown + ", OPENAI_API_KEY — любая строка"},
        {"name": "Hermes Agent", "how": "провайдер openai, base_url " + shown + ", model " + name},
        {"name": "Claude Code / Codex CLI", "how": "ждут Anthropic/Responses API — нужен прокси-переводчик, напрямую не подключатся"},
    ]
    return {"ok": True, "base_url": url, "model": model, "running": running,
            "kind": provider.get("kind"), "clients": clients}


def _expand_args(args) -> list:
    """Подставить домашнюю папку и папку данных в аргументы MCP-сервера."""
    home = str(pathlib.Path.home())
    data = str(config.DATA_DIR)
    here = str(pathlib.Path.cwd())
    out = []
    for item in args or []:
        out.append(str(item).replace("%HOME%", home).replace("%DATA%", data).replace("%CWD%", here))
    return out


def _toolchains() -> dict:
    import shutil
    return {"node": bool(shutil.which("npx")), "python": bool(shutil.which("uvx"))}


def _mcp_state() -> list:
    """Серверы из настроек + их состояние: сколько инструментов, ошибка."""
    cfg = config.load()
    servers = cfg.get("mcp") or []
    result = []
    for server in servers:
        info = {"id": server.get("id"), "command": server.get("command"),
                "args": _expand_args(server.get("args")), "enabled": server.get("enabled", True),
                "tools": [], "error": ""}
        try:
            ready = mcp.session(server["id"], server.get("command", ""), _expand_args(server.get("args")))
            info["tools"] = [tool["raw"] for tool in ready.tools]
            info["error"] = ready.error
        except Exception as exc:
            info["error"] = str(exc)
        result.append(info)
    return result

DL = {"active": False, "name": "", "pct": 0.0, "text": "", "error": None, "done": None}
DL_LOCK = threading.Lock()
_SCAN_CACHE = {"ts": 0.0, "value": []}
DISC = {"active": False, "found": [], "scanned": 0, "text": "", "error": None}


def _discover() -> None:
    """Обход дисков в поисках папок с моделями."""
    DISC.update(active=True, found=[], scanned=0, text="Ищу модели на дисках", error=None)

    def progress(count: int, path: str) -> None:
        DISC.update(scanned=count, text=path)

    try:
        dirs = drives.find_model_dirs(progress)
        useful = [item for item in dirs if scan.scan([item], limit=1)]
        cfg = config.load()
        primary = (cfg.get("models_dir") or "").lower().rstrip("\\")
        extra = []
        for item in useful:
            low = item.lower().rstrip("\\")
            if low == primary or low.startswith(primary + "\\"):
                continue
            extra.append(item)
        config.save({"extra_dirs": extra})
        DISC.update(active=False, found=useful, text="Найдено папок с моделями: %d" % len(useful))
    except Exception as exc:
        DISC.update(active=False, error=str(exc), text="Ошибка поиска")


def state() -> dict:
    cfg = config.load()
    hw = hardware.snapshot(cfg["models_dir"])
    dirs = [cfg["models_dir"]] + [str(path) for path in (cfg.get("extra_dirs") or [])]
    now = time.time()
    if now - _SCAN_CACHE["ts"] > 5 or not _SCAN_CACHE["value"]:
        _SCAN_CACHE["value"] = scan.rank(scan.scan(dirs), hw["budget_gb"])
        _SCAN_CACHE["ts"] = now
    local = _SCAN_CACHE["value"]
    pick = catalog.recommend(hw["budget_gb"], local)
    return {
        "hardware": hw,
        "settings": cfg,
        "provider": providers.summary(cfg),
        "team": {"enabled": bool(cfg.get("use_team")), "roles": roles.merge(cfg)},
        "mcp": {"servers": cfg.get("mcp") or []},
        "chats": chats.list_all(),
        "engine": engine.status(),
        "engine_check": dict(engine.STARTUP),
        "plan": plan.snapshot(),
        "skills": skills.overview(),
        "models": local,
        "pick": pick,
        "catalog": [dict(c, url=catalog.url(c)) for c in catalog.CATALOG],
        "hf": {"token_set": bool(keystore.get("hf_token"))},
        "embeddings": embed.status(),
        "vision": vision.status(),
        "download": dict(DL),
        "server": {"running": bool(engine.base_url()), "port": engine.SERVER.get("port"),
                   "model": engine.SERVER.get("model")},
    }


def _fetch_model(entry: dict) -> None:
    """Скачать модель из каталога в папку моделей."""
    folder = pathlib.Path(config.load()["models_dir"]) / "LocalAgent"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / entry["file"]
    part = dest.with_name(dest.name + ".part")
    DL.update(active=True, name=entry["title"], pct=0.0, text="Скачиваем модель", error=None, done=None)
    try:
        req = urllib.request.Request(catalog.url(entry), headers={"User-Agent": "LocalAgent/0.1"})
        with urllib.request.urlopen(req, timeout=180) as resp, open(part, "wb") as fh:
            total = int(resp.headers.get("Content-Length") or 0) or int(entry["size_gb"] * 1073741824)
            done = 0
            while True:
                block = resp.read(1 << 20)
                if not block:
                    break
                fh.write(block)
                done += len(block)
                DL["pct"] = round(done * 100.0 / total, 1) if total else 0.0
                DL["text"] = "%.2f из %.2f ГБ" % (done / 1073741824, total / 1073741824)
        part.replace(dest)
        DL.update(active=False, pct=100.0, text="Готово", done=str(dest))
        config.save({"last_model": str(dest)})
    except Exception as exc:
        DL.update(active=False, error=str(exc), text="Ошибка загрузки")


def start_download(entry_id: str) -> dict:
    entry = None
    for item in catalog.CATALOG:
        if item["id"] == entry_id:
            entry = item
            break
    if entry is None:
        return {"ok": False, "error": "модель не найдена в каталоге"}
    if DL.get("active"):
        return {"ok": False, "error": "загрузка уже идёт"}
    threading.Thread(target=_fetch_model, args=(entry,), daemon=True).start()
    return {"ok": True, "file": entry["file"]}


def _fetch_hf(repo: str, file: str) -> None:
    """Загрузка выбранного файла с Hugging Face в папку моделей."""
    name = pathlib.Path(file).name
    DL.update(active=True, name=name, pct=0.0, text="Скачиваем " + name, error=None, done=None)

    def progress(done: int, total: int) -> None:
        if total:
            DL["pct"] = round(done * 100.0 / total, 1)
            DL["text"] = "%s: %.2f из %.2f ГБ" % (name, done / 1024 ** 3, total / 1024 ** 3)
        else:
            DL["pct"] = 0.0
            DL["text"] = "%s: %.0f МБ" % (name, done / 1048576.0)

    try:
        result = hf.download(repo, file, progress)
        if result.get("ok"):
            DL.update(active=False, pct=100.0, text="Модель скачана", done=result.get("path"))
            saved = str(result.get("path") or "")
            if saved and "embed" not in name.lower():
                config.save({"last_model": saved})
        else:
            DL.update(active=False, error=result.get("error") or "ошибка", text="Ошибка загрузки")
    except Exception as exc:
        DL.update(active=False, error=str(exc), text="Ошибка загрузки")


def start_hf_download(repo: str, file: str) -> dict:
    if not repo or not file:
        return {"ok": False, "error": "не указана модель"}
    if not hf.REPO_RE.match(repo):
        return {"ok": False, "error": "некорректное имя модели"}
    if not str(file).lower().endswith(".gguf"):
        return {"ok": False, "error": "можно скачивать только .gguf"}
    if DL.get("active"):
        return {"ok": False, "error": "загрузка уже идёт"}
    threading.Thread(target=_fetch_hf, args=(repo, file), daemon=True).start()
    return {"ok": True, "repo": repo, "file": file}


def _embeddings_check() -> dict:
    path = embed.find_model()
    if not path:
        return {"ok": False, "error": "Модель эмбеддингов не найдена — скачайте её на вкладке «Модели»"}
    started = engine.start_embed(path)
    if not started.get("ok"):
        return {"ok": False, "error": started.get("error") or "модель эмбеддингов не запустилась"}
    probe = embed.vectors(["probe"], kind="query")
    if not probe:
        return {"ok": False, "error": "модель эмбеддингов не запустилась"}
    return {"ok": True, "dim": len(probe[0]), "model": path}


def _attach_images(convo: list, images: list) -> list:
    """Картинки кладём в последнее сообщение пользователя (поле images)."""
    out = [dict(item) for item in convo]
    for item in reversed(out):
        if item.get("role") == "user":
            item["images"] = list(images)
            break
    return out


def _strip_images(convo: list) -> list:
    return [{key: value for key, value in item.items() if key != "images"} for item in convo]


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalAgent/0.1"

    def log_message(self, fmt, *args):
        return

    def _authorized(self) -> bool:
        header = self.headers.get("X-Agent-Token") or ""
        if header and header == ACCESS_TOKEN:
            return True
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return (query.get("token") or [""])[0] == ACCESS_TOKEN

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0].strip().lower()
        if host in ("", "127.0.0.1", "localhost", "[::1]"):
            return True
        if not config.load().get("lan_access"):
            return False
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return False
        return address.is_private or address.is_loopback

    def _need_token(self) -> bool:
        route = urllib.parse.urlparse(self.path).path
        if not route.startswith("/api/"):
            return False
        if self._authorized():
            return False
        self._send({"error": "нет доступа: нужен токен приложения"}, 401)
        return True

    def _send(self, payload: dict, code: int = 200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self, name: str):
        if name.endswith(".webmanifest"):
            body = (UI_DIR / "manifest.webmanifest").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/manifest+json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        safe = name.replace("\\", "/").split("/")[-1]
        path = UI_DIR / safe
        if not path.is_file():
            self.send_error(404, "not found")
            return
        peer = str(self.client_address[0])
        if peer.startswith("::ffff:"):
            peer = peer[7:]
        local_peer = peer.startswith("127.") or peer in ("::1", "0:0:0:0:0:0:0:1")
        if path.name == "index.html" and not local_peer and not self._authorized():
            self._send({"error": "нужен токен: откройте ссылку из приложения"}, 401)
            return
        data = path.read_bytes()
        if path.name == "index.html":
            meta = "<meta name=\"agent-token\" content=\"" + ACCESS_TOKEN + "\"></head>"
            data = data.replace(b"</head>", meta.encode("utf-8"), 1)
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        route = urllib.parse.urlparse(self.path).path
        if not self._host_ok():
            self.send_error(403, "bad host")
            return
        if self._need_token():
            return
        try:
            if route in ("/", "/index.html"):
                self._static("index.html")
            elif route.startswith("/ui/"):
                self._static(route[4:])
            elif route == "/api/state":
                self._send(state())
            elif route == "/api/mcp":
                cfg = config.load()
                installed = {str(item.get("id")) for item in (cfg.get("mcp") or [])}
                catalog = []
                for entry in MCP_CATALOG:
                    item = dict(entry)
                    item["installed"] = entry["id"] in installed
                    catalog.append(item)
                self._send({"servers": _mcp_state(), "catalog": catalog,
                            "toolchains": _toolchains(), "auto": bool(cfg.get("tools_auto"))})
            elif route == "/api/tools":
                cfg = config.load()
                catalog = []
                for entry in MCP_CATALOG:
                    item = dict(entry)
                    item["installed"] = any(str(s.get("id")) == entry["id"] for s in (cfg.get("mcp") or []))
                    catalog.append(item)
                self._send({"ok": True, "builtin": tools.catalog(), "off": cfg.get("tools_off") or [],
                            "auto": bool(cfg.get("tools_auto")),
                            "allowed": sorted(tools._allowed(cfg)),
                            "mcp_catalog": catalog, "servers": _mcp_state(),
                            "toolchains": _toolchains()})
            elif route == "/api/voice":
                self._send(voice.status())
            elif route == "/api/hf/search":
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                asked = (query.get("q") or [""])[0].strip()
                if not asked:
                    self._send({"ok": True, "q": "", "items": []})
                else:
                    try:
                        self._send({"ok": True, "q": asked, "items": hf.search(asked, 24)})
                    except Exception as exc:
                        diag.warn("hf/search", exc)
                        self._send({"ok": False, "error": str(exc), "items": []})
            elif route == "/api/hf/files":
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                repo = (query.get("repo") or [""])[0]
                if not hf.REPO_RE.match(repo):
                    probe = start_hf_download(repo, "x.gguf")
                    self._send({"ok": False, "error": probe.get("error") or "bad name", "files": []})
                else:
                    try:
                        self._send({"ok": True, "repo": repo, "files": hf.files(repo)})
                    except Exception as exc:
                        diag.warn("hf/files", exc)
                        self._send({"ok": False, "error": str(exc), "files": []})
            elif route == "/api/stats":
                snapshot = stats.snapshot()
                snapshot["vision"] = vision.status()
                self._send(snapshot)
            elif route == "/api/chats":
                self._send({"chats": chats.list_all()})
            elif route == "/api/chats/get":
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                chat_id = str((query.get("id") or [""])[0])
                payload = chats.load(chat_id)
                try:
                    payload["plan"] = plan.snapshot(chat_id)
                except Exception as exc:
                    diag.warn("main.py:chats_get_plan", exc)
                self._send(payload)
            elif route == "/api/job":
                self._send({"engine": dict(engine.JOB), "download": dict(DL), "discover": dict(DISC)})
            elif route == "/api/skills":
                self._send({"ok": True, **skills.overview()})
            elif route == "/api/skills/one":
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                self._send(skills.one((query.get("name") or [""])[0]))
            elif route == "/api/agents/info":
                self._send(_agents_info())
            elif route == "/api/plan":
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                chat_id = (query.get("chat") or [""])[0]
                self._send({"ok": True, "plan": plan.snapshot(chat_id)})
            elif route == "/api/subagents/roles":
                items = [{"id": item.get("id"), "title": item.get("title")}
                         for item in roles.merge(config.load())]
                self._send({"ok": True, "items": items})
            elif route == "/api/vision/models":
                cfg = config.load()
                self._send({"ok": True, "items": vision.candidates(cfg), "pick": vision.pick(cfg),
                            "status": vision.status()})
            elif route == "/api/panels":
                self._send(panels.snapshot())
            elif route == "/api/telegram":
                tg_token = keystore.get("telegram_bot")
                info = {"token_set": bool(tg_token), "chat": str(config.load().get("telegram_chat") or "")}
                if tg_token:
                    try:
                        import json as _json
                        import urllib.request as _url
                        with _url.urlopen("https://api.telegram.org/bot" + tg_token + "/getMe", timeout=10) as _resp:
                            info["bot"] = (_json.loads(_resp.read().decode("utf-8")).get("result") or {}).get("username") or ""
                    except Exception as exc:
                        diag.warn("telegram/getMe", exc)
                self._send(info)
            elif route == "/api/lan":
                cfg_now = config.load()
                port_now = self.server.server_port
                links = []
                for ip in lan_urls():
                    links.append("http://" + ip + ":" + str(port_now) + "/?token=" + ACCESS_TOKEN)
                self._send({"enabled": bool(cfg_now.get("lan_access")), "port": port_now, "urls": links})
            elif route == "/api/schedule":
                self._send({"items": schedule.list_all()})
            elif route == "/api/jobs":
                self._send({"jobs": JOBS})
            elif route == "/api/version":
                self._send({"version": updater.current_version(),
                            "port": self.server.server_port,
                            "data_dir": str(config.DATA_DIR),
                            "pending": updater.pending_info()})
            elif route == "/api/autostart":
                self._send(autostart.status())
            elif route == "/api/rag":
                payload = dict(rag.stats())
                payload["docs"] = rag.list_docs()
                payload["count"] = len(payload["docs"])
                self._send(payload)
            elif route == "/api/rag/search":
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                needle = (query.get("q") or [""])[0]
                limit = int((query.get("limit") or ["6"])[0])
                self._send({"items": rag.search(needle, limit), "query": needle})
            else:
                self.send_error(404, "not found")
        except Exception as exc:
            import traceback
            try:
                with open(str(config.LOGS_DIR / "errors.log"), "a", encoding="utf-8") as fh:
                    fh.write(traceback.format_exc() + chr(10) + "---" + chr(10))
            except OSError as exc:
                diag.warn("main.py:259", exc)
            self._send({"error": "Внутренняя ошибка: %s" % exc}, 500)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return {}

    def _stream_chat(self, payload: dict):
        cfg = dict(config.load())
        chat_id = str(payload.get("chat") or payload.get("chat_id") or "").strip()
        if chat_id:
            cfg["_chat_id"] = chat_id
        convo = payload.get("messages") or []
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        def emit(event: dict):
            line = "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
            self.wfile.write(line.encode("utf-8"))
            self.wfile.flush()

        agent.STOP["flag"] = False
        try:
            target = providers.resolve(cfg)
            images = [str(item) for item in (payload.get("images") or []) if str(item) and pathlib.Path(str(item)).is_file()]
            if images:
                convo = _attach_images(convo, images)
                main_sees = bool(engine.SERVER.get("projector")) or (
                    target.get("kind") == "local" and bool(engine.find_projector(str(cfg.get("last_model") or ""))))
                if not main_sees:
                    if not cfg.get("vision_enabled", True):
                        emit({"type": "note", "text": "Зрение выключено в настройках — читаю только текст"})
                        convo = _strip_images(convo)
                    else:
                        emit({"type": "note", "text": "Загружаю модель зрения…"})
                        started = vision.start(str(cfg.get("vision_model") or ""))
                        if started.get("ok"):
                            emit({"type": "note", "text": "Картинки разбирает " + pathlib.Path(str(started.get("model") or "")).name})
                            agent.chat_plain(convo, cfg, emit, {"kind": "local", "base_url": vision.base_url(),
                                                               "model": "", "api_key": ""})
                            return
                        emit({"type": "note", "text": "Модель зрения не поднялась: " + str(started.get("error") or "")})
                        convo = _strip_images(convo)
            if target["kind"] == "local" and not target.get("base_url"):
                model = cfg.get("last_model")
                started = engine.start(model) if model else {"ok": False, "error": "модель не выбрана"}
                if not started.get("ok"):
                    emit({"type": "error", "text": started.get("error", "не удалось запустить модель")})
                    return
                target = providers.resolve(cfg)
            if not target.get("base_url"):
                emit({"type": "error", "text": "не выбран провайдер модели"})
                return
            task = ""
            for item in reversed(convo):
                if item.get("role") == "user":
                    task = str(item.get("content") or "")
                    break
            if payload.get("mode") == "team" or cfg.get("use_team"):
                orchestrator.run(task, cfg, emit, target)
            agent.chat(convo, cfg, emit, target)
        except RuntimeError as exc:
            if agent.STOP["flag"] or "остановлено" in str(exc):
                emit({"type": "done", "messages": convo, "cancelled": True})
                return
            raise
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            try:
                import traceback
                with open(str(config.LOGS_DIR / "errors.log"), "a", encoding="utf-8") as fh:
                    fh.write(traceback.format_exc() + chr(10) + "---" + chr(10))
                emit({"type": "error", "text": str(exc)})
            except OSError:
                return

    def do_POST(self):
        route = urllib.parse.urlparse(self.path).path
        if not self._host_ok():
            self.send_error(403, "bad host")
            return
        if self._need_token():
            return
        try:
            if route == "/api/chat":
                self._stream_chat(self._body())
                return
            data = self._body()
            if route == "/api/settings":
                patch = {k: v for k, v in data.items() if k in config.DEFAULTS}
                config.save(patch)
                self._send({"ok": True, "settings": config.load()})
            elif route == "/api/model/load":
                wanted = data.get("path") or config.load().get("last_model") or ""
                if data.get("background"):
                    self._send(_start_job("model_load", lambda p=wanted: engine.start(p)))
                else:
                    self._send(engine.start(wanted))
            elif route == "/api/model/stop":
                engine.stop()
                self._send({"ok": True})
            elif route == "/api/model/download":
                self._send(start_download(data.get("id", "")))
            elif route == "/api/skills/seed":
                count = skills.seed()
                self._send({"ok": True, "added": count, **skills.overview()})
            elif route == "/api/skills/save":
                saved = skills.save(str(data.get("name") or ""), str(data.get("text") or ""))
                self._send(saved)
            elif route == "/api/skills/delete":
                self._send(skills.delete(str(data.get("name") or "")))
            elif route == "/api/plan/save":
                chat_id = str(data.get("chat") or "")
                saved = plan.set_plan(data.get("title") or "", data.get("steps") or [], chat_id)
                self._send({"ok": True, "plan": saved})
            elif route == "/api/plan/reset":
                chat_id = str(data.get("chat") or "")
                self._send({"ok": True, "plan": plan.reset(chat_id)})
            elif route == "/api/tools/toggle":
                name = str(data.get("name") or "").strip()
                if not name:
                    self._send({"ok": False, "error": "не указан инструмент"})
                else:
                    off = [str(item) for item in (config.load().get("tools_off") or []) if str(item) != name]
                    if not data.get("on"):
                        off.append(name)
                    config.save({"tools_off": off})
                    self._send({"ok": True, "off": off})
            elif route == "/api/tools/auto":
                config.save({"tools_auto": bool(data.get("on"))})
                self._send({"ok": True, "auto": bool(data.get("on"))})
            elif route == "/api/tools/pick":
                task = str(data.get("task") or "")
                self._send({"ok": True, "picked": tools.select(task, config.load())})
            elif route == "/api/vision/start":
                awaited = vision.start(str(data.get("path") or ""))
                self._send(awaited)
            elif route == "/api/vision/stop":
                vision.stop()
                self._send({"ok": True})
            elif route == "/api/embeddings/check":
                self._send(_embeddings_check())
            elif route == "/api/rag/reindex":
                self._send(_start_job("rag_reindex", lambda: rag.reindex()))
            elif route == "/api/hf/download":
                self._send(start_hf_download(str(data.get("repo") or ""), str(data.get("file") or "")))
            elif route == "/api/hf/token":
                hf.set_token(str(data.get("token") or ""))
                self._send({"ok": True, "token_set": bool(keystore.get("hf_token"))})
            elif route == "/api/provider":
                current = config.load().get("provider") or {}
                merged = {
                    "kind": data.get("kind") or current.get("kind") or "local",
                    "base_url": (data.get("base_url") if data.get("base_url") is not None else current.get("base_url")) or "",
                    "model": (data.get("model") if data.get("model") is not None else current.get("model")) or "",
                }
                key = (data.get("api_key") or "").strip()
                base = providers.normalize_base(merged["base_url"])
                if key and base:
                    keystore.put(providers.key_name(base), key)
                config.save({"provider": merged})
                self._send({"ok": True, "provider": providers.summary(config.load())})
            elif route == "/api/chats/save":
                saved = chats.save(str(data.get("id") or ""), data.get("messages") or [], str(data.get("title") or ""))
                if isinstance(data.get("plan"), dict):
                    try:
                        plan.save(str(data.get("id") or saved.get("id") or ""), data["plan"])
                    except Exception as exc:
                        diag.warn("main.py:chats_save_plan", exc)
                result = {"ok": True}
                result.update(saved)
                self._send(result)
            elif route == "/api/chats/get":
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                chat_id = str((query.get("id") or [""])[0])
                payload = chats.load(chat_id)
                try:
                    payload["plan"] = plan.snapshot(chat_id)
                except Exception as exc:
                    diag.warn("main.py:chats_get_plan", exc)
                self._send(payload)
            elif route == "/api/chats/delete":
                chats.delete(str(data.get("id") or ""))
                self._send({"ok": True})
            elif route == "/api/projects":
                items = data.get("projects")
                if isinstance(items, list):
                    config.save({"projects": [str(i) for i in items]})
                self._send({"ok": True, "projects": config.load().get("projects") or []})
            elif route == "/api/chat/cancel":
                agent.STOP["flag"] = True
                self._send({"ok": True})
            elif route == "/api/approve":
                ok = tools.resolve_approval(str(data.get("id") or ""), bool(data.get("allow")))
                self._send({"ok": ok})
            elif route == "/api/update/check":
                link = str(data.get("url") or config.load().get("update_url") or "")
                if not link:
                    self._send({"ok": False, "error": "Адрес обновлений не задан в настройках"})
                    return
                self._send(updater.check(link))
            elif route == "/api/update/apply":
                link = str(data.get("url") or config.load().get("update_url") or "")
                zip_name = str(data.get("zip") or "")
                if data.get("wait"):
                    self._send(updater.download_and_apply(link, zip_name))
                else:
                    self._send(_start_job("update_apply", lambda: updater.download_and_apply(link, zip_name)))
            elif route == "/api/autostart":
                want = bool(data.get("enabled"))
                self._send(autostart.set_enabled(want))
            elif route == "/api/schedule/save":
                self._send(schedule.upsert(data or {}))
            elif route == "/api/schedule/delete":
                self._send(schedule.remove(str(data.get("id") or "")))
            elif route == "/api/schedule/run":
                wanted = schedule.get(str(data.get("id") or ""))
                if not wanted:
                    self._send({"ok": False, "error": "задача не найдена"})
                else:
                    threading.Thread(target=_run_scheduled, args=(wanted,), daemon=True).start()
                    self._send({"ok": True, "started": True})
            elif route == "/api/telegram":
                new_token = str(data.get("token") or "").strip()
                if new_token:
                    keystore.put("telegram_bot", new_token)
                if data.get("chat") is not None:
                    config.save({"telegram_chat": str(data.get("chat") or "")})
                self._send({"ok": True})
            elif route == "/api/attach":
                self._send(attach.extract(str(data.get("path") or "")))
            elif route == "/api/attach/upload":
                self._send(attach.save_upload(str(data.get("name") or "file.txt"),
                                              str(data.get("data") or "")))

            elif route == "/api/rag/add":
                folder = str(data.get("path") or "")
                if data.get("background", True):
                    self._send(_start_job("rag_add", lambda t=folder: rag.add_path(t)))
                else:
                    self._send(rag.add_path(folder))
            elif route == "/api/rag/remove":
                self._send(rag.remove_doc(str(data.get("path") or "")))
            elif route == "/api/rag/clear":
                self._send(rag.clear())
            elif route == "/api/voice/model":
                name = str(data.get("name") or "turbo")
                config.save({"voice_model": name})
                threading.Thread(target=voice.ensure_model, args=(name,), daemon=True).start()
                self._send({"ok": True, "model": name})
            elif route == "/api/voice/install":
                threading.Thread(target=voice.install, daemon=True).start()
                self._send({"ok": True})
            elif route == "/api/voice/transcribe":
                import base64
                import tempfile
                raw = str(data.get("audio") or "")
                if "," in raw:
                    raw = raw.split(",", 1)[1]
                try:
                    blob = base64.b64decode(raw)
                except Exception as exc:
                    self._send({"ok": False, "error": "плохие данные: %s" % exc})
                    return
                handle = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
                handle.write(blob)
                handle.close()
                try:
                    result = voice.transcribe(handle.name)
                    if not result.get("ok"):
                        self._send({"ok": False, "error": result.get("error") or "речь не распознана"})
                    else:
                        self._send({"ok": True, "text": str(result.get("text") or "")})
                except Exception as exc:
                    self._send({"ok": False, "error": str(exc)})
                finally:
                    try:
                        pathlib.Path(handle.name).unlink()
                    except OSError as exc:
                        diag.warn("main.py:439", exc)
            elif route == "/api/voice/say":
                voice.say(str(data.get("text") or ""))
                self._send({"ok": True})
            elif route == "/api/open":
                raw = str(data.get("path") or "").strip()
                try:
                    target = tools._resolve(raw, config.load())
                except ValueError as exc:
                    self._send({"ok": False, "error": str(exc)})
                    return
                if not target.exists():
                    self._send({"ok": False, "error": "не найден: %s" % target})
                    return
                try:
                    os.startfile(str(target))
                    self._send({"ok": True})
                except Exception as exc:
                    self._send({"ok": False, "error": str(exc)})
            elif route == "/api/mcp/add":
                known = next((c for c in MCP_CATALOG if c["id"] == str(data.get("id") or "")), {})
                entry = {"id": str(data.get("id") or "").strip(),
                         "title": known.get("title") or str(data.get("title") or ""),
                         "command": str(data.get("command") or "").strip(),
                         "args": _expand_args(data.get("args") or []), "enabled": True}
                if not entry["id"] or not entry["command"]:
                    self._send({"ok": False, "error": "нужны id и команда"})
                else:
                    servers = [s for s in (config.load().get("mcp") or []) if s.get("id") != entry["id"]]
                    servers.append(entry)
                    config.save({"mcp": servers})
                    self._send({"ok": True, "servers": servers})
            elif route == "/api/mcp/remove":
                servers = [s for s in (config.load().get("mcp") or []) if s.get("id") != str(data.get("id"))]
                config.save({"mcp": servers})
                mcp.stop_all()
                self._send({"ok": True, "servers": servers})
            elif route == "/api/mcp/test":
                sid = str(data.get("id") or "")
                servers = [s for s in (config.load().get("mcp") or []) if s.get("id") == sid]
                if not servers:
                    self._send({"ok": False, "error": "сервер не найден"})
                else:
                    try:
                        names = [item["function"]["name"] for item in mcp.tools_for(servers)]
                        self._send({"ok": True, "tools": names})
                    except Exception as exc:
                        self._send({"ok": False, "error": str(exc)})
            elif route == "/api/roles":
                roles.save(config.load(), data.get("roles") or [])
                self._send({"ok": True, "roles": roles.merge(config.load())})
            elif route == "/api/subagents/run":
                task = str(data.get("task") or "").strip()
                wanted = [str(item) for item in (data.get("roles") or []) if str(item).strip()]
                if not task:
                    self._send({"ok": False, "error": "пустая задача"})
                elif len(wanted) > subagents.MAX_ROLES:
                    self._send({"ok": False, "error": "не более %d ролей за запуск" % subagents.MAX_ROLES})
                else:
                    cfg = dict(config.load())
                    target = providers.resolve(cfg)
                    events = []

                    def emit(event, _events=events):
                        _events.append(event)

                    started = _start_job("subagents",
                                         lambda: subagents.run(task, wanted, cfg, target, emit))
                    if started.get("ok") and "subagents" in JOBS:
                        JOBS["subagents"]["events"] = events
                    self._send(started)
            elif route == "/api/team":
                config.save({"use_team": bool(data.get("enabled"))})
                self._send({"ok": True, "enabled": bool(config.load().get("use_team"))})
            elif route == "/api/providers/discover":
                self._send({"servers": providers.discover()})
            elif route == "/api/providers/probe":
                base = (data.get("base_url") or "").strip()
                key = (data.get("api_key") or "").strip() or keystore.get(providers.key_name(base))
                self._send(providers.probe(base, key))
            elif route == "/api/models/discover":
                if not DISC.get("active"):
                    threading.Thread(target=_discover, daemon=True).start()
                self._send({"ok": True})
            elif route == "/api/engine/strays":
                self._send({"ok": True, "pids": engine.list_strays()})
            elif route == "/api/engine/reap":
                self._send(engine.reap_strays())
            elif route == "/api/engine/check":
                self._send(engine.check_latest(force=True))
            elif route == "/api/engine/install":
                variant = data.get("variant") or "cpu"
                found = engine.builds()
                tag = data.get("tag") or (found[0]["tag"] if found else "")
                if not tag:
                    self._send({"ok": False, "error": "не удалось узнать последнюю сборку"})
                else:
                    threading.Thread(target=engine.install, args=(tag, variant), daemon=True).start()
                    self._send({"ok": True, "tag": tag, "variant": variant})
            elif route == "/api/engine/stop":
                engine.stop()
                self._send({"ok": True})
            else:
                self.send_error(404, "not found")
        except Exception as exc:
            self._send({"error": str(exc)}, 500)



JOBS = {}


def _start_job(name, func):
    if JOBS.get(name, {}).get("running"):
        return {"ok": False, "error": "уже выполняется"}
    JOBS[name] = {"running": True, "done": False, "result": None,
                   "started": time.time(), "name": name}
    record = JOBS[name]

    def worker():
        try:
            record["result"] = func()
        except Exception as exc:
            record["result"] = {"ok": False, "error": str(exc)}
        record["running"] = False
        record["done"] = True

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "started": True, "job": name}


def _run_scheduled(entry):
    try:
        cfg = config.load()
        target = providers.resolve(cfg)
        if not target.get("base_url"):
            diag.warn("schedule", "модель не загружена, задача пропущена")
            return
        convo = [{"role": "user", "content": str(entry.get("task") or "")}]
        if not convo[0]["content"]:
            return
        quiet = lambda event: None
        if str(entry.get("mode") or "agent") == "team":
            orchestrator.run(convo[0]["content"], cfg, quiet, target)
            return
        result = agent.chat(convo, cfg, quiet, target)
        try:
            chat_id = "task-" + str(entry.get("id")) + "-" + time.strftime("%Y%m%d-%H%M")
            chats_mod.save(chat_id, result, "Задача по расписанию: " + str(entry.get("title") or entry.get("task"))[:60])
        except Exception as exc:
            diag.warn("schedule/save", exc)
    except Exception as exc:
        diag.warn("schedule/run", exc)


def _scheduler_loop():
    while True:
        try:
            for entry in schedule.due():
                schedule.mark_ran(str(entry.get("id")), "запущено")
                threading.Thread(target=_run_scheduled, args=(entry,), daemon=True).start()
        except Exception as exc:
            diag.warn("scheduler", exc)
        time.sleep(20)

def emit_console(event: dict) -> None:
    print("approval:", event.get("tool"), flush=True)




def lan_urls():
    out = []
    try:
        import socket as _socket
        for info in _socket.getaddrinfo(_socket.gethostname(), None, _socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in out:
                out.append(ip)
    except Exception as exc:
        diag.warn("lan_urls", exc)
    return out

def _alive(port: int) -> bool:
    try:
        url = "http://" + HOST + ":" + str(port) + "/api/version"
        request = urllib.request.Request(url, headers={"X-Agent-Token": ACCESS_TOKEN})
        with urllib.request.urlopen(request, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False

class Server(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True


def run(port: int = 7867, open_browser: bool = True):
    try:
        if updater.has_pending():
            updater.apply_pending()
    except Exception as exc:
        diag.warn("main.py:572", exc)
    try:
        engine.embed_cleanup()
    except Exception as exc:
        diag.warn("main.py:embed_cleanup", exc)
    try:
        vision.cleanup()
    except Exception as exc:
        diag.warn("main.py:vision_cleanup", exc)
    try:
        added = skills.seed()
        if added:
            print("навыки: добавлено встроенных %d" % added, flush=True)
    except Exception as exc:
        diag.warn("main.py:skills_seed", exc)
    if config.load().get("auto_update_engine"):
        threading.Thread(target=lambda: engine.check_latest(force=True), daemon=True).start()
    url = "http://" + HOST + ":%d/" % port
    if _alive(port):
        print("Приложение уже запущено: " + url, flush=True)
        if open_browser:
            webbrowser.open(url)
        return
    for candidate in range(port, port + 20):
        if not _alive(candidate):
            port = candidate
            break
    bind_host = "0.0.0.0" if config.load().get("lan_access") else HOST
    httpd = Server((bind_host, port), Handler)
    httpd.daemon_threads = True
    url = "http://" + HOST + ":%d/" % httpd.server_port
    if bind_host != HOST:
        print("доступ по локальной сети включён, слушаем " + bind_host, flush=True)
    print(url, flush=True)
    icon = None
    try:
        if config.load().get("tray"):
            from . import tray
            icon = tray.start(url, on_exit=lambda: (engine.stop(), os._exit(0)))
    except Exception:
        icon = None
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt as exc:
        diag.warn("main.py:599", exc)
    finally:
        if icon is not None:
            try:
                icon.stop()
            except Exception as exc:
                diag.warn("main.py:605", exc)
        engine.stop()
        try:
            engine.embed_stop()
        except Exception as exc:
            diag.warn("main.py:embed_stop", exc)


if __name__ == "__main__":
    run()
