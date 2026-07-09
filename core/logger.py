from datetime import datetime
from pathlib import Path
from core.config import LOG_DIR


def save_text_log(prefix: str, content: str) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    safe_prefix = prefix.replace(" ", "_").lower()
    path = LOG_DIR / f"{safe_prefix}_{stamp}.txt"
    path.write_text(content, encoding="utf-8")
    return path
