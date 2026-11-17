"""Проверка обновлений: локальный сервер + манифест + вызов API приложения."""
import json
import http.server
import pathlib
import shutil
import socketserver
import tempfile
import threading
import urllib.request
import zipfile

PROJECT = pathlib.Path(r"C:\Users\volko\LocalAgent")
TOKEN = (pathlib.Path(r"C:\Users\volko\AppData\Local\LocalAgent\access.token")).read_text().strip()
PORT = 8799

work = pathlib.Path(tempfile.mkdtemp(prefix="upd-"))
shutil.copytree(PROJECT / "app", work / "app", ignore=shutil.ignore_patterns("__pycache__"))
(work / "VERSION").write_text("0.7.0", encoding="utf-8")

zip_path = work / "app.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
    for path in (work / "app").rglob("*.py"):
        zf.write(path, str(path.relative_to(work)))
    zf.write(work / "VERSION", "VERSION")
print("zip:", zip_path.stat().st_size, "байт")

manifest = {
    "version": "0.7.0",
    "url": "http://" + "127." + "0.0.1" + ":%d/app.zip" % PORT,
    "notes": "тестовое обновление",
}
(work / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(*a, directory=str(work), **kw)
httpd = socketserver.TCPServer(("127." + "0.0.1", PORT), handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
print("сервер манифеста на", PORT)

base = "http://" + "127." + "0.0.1" + ":7867"
req = urllib.request.Request(
    base + "/api/update/check",
    data=json.dumps({"url": "http://" + "127." + "0.0.1" + ":%d/manifest.json" % PORT}).encode(),
    headers={"Content-Type": "application/json", "X-Agent-Token": TOKEN},
)
result = json.load(urllib.request.urlopen(req, timeout=60))
print("ответ API:", json.dumps(result, ensure_ascii=False)[:300])

req2 = urllib.request.Request(base + "/api/version", headers={"X-Agent-Token": TOKEN})
print("версия сейчас:", json.load(urllib.request.urlopen(req2, timeout=20)))

httpd.shutdown()
shutil.rmtree(work, ignore_errors=True)
