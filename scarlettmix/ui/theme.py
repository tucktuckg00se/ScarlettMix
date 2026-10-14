"""Look of the application: spacing, colours and one stylesheet.

Colours that carry meaning (mute, solo, monitor states, unsaved) are fixed;
everything else is derived from the desktop's palette, so the window follows
a light or dark desktop theme.
"""

from __future__ import annotations

import os

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


def _icon(name: str) -> str:
    return os.path.join(os.path.dirname(__file__), "icons", name).replace(os.sep, "/")


CONTROL_H = 28          # every button, selector and toggle


def stylesheet(pal: QPalette | None = None) -> str:
    c = colors(pal)
    chevron = _icon("chevron-down-on-dark.svg" if is_dark(pal) else "chevron-down-on-light.svg")
    check = _icon("check.svg")
    inner = CONTROL_H - 2       # minus the 1 px border top and bottom
    return f"""
QMainWindow, QDialog {{ background: {c['window']}; }}
QToolTip {{
    background: {c['card']}; color: {c['text']}; border: 1px solid {c['border']};
    border-radius: {RADIUS}px; padding: 6px 8px;
}}
QMenu {{
    background: {c['card']}; border: 1px solid {c['border']}; border-radius: {RADIUS}px;
    padding: 4px;
}}
QMenu::item {{ padding: 5px 22px 5px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background: {c['accent']}; color: {c['accent_text']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 6px; }}
QMenu::indicator {{ width: 14px; height: 14px; left: 4px; }}
QMenuBar {{ background: transparent; }}
QMenuBar::item {{ padding: 4px 10px; border-radius: 4px; }}
QMenuBar::item:selected {{ background: {c['hover']}; }}

/* Panels */
QFrame#card, QFrame#strip {{
    background: {c['card']}; border: 1px solid {c['border']}; border-radius: {RADIUS + 2}px;
}}
QLabel#sectionTitle {{ color: {c['muted']}; font-weight: 600; }}
QLabel#caption {{ color: {c['muted']}; }}
QLabel#value {{ color: {c['text']}; }}
QLabel#deviceName {{ font-weight: 700; }}
QLabel#knobValue {{ font-weight: 700; }}
QFrame#chip {{
    background: {c['card']}; border: 1px solid {c['border']}; border-radius: {CONTROL_H // 2}px;
    min-height: {inner}px; max-height: {inner}px;
}}

/* Buttons */
QPushButton, QToolButton {{
    background: {c['control']}; color: {c['text']}; border: 1px solid {c['border']};
    border-radius: {RADIUS}px; padding: 0 10px; min-height: {inner}px; max-height: {inner}px;
}}
QPushButton:hover, QToolButton:hover {{ background: {c['hover']}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {c['border']}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {c['muted']}; background: transparent; }}
QPushButton:focus, QToolButton:focus {{ border-color: {c['accent']}; }}
QToolButton[popupMode="2"] {{ padding-right: 22px; }}
QToolButton::menu-indicator {{
    image: url({chevron}); subcontrol-origin: padding; subcontrol-position: center right;
    right: 8px; width: 10px; height: 6px;
}}
QToolButton::menu-indicator:pressed, QToolButton::menu-indicator:open {{ top: 0; left: 0; }}
QPushButton#primary {{
    background: {c['accent']}; color: {c['accent_text']}; border-color: {c['accent']};
    font-weight: 600; padding: 0 14px;
}}
QPushButton#primary:hover {{ background: {_mix(QColor(c['accent']), QColor('#ffffff'), 0.12).name()}; }}
QPushButton#flat {{ background: transparent; border-color: transparent; }}
QPushButton#flat:hover {{ background: {c['hover']}; }}
QPushButton#flat:disabled {{ color: {c['muted']}; }}
QPushButton#segment {{ border-radius: 0; padding: 0 12px; border-right-width: 0; }}
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
QToolButton#help {{
    border-radius: 11px; min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px;
    padding: 0; font-weight: 700; color: {c['muted']};
}}
QPushButton#linkToggle {{ min-height: 18px; max-height: 18px; padding: 0; font-size: 8pt; }}
QToolButton#sendSelect {{ padding: 0 6px; }}
QToolButton#sendSelect::menu-indicator {{ image: none; width: 0; }}
QToolButton#split {{ padding: 0 6px; color: {c['muted']}; }}
QToolButton#split:checked {{ color: {c['text']}; border-color: {c['accent']}; }}
QToolButton#more {{
    padding: 0 4px; min-width: 18px; min-height: 18px; max-height: 18px;
    border-color: transparent; background: transparent;
}}
QToolButton#more:hover {{ background: {c['hover']}; }}
QToolButton#more::menu-indicator {{ image: none; width: 0; }}

/* Selectors */
QComboBox {{
    background: {c['control']}; color: {c['text']}; border: 1px solid {c['border']};
    border-radius: {RADIUS}px; padding: 0 18px 0 6px; min-height: {inner}px; max-height: {inner}px;
}}
QComboBox:hover {{ background: {c['hover']}; }}
QComboBox:focus {{ border-color: {c['accent']}; }}
QComboBox:disabled {{ color: {c['muted']}; }}
QComboBox::drop-down {{
    subcontrol-origin: padding; subcontrol-position: center right; width: 18px; border: none;
}}
QComboBox::down-arrow {{ image: url({chevron}); width: 10px; height: 6px; }}
QComboBox QAbstractItemView {{
    background: {c['card']}; color: {c['text']}; border: 1px solid {c['border']};
    selection-background-color: {c['accent']}; selection-color: {c['accent_text']};
    outline: 0; padding: 2px;
}}
QComboBox#sourceSelect {{ padding: 0 6px; }}
QComboBox#sourceSelect::drop-down {{ width: 0; }}
QComboBox#sourceSelect::down-arrow {{ image: none; width: 0; }}
QCheckBox {{ spacing: 8px; min-height: {inner}px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 4px; border: 1px solid {c['border']};
    background: {c['control']};
}}
QCheckBox::indicator:hover {{ border-color: {c['accent']}; }}
QCheckBox::indicator:checked {{
    background: {c['accent']}; border-color: {c['accent']}; image: url({check});
}}

/* Pan sliders */
QSlider:horizontal {{ min-height: 18px; }}
QSlider::groove:horizontal {{ height: 4px; border-radius: 2px; background: {c['border']}; }}
QSlider::handle:horizontal {{
    width: 12px; height: 12px; margin: -5px 0; border-radius: 6px;
    background: {c['accent']}; border: 2px solid {c['card']};
}}

/* Tabs */
QTabWidget::pane {{ border: none; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{
    background: transparent; border: none; border-bottom: 2px solid transparent;
    padding: 6px 12px; margin-right: 2px; color: {c['muted']};
}}
QTabBar::tab:selected {{ color: {c['text']}; border-bottom: 2px solid {c['accent']}; }}
QTabBar::tab:hover {{ color: {c['text']}; }}

QSplitter::handle {{ background: transparent; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:horizontal {{ height: 10px; background: transparent; margin: 2px 0 0 0; }}
QScrollBar:vertical {{ width: 10px; background: transparent; margin: 0 0 0 2px; }}
QScrollBar::handle {{ background: {c['border']}; border-radius: 4px; min-width: 24px; min-height: 24px; }}
QScrollBar::handle:hover {{ background: {c['muted']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QStatusBar {{ color: {c['muted']}; }}
QStatusBar::item {{ border: none; }}

/* One-line message bars */
QFrame#warnBar {{ background: {c['warn_bg']}; border-radius: {RADIUS}px; }}
QFrame#warnBar QLabel {{ color: {c['warn_text']}; }}
QFrame#noticeBar {{ background: {c['notice_bg']}; border-radius: {RADIUS}px; }}
QFrame#noticeBar QLabel {{ color: {c['notice_text']}; font-weight: 600; }}
"""


def apply(app: QApplication) -> None:
    """Fusion as the base on every desktop, so the stylesheet always styles
    the same thing; the desktop's palette and fonts are kept."""
    pal = app.palette()
    app.setStyle("Fusion")
    app.setPalette(pal)
    app.setStyleSheet(stylesheet(pal))
