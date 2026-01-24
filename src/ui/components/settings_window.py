from PyQt6.QtWidgets import QPushButton, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel, QDialog, QMessageBox
from PyQt6.QtCore import Qt, pyqtSignal


class SettingsWindow(QDialog):
    """Settings window for API configuration.
    
    Pure UI component - does not access storage directly.
    Emits signals for parent (Widget) to forward to App.
    """
    settings_save_requested = pyqtSignal(dict)  # Request to save settings
    settings_load_requested = pyqtSignal()  # Request to load current settings
    
    def __init__(self, parent=None):
        super().__init__(parent)
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

        self.save_btn.clicked.connect(self._on_save)
        self.close_btn.clicked.connect(self.close)
        layout.addStretch(1)
    
    def load_settings(self, base_url: str = "", api_token: str = ""):
        """Load settings into UI from parent.
        
        Args:
            base_url: Base URL to display
            api_token: API token to display
        """
        self.url_input.setText(base_url)
        self.token_input.setText(api_token)
    
    def _on_save(self):
        """Emit save request with settings data."""
        url = self.url_input.text().strip()
        token = self.token_input.text().strip()
        settings = {"base_url": url, "api_token": token}
        self.settings_save_requested.emit(settings)
    
    def show_save_success(self):
        """Show success message after save completes."""
        QMessageBox.information(self, "Settings", "Settings saved successfully.")
    
    def show_save_error(self, error_message: str):
        """Show error message if save fails."""
        QMessageBox.warning(self, "Settings Error", f"Failed to save settings:\n{error_message}")