"""Голос: локальное распознавание речи (whisper.cpp) и озвучка (Windows SAPI)."""
from __future__ import annotations

from . import diag

import pathlib
import re
import struct
import subprocess
import threading
import urllib.request
import wave
import zipfile

from . import config

GH = "https://api.github.com/repos/ggml-org/whisper.cpp/releases?per_page=40"
HF = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
UA = {"User-Agent": "LocalAgent/0.5"}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ENGINES = config.DATA_DIR / "engines"
MODELS_DIR = config.DATA_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

JOB = {"active": False, "pct": 0.0, "text": "", "error": None}

MODELS = {
    "base": {"file": "ggml-base.bin", "title": "быстрая · 141 МБ", "mb": 141},
    "small": {"file": "ggml-small.bin", "title": "точнее · 488 МБ", "mb": 488},
    "turbo": {"file": "ggml-large-v3-turbo-q5_0.bin", "title": "максимальная точность · 574 МБ", "mb": 574},
}
DEFAULT_MODEL = "turbo"


def current_model() -> str:
    name = str(config.load().get("voice_model") or DEFAULT_MODEL)
    return name if name in MODELS else DEFAULT_MODEL


def model_path(name: str = "") -> pathlib.Path:
    return MODELS_DIR / MODELS[name or current_model()]["file"]


def find_cli():
    for pattern in ("whisper-cli.exe", "main.exe"):
        hits = sorted(ENGINES.glob("whisper*/**/" + pattern))
        if hits:
            return str(hits[0])
    which = None
    import shutil
    which = shutil.which("whisper-cli")
    return which


def _json(url: str):
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=40) as response:
        import json
        return json.loads(response.read().decode("utf-8"))


def _latest_zip() -> str:
    for release in _json(GH):
        for asset in release.get("assets", []):
            if asset.get("name") == "whisper-bin-x64.zip":
                return asset["browser_download_url"]
    raise RuntimeError("не нашёл сборку whisper.cpp для Windows")


def _download(url: str, dest: pathlib.Path, what: str) -> None:
    JOB.update(active=True, pct=0.0, text="Скачиваем " + what, error=None)
    part = dest.with_name(dest.name + ".part")
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=180) as response, open(part, "wb") as handle:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            handle.write(chunk)
            done += len(chunk)
            if total:
                JOB["pct"] = round(done * 100.0 / total, 1)
                JOB["text"] = "%s: %.0f из %.0f МБ" % (what, done / 1048576, total / 1048576)
    part.replace(dest)


def ensure_model(name: str = "") -> pathlib.Path:
    target = model_path(name)
    if target.exists() and target.stat().st_size > 10 * 1024 * 1024:
        return target
    spec = MODELS[name or current_model()]
    _download(HF + spec["file"], target, "модель распознавания")
    JOB.update(active=False, pct=100.0, text="Модель готова")
    return target


def install() -> dict:
    try:
        if not find_cli():
            zip_path = config.DOWNLOAD_DIR / "whisper-bin-x64.zip"
            if not zip_path.exists():
                _download(_latest_zip(), zip_path, "whisper.cpp")
            staging = ENGINES / ".whisper-new"
            if staging.exists():
                import shutil
                shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(str(zip_path)) as archive:
                archive.extractall(str(staging))
            target = ENGINES / "whisper"
            if target.exists():
                import shutil
                shutil.rmtree(target, ignore_errors=True)
            staging.replace(target)
        ensure_model(current_model())
        JOB.update(active=False, error=None, text="Голос готов")
        return {"ok": True, "cli": find_cli(), "model": str(model_path())}
    except Exception as exc:
        JOB.update(active=False, error=str(exc), text="Ошибка установки")
        return {"ok": False, "error": str(exc)}


