from abc import ABC, abstractmethod


class AIProvider(ABC):
    @abstractmethod
    def ask(self, *, model: str, system_prompt: str, user_prompt: str) -> str:
        raise NotImplementedError
