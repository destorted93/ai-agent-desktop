"""Message display components."""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextBrowser, QApplication
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from ..styles import Styles


class MessageBubble(QWidget):
    """Chat message bubble widget."""
    
    def __init__(self, text: str, is_user: bool = False, parent=None):
        super().__init__(parent)
        self.text = text
        self.is_user = is_user
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        if self.is_user:
            # Right-aligned user message
            layout.addStretch(1)
        
        # Message content
        msg_label = QLabel(self.text)
        msg_label.setWordWrap(True)
        msg_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        msg_label.setStyleSheet(
            Styles.USER_MESSAGE if self.is_user else Styles.ASSISTANT_MESSAGE
        )
        
        layout.addWidget(msg_label, 4)
        
        if not self.is_user:
            # Left-aligned assistant message
            layout.addStretch(1)


class CodeBlockWidget(QWidget):
    """Code block with syntax highlighting and copy button."""
    
    def __init__(self, code: str, language: str = "", parent=None):
        super().__init__(parent)
        self.code = code
        self.language = language
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 5, 0, 5)
        layout.setSpacing(0)
        
        # Header with language and copy button
        header = QWidget()
        header.setStyleSheet(Styles.CODE_HEADER)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 5, 10, 5)
        
        lang_label = QLabel(self.language.upper() if self.language else "CODE")
        lang_label.setStyleSheet("color: #888; font-size: 11px; font-weight: bold;")
        
        copy_btn = QPushButton("Copy")
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.clicked.connect(self._copy_code)
        copy_btn.setStyleSheet("""
            QPushButton {
                background-color: #3a3a3a;
                color: #d4d4d4;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 4px 12px;
                font-size: 11px;
            }
            QPushButton:hover { background-color: #4a4a4a; }
        """)
        
        header_layout.addWidget(lang_label)
        header_layout.addStretch()
        header_layout.addWidget(copy_btn)
        
        # Code display
        code_display = QTextBrowser()
        code_display.setReadOnly(True)
        code_display.setFont(QFont("Consolas", 10))
        code_display.setStyleSheet(Styles.CODE_BLOCK)
        code_display.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        code_display.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        
        # Apply syntax highlighting
        highlighted = self._highlight_code()
        code_display.setHtml(f"<pre style='margin:0;'>{highlighted}</pre>")
        
        # Calculate height
        doc_height = int(code_display.document().size().height()) + 20
        max_height = 400
        code_display.setFixedHeight(min(doc_height, max_height))
        
        layout.addWidget(header)
        layout.addWidget(code_display)
    
    def _highlight_code(self) -> str:
        """Apply syntax highlighting to code."""
        try:
            from pygments import highlight
            from pygments.lexers import get_lexer_by_name, guess_lexer
            from pygments.formatters import HtmlFormatter
            
            if self.language:
                try:
                    lexer = get_lexer_by_name(self.language)
                except Exception:
                    lexer = guess_lexer(self.code)
            else:
                lexer = guess_lexer(self.code)
            
            formatter = HtmlFormatter(nowrap=True, style="monokai")
            return highlight(self.code, lexer, formatter)
        except Exception:
            # Fallback to plain text
            import html
            return html.escape(self.code)
    
    def _copy_code(self):
        """Copy code to clipboard."""
        QApplication.clipboard().setText(self.code)
