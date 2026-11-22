"""Проверка релизной упаковки: чистая сборка, манифест, подпись.

  python tests/check_release.py

Код 0 — всё в порядке, 1 — есть ошибки. Подпись проверяется только там,
где доступна Windows CNG; иначе пункт мягко пропускается.
"""
import hashlib
import json
import os
import pathlib
import shutil
import sys
import tempfile
import zipfile

TMP = tempfile.mkdtemp(prefix="agent-release-")
os.environ["LOCALAGENT_HOME"] = os.path.join(TMP, "home")

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_portable as bp
import make_manifest as mm

TMPDIR = pathlib.Path(TMP)
FAILS = []


def check(name, ok, detail=""):
    print("  %s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else " — %s" % detail))
    if not ok:
        FAILS.append(name)
    return ok


def touch(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def names(files):
    return {str(p).replace("\\", "/") for p in files}


# ---------------------------------------------------------------- (a) манифест
print("(a) манифест из zip")
zip_path = TMPDIR / "LocalAgent-9.9.9.zip"
with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
    zf.writestr("LocalAgent/run.py", "print('hi')\n")
    zf.writestr("LocalAgent/app/main.py", "# main\n")

url = "https://example.com/LocalAgent-9.9.9.zip"
data = mm.build_manifest(zip_path, "v9.9.9", url, notes="Тест: кириллица и «кавычки»")
check("поля version/url/sha256/notes", set(data) == {"version", "url", "sha256", "notes"},
      "получили %s" % sorted(data))
check("версия нормализована (v9.9.9 -> 9.9.9)", data["version"] == "9.9.9", str(data.get("version")))
expected = hashlib.sha256(zip_path.read_bytes()).hexdigest()
check("sha256 совпадает с посчитанным", data["sha256"] == expected,
      "%s != %s" % (data.get("sha256"), expected))
check("sha256 длиной 64 hex", len(data["sha256"]) == 64 and data["sha256"].islower())
check("notes сохранены дословно", data["notes"].startswith("Тест: кириллица"))

manifest_path = TMPDIR / "out" / "manifest.json"
saved = mm.write_manifest(data, manifest_path)
raw = saved.read_text(encoding="utf-8")
loaded = json.loads(raw)
check("манифест записан в UTF-8 без экранирования", "кириллица" in raw and loaded == data)
check("подписи нет без --sign", "signature" not in loaded)

check("код 0 при успехе", mm.main(["--zip", str(zip_path), "--version", "9.9.9",
                                   "--url", url, "--out", str(TMPDIR / "cli.json")]) == 0)
check("код 2 без архива", mm.main(["--zip", str(TMPDIR / "nope.zip"), "--version", "9.9.9",
                                   "--url", url, "--out", str(TMPDIR / "x.json")]) == 2)
check("код 2 при кривой версии", mm.main(["--zip", str(zip_path), "--version", "девять",
                                          "--url", url, "--out", str(TMPDIR / "x.json")]) == 2)

# ---------------------------------------------------------------- (b) сборка
print("(b) чистая сборка: что попадает в архив")
fake = TMPDIR / "fake"
touch(fake / "run.py")
touch(fake / "VERSION", "9.9.9\n")
touch(fake / "README.md", "# readme\n")
touch(fake / "app" / "__init__.py", "")
touch(fake / "app" / "main.py", "# main\n")
touch(fake / "app" / "ui" / "panels.py", "# ui\n")
touch(fake / "app" / "__pycache__" / "main.cpython-311.pyc")
touch(fake / "app" / "old.pyc")
touch(fake / "app" / "debug.log")
touch(fake / "app" / "data" / "chats.json", "[]")
touch(fake / "data" / "access.token", "SECRET")
touch(fake / "data" / "settings.json", "{}")
touch(fake / "dist" / "LocalAgent" / "run.py")
touch(fake / ".git" / "config")
touch(fake / "tests" / "__pycache__" / "x.pyc")
touch(fake / "venv" / "pyvenv.cfg")
touch(fake / ".env", "API_KEY=1")

clean = names(bp.collect_include(fake, clean=True))
check("run.py включён", "run.py" in clean, str(sorted(clean)))
check("app/ включён", "app/main.py" in clean and "app/__init__.py" in clean)
check("app/data исключён", not any(p.startswith("app/data/") for p in clean))
check("data/ исключён", not any(p.split("/")[0] == "data" for p in clean), str(sorted(clean)))
check("dist/ исключён", not any(p.split("/")[0] == "dist" for p in clean))
check("__pycache__ исключён", not any("__pycache__" in p for p in clean))
check("*.pyc исключены", not any(p.endswith(".pyc") for p in clean))
check("*.log исключены", not any(p.endswith(".log") for p in clean))
check("venv/ и .env исключены",
      not any(p.split("/")[0] in ("venv", ".venv") for p in clean) and ".env" not in clean)

plain = names(bp.collect_include(fake, clean=False))
check("без --clean код на месте", "run.py" in plain and "app/main.py" in plain)
check("без --clean __pycache__ по-прежнему не копируется",
      not any("__pycache__" in p for p in plain))

real = names(bp.collect_include(ROOT, clean=True))
check("реальный проект: run.py и app/", "run.py" in real and "app/main.py" in real)
check("реальный проект: нет data/ и токенов",
      not any(p.split("/")[0] == "data" for p in real)
      and not any("access.token" in p for p in real), str(sorted(real))[:200])
check("реальный проект: нет __pycache__/*.pyc",
      not any("__pycache__" in p or p.endswith(".pyc") for p in real))

target = TMPDIR / "dist" / "LocalAgent"
touch(target / "data" / "access.token", "SECRET")
touch(target / "data" / "engines" / "llama.dll", "bin")
touch(target / "app.old" / "main.py", "# old\n")
touch(target / "models" / "m.gguf", "bin")
touch(target / "python" / "python.exe", "exe")
bp.clean_target(target)
check("clean_target убирает data/models/app.old",
      not (target / "data").exists() and not (target / "models").exists()
      and not (target / "app.old").exists())
check("clean_target не трогает python/", (target / "python" / "python.exe").is_file())

launch_clean = TMPDIR / "launch-clean"
launch_plain = TMPDIR / "launch-plain"
bp.write_launchers(launch_clean, clean=True)
bp.write_launchers(launch_plain, clean=False)
check("--clean не создаёт data/ и models/",
      not (launch_clean / "data").exists() and not (launch_clean / "models").exists())
check("без --clean data/ и models/ создаются",
      (launch_plain / "data").is_dir() and (launch_plain / "models").is_dir())
check("portable.txt на месте", (launch_clean / "portable.txt").is_file())

# ---------------------------------------------------------------- (c) подпись
print("(c) подпись манифеста (Windows CNG)")
cng_ok = False
signature = None
try:
    from app import signature as _sig
    signature = _sig
    cng_ok = bool(_sig.available())
except Exception as exc:  # не Windows или нет bcrypt.dll
    print("  skip  CNG недоступна: %s" % (exc,))

if not cng_ok:
    print("  skip  подпись пропущена: криптография недоступна")
else:
    assert signature is not None
    public, private = signature.generate_pair()
    signed = mm.build_manifest(zip_path, "9.9.9", url, notes="подпись",
                               sign=True, private_key=private)
    check("поле signature появилось", bool(signed.get("signature")))
    check("подпись проверяется публичным ключом", mm.verify_manifest(signed, public))
    tampered = dict(signed)
    tampered["url"] = url + "-evil"
    check("чужая ссылка не проходит проверку", not mm.verify_manifest(tampered, public))

    key_file = TMPDIR / "update_private.key"
    key_file.write_text(private, encoding="utf-8")
    out_signed = TMPDIR / "signed.json"
    rc = mm.main(["--zip", str(zip_path), "--version", "9.9.9", "--url", url,
                  "--notes", "подпись", "--out", str(out_signed), "--sign",
                  "--key", str(key_file)])
    payload = json.loads(out_signed.read_text(encoding="utf-8")) if out_signed.is_file() else {}
    check("CLI --sign: код 0", rc == 0, "код %s" % rc)
    check("CLI --sign: подпись в файле", bool(payload.get("signature")))
    check("CLI --sign: подпись верна", mm.verify_manifest(payload, public))

    missing = TMPDIR / "missing.key"
    check("код 2 без приватного ключа",
          mm.main(["--zip", str(zip_path), "--version", "9.9.9", "--url", url,
                   "--out", str(TMPDIR / "y.json"), "--sign", "--key", str(missing)]) == 2)

# ---------------------------------------------------------------- итог
print("")
if FAILS:
    print("ПРОВАЛЕНО: %d — %s" % (len(FAILS), ", ".join(FAILS)))
else:
    print("все проверки пройдены")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAILS else 0)
