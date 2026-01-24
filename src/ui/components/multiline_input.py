from PyQt6.QtWidgets import QApplication, QTextEdit
from PyQt6.QtGui import QKeyEvent,  QPixmap
from PyQt6.QtCore import Qt, QEvent, pyqtSignal


class MultilineInput(QTextEdit):
    """Custom QTextEdit that sends message on Enter and adds newline on Shift+Enter."""
    send_message = pyqtSignal()
    paste_image = pyqtSignal(QPixmap)  # New signal for pasted images
    paste_files = pyqtSignal(list)     # New signal for pasted files
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setPlaceholderText("Type your message or drag & drop files...")
        
        # Enable word wrap
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        # Set fixed comfortable height matching original QLineEdit (with padding from stylesheet)
        self.base_height = 40  # Standard single-line input height
        self.max_lines = 10
        
        font_metrics = self.fontMetrics()
        self.line_height = font_metrics.lineSpacing()
        self.max_height = self.base_height + (self.line_height * (self.max_lines - 1))
        
        self.setFixedHeight(self.base_height)
        
        # Connect to textChanged to adjust height dynamically
        self.textChanged.connect(self.adjust_height)
        
    def adjust_height(self):
        """Adjust height based on content, up to max_lines."""
        # Get document height and calculate needed height
        doc_height = int(self.document().size().height())
        
        # Add padding (16px total - 8px top + 8px bottom from stylesheet)
        new_height = doc_height + 16
        
        # Constrain between base and max height
        new_height = max(self.base_height, min(new_height, self.max_height))
        
        if new_height != self.height():
            self.setFixedHeight(new_height)
        
    def keyPressEvent(self, event: QKeyEvent):
        """Handle Enter and Shift+Enter differently, plus Ctrl+V for paste."""
        # Handle Ctrl+V for paste
        if event.key() == Qt.Key.Key_V and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self.handle_paste()
            event.accept()
            return
        
        if event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter:
            if event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
                # Shift+Enter: insert newline
                super().keyPressEvent(event)
            else:
                # Plain Enter: send message
                self.send_message.emit()
                event.accept()
        else:
            super().keyPressEvent(event)
    
    def handle_paste(self):
        """Handle paste event - check for images or files in clipboard."""
        clipboard = QApplication.clipboard()
        mime_data = clipboard.mimeData()
        
        # Check for image data first (highest priority)
        if mime_data.hasImage():
            image = clipboard.image()
            if not image.isNull():
                pixmap = QPixmap.fromImage(image)
                self.paste_image.emit(pixmap)
                return
        
        # Check for file URLs (from file explorer)
        if mime_data.hasUrls():
            urls = mime_data.urls()
            file_paths = []
            for url in urls:
                if url.isLocalFile():
                    path = url.toLocalFile()
                    file_paths.append(path)
            
            if file_paths:
                self.paste_files.emit(file_paths)
                return
        
        # If no image or files, handle as normal text paste
        if mime_data.hasText():
            super().keyPressEvent(QKeyEvent(
                QEvent.Type.KeyPress,
                Qt.Key.Key_V,
                Qt.KeyboardModifier.ControlModifier
            ))
    
    def clear_text(self):
        """Clear the text content."""
        self.clear()
        # Reset to base height
        self.setFixedHeight(self.base_height)