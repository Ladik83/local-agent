"""Веб-сервер приложения: состояние, модели, движок, чат."""
from __future__ import annotations

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

from . import agent, autostart, catalog, chats, config, drives, engine, hardware, keystore, mcp, orchestrator, panels, providers, rag, roles, scan, stats, tools, updater, voice

UI_DIR = pathlib.Path(__file__).parent / "ui"
TOKEN_FILE = config.DATA_DIR / "access.token"
def _load_token() -> str:
    try:
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if len(token) >= 24:
            return token
    except OSError:
        pass
    import secrets as _secrets
    token = _secrets.token_hex(24)
    try:
        TOKEN_FILE.write_text(token, encoding="utf-8")
    except OSError:
        pass
    return token


ACCESS_TOKEN = _load_token()

HOST = "127.0.0.1"
MCP_CATALOG = [
    {"id": "filesystem", "title": "Файлы — официальный сервер", "command": "npx",
     "args": ["-y", "@modelcontextprotocol/server-filesystem", "%HOME%"]},
    {"id": "memory", "title": "Память — официальный сервер", "command": "npx",
     "args": ["-y", "@modelcontextprotocol/server-memory"]},
    {"id": "fetch", "title": "Загрузка страниц — python-сервер", "command": "uvx",
     "args": ["mcp-server-fetch"]},
]
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
        "models": local,
        "pick": pick,
        "catalog": [dict(c, url=catalog.url(c)) for c in catalog.CATALOG],
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
        return host in ("", "127.0.0.1", "localhost", "[::1]")

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
        safe = name.replace("\\", "/").split("/")[-1]
        path = UI_DIR / safe
        if not path.is_file():
            self.send_error(404, "not found")
            return
        data = path.read_bytes()
        if path.name == "index.html":
            data = data.replace(b"</head>", ("<meta name=\"agent-token\" content=\"" + ACCESS_TOKEN + "\"></head>").encode("utf-8"), 1)
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
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
                servers = config.load().get("mcp") or []
                try:
                    found = [item["function"]["name"] for item in mcp.tools_for(servers)]
                except Exception as exc:
                    found = ["ошибка: " + str(exc)]
                self._send({"servers": servers, "tools": found, "catalog": MCP_CATALOG})
            elif route == "/api/voice":
                self._send(voice.status())
            elif route == "/api/stats":
                self._send(stats.snapshot())
            elif route == "/api/chats":
                self._send({"chats": chats.list_all()})
            elif route == "/api/chats/get":
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                self._send(chats.load((query.get("id") or [""])[0]))
            elif route == "/api/job":
                self._send({"engine": dict(engine.JOB), "download": dict(DL), "discover": dict(DISC)})
            elif route == "/api/panels":
                self._send(panels.snapshot())
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
            except OSError:
                pass
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
        cfg = config.load()
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
                result = {"ok": True}
                result.update(saved)
                self._send(result)
            elif route == "/api/chats/get":
                self._send(chats.load(str(data.get("id") or "")))
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
                self._send(updater.download_and_apply(link, str(data.get("zip") or "")))
            elif route == "/api/autostart":
                want = bool(data.get("enabled"))
                self._send(autostart.set_enabled(want))
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
                    except OSError:
                        pass
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
                    self._send({"ok": False, "error": "[PERSON_NAME] не найден: %s" % target})
                    return
                try:
                    os.startfile(str(target))
                    self._send({"ok": True})
                except Exception as exc:
                    self._send({"ok": False, "error": str(exc)})
            elif route == "/api/mcp/add":
                if not tools.approval_gate("mcp", {"server": str(data.get("id") or "")}, config.load(), emit_console):
                    self._send({"ok": False, "error": "Пользователь не разрешил запуск MCP-сервера"})
                    return
            elif route == "/api/mcp/add":
                entry = {"id": str(data.get("id") or "").strip(),
                         "command": str(data.get("command") or "").strip(),
                         "args": [str(a) for a in (data.get("args") or [])], "enabled": True}
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
                if not tools.approval_gate("mcp", {"server": str(data.get("id") or "")}, config.load(), emit_console):
                    self._send({"ok": False, "error": "Пользователь не разрешил проверку MCP-сервера"})
                    return
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

def emit_console(event: dict) -> None:
    print("approval:", event.get("tool"), flush=True)


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
    except Exception:
        pass
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
    httpd = Server((HOST, port), Handler)
    httpd.daemon_threads = True
    url = "http://" + HOST + ":%d/" % httpd.server_port
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
    except KeyboardInterrupt:
        pass
    finally:
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass
        engine.stop()


if __name__ == "__main__":
    run()
