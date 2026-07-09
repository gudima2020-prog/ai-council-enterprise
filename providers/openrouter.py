from openai import OpenAI
from providers.base import AIProvider


class OpenRouterProvider(AIProvider):
    def __init__(self, api_key: str) -> None:
        self.client = OpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            default_headers={
                "HTTP-Referer": "https://github.com/gudima2020-prog/ai-council-enterprise",
                "X-Title": "AI Council Enterprise",
            },
        )

    def ask(self, *, model: str, system_prompt: str, user_prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.4,
        )
        return response.choices[0].message.content or ""
