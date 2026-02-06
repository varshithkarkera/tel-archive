"""Configuration management"""
import json
from pathlib import Path

WORKSPACE_DIR = Path.cwd() / "archive"
WORKSPACE_DIR.mkdir(exist_ok=True)  # Create archive folder if it doesn't exist
CONFIG_FILE = Path.cwd() / "dailyarchive_config.json"  # Config stays in root
FFMPEG_CRF = 28

DEFAULT_CONFIG = {
    "password": "",
    "telegram_api_id": "",
    "telegram_api_hash": "",
    "upload_destination": "me",
    "split_size_mb": 2000,
    "first_run": True,
    "upload_caption": "detailed",
    "video_keep_audio": True,
    "cpu_preset": "normal",
    "cpu_threads": 0,
    "parallel_connections": 20
}

# Map friendly names to ffmpeg preset names
PRESET_MAP = {
    "fastest": "ultrafast",
    "fast": "superfast",
    "normal": "veryfast"
}

def get_ffmpeg_preset(friendly_name):
    """Convert friendly preset name to ffmpeg preset name"""
    return PRESET_MAP.get(friendly_name, "veryfast")

config = {}

def load_config():
    global config
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, 'r') as f:
            loaded = json.load(f)
            config.clear()
            config.update(loaded)
    else:
        config.clear()
        config.update(DEFAULT_CONFIG)
    
    # Ensure workspace folder exists
    WORKSPACE_DIR.mkdir(exist_ok=True)
    
    return config

def save_config():
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)
    
    # Ensure workspace folder exists
    WORKSPACE_DIR.mkdir(exist_ok=True)
