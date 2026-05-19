from PyQt6.QtWidgets import (
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QLabel,
    QDialog,
    QMessageBox,
    QComboBox,
    QListWidget,
    QListWidgetItem,
    QGroupBox,
)
from PyQt6.QtCore import Qt, pyqtSignal
from ..screen_utils import validate_window_position


class SettingsWindow(QDialog):
    """Settings window for API configuration.

    Pure UI component - does not access storage directly.
    Emits signals for parent (Widget) to forward to App.
    """

    settings_save_requested = pyqtSignal(dict)  # Request to save settings
    settings_load_requested = pyqtSignal()  # Request to load current settings

    # Confluence tokens (per base URL) are persisted immediately via bus.
    confluence_upsert_requested = pyqtSignal(dict)  # {base_url, token}
    confluence_delete_requested = pyqtSignal(str)   # base_url

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI Agent Settings")
        self.setModal(False)
        self.resize(420, 560)
        self.setWindowFlags(Qt.WindowType.Window)

        # In-memory confluence token map: base_url -> token
        self._confluence_tokens = {}

        # Restore last position if available
        from PyQt6.QtCore import QSettings

        settings = QSettings("ai-agent", "widget")
        saved_pos = settings.value("settings_window_pos", None)
        validated_pos = validate_window_position(saved_pos, 420, 560)
        if validated_pos is not None:
            x, y = validated_pos
            self.move(x, y)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        title = QLabel("Settings")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(title)

        desc = QLabel("Configure your provider and credentials. Tokens are stored securely using the OS keychain.")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # --- Runner ---
        mode_label = QLabel("Runner")
        self.mode_combo = QComboBox()
        # Stored values are the config strings.
        self.mode_combo.addItem("Responses", "responses")
        self.mode_combo.addItem("Chat Completions", "chat_completions")
        layout.addWidget(mode_label)
        layout.addWidget(self.mode_combo)

        # --- Base URL + API token ---
        url_label = QLabel("Base URL")
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://api.openai.com/v1")
        layout.addWidget(url_label)
        layout.addWidget(self.url_input)

        token_label = QLabel("API Token")
        self.token_input = QLineEdit()
        self.token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_input.setPlaceholderText("Enter token (leave empty to clear)")
        layout.addWidget(token_label)
        layout.addWidget(self.token_input)

        # --- Confluence tokens ---
        confluence_group = QGroupBox("Confluence Personal Tokens")
        confluence_layout = QVBoxLayout(confluence_group)
        confluence_layout.setContentsMargins(10, 10, 10, 10)
        confluence_layout.setSpacing(8)


        self.confluence_list = QListWidget()
        self.confluence_list.setMaximumHeight(120)
        self.confluence_list.itemSelectionChanged.connect(self._on_confluence_selected)
        confluence_layout.addWidget(self.confluence_list)

        base_label = QLabel("Confluence Base URL")
        self.confluence_base_input = QLineEdit()
        self.confluence_base_input.setPlaceholderText("https://acme.atlassian.net/wiki")
        confluence_layout.addWidget(base_label)
        confluence_layout.addWidget(self.confluence_base_input)

        pat_label = QLabel("Token")
        self.confluence_pat_input = QLineEdit()
        self.confluence_pat_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.confluence_pat_input.setPlaceholderText("Enter token")
        confluence_layout.addWidget(pat_label)
        confluence_layout.addWidget(self.confluence_pat_input)

        confluence_btns = QHBoxLayout()
        self.confluence_add_btn = QPushButton("Add / Update")
        self.confluence_remove_btn = QPushButton("Remove")
        self.confluence_add_btn.clicked.connect(self._on_confluence_upsert)
        self.confluence_remove_btn.clicked.connect(self._on_confluence_remove)
        confluence_btns.addStretch(1)
        confluence_btns.addWidget(self.confluence_add_btn)
        confluence_btns.addWidget(self.confluence_remove_btn)
        confluence_layout.addLayout(confluence_btns)

        layout.addWidget(confluence_group)

        # --- Save/Close ---
        buttons = QHBoxLayout()
        self.save_btn = QPushButton("Save")
        self.close_btn = QPushButton("Close")
        buttons.addStretch(1)
        buttons.addWidget(self.save_btn)
        buttons.addWidget(self.close_btn)
        layout.addLayout(buttons)

        self.save_btn.clicked.connect(self._on_save)
        self.close_btn.clicked.connect(self.close)

    def load_settings(
        self,
        *,
        base_url: str = "",
        api_token: str = "",
        api_mode: str = "responses",
        confluence_tokens=None,
    ):
        """Load settings into UI from parent."""
        self.url_input.setText(base_url)
        self.token_input.setText(api_token)

        # runner selection
        try:
            md = str(api_mode or "").strip().lower()
            if md not in ("responses", "chat_completions"):
                md = "responses"
            for i in range(self.mode_combo.count()):
                if str(self.mode_combo.itemData(i)).strip().lower() == md:
                    self.mode_combo.setCurrentIndex(i)
                    break
        except Exception:
            pass

        # confluence tokens
        self._confluence_tokens = {}
        try:
            if isinstance(confluence_tokens, list):
                for it in confluence_tokens:
                    if not isinstance(it, dict):
                        continue
                    b = str(it.get("base_url") or "").strip()
                    t = str(it.get("token") or "").strip()
                    if b:
                        self._confluence_tokens[b] = t
        except Exception:
            self._confluence_tokens = {}

        self._refresh_confluence_list()


    def _refresh_confluence_list(self):
        self.confluence_list.clear()
        for b in sorted(self._confluence_tokens.keys()):
            self.confluence_list.addItem(QListWidgetItem(b))

    def _on_confluence_selected(self):
        items = self.confluence_list.selectedItems()
        if not items:
            return
        base = items[0].text()
        self.confluence_base_input.setText(base)
        self.confluence_pat_input.setText(self._confluence_tokens.get(base, ""))

    def _on_confluence_upsert(self):
        base = (self.confluence_base_input.text() or "").strip()
        tok = (self.confluence_pat_input.text() or "").strip()
        if not base:
            QMessageBox.warning(self, "Confluence", "Confluence Base URL is required.")
            return
        if not tok:
            QMessageBox.warning(self, "Confluence", "Token is required.")
            return

        # Optimistic UI update, then persist via bus (Widget will handle reply/errors).
        self._confluence_tokens[base] = tok
        self._refresh_confluence_list()

        matches = self.confluence_list.findItems(base, Qt.MatchFlag.MatchExactly)
        if matches:
            self.confluence_list.setCurrentItem(matches[0])

        self.confluence_upsert_requested.emit({"base_url": base, "token": tok})

    def _on_confluence_remove(self):
        items = self.confluence_list.selectedItems()
        if not items:
            return
        base = items[0].text()
        try:
            self._confluence_tokens.pop(base, None)
        except Exception:
            pass
        self._refresh_confluence_list()
        self.confluence_base_input.setText("")
        self.confluence_pat_input.setText("")

        self.confluence_delete_requested.emit(base)

    def _on_save(self):
        """Emit save request with settings data."""
        url = self.url_input.text().strip()
        token = self.token_input.text().strip()

        try:
            api_mode = str(self.mode_combo.currentData() or "responses").strip().lower()
        except Exception:
            api_mode = "responses"

        # Confluence tokens are saved immediately via Add/Update.
        # Save here only applies to core API settings.
        settings = {
            "base_url": url,
            "api_token": token,
            "api_mode": api_mode,
        }
        self.settings_save_requested.emit(settings)

    def show_save_success(self):
        """Show success message after save completes."""
        QMessageBox.information(self, "Settings", "Settings saved successfully.")

    def show_save_error(self, error_message: str):
        """Show error message if save fails."""
        QMessageBox.warning(self, "Settings Error", f"Failed to save settings:\n{error_message}")

    def closeEvent(self, event):
        """Save window position when closing."""
        from PyQt6.QtCore import QSettings

        settings = QSettings("ai-agent", "widget")
        settings.setValue("settings_window_pos", (self.pos().x(), self.pos().y()))
        super().closeEvent(event)

    def showEvent(self, event):
        """Restore window position when showing."""
        from PyQt6.QtCore import QSettings

        settings = QSettings("ai-agent", "widget")
        saved_pos = settings.value("settings_window_pos", None)
        validated_pos = validate_window_position(saved_pos, 420, 560)
        if validated_pos is not None:
            x, y = validated_pos
            self.move(x, y)
        super().showEvent(event)
