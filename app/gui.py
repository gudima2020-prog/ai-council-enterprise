import sys

from PySide6.QtWidgets import QApplication

from core.config import Settings
from providers.openrouter import OpenRouterProvider
from council.orchestrator import CouncilOrchestrator
from ui.main_window import MainWindow


def main() -> None:
    settings = Settings.load()
    provider = OpenRouterProvider(api_key=settings.openrouter_api_key)
    council = CouncilOrchestrator(provider=provider, settings=settings)

    app = QApplication(sys.argv)
    window = MainWindow(council=council)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
