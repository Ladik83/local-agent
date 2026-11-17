"""Проверка распознавания: синтезируем фразу SAPI и прогоняем через whisper."""
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import config, voice  # noqa: E402

FORMAT = ("New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,"
          "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,"
          "[System.Speech.AudioFormat.AudioChannel]::Mono)")


def synth(text: str, path: pathlib.Path) -> int:
    script = ("Add-Type -AssemblyName System.Speech;"
              "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
              "$f=" + FORMAT + ";"
              "$s.SetOutputToWaveFile('" + str(path) + "',$f);"
              "$s.Speak('" + text + "');"
              "$s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True)
    return path.stat().st_size if path.exists() else 0


base = pathlib.Path(os.environ["LOCALAPPDATA"]) / "LocalAgent"
english = base / "check_en.wav"
print("english wav bytes:", synth("Check the connection. This is a speech recognition test.", english))
for name in ("turbo", "base"):
    config.save({"voice_model": name})
    result = voice.transcribe(str(english), language="en")
    print(name, "->", result)
config.save({"voice_model": "turbo"})
