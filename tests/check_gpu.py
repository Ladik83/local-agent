"""Проверки видеокарты: выбор сборки с Vulkan, флаг --device, разбор выгрузки."""
from __future__ import annotations

import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, engine  # noqa: E402

# тест работает со своей копией настроек и не трогает настоящие
_real_settings = config.SETTINGS_PATH
_work = pathlib.Path(tempfile.mkdtemp(prefix="gpu-settings-")) / "settings.json"
_work.write_text(_real_settings.read_text(encoding="utf-8") if _real_settings.exists() else "{}", encoding="utf-8")
config.SETTINGS_PATH = _work
config._cfg = None
config._cfg_stamp = -1.0

problems = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global problems
    if ok:
        print("OK  ", name, ("| " + extra) if extra else "")
    else:
        problems += 1
        print("FAIL", name, ("| " + extra) if extra else "")


tmp = pathlib.Path(tempfile.mkdtemp(prefix="gpu-"))
cpu_dir = tmp / "llamacpp-b99999-vulkan"
vulkan_dir = tmp / "llamacpp-b90000-vulkan"
for folder in (cpu_dir, vulkan_dir):
    folder.mkdir(parents=True)
    (folder / engine.EXE).write_bytes(b"MZ")
(vulkan_dir / engine.VULKAN_DLL).write_bytes(b"dll")

check("папка без dll не считается Vulkan", engine.has_vulkan(cpu_dir) is False)
check("папка с dll считается Vulkan", engine.has_vulkan(vulkan_dir) is True)

saved_dir = config.ENGINES_DIR
saved_cfg = config.load()
try:
    config.ENGINES_DIR = tmp
    config.save({"engine_tag": "b99999", "engine_variant": "vulkan"})
    picked = engine.find_exe()
    check("битая сборка пропускается", bool(picked and "b90000" in picked), str(picked or ""))
finally:
    config.ENGINES_DIR = saved_dir
    config.save({"engine_tag": saved_cfg.get("engine_tag"), "engine_variant": saved_cfg.get("engine_variant")})

log = tmp / "llama-server.log"
log.write_text("load_model: model loaded\n"
               "resolve_fused_ops: layer 0 is assigned to device Vulkan0 but Flash Attention\n"
               "server_on_li: listening on http://127.0.0.1:8090\n", encoding="utf-8")
info = engine.offload_info(log, "Intel(R) Graphics")
check("выгрузка распознана", info.get("on_gpu") is True and info.get("gpu") == "Intel(R) Graphics", str(info))

log.write_text("load_model: no usable GPU found, using CPU\n", encoding="utf-8")
info = engine.offload_info(log, "Intel(R) Graphics")
check("без видеокарты — процессор", info.get("on_gpu") is False, str(info))

staging = tmp / ".new-b99999-vulkan"
staging.mkdir()
(staging / engine.EXE).write_bytes(b"MZ")
saved_dir2 = config.ENGINES_DIR
try:
    config.ENGINES_DIR = tmp
    result = engine._vulkan_fallback("b99999", staging)
    check("без Vulkan включается рабочая сборка", bool(result.get("ok") and result.get("fallback")),
          str(result.get("message") or result.get("error")))
    check("настройка указывает на рабочую сборку", config.load().get("engine_tag") == "b90000",
          str(config.load().get("engine_tag")))
finally:
    config.ENGINES_DIR = saved_dir2
    config.save({"engine_tag": saved_cfg.get("engine_tag"), "engine_variant": saved_cfg.get("engine_variant")})
    clean = [item for item in (config.load().get("engine_skip") or []) if item in ("b12633",)]
    config.save({"engine_skip": clean})

check("по умолчанию видеокарта", (config.DEFAULTS.get("gpu") or "") == "auto", str(config.DEFAULTS.get("gpu") or ""))

captured = {}


class FakeProc:
    pid = 0

    def poll(self):
        return 1


def fake_popen(cmd, **kwargs):
    captured["cmd"] = list(cmd)
    return FakeProc()


saved_popen = engine.subprocess.Popen
saved_healthy = engine._healthy
saved_devices = engine.devices
saved_stop = engine.stop
saved_find = engine.find_exe
saved_projector = engine.find_projector
model_file = tmp / "model.gguf"
model_file.write_bytes(b"gguf")
try:
    engine.subprocess.Popen = fake_popen
    engine._healthy = lambda port, timeout: False
    engine.devices = lambda exe=None, force=False: [{"name": "Vulkan0", "text": "Intel(R) Graphics (8 GiB)"}]
    engine.stop = lambda: None
    engine.find_exe = lambda: str(vulkan_dir / engine.EXE)
    engine.find_projector = lambda path: ""
    config.save({"gpu": "auto", "ngl": 99, "context": 4096})
    engine.start(str(model_file))
    cmd = captured.get("cmd") or []
    joined = " ".join(cmd)
    check("движок получает Vulkan-устройство", "--device Vulkan0" in joined, joined[:160])
    check("слои выгружаются", "-ngl 99" in joined)
    check("чат больше не в режиме эмбеддингов", "--embeddings" not in cmd and "--pooling" not in cmd)

    engine.devices = lambda exe=None, force=False: []
    engine.start(str(model_file))
    joined = " ".join(captured.get("cmd") or [])
    check("без устройства — процессор", "-ngl 0" in joined, joined[:120])

    config.save({"gpu": "cpu"})
    engine.devices = lambda exe=None, force=False: [{"name": "Vulkan0", "text": "Intel"}]
    engine.start(str(model_file))
    joined = " ".join(captured.get("cmd") or [])
    check("режим «процессор» уважается", "-ngl 0" in joined and "--device" not in joined)
finally:
    engine.subprocess.Popen = saved_popen
    engine._healthy = saved_healthy
    engine.devices = saved_devices
    engine.stop = saved_stop
    engine.find_exe = saved_find
    engine.find_projector = saved_projector
    config.save({"gpu": "auto"})

config.SETTINGS_PATH = _real_settings
config._cfg = None
config._cfg_stamp = -1.0

print()
if problems:
    print("ПРОБЛЕМЫ:", problems)
    sys.exit(1)
print("видеокарта: все проверки пройдены")