def _ensure_16k(path: str) -> str:
    """whisper ждёт 16 кГц моно; браузер может отдать 44–48 кГц."""
    import wave
    try:
        with wave.open(path, "rb") as wav:
            rate = wav.getframerate()
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            frames = wav.readframes(wav.getnframes())
    except Exception:
        return path
    if rate == 16000 and channels == 1 and width == 2:
        return path
    if width != 2:
        return path
    total = len(frames) // 2
    samples = list(struct.unpack("<" + str(total) + "h", frames[:total * 2]))
    if channels > 1:
        samples = samples[0::channels]
    ratio = 16000.0 / float(rate or 16000)
    count = int(len(samples) * ratio)
    out = []
    for index in range(count):
        position = index / ratio
        left = int(position)
        right = min(left + 1, len(samples) - 1)
        frac = position - left
        out.append(int(samples[left] * (1 - frac) + samples[right] * frac))
    target = str(path).replace(".wav", "_16k.wav")
    with wave.open(target, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(struct.pack("<" + str(len(out)) + "h", *out))
    return target


def transcribe(wav_path: str, language: str = "ru") -> dict:
    cli = find_cli()
    if not cli:
        return {"ok": False, "error": "движок распознавания не установлен"}
    try:
        model = ensure_model()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    audio = _ensure_16k(str(wav_path))
    base = pathlib.Path(audio).with_suffix("")
    lang = str(language or config.load().get("voice_lang") or "ru")
    command = [cli, "-m", str(model), "-f", audio, "-l", lang,
               "-bs", "5", "-bo", "5", "-nt", "-otxt", "-of", str(base)]
    if "ru" in lang.lower():
        # длинная подсказка ломает распознавание на large-v3-turbo («Р» вместо текста)
        command += ["--prompt", "Локальный агент, файлы, папки, модель"]
    try:
        done = subprocess.run(command, capture_output=True, timeout=300, creationflags=NO_WINDOW)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}
    text = ""
    try:
        text = base.with_suffix(".txt").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        text = done.stdout.decode("utf-8", "replace").strip()
    cleaned = re.sub(r"\[[0-9:. ]+\]", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned == "[BLANK_AUDIO]" or not cleaned:
        return {"ok": False, "error": "речь не распознана"}
    return {"ok": True, "text": cleaned, "model": current_model()}


def say(text: str) -> None:
    """Озвучка ответа через Windows SAPI (офлайн)."""
    phrase = re.sub(r"[`*#>|]", " ", str(text or ""))[:600]
    if not phrase.strip():
        return
    safe = phrase.replace("'", "''")
    script = ("Add-Type -AssemblyName System.Speech;"
              "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
              "$s.Rate=0;$s.Speak('" + safe + "')")
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         creationflags=NO_WINDOW)
    except OSError as exc:
        diag.warn("voice.py:203", exc)


def make_test_wav(path: str) -> bool:
    """Синтезирует тестовую фразу через SAPI в 16 кГц — для проверки распознавания."""
    phrase = "Проверка связи. Это тест распознавания речи."
    target = str(path).replace("'", "''")
    fmt = ("New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,"
           "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,"
           "[System.Speech.AudioFormat.AudioChannel]::Mono)")
    script = ("Add-Type -AssemblyName System.Speech;"
              "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
              "$f=" + fmt + ";"
              "$s.SetOutputToWaveFile('" + target + "',$f);"
              "$s.Speak('" + phrase + "');"
              "$s.Dispose()")
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, timeout=120, creationflags=NO_WINDOW)
    except Exception:
        return False
    return pathlib.Path(path).exists() and pathlib.Path(path).stat().st_size > 1000

def status() -> dict:
    cli = find_cli()
    path = model_path()
    return {
        "cli": cli,
        "model": str(path) if path.exists() else None,
        "model_name": current_model(),
        "ready": bool(cli) and path.exists(),
        "job": dict(JOB),
        "tts": bool(config.load().get("voice_tts")),
        "models": [{"id": key, "title": value["title"],
                    "installed": (MODELS_DIR / value["file"]).exists()}
                   for key, value in MODELS.items()],
    }
