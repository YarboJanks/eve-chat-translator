import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "ChatTranslator"
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULTS = {
    "language": "en",  # the user's language: "en" reads Chinese chat in English, "zh" the reverse
    "region": None,  # [x, y, w, h] in global logical (Qt) coordinates
    "interval_ms": 1000,
    "backend": "google",  # "google" | "baidu" | "claude"
    "claude_model": "claude-opus-5-5",
    "anthropic_api_key": "",
    "baidu_appid": "",
    "baidu_key": "",
    "translate_all": False,  # False: only lines mostly in the other language
    "show_original": True,
    "show_outline": True,
    "font_size": 14,
    "opacity": 0.8,
    "ocr_min_score": 0.6,
    "upscale": 1.0,
    "max_messages": 200,
    "panel_geometry": None,  # [x, y, w, h]
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return cfg


def save(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
