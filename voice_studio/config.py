"""Settings: defaults, overridden by <data>/config.json. Missing tools only switch their feature off."""
import json
import os
import shutil
import sys

HOME = os.path.expanduser("~")
SKILLS = os.path.join(HOME, ".claude", "skills")

DEFAULTS = {
    "host": "127.0.0.1",
    "port": 8765,
    "language": "hi",            # Whisper language code
    "vocab_prompt": "",          # names and terms Whisper should spell right
    "preferred_mic": "",         # regex on the device label, picked when no mic was chosen yet
    "ffmpeg": "ffmpeg",
    "ffprobe": "ffprobe",
    "whisper_python": os.path.join(os.environ.get("APPDATA", ""), "uv", "tools", "video-timeline-copilot", "Scripts", "python.exe"),
    "whisper_scripts": os.path.join(SKILLS, "video-edit", "scripts"),
    "enhance_python": sys.executable,
    "enhance_script": os.path.join(SKILLS, "voice-enhance", "scripts", "enhance.py"),
    "max_video_mb": 8192,
    "max_take_mb": 2048,
}
LOCAL_HOSTS = ("127.0.0.1", "localhost")  # the server is IPv4 only


def default_data_dir():
    return os.environ.get("VOICE_STUDIO_DATA") or os.path.join(HOME, "VoiceStudio")


def load(data_dir):
    cfg = dict(DEFAULTS)
    try:
        with open(os.path.join(data_dir, "config.json"), encoding="utf-8") as f:
            user = json.load(f)
        cfg.update({k: v for k, v in user.items() if k in DEFAULTS})
    except FileNotFoundError:
        pass
    except ValueError as e:
        raise SystemExit("config.json is not valid JSON: %s" % e)
    if cfg["host"] not in LOCAL_HOSTS:
        # takes, scripts and the file-system actions must never be reachable from the network
        raise SystemExit("host must be one of %s" % ", ".join(LOCAL_HOSTS))
    return cfg


def capabilities(cfg):
    """Which optional features can run on this machine."""
    w = cfg["whisper_scripts"]
    return {
        "ffmpeg": bool(shutil.which(cfg["ffmpeg"]) and shutil.which(cfg["ffprobe"])),
        "transcribe": os.path.isfile(cfg["whisper_python"]) and os.path.isfile(os.path.join(w, "whisper_gpu.py")),
        "clean": os.path.isfile(cfg["enhance_python"]) and os.path.isfile(cfg["enhance_script"]),
    }
