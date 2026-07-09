from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv
import os


ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"
LOG_DIR = ROOT_DIR / "logs"
DATA_DIR = ROOT_DIR / "data"

LOG_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)


@dataclass(frozen=True)
class Settings:
    openrouter_api_key: str
    default_model: str = "deepseek/deepseek-chat-v3-0324"
    language: str = "ru"

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv(ENV_PATH)

        api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        default_model = os.getenv(
            "OPENROUTER_DEFAULT_MODEL",
            "deepseek/deepseek-chat-v3-0324",
        ).strip()

        if not api_key:
            raise RuntimeError(
                "Не найден OPENROUTER_API_KEY. "
                "Создайте файл .env на основе .env.example и вставьте ключ OpenRouter."
            )

        return cls(
            openrouter_api_key=api_key,
            default_model=default_model,
        )
