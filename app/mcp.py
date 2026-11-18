"""MCP-клиент: внешние наборы инструментов по протоколу MCP (stdio, JSON-RPC)."""
from __future__ import annotations

from . import diag

import json
import subprocess
import threading
import time

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TIMEOUT = 25.0
PREFIX = "mcp__"


def _safe(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(text))[:40]


class Session:
    """Один MCP-сервер: запуск процесса, список инструментов, вызов."""

    def __init__(self, server_id: str, command: str, args: list, cwd: str = ""):
        self.server_id = server_id
        self.command = command
        self.args = list(args or [])
        self.cwd = cwd or None
        self.proc = None
        self.tools: list = []
        self.error = ""
        self.lock = threading.Lock()
        self._id = 0

    def _next(self) -> int:
        self._id += 1
        return self._id

    def _send(self, method: str, params=None, expect: bool = True):
        with self.lock:
            payload = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                payload["params"] = params
            if expect:
                payload["id"] = self._next()
            self.proc.stdin.write(json.dumps(payload) + "\n")
            self.proc.stdin.flush()
            if not expect:
                return None
            deadline = time.time() + TIMEOUT
            while time.time() < deadline:
                line = self.proc.stdout.readline()
                if not line:
                    raise RuntimeError("сервер закрыл соединение")
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if message.get("id") == payload["id"]:
                    if "error" in message:
                        raise RuntimeError(str(message["error"].get("message") or message["error"]))
                    return message.get("result")
            raise RuntimeError("сервер не ответил за %.0f с" % TIMEOUT)

    def start(self) -> "Session":
        if self.proc is not None and self.proc.poll() is None:
            return self
        self.proc = subprocess.Popen(
            [self.command] + self.args,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
            cwd=self.cwd, creationflags=NO_WINDOW,
        )
        self._send("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "local-agent", "version": "0.3"},
        })
        try:
            self._send("notifications/initialized", None, expect=False)
        except Exception as exc:
            diag.warn("mcp.py:81", exc)
        tools = []
        try:
            listed = self._send("tools/list", {}) or {}
            for item in listed.get("tools") or []:
                name = str(item.get("name") or "")
                if not name:
                    continue
                schema = item.get("inputSchema") or {"type": "object", "properties": {}}
                tools.append({
                    "name": PREFIX + _safe(self.server_id) + "__" + _safe(name),
                    "raw": name,
                    "description": str(item.get("description") or ""),
                    "parameters": schema,
                })
        except Exception as exc:
            self.error = str(exc)
        self.tools = tools
        return self

    def call(self, raw_name: str, args: dict) -> str:
        result = self._send("tools/call", {"name": raw_name, "arguments": args or {}})
        parts = []
        for item in (result or {}).get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif isinstance(item, dict):
                parts.append(json.dumps(item, ensure_ascii=False)[:400])
        text = "\n".join(parts).strip()
        return text or "(пустой ответ)"

    def stop(self) -> None:
        if self.proc is not None:
            try:
                self.proc.terminate()
            except OSError as exc:
                diag.warn("mcp.py:117", exc)
            self.proc = None
        self.tools = []

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


_SESSIONS: dict = {}


def session(server_id: str, command: str, args: list, cwd: str = "") -> Session:
    key = server_id
    existing = _SESSIONS.get(key)
    if existing and existing.alive() and existing.command == command and existing.args == list(args or []):
        return existing
    if existing:
        existing.stop()
    made = Session(server_id, command, args, cwd).start()
    _SESSIONS[key] = made
    return made


def stop_all() -> None:
    for item in list(_SESSIONS.values()):
        item.stop()
    _SESSIONS.clear()


def tools_for(servers: list) -> list:
    """Собрать все инструменты включённых MCP-серверов в формате OpenAI."""
    out = []
    for server in servers or []:
        if not server.get("enabled", True):
            continue
        try:
            ready = session(server["id"], server.get("command", ""), server.get("args") or [])
        except Exception as exc:
            out.append({"error": "%s: %s" % (server.get("id"), exc), "broken": True})
            continue
        for tool in ready.tools:
            out.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": (tool["description"] or "")[:900],
                    "parameters": tool["parameters"] or {"type": "object", "properties": {}},
                },
                "_server": server["id"],
                "_raw": tool["raw"],
            })
    return out


def call(name: str, args: dict, servers: list) -> str:
    for server in servers or []:
        if not server.get("enabled", True):
            continue
        ready = _SESSIONS.get(server["id"])
        if ready is None:
            continue
        for tool in ready.tools:
            if tool["name"] == name:
                return ready.call(tool["raw"], args)
    return "Инструмент MCP не найден или сервер не запущен"
