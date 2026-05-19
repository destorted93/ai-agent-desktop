from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QTabWidget,
    QScrollArea,
    QGridLayout,
    QToolButton,
    QLabel,
)


@dataclass(frozen=True)
class EmojiCategory:
    key: str
    label: str
    icon: str
    emojis: List[str]


# Curated set (no dependency). Add more anytime.
_EMOJI_CATEGORIES: List[EmojiCategory] = [
    EmojiCategory(
        key="smileys",
        label="Smileys",
        icon="🙂",
        emojis=[
            "😀", "😃", "😄", "😁", "😆", "😅", "😂", "🤣",
            "😊", "🙂", "😉", "😌", "😍", "🥰", "😘", "😗",
            "😙", "😚", "😋", "😛", "😜", "🤪", "😝", "🤑",
            "🤗", "🤭", "🤫", "🤔", "😐", "😑", "😶", "🫥",
            "😏", "😒", "🙄", "😬", "😮‍💨", "😴", "🤤", "😪",
            "😵", "😵‍💫", "🤯", "😳", "🥺", "😭", "😤", "😠",
            "😡", "🤬", "😱", "😨", "😰", "😥", "😓", "🤧",
            "🤒", "🤕", "🤢", "🤮", "😇", "🤠", "🥸", "😎",
        ],
    ),
    EmojiCategory(
        key="people",
        label="People & Gestures",
        icon="🫶",
        emojis=[
            "👍", "👎", "👌", "🤌", "🤏", "✌️", "🤞", "🤟",
            "🤘", "🤙", "👋", "🖐️", "✋", "🫱", "🫲", "🫳",
            "🫴", "👏", "🙌", "🫶", "🙏", "💪", "🦾", "🧠",
            "🫀", "🫁", "👀", "👁️", "🫦", "👄", "💋", "🦷",
            "🧍", "🧎", "🏃", "💃", "🕺", "🧘", "🛌", "🧑‍💻",
        ],
    ),
    EmojiCategory(
        key="animals",
        label="Animals & Nature",
        icon="🐺",
        emojis=[
            "🐶", "🐱", "🐭", "🐹", "🐰", "🦊", "🐻", "🐼",
            "🐻‍❄️", "🐨", "🐯", "🦁", "🐮", "🐷", "🐸", "🐵",
            "🐔", "🐧", "🐦", "🦉", "🦅", "🦇", "🐺", "🦄",
            "🐝", "🦋", "🐌", "🐞", "🦂", "🐢", "🐍", "🦎",
            "🐙", "🦑", "🐠", "🐬", "🦈", "🐳", "🦭", "🦩",
            "🌵", "🌲", "🌳", "🌿", "🍀", "🌸", "🌙", "⭐",
            "✨", "⚡", "🔥", "💧", "🌊",
        ],
    ),
    EmojiCategory(
        key="food",
        label="Food & Drink",
        icon="🍕",
        emojis=[
            "☕", "🫖", "🍵", "🥤", "🧃", "🧊", "🍺", "🍷",
            "🥐", "🥖", "🍞", "🧀", "🥚", "🥓", "🥞", "🧇",
            "🍔", "🍟", "🍕", "🌭", "🥪", "🌮", "🌯", "🥙",
            "🍜", "🍲", "🍣", "🍱", "🍛", "🍝", "🥗", "🍿",
            "🍎", "🍌", "🍇", "🍉", "🍓", "🍒", "🥝", "🍍",
            "🍑", "🍊", "🥑", "🍅", "🥕", "🌶️", "🥦", "🧄",
            "🧅", "🍰", "🧁", "🍩", "🍪", "🍫", "🍬",
        ],
    ),
    EmojiCategory(
        key="travel",
        label="Travel & Places",
        icon="🗺️",
        emojis=[
            "🚗", "🚕", "🚙", "🚌", "🚎", "🚓", "🚑", "🚒",
            "🚜", "🏍️", "🚲", "🛴", "🚂", "🚆", "🚇", "✈️",
            "🛫", "🛬", "🛩️", "🚀", "🛸", "🚁", "⛵", "🚢",
            "🗺️", "🧭", "🏕️", "🏝️", "🏜️", "🏙️", "🌋", "🗽",
            "🗼", "🏰", "⛩️", "🕌", "🏟️", "🎡", "🎢",
        ],
    ),
    EmojiCategory(
        key="objects",
        label="Objects",
        icon="🧰",
        emojis=[
            "📎", "📌", "🧷", "🧲", "🧰", "🪛", "🔧", "🔨",
            "🪓", "🗡️", "🛡️", "🧪", "🧫", "🧬", "💻", "🖥️",
            "⌨️", "🖱️", "📱", "📷", "🎥", "🎬", "🎧", "📚",
            "📝", "✏️", "🖊️", "🗒️", "📦", "🗑️", "🔒", "🔓",
            "🔑", "💡", "🕯️", "⏱️", "⏰", "📡", "🧯",
        ],
    ),
    EmojiCategory(
        key="symbols",
        label="Symbols",
        icon="❤️",
        emojis=[
            "❤️", "🧡", "💛", "💚", "💙", "💜", "🖤", "🤍",
            "🤎", "💔", "❤️‍🔥", "❤️‍🩹", "💖", "💗", "💓", "💞",
            "💯", "✅", "☑️", "❌", "⚠️", "❗", "❓", "💤",
            "✨", "🔥", "💥", "💫", "🎯", "🏁", "🔺", "🔻",
            "🟢", "🟡", "🔴", "⚫", "⚪", "🧿", "☠️",
        ],
    ),
]


