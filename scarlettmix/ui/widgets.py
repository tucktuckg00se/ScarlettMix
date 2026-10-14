"""Shared widgets: the dB fader and small helpers."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSignalBlocker, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPalette, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QLabel, QPushButton, QScrollArea,
                               QSizePolicy, QSlider, QWidget)

from .. import laws, names

from . import theme

MUTE_COLOR = theme.MUTE
SOLO_COLOR = theme.SOLO
# Not saved to hardware: drawn as an outline. Saved: drawn solid.
UNSAVED_COLOR = theme.UNSAVED
UNSAVED_TIP = "Not saved to hardware: lost when the interface is switched off."


class Outlined:
    """Mix-in for stock widgets: an outline around the widget marks a value
    that is not saved to hardware."""

    _unsaved = False

    def setUnsaved(self, on: bool) -> None:
        if on != self._unsaved:
            self._unsaved = on
            self.update()

    def unsaved(self) -> bool:
        return self._unsaved

    def paintEvent(self, ev) -> None:
        super().paintEvent(ev)
        if self._unsaved:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(QColor(UNSAVED_COLOR), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 3, 3)


class MarkSlider(Outlined, QSlider):
    pass


class MarkCheckBox(Outlined, QCheckBox):
    pass


def fmt_db(db: float) -> str:
    if db <= laws.MIN_DB:
        return "\u2212\u221e"
    r = round(db)
    return "0" if r == 0 else (f"+{r}" if r > 0 else f"\u2212{-r}")


class Fader(QWidget):
    """Vertical dB fader with the original MixControl taper.

    Values snap to whole decibels because that is the resolution the Linux
    driver offers. ``changed`` fires only for user input.
    """

    changed = Signal(float)

    HANDLE_H = 14           # drawn height; the grab area is larger
    HANDLE_W = 26
    GRAB = 22
    TICKS = (6, 0, -6, -12, -24, -48)

    def __init__(self, min_db: float = laws.MIN_DB, max_db: float = laws.MAX_DB,
                 default_db: float = 0.0, parent: QWidget | None = None, meters: int = 0):
        super().__init__(parent)
        self._min, self._max = float(min_db), float(max_db)
        self._default = min(self._max, default_db)
        self._db = self._min
        self._top = laws.db_to_fader(self._max)
        self._drag_offset: float | None = None
        # Optional level meter beside the groove: up to ``meters`` bars.
        self._meter_w = 5.0 * meters + (4.0 if meters else 0.0)
        self._levels: list[float] = []
        self._unsaved = False
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setMinimumHeight(110)

    def _groove_x(self) -> float:
        return self._meter_w + self.HANDLE_W / 2 + 2

    def sizeHint(self) -> QSize:
        return QSize(int(self._groove_x() + self.HANDLE_W / 2 + 30), 240)

    def minimumSizeHint(self) -> QSize:
        return QSize(self.sizeHint().width(), 110)

    def db(self) -> float:
        return self._db

    def setUnsaved(self, on: bool) -> None:
        """Draw the handle and bar hollow: this level is not saved to hardware."""
        if on != self._unsaved:
            self._unsaved = on
            self.update()

    def unsaved(self) -> bool:
        return self._unsaved

    METER_FLOOR = -60.0

    def setLevels(self, levels: list[float]) -> None:
        """Show signal levels in dBFS on the meter bars."""
        levels = [max(self.METER_FLOOR, min(0.0, round(v, 1))) for v in levels]
        if levels != self._levels:
            self._levels = levels
            self.update()

    def _paint_meter(self, p: QPainter, track: QColor) -> None:
        top, bottom = self._track()
        height = bottom - top
        for i, level in enumerate(self._levels):
            bar = QRectF(1.0 + 5.0 * i, top, 3, height)
            p.setBrush(track)
            p.drawRoundedRect(bar, 1.5, 1.5)
            frac = (level - self.METER_FLOOR) / -self.METER_FLOOR
            if frac <= 0:
                continue
            # green to -12 dBFS, amber to -3, red above
            for lo, hi, color in ((0.0, 0.8, "#3fa34d"), (0.8, 0.95, "#d4a72c"), (0.95, 1.0, "#e5534b")):
                if frac > lo:
                    seg_top = bottom - min(frac, hi) * height
                    p.setBrush(QColor(color))
                    p.drawRoundedRect(QRectF(bar.x(), seg_top, 3, bottom - lo * height - seg_top), 1.5, 1.5)

    def setDb(self, db: float) -> None:
        db = float(max(self._min, min(self._max, round(db))))
        if db != self._db:
            self._db = db
            self.update()

    # -- geometry ------------------------------------------------------------

    def _track(self) -> tuple[float, float]:
        """(y of maximum, y of minimum) for the handle centre."""
        pad = self.HANDLE_H / 2 + 3
        return pad, self.height() - pad

    def _y_for(self, db: float) -> float:
        top, bottom = self._track()
        frac = laws.db_to_fader(db) / self._top
        return bottom - frac * (bottom - top)

    def _db_for(self, y: float) -> float:
        top, bottom = self._track()
        frac = (bottom - y) / (bottom - top)
        return laws.fader_to_db(max(0.0, min(1.0, frac)) * self._top)

    def _user_set(self, db: float) -> None:
        before = self._db
        self.setDb(db)
        if self._db != before:
            self.changed.emit(self._db)

    # -- input ---------------------------------------------------------------

    def mousePressEvent(self, ev) -> None:
        if ev.button() != Qt.LeftButton or not self.isEnabled():
            return
        y = ev.position().y()
        handle_y = self._y_for(self._db)
        if abs(y - handle_y) <= self.GRAB / 2:
            self._drag_offset = y - handle_y
        else:
            self._drag_offset = 0.0
            self._user_set(self._db_for(y))
        self.setFocus(Qt.MouseFocusReason)

    def mouseMoveEvent(self, ev) -> None:
        if self._drag_offset is not None:
            self._user_set(self._db_for(ev.position().y() - self._drag_offset))

    def mouseReleaseEvent(self, ev) -> None:
        self._drag_offset = None

    def mouseDoubleClickEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton and self.isEnabled():
            self._user_set(self._default)

    def wheelEvent(self, ev) -> None:
        steps = ev.angleDelta().y() / 120.0
        if steps:
            self._user_set(self._db + (1 if steps > 0 else -1) * max(1, round(abs(steps))))
            ev.accept()

    def keyPressEvent(self, ev) -> None:
        step = {Qt.Key_Up: 1, Qt.Key_Down: -1, Qt.Key_PageUp: 6, Qt.Key_PageDown: -6}.get(ev.key())
        if step is not None:
            self._user_set(self._db + step)
        elif ev.key() == Qt.Key_Home:
            self._user_set(self._max)
        elif ev.key() == Qt.Key_End:
            self._user_set(self._min)
        else:
            super().keyPressEvent(ev)

    # -- painting ------------------------------------------------------------

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        group = QPalette.Active if self.isEnabled() else QPalette.Disabled
        text = pal.color(group, QPalette.WindowText)
        accent = pal.color(group, QPalette.Highlight)
        if not self.isEnabled():
            accent = QColor(text)
            accent.setAlphaF(0.35)
        gx = self._groove_x()
        top, bottom = self._track()

        track = QColor(text)
        track.setAlphaF(0.14)
        p.setPen(Qt.NoPen)
        if self._meter_w:
            self._paint_meter(p, track)

        # groove, filled from the bottom up to the level
        handle_y = self._y_for(self._db)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(gx - 2, top, 4, bottom - top), 2, 2)
        fill = QRectF(gx - 2, handle_y, 4, bottom - handle_y)
        if self._unsaved:
            p.setPen(QPen(accent, 1.2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(fill.adjusted(-0.5, 0, 0.5, 0), 2, 2)
            p.setPen(Qt.NoPen)
        else:
            p.setBrush(accent)
            p.drawRoundedRect(fill, 2, 2)

        # scale: as many labels as fit without touching
        font = self.font()
        font.setPointSizeF(max(7.0, font.pointSizeF() * 0.82))
        p.setFont(font)
        fm = QFontMetrics(font)
        dim = QColor(text)
        dim.setAlphaF(0.55)
        p.setPen(QPen(dim, 1))
        lx = gx + self.HANDLE_W / 2 + 4
        marks = [(t, "0" if t == 0 else fmt_db(t)) for t in self.TICKS if self._min < t <= self._max]
        marks.append((None, "\u2212\u221e"))
        last_y = -1000.0
        for tick, label in marks:
            y = bottom if tick is None else self._y_for(tick)
            if y - last_y < fm.height() + 1 or (tick is not None and bottom - y < fm.height() + 1):
                continue
            last_y = y
            p.drawText(QPointF(lx, y + fm.capHeight() / 2), label)

        # handle: a small pill with a centre line
        handle = QRectF(gx - self.HANDLE_W / 2, handle_y - self.HANDLE_H / 2,
                        self.HANDLE_W, self.HANDLE_H)
        if self._unsaved:
            p.setPen(QPen(accent, 2))
            p.setBrush(pal.color(group, QPalette.Window))
        else:
            edge = QColor(text)
            edge.setAlphaF(0.30)
            p.setPen(QPen(edge, 1))
            p.setBrush(pal.color(group, QPalette.Button).lighter(108))
        p.drawRoundedRect(handle.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
        p.setPen(QPen(accent if self.hasFocus() else text, 1.6))
        p.drawLine(QPointF(handle.left() + 6, handle_y), QPointF(handle.right() - 6, handle_y))


class ToggleButton(QPushButton):
    """Small checkable button that lights up in a colour when on."""

    def __init__(self, text: str, color: str, tooltip: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip or text)
        self.setFixedHeight(theme.CONTROL_H)
        # Same width whether drawn solid or as an outline (the two styles have
        # different natural sizes): share the row equally instead.
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setMinimumWidth(26)
        self._color, self._unsaved = color, None
        self.setUnsaved(False)

    def _base(self) -> str:
        # The small Link toggle keeps its compact size in both looks.
        return "min-height: 18px; max-height: 18px; padding: 0;" if self.objectName() == "linkToggle" else "padding: 0 6px;"

    def setUnsaved(self, on: bool, force: bool = False) -> None:
        """Outline instead of fill: this setting is not saved to hardware."""
        if on == self._unsaved and not force:
            return
        self._unsaved = on
        c = self._color
        if on:
            self.setStyleSheet(
                "QPushButton { background-color: transparent; border: 2px solid #8a8f96;"
                f" border-radius: {theme.RADIUS}px; {self._base()} }}"
                f"QPushButton:checked {{ border: 2px solid {c}; color: {c}; font-weight: 600; }}")
        else:
            self.setStyleSheet(
                f"QPushButton {{ {self._base()} }}"
                f"QPushButton:checked {{ background-color: {c}; color: #111; border-color: {c};"
                " font-weight: 600; }")

    def unsaved(self) -> bool:
        return bool(self._unsaved)


class GroupedCombo(Outlined, QComboBox):
    """Selector with entries grouped under headings that cannot be chosen.
    Each entry carries a string key; ``picked`` fires for user choices only."""

    picked = Signal(str)
    CUSTOM = "__custom__"

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMaxVisibleItems(30)
        self.activated.connect(self._activated)

    def set_entries(self, entries: list[tuple[str, str, str]]) -> None:
        """(heading, text, key) in order; a new heading starts a group."""
        self.clear()
        last = None
        for heading, text, key in entries:
            if heading and heading != last:
                self.addItem(heading)
                item = self.model().item(self.count() - 1)
                item.setFlags(Qt.NoItemFlags)
                font = item.font()
                font.setBold(True)
                font.setPointSizeF(font.pointSizeF() * 0.9)
                item.setFont(font)
                last = heading
            self.addItem(text, key)

    def select(self, key: str, custom_text: str = "") -> None:
        """Show the entry with this key; with ``custom_text``, show that
        instead as a temporary first entry (a choice not on the list)."""
        with QSignalBlocker(self):
            if self.itemData(0) == self.CUSTOM:
                self.removeItem(0)
            if custom_text:
                self.insertItem(0, custom_text, self.CUSTOM)
                self.setCurrentIndex(0)
            else:
                self.setCurrentIndex(max(0, self.findData(key)))

    def _activated(self, index: int) -> None:
        key = self.itemData(index)
        if key and key != self.CUSTOM:
            self.picked.emit(key)

    def wheelEvent(self, ev) -> None:
        if self.hasFocus():
            super().wheelEvent(ev)
        else:
            ev.ignore()


class ItemCombo(Outlined, QComboBox):
    """Combo box for an enumerated control; ignores the wheel unless focused,
    so scrolling a page never changes routing by accident."""

    def __init__(self, items: list[str], parent: QWidget | None = None):
        super().__init__(parent)
        self.addItems([names.source(i) for i in items])
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMaxVisibleItems(24)

    def wheelEvent(self, ev) -> None:
        if self.hasFocus():
            super().wheelEvent(ev)
        else:
            ev.ignore()


class FitScrollArea(QScrollArea):
    """Scroll area that asks for the full size of its contents, so a window
    built from these opens large enough to show everything when the screen
    allows, and only scrolls when it does not."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)

    def sizeHint(self) -> QSize:
        inner = self.widget()
        if not inner:
            return super().sizeHint()
        bar = self.style().pixelMetric(self.style().PixelMetric.PM_ScrollBarExtent)
        return inner.sizeHint() + QSize(bar + 4, bar + 4)


def section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("sectionTitle")
    return label


def caption(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("caption")
    return label


def card(name: str = "card") -> QFrame:
    """A rounded panel that groups related controls (styled by the theme)."""
    frame = QFrame()
    frame.setObjectName(name)
    return frame
