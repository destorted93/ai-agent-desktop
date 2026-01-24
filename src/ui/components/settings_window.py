from PyQt6.QtWidgets import QPushButton, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel, QDialog, QMessageBox
from PyQt6.QtCore import Qt, pyqtSignal


class SettingsWindow(QDialog):
    """Settings window for API configuration."""
    settings_saved = pyqtSignal(dict)
    
    def __init__(self, parent=None, secure_storage=None):
        super().__init__(parent)
        self.secure_storage = secure_storage
        self.setWindowTitle("AI Agent Settings")
        self.setModal(False)
        self.resize(380, 260)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel("Settings")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(title)

        desc = QLabel("Configure your provider and credentials. Tokens are stored securely using the OS keychain.")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        url_label = QLabel("Base URL")
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://api.openai.com/v1")
        layout.addWidget(url_label)
        layout.addWidget(self.url_input)

        token_label = QLabel("API Token")
        self.token_input = QLineEdit()
        self.token_input.setPlaceholderText("Enter token (leave empty to clear)")
        layout.addWidget(token_label)
        layout.addWidget(self.token_input)

        buttons = QHBoxLayout()
        self.save_btn = QPushButton("Save")
        self.close_btn = QPushButton("Close")
        buttons.addStretch(1)
        buttons.addWidget(self.save_btn)
        buttons.addWidget(self.close_btn)
        layout.addLayout(buttons)

        self._load_settings()
        self.save_btn.clicked.connect(self._on_save)
        self.close_btn.clicked.connect(self.close)
        layout.addStretch(1)
    
    def _load_settings(self):
        if self.secure_storage:
            existing_base_url = self.secure_storage.get_config_value("base_url", "")
            if existing_base_url:
                self.url_input.setText(existing_base_url)
            existing_token = self.secure_storage.get_secret("api_token")
            if existing_token:
                self.token_input.setText(existing_token)

    def _on_save(self):
        url = self.url_input.text().strip()
        token = self.token_input.text().strip()
        settings = {"base_url": url, "api_token": token}
        
        if self.secure_storage:
            self.secure_storage.set_config_value("base_url", url)
            if token:
                self.secure_storage.set_secret("api_token", token)
            else:
                self.secure_storage.delete_secret("api_token")
        
        self.settings_saved.emit(settings)
        QMessageBox.information(self, "Settings", "Settings saved successfully.")