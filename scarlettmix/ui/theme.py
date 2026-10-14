"""Look of the application: spacing, colours and one stylesheet.

Colours that carry meaning (mute, solo, monitor states, unsaved) are fixed;
everything else is derived from the desktop's palette, so the window follows
a light or dark desktop theme.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# Spacing scale and corner radius, in pixels.
S1, S2, S3, S4 = 4, 8, 12, 16
RADIUS = 6

MUTE = "#e5534b"
SOLO = "#d4a72c"
LINK = "#4f8fd9"
DIM = "#d4a72c"
MONITOR_BLUE = "#4a90d9"
MONITOR_SILVER = "#a3a8ae"
UNSAVED = "#d9822b"
OK = "#3fa34d"
WARN_BG = "#f6e3a8"


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(round(a.red() + (b.red() - a.red()) * t),
                  round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t))


def is_dark(pal: QPalette | None = None) -> bool:
    pal = pal or QApplication.palette()
    return pal.color(QPalette.Window).lightness() < 128


def colors(pal: QPalette | None = None) -> dict[str, str]:
    """Named colours for the current palette."""
    pal = pal or QApplication.palette()
    window = pal.color(QPalette.Window)
    text = pal.color(QPalette.WindowText)
    base = pal.color(QPalette.Base)
    dark = is_dark(pal)
    return {
        "window": window.name(),
        "text": text.name(),
        "muted": _mix(text, window, 0.45).name(),
        "card": _mix(window, base, 0.55 if dark else 0.7).name(),
        "border": _mix(text, window, 0.82 if dark else 0.86).name(),
        "control": _mix(window, base, 0.85).name(),
        "hover": _mix(window, text, 0.08).name(),
        "accent": pal.color(QPalette.Highlight).name(),
        "accent_text": pal.color(QPalette.HighlightedText).name(),
        "warn_bg": "#5a4a1c" if dark else WARN_BG,
        "warn_text": "#f6e3a8" if dark else "#3d3200",
        "notice_bg": "#6b4417" if dark else "#f7d2a6",
        "notice_text": "#fde3c3" if dark else "#3d2200",
    }


def stylesheet(pal: QPalette | None = None) -> str:
    c = colors(pal)
    return f"""
QMainWindow, QDialog {{ background: {c['window']}; }}

/* Cards: the panels that group controls */
QFrame#card, QFrame#strip {{
    background: {c['card']}; border: 1px solid {c['border']}; border-radius: {RADIUS + 2}px;
}}
QLabel#sectionTitle {{ color: {c['muted']}; font-weight: 600; }}
QLabel#caption {{ color: {c['muted']}; }}
QLabel#deviceName {{ font-weight: 700; }}
QLabel#knobValue {{ font-weight: 700; }}
QFrame#chip {{
    background: {c['card']}; border: 1px solid {c['border']}; border-radius: 11px;
}}

/* Buttons and selectors */
QPushButton, QToolButton {{
    background: {c['control']}; border: 1px solid {c['border']}; border-radius: {RADIUS}px;
    padding: 3px 10px; min-height: 18px;
}}
QPushButton:hover, QToolButton:hover {{ background: {c['hover']}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {c['muted']}; }}
QToolButton::menu-indicator {{ subcontrol-position: right center; right: 4px; }}
QToolButton[popupMode="2"] {{ padding-right: 18px; }}
QPushButton#primary {{
    background: {c['accent']}; color: {c['accent_text']}; border: 1px solid {c['accent']};
    font-weight: 600; padding: 3px 14px;
}}
QPushButton#primary:hover {{ background: {_mix(QColor(c['accent']), QColor('#ffffff'), 0.12).name()}; }}
QPushButton#flat {{ background: transparent; border: 1px solid transparent; }}
QPushButton#flat:hover {{ background: {c['hover']}; }}
QPushButton#segment {{
    border-radius: 0; margin: 0; padding: 3px 12px; border-right-width: 0;
}}
QPushButton#segment[first="true"] {{
    border-top-left-radius: {RADIUS}px; border-bottom-left-radius: {RADIUS}px;
}}
QPushButton#segment[last="true"] {{
    border-top-right-radius: {RADIUS}px; border-bottom-right-radius: {RADIUS}px;
    border-right-width: 1px;
}}
QPushButton#segment:checked {{
    background: {c['accent']}; color: {c['accent_text']}; border-color: {c['accent']};
}}
QComboBox {{
    background: {c['control']}; border: 1px solid {c['border']}; border-radius: {RADIUS}px;
    padding: 2px 6px; min-height: 18px;
}}
QComboBox:hover {{ background: {c['hover']}; }}
QToolButton#help {{
    border-radius: 10px; min-width: 20px; max-width: 20px; min-height: 20px; max-height: 20px;
    padding: 0; font-weight: 700; color: {c['muted']};
}}

/* Tabs */
QTabWidget::pane {{ border: none; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{
    background: transparent; border: none; padding: 5px 12px; margin-right: 2px;
    color: {c['muted']};
}}
QTabBar::tab:selected {{ color: {c['text']}; border-bottom: 2px solid {c['accent']}; }}
QTabBar::tab:hover {{ color: {c['text']}; }}

QSplitter::handle {{ background: transparent; }}
QScrollBar:horizontal {{ height: 10px; background: transparent; margin: 2px 0 0 0; }}
QScrollBar:vertical {{ width: 10px; background: transparent; margin: 0 0 0 2px; }}
QScrollBar::handle {{ background: {c['border']}; border-radius: 4px; min-width: 24px; min-height: 24px; }}
QScrollBar::handle:hover {{ background: {c['muted']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QStatusBar {{ color: {c['muted']}; }}

/* One-line message bars */
QFrame#warnBar {{ background: {c['warn_bg']}; border-radius: {RADIUS}px; }}
QFrame#warnBar QLabel {{ color: {c['warn_text']}; }}
QFrame#noticeBar {{ background: {c['notice_bg']}; border-radius: {RADIUS}px; }}
QFrame#noticeBar QLabel {{ color: {c['notice_text']}; font-weight: 600; }}
"""


def apply(app: QApplication) -> None:
    app.setStyleSheet(stylesheet(app.palette()))
