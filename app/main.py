from core.config import Settings
from providers.openrouter import OpenRouterProvider
from council.orchestrator import CouncilOrchestrator


def main() -> None:
    settings = Settings.load()
    provider = OpenRouterProvider(api_key=settings.openrouter_api_key)
    council = CouncilOrchestrator(provider=provider, settings=settings)

    print("=" * 70)
    print("AI Council Enterprise v0.1")
    print("Команды: выход / exit / quit")
    print("=" * 70)

    while True:
        prompt = input("\nВаш запрос:\n> ").strip()

        if prompt.lower() in {"выход", "exit", "quit"}:
            print("Работа завершена.")
            break

        if not prompt:
            continue

        try:
            answer = council.ask_single(prompt)
            print("\nОтвет:\n")
            print(answer)
        except Exception as exc:
            print("\nОшибка:")
            print(exc)


if __name__ == "__main__":
    main()
