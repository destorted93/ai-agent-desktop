"""Screenshot selection component."""

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QRect, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPen, QPixmap


class ScreenshotSelector(QWidget):
    """Fullscreen overlay for selecting a screen region."""
    
    screenshot_selected = pyqtSignal(QPixmap)
    screenshot_cancelled = pyqtSignal()
    
    def __init__(self, screenshot: QPixmap):
        super().__init__()
        self.screenshot = screenshot
        self.start_pos = None
        self.end_pos = None
        self.selecting = False
        
        # Fullscreen transparent overlay
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
    
    def paintEvent(self, event):
        """Draw screenshot with selection overlay."""
        painter = QPainter(self)
        
        # Draw screenshot
        painter.drawPixmap(0, 0, self.screenshot)
        
        # Semi-transparent overlay
        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))
        
        # Selection rectangle
        if self.start_pos and self.end_pos:
            rect = QRect(self.start_pos, self.end_pos).normalized()
            
            # Clear selection area
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.fillRect(rect, Qt.GlobalColor.transparent)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.drawPixmap(rect.topLeft(), self.screenshot, rect)
            
            # Border
            pen = QPen(QColor(0, 150, 255), 2, Qt.PenStyle.SolidLine)
            painter.setPen(pen)
            painter.drawRect(rect)
            
            # Dimensions
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(rect.x(), rect.y() - 5, f"{rect.width()}x{rect.height()}")
    
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_pos = event.pos()
            self.end_pos = event.pos()
            self.selecting = True
            self.update()
    
    def mouseMoveEvent(self, event):
        if self.selecting:
            self.end_pos = event.pos()
            self.update()
    
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.selecting:
            self.selecting = False
            self.end_pos = event.pos()
            
            rect = QRect(self.start_pos, self.end_pos).normalized()
            
            if rect.width() > 10 and rect.height() > 10:
                selected = self.screenshot.copy(rect)
                self.screenshot_selected.emit(selected)
            else:
                self.screenshot_cancelled.emit()
            
            self.close()
    
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.screenshot_cancelled.emit()
            self.close()
