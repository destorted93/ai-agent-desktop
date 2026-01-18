"""Stylesheet constants for the UI."""


class Styles:
    """UI stylesheet constants."""
    
    # Colors
    BG_DARK = "#1e1e1e"
    BG_MEDIUM = "#2d2d2d"
    BG_LIGHT = "#3d3d3d"
    TEXT_PRIMARY = "#ffffff"
    TEXT_SECONDARY = "#d4d4d4"
    TEXT_MUTED = "#888888"
    ACCENT_BLUE = "#4da6ff"
    ACCENT_BLUE_DARK = "#0e639c"
    ACCENT_RED = "#ff6b6b"
    ACCENT_YELLOW = "#ffcc00"
    ACCENT_GREEN = "#4ec9b0"
    
    # Main window
    MAIN_WINDOW = f"""
        QWidget {{
            background-color: {BG_DARK};
            color: {TEXT_PRIMARY};
        }}
    """
    
    # Text input
    TEXT_INPUT = f"""
        QTextEdit {{
            background-color: {BG_MEDIUM};
            color: {TEXT_PRIMARY};
            border: 1px solid {BG_LIGHT};
            border-radius: 8px;
            padding: 8px;
            font-size: 13px;
        }}
        QTextEdit:focus {{
            border-color: {ACCENT_BLUE};
        }}
    """
    
    # Send button
    SEND_BUTTON = f"""
        QPushButton {{
            background-color: {ACCENT_BLUE_DARK};
            color: white;
            border: none;
            border-radius: 20px;
            font-size: 18px;
        }}
        QPushButton:hover {{
            background-color: #1177bb;
        }}
        QPushButton:disabled {{
            background-color: #444444;
            color: {TEXT_MUTED};
        }}
    """
    
    # User message bubble
    USER_MESSAGE = f"""
        QLabel {{
            background-color: {ACCENT_BLUE_DARK};
            color: white;
            border-radius: 10px;
            padding: 10px;
            font-size: 13px;
        }}
    """
    
    # Assistant message bubble
    ASSISTANT_MESSAGE = f"""
        QLabel {{
            background-color: {BG_MEDIUM};
            color: {TEXT_SECONDARY};
            border-radius: 10px;
            padding: 10px;
            font-size: 13px;
        }}
    """
    
    # Toolbar
    TOOLBAR = f"""
        QWidget {{
            background-color: {BG_MEDIUM};
            border-bottom: 1px solid {BG_LIGHT};
        }}
    """
    
    # Token label
    TOKEN_LABEL = f"""
        QLabel {{
            color: {ACCENT_YELLOW};
            font-size: 13px;
            font-weight: bold;
            background: transparent;
        }}
    """
    
    # Icon button
    ICON_BUTTON = f"""
        QPushButton {{
            background-color: transparent;
            color: {TEXT_MUTED} !important;
            border: none;
            border-radius: 5px;
            font-size: 18px;
            padding: 0px;
        }}
        QPushButton:hover {{
            background-color: rgba(255, 255, 255, 0.1);
            color: {ACCENT_BLUE} !important;
        }}
    """
    
    # Danger icon button
    ICON_BUTTON_DANGER = f"""
        QPushButton {{
            background-color: transparent;
            color: {TEXT_MUTED} !important;
            border: none;
            border-radius: 5px;
            font-size: 18px;
            padding: 0px;
        }}
        QPushButton:hover {{
            background-color: rgba(255, 255, 255, 0.1);
            color: {ACCENT_RED} !important;
        }}
    """
    
    # Code block
    CODE_BLOCK = f"""
        QTextBrowser {{
            background-color: #272822;
            color: {TEXT_SECONDARY};
            border: none;
            border-bottom-left-radius: 6px;
            border-bottom-right-radius: 6px;
            padding: 10px;
            font-family: 'Consolas', 'Monaco', monospace;
            font-size: 12px;
        }}
    """
    
    CODE_HEADER = f"""
        QWidget {{
            background-color: #2a2a2a;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
        }}
    """
    
    # Scroll area
    SCROLL_AREA = f"""
        QScrollArea {{
            border: none;
            background-color: {BG_DARK};
        }}
        QScrollBar:vertical {{
            background-color: {BG_DARK};
            width: 10px;
            margin: 0px;
        }}
        QScrollBar::handle:vertical {{
            background-color: {BG_LIGHT};
            border-radius: 5px;
            min-height: 20px;
        }}
        QScrollBar::handle:vertical:hover {{
            background-color: #555555;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
    """
    
    # Dropdown/combo box
    DROPDOWN = f"""
        QComboBox {{
            background-color: #23272e;
            color: {TEXT_SECONDARY};
            border: 1px solid {BG_LIGHT};
            border-radius: 6px;
            padding: 2px 8px;
            font-size: 13px;
            min-width: 120px;
        }}
        QComboBox QAbstractItemView {{
            background-color: #23272e;
            color: {TEXT_SECONDARY};
            selection-background-color: {ACCENT_BLUE};
            selection-color: white;
        }}
    """
    
    # Floating widget
    FLOATING_WIDGET = f"""
        QWidget {{
            background-color: {BG_MEDIUM};
            border-radius: 10px;
        }}
    """
    
    # Recording indicator
    RECORDING_ACTIVE = f"""
        QPushButton {{
            background-color: {ACCENT_RED};
            color: white;
            border: none;
            border-radius: 25px;
        }}
    """
    
    RECORDING_INACTIVE = f"""
        QPushButton {{
            background-color: {BG_LIGHT};
            color: {TEXT_MUTED};
            border: none;
            border-radius: 25px;
        }}
        QPushButton:hover {{
            background-color: #4a4a4a;
        }}
    """