class EmojiPickerWidget(QWidget):
    """A chat-app-ish emoji picker: tabs (categories) + grid + no ugly borders."""

    emojiSelected = pyqtSignal(str)

    def __init__(
        self,
        recent_emojis: Optional[List[str]] = None,
        max_recent: int = 24,
        cols: int = 8,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._cols = int(cols) if cols else 8
        self._max_recent = int(max_recent) if max_recent else 24
        self._recent: List[str] = list(recent_emojis or [])

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setUsesScrollButtons(True)
        root.addWidget(self.tabs)

        self._recent_tab_index = -1
        self._recent_grid_host: Optional[QWidget] = None

        self._build_tabs()

        # Size: enough to feel like a real picker, not a sad little stamp.
        self.setMinimumSize(360, 260)

        self.setStyleSheet(
            ""
            "QWidget { background-color: #1e1e1e; }"
            "QTabWidget::pane { border: none; }"
            "QTabBar::tab { background: transparent; color: #b5b5b5; padding: 6px 10px; }"
            "QTabBar::tab:selected { color: white; background-color: rgba(255,255,255,0.06); border-radius: 8px; }"
            "QTabBar::tab:hover { color: white; }"
            "QScrollArea { border: none; background: transparent; }"
            "QToolButton { background: transparent; border: none; font-size: 16pt; }"
            "QToolButton:hover { background-color: rgba(255,255,255,0.08); border-radius: 8px; }"
            "QToolButton:pressed { background-color: rgba(77,166,255,0.22); border-radius: 8px; }"
            ""
        )

    def set_recent_emojis(self, recent_emojis: List[str]) -> None:
        self._recent = list(recent_emojis or [])[: self._max_recent]
        self._rebuild_recent_tab()

    def _build_tabs(self) -> None:
        self.tabs.clear()

        # Recent
        self._recent_tab_index = self.tabs.addTab(self._make_category_page(self._recent), "🕘")
        self.tabs.setTabToolTip(self._recent_tab_index, "Recent")

        # Categories
        for cat in _EMOJI_CATEGORIES:
            idx = self.tabs.addTab(self._make_category_page(cat.emojis), cat.icon)
            self.tabs.setTabToolTip(idx, cat.label)

        # Default tab: if Recent is empty, jump straight to Smileys.
        self.tabs.setCurrentIndex(0 if self._recent else 1)

    def _rebuild_recent_tab(self) -> None:
        if self._recent_tab_index < 0:
            return
        # Replace the whole page widget (simple + avoids layout surgery).
        self.tabs.removeTab(self._recent_tab_index)
        self._recent_tab_index = self.tabs.insertTab(0, self._make_category_page(self._recent), "🕘")
        self.tabs.setTabToolTip(self._recent_tab_index, "Recent")

        # Same idea: if no recents, don't land them on an empty tab.
        self.tabs.setCurrentIndex(0 if self._recent else 1)

    def _make_category_page(self, emojis: List[str]) -> QWidget:
        emojis = list(emojis or [])

        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        if not emojis:
            lbl = QLabel("No recent emojis")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color: #6f6f6f; font-size: 9pt;")
            outer.addWidget(lbl, 1)
            return page

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setSpacing(4)

        # Grid
        for i, em in enumerate(emojis):
            btn = QToolButton()
            btn.setText(em)
            btn.setAutoRaise(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedSize(34, 34)
            btn.clicked.connect(partial(self._emit_selected, em))
            grid.addWidget(btn, i // self._cols, i % self._cols)

        scroll.setWidget(grid_host)
        outer.addWidget(scroll)
        return page

    def _emit_selected(self, emoji: str, _checked: bool = False) -> None:
        self.emojiSelected.emit(emoji)
