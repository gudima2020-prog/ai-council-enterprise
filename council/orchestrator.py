from core.config import Settings
from core.logger import save_text_log
from council.prompts import UNIVERSAL_SYSTEM_PROMPT
from providers.base import AIProvider


class CouncilOrchestrator:
    def __init__(self, provider: AIProvider, settings: Settings) -> None:
        self.provider = provider
        self.settings = settings

    def ask_single(self, prompt: str) -> str:
        answer = self.provider.ask(
            model=self.settings.default_model,
            system_prompt=UNIVERSAL_SYSTEM_PROMPT,
            user_prompt=prompt,
        )

        save_text_log(
            "single",
            f"MODEL: {self.settings.default_model}\n\nPROMPT:\n{prompt}\n\nANSWER:\n{answer}\n",
        )

        return answer
