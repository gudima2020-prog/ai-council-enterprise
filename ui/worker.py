from PySide6.QtCore import QThread, Signal

from council.orchestrator import CouncilOrchestrator


class ChatWorker(QThread):
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, council: CouncilOrchestrator, prompt: str) -> None:
        super().__init__()
        self.council = council
        self.prompt = prompt

    def run(self) -> None:
        try:
            answer = self.council.ask_single(self.prompt)
            self.finished_ok.emit(answer)
        except Exception as exc:
            self.failed.emit(str(exc))
