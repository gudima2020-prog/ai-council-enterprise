from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTextEdit,
    QPushButton,
    QMessageBox,
    QSplitter,
    QListWidget,
    QFrame,
)

from ui.worker import ChatWorker
from council.orchestrator import CouncilOrchestrator


class MainWindow(QMainWindow):
    def __init__(self, council: CouncilOrchestrator) -> None:
        super().__init__()
        self.council = council
        self.worker: ChatWorker | None = None

        self.setWindowTitle("AI Studio Enterprise v0.2")
        self.resize(1150, 760)

        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QHBoxLayout(root)

        splitter = QSplitter(Qt.Horizontal)
        root_layout.addWidget(splitter)

        sidebar = self._build_sidebar()
        chat_area = self._build_chat_area()

        splitter.addWidget(sidebar)
        splitter.addWidget(chat_area)
        splitter.setSizes([270, 880])

        self._apply_style()

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        layout = QVBoxLayout(sidebar)

        title = QLabel("AI Studio")
        title.setObjectName("appTitle")
        layout.addWidget(title)

        subtitle = QLabel("Enterprise v0.2")
        subtitle.setObjectName("subtitle")
        layout.addWidget(subtitle)

        layout.addSpacing(15)
        layout.addWidget(QLabel("Разделы"))

        self.sections = QListWidget()
        self.sections.addItems([
            "💬 Чат",
            "🧠 AI Council",
            "📈 Crypto AI",
            "📄 Документы",
            "💻 Код",
            "⚙ Настройки",
        ])
        self.sections.setCurrentRow(0)
        layout.addWidget(self.sections)

        layout.addStretch()

        note = QLabel("Сейчас активен базовый чат через OpenRouter.")
        note.setWordWrap(True)
        note.setObjectName("note")
        layout.addWidget(note)

        return sidebar

    def _build_chat_area(self) -> QWidget:
        area = QWidget()
        layout = QVBoxLayout(area)

        header = QLabel("Чат")
        header.setObjectName("header")
        layout.addWidget(header)

        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("Здесь появятся ответы модели.")
        layout.addWidget(self.output, 4)

        self.input = QTextEdit()
        self.input.setPlaceholderText("Введите запрос и нажмите Отправить...")
        self.input.setMaximumHeight(130)
        layout.addWidget(self.input, 1)

        buttons = QHBoxLayout()
        self.send_button = QPushButton("Отправить")
        self.clear_button = QPushButton("Очистить")
        self.send_button.clicked.connect(self.send_message)
        self.clear_button.clicked.connect(self.clear_chat)
        buttons.addStretch()
        buttons.addWidget(self.clear_button)
        buttons.addWidget(self.send_button)
        layout.addLayout(buttons)

        return area

    def send_message(self) -> None:
        prompt = self.input.toPlainText().strip()
        if not prompt:
            QMessageBox.warning(self, "Пустой запрос", "Введите текст запроса.")
            return

        self.input.clear()
        self.output.append(f"<b>Вы:</b><br>{prompt}<br>")
        self.output.append("<b>AI:</b><br>Думаю...<br>")
        self.send_button.setEnabled(False)

        self.worker = ChatWorker(self.council, prompt)
        self.worker.finished_ok.connect(self.on_answer)
        self.worker.failed.connect(self.on_error)
        self.worker.start()

    def on_answer(self, answer: str) -> None:
        self.output.append(answer.replace("\n", "<br>"))
        self.output.append("<hr>")
        self.send_button.setEnabled(True)

    def on_error(self, error: str) -> None:
        self.output.append(f"<span style='color:#b00020'><b>Ошибка:</b><br>{error}</span><hr>")
        self.send_button.setEnabled(True)

    def clear_chat(self) -> None:
        self.output.clear()

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow { background: #f5f5f5; }
            #sidebar { background: #202124; color: white; }
            #appTitle { font-size: 24px; font-weight: bold; color: white; }
            #subtitle { color: #cfcfcf; }
            #note { color: #cfcfcf; font-size: 12px; }
            #header { font-size: 22px; font-weight: bold; }
            QListWidget { background: #2d2f31; color: white; border: none; padding: 6px; }
            QListWidget::item { padding: 10px; }
            QListWidget::item:selected { background: #3f51b5; }
            QTextEdit { background: white; border: 1px solid #d0d0d0; border-radius: 6px; padding: 8px; }
            QPushButton { padding: 8px 16px; border-radius: 6px; background: #3f51b5; color: white; }
            QPushButton:disabled { background: #9e9e9e; }
            """
        )
