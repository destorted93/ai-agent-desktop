"""Input components for the UI."""

from PyQt6.QtWidgets import QTextEdit
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent

from ..styles import Styles


class MultilineInput(QTextEdit):
    """Custom input that sends on Enter, newline on Shift+Enter."""
    
    send_message = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setPlaceholderText("Type your message or drag & drop files...")
        
        # Word wrap
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        # Height constraints
        self.base_height = 40
        self.max_lines = 10
        self.line_height = self.fontMetrics().lineSpacing()
        self.max_height = self.base_height + (self.line_height * (self.max_lines - 1))
        
        self.setFixedHeight(self.base_height)
        self.textChanged.connect(self._adjust_height)
        
        self.setStyleSheet(Styles.TEXT_INPUT)
    
    def _adjust_height(self):
        """Adjust height based on content."""
        doc_height = int(self.document().size().height())
        new_height = doc_height + 16  # Add padding
        new_height = max(self.base_height, min(new_height, self.max_height))
        
        if new_height != self.height():
            self.setFixedHeight(new_height)
    
    def keyPressEvent(self, event: QKeyEvent):
        """Handle Enter/Shift+Enter."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
            else:
                self.send_message.emit()
                event.accept()
        else:
            super().keyPressEvent(event)
    
    def clear_text(self):
        """Clear content and reset height."""
        self.clear()
        self.setFixedHeight(self.base_height)
