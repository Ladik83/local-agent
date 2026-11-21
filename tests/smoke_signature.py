import json
import os
import pathlib
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TMP = tempfile.mkdtemp(prefix="agent-sign-")
os.environ["LOCALAGENT_HOME"] = TMP

import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, signature, updater

STATE = {"body": b"{}"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        body = STATE["body"]
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
port = server.server_port
manifest_url = "http://127.0.0.1:%d/manifest.json" % port

print("1. CNG доступна:", signature.available())
public, private = signature.generate_pair()
config.save({"update_pubkey": public})
print("2. ключ записан в настройки:", bool(config.load().get("update_pubkey")))

digest = "b" * 64
package_url = "http://127.0.0.1:%d/app.zip" % port
manifest = {"version": "99.0.0", "url": package_url, "sha256": digest, "notes": "смоук"}
message = signature.manifest_message(manifest["version"], manifest["url"], manifest["sha256"])
manifest["signature"] = signature.sign(private, message)
STATE["body"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")

info = updater.check(manifest_url)
print("3. подписанный манифест:", "ok" if info.get("ok") else info.get("error"),
      "| signed:", info.get("signed"), "| update:", info.get("update"), "| latest:", info.get("latest"))

tampered = dict(manifest)
tampered["url"] = "http://127.0.0.1:%d/evil.zip" % port
STATE["body"] = json.dumps(tampered, ensure_ascii=False).encode("utf-8")
bad = updater.check(manifest_url)
print("4. подменённая ссылка отклонена:", bad.get("ok") is False, "|", str(bad.get("error"))[:60])

unsigned = dict(manifest)
unsigned.pop("signature", None)
STATE["body"] = json.dumps(unsigned, ensure_ascii=False).encode("utf-8")
empty = updater.check(manifest_url)
print("5. без подписи отклонено:", empty.get("ok") is False, "|", str(empty.get("error"))[:60])

STATE["body"] = json.dumps(tampered, ensure_ascii=False).encode("utf-8")
applied = updater.download_and_apply(manifest_url)
print("6. установка по подменённому манифесту отклонена:", applied.get("ok") is False,
      "|", str(applied.get("error"))[:60])

config.save({"update_pubkey": ""})
STATE["body"] = json.dumps(unsigned, ensure_ascii=False).encode("utf-8")
free = updater.check(manifest_url)
print("7. без ключа проверка не требуется:", free.get("ok") is True, "| signed:", free.get("signed"))

server.shutdown()
