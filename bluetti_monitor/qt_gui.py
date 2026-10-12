"""Qt (PySide6) interface for Bluetti Monitor, modelled on the macOS app.

Native on KDE Plasma (follows the Plasma colour scheme and style), with a
system-tray battery readout standing in for the Mac menu-bar extra.
"""

import json
import math
import os
import queue
import time
from collections import deque

from PySide6.QtCore import QEvent, QPointF, QRectF, QSettings, QSize, Qt, QTimer, QUrl, QVariantAnimation
from PySide6.QtGui import (QAction, QActionGroup, QColor, QDesktopServices, QFont, QFontDatabase, QIcon,
                           QPainter, QPainterPath, QPalette, QPen, QPixmap)
from PySide6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSystemTrayIcon,
                               QTabWidget, QToolBar, QToolButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from . import __version__
from . import registers as R

# Accent colours shared with the macOS app.
GREEN, ORANGE, BLUE, PURPLE, TEAL, RED = "#30d158", "#ff9f0a", "#0a84ff", "#bf5af2", "#40c8e0", "#ff453a"
APP_ID = os.environ.get("FLATPAK_ID", "bluetti-monitor")
DATA_DIR = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share"), "bluetti-monitor")


# ----------------------------------------------------------------------------- helpers

def is_dark(w: QWidget) -> bool:
    return w.palette().color(QPalette.Window).lightness() < 128


def card_colour(w: QWidget) -> QColor:
    win = w.palette().color(QPalette.Window)
    return win.lighter(128) if win.lightness() < 128 else w.palette().color(QPalette.Base)


def muted(w: QWidget, alpha=0.62) -> QColor:
    c = QColor(w.palette().color(QPalette.WindowText))
    c.setAlphaF(alpha)
    return c


def soc_colour(v):
    if v is None:
        return QColor("#8e8e93")
    return QColor(RED if v < 15 else ORANGE if v < 35 else GREEN)


def tabular(font: QFont) -> QFont:
    try:                                   # Qt 6.7+: tabular digits so numbers don't jiggle
        font.setFeature(QFont.Tag("tnum"), 1)
    except Exception:
        pass
    return font


def font(size=None, weight=None, base: QFont = None) -> QFont:
    f = QFont(base or QApplication.font())
    if size:
        f.setPointSizeF(size)
    if weight:
        f.setWeight(weight)
    return tabular(f)


def glyph(name: str, size: int, colour) -> QPixmap:
    """Small vector icons (consistent across icon themes)."""
    ratio = QApplication.instance().devicePixelRatio() if QApplication.instance() else 1
    pm = QPixmap(int(size * ratio), int(size * ratio))
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    c = QColor(colour)
    pen = QPen(c, max(1.4, size / 12), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    s = size
    if name == "sun":
        p.setBrush(c)
        p.drawEllipse(QPointF(s / 2, s / 2), s * 0.2, s * 0.2)
        for i in range(8):
            a = i * math.pi / 4
            p.drawLine(QPointF(s / 2 + math.cos(a) * s * 0.32, s / 2 + math.sin(a) * s * 0.32),
                       QPointF(s / 2 + math.cos(a) * s * 0.45, s / 2 + math.sin(a) * s * 0.45))
    elif name == "plug":
        p.setBrush(c)
        p.drawRoundedRect(QRectF(s * 0.25, s * 0.3, s * 0.5, s * 0.32), s * 0.08, s * 0.08)
        p.drawLine(QPointF(s * 0.38, s * 0.3), QPointF(s * 0.38, s * 0.12))
        p.drawLine(QPointF(s * 0.62, s * 0.3), QPointF(s * 0.62, s * 0.12))
        p.drawLine(QPointF(s * 0.5, s * 0.62), QPointF(s * 0.5, s * 0.9))
    elif name == "outlet":                  # UK socket
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(s * 0.12, s * 0.12, s * 0.76, s * 0.76), s * 0.18, s * 0.18)
        p.setBrush(c)
        p.drawRect(QRectF(s * 0.46, s * 0.26, s * 0.08, s * 0.18))
        p.drawRect(QRectF(s * 0.28, s * 0.52, s * 0.16, s * 0.08))
        p.drawRect(QRectF(s * 0.56, s * 0.52, s * 0.16, s * 0.08))
    elif name == "bolt":
        path = QPainterPath()
        pts = [(0.56, 0.08), (0.24, 0.56), (0.48, 0.56), (0.40, 0.92), (0.76, 0.42), (0.52, 0.42), (0.62, 0.08)]
        path.moveTo(pts[0][0] * s, pts[0][1] * s)
        for x, y in pts[1:]:
            path.lineTo(x * s, y * s)
        path.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawPath(path)
    elif name == "battery":
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(s * 0.08, s * 0.3, s * 0.74, s * 0.4), s * 0.08, s * 0.08)
        p.setBrush(c)
        p.drawRect(QRectF(s * 0.84, s * 0.42, s * 0.08, s * 0.16))
        p.drawRect(QRectF(s * 0.16, s * 0.38, s * 0.4, s * 0.24))
    elif name == "cells":
        p.setBrush(Qt.NoBrush)
        for i in range(3):
            for j in range(2):
                p.drawRoundedRect(QRectF(s * (0.1 + i * 0.28), s * (0.22 + j * 0.3), s * 0.22, s * 0.24), 2, 2)
    elif name == "chart":
        p.drawLine(QPointF(s * 0.12, s * 0.12), QPointF(s * 0.12, s * 0.86))
        p.drawLine(QPointF(s * 0.12, s * 0.86), QPointF(s * 0.9, s * 0.86))
        path = QPainterPath(QPointF(s * 0.2, s * 0.7))
        path.lineTo(s * 0.4, s * 0.45)
        path.lineTo(s * 0.58, s * 0.58)
        path.lineTo(s * 0.86, s * 0.22)
        p.drawPath(path)
    elif name == "gear":
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(s / 2, s / 2), s * 0.24, s * 0.24)
        for i in range(8):
            a = i * math.pi / 4
            p.drawLine(QPointF(s / 2 + math.cos(a) * s * 0.3, s / 2 + math.sin(a) * s * 0.3),
                       QPointF(s / 2 + math.cos(a) * s * 0.42, s / 2 + math.sin(a) * s * 0.42))
    elif name == "info":
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(s / 2, s / 2), s * 0.4, s * 0.4)
        p.drawLine(QPointF(s / 2, s * 0.44), QPointF(s / 2, s * 0.72))
        p.setBrush(c)
        p.drawEllipse(QPointF(s / 2, s * 0.3), s * 0.04, s * 0.04)
    elif name == "chip":
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(s * 0.24, s * 0.24, s * 0.52, s * 0.52), 2, 2)
        for i in range(3):
            o = s * (0.34 + i * 0.16)
            for a, b in (((o, s * 0.1), (o, s * 0.24)), ((o, s * 0.76), (o, s * 0.9)),
                         ((s * 0.1, o), (s * 0.24, o)), ((s * 0.76, o), (s * 0.9, o))):
                p.drawLine(QPointF(*a), QPointF(*b))
    elif name == "cable":
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(s * 0.3, s * 0.1, s * 0.4, s * 0.4), 3, 3)
        p.drawLine(QPointF(s * 0.42, s * 0.1), QPointF(s * 0.42, s * 0.24))
        p.drawLine(QPointF(s * 0.58, s * 0.1), QPointF(s * 0.58, s * 0.24))
        path = QPainterPath(QPointF(s * 0.5, s * 0.5))
        path.cubicTo(s * 0.5, s * 0.8, s * 0.2, s * 0.7, s * 0.2, s * 0.92)
        p.drawPath(path)
    elif name == "antenna":
        p.setBrush(c)
        p.drawEllipse(QPointF(s / 2, s / 2), s * 0.07, s * 0.07)
        p.setBrush(Qt.NoBrush)
        for r in (0.22, 0.38):
            p.drawArc(QRectF(s / 2 - s * r, s / 2 - s * r, 2 * s * r, 2 * s * r), 30 * 16, 120 * 16)
            p.drawArc(QRectF(s / 2 - s * r, s / 2 - s * r, 2 * s * r, 2 * s * r), 210 * 16, 120 * 16)
    p.end()
    return pm


# ----------------------------------------------------------------------------- building blocks

class Card(QFrame):
    def __init__(self, title=None, icon=None, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 14, 16, 16)
        self.body.setSpacing(8)
        self.title_label = None
        if title:
            row = QHBoxLayout()
            row.setSpacing(8)
            self.icon_label = QLabel()
            self.icon_name = icon
            row.addWidget(self.icon_label)
            self.title_label = QLabel(title)
            self.title_label.setFont(font(None, QFont.DemiBold))
            row.addWidget(self.title_label)
            row.addStretch(1)
            self.header = row
            self.body.addLayout(row)
            self._style_header()

    def _style_header(self):
        if self.title_label:
            pal = self.title_label.palette()
            pal.setColor(QPalette.WindowText, muted(self, 0.75))
            self.title_label.setPalette(pal)
            if self.icon_name:
                self.icon_label.setPixmap(glyph(self.icon_name, 16, muted(self, 0.75)))

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.Type.PaletteChange:
            self._style_header()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        border = QColor(self.palette().color(QPalette.WindowText))
        border.setAlphaF(0.10)
        p.setPen(QPen(border, 1))
        p.setBrush(card_colour(self))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)


class BatteryRing(QWidget):
    def __init__(self, diameter=170, line=16, parent=None):
        super().__init__(parent)
        self.diameter, self.line = diameter, line
        self.value = None
        self._shown = 0.0
        self.setFixedSize(diameter, diameter)
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(500)
        self.anim.valueChanged.connect(self._tick)

    def _tick(self, v):
        self._shown = float(v)
        self.update()

    def set_value(self, v):
        if v == self.value:
            return
        start = self._shown
        self.value = v
        self.anim.stop()
        self.anim.setStartValue(start)
        self.anim.setEndValue(float(v or 0))
        self.anim.start()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        m = self.line / 2 + 1
        rect = QRectF(m, m, self.diameter - 2 * m, self.diameter - 2 * m)
        track = QColor(self.palette().color(QPalette.WindowText))
        track.setAlphaF(0.10)
        p.setPen(QPen(track, self.line, Qt.SolidLine, Qt.RoundCap))
        p.drawEllipse(rect)
        if self._shown > 0:
            p.setPen(QPen(soc_colour(self.value), self.line, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(rect, 90 * 16, int(-self._shown / 100 * 360 * 16))
        p.setPen(self.palette().color(QPalette.WindowText))
        big = font(self.diameter * 0.2, QFont.Bold)
        p.setFont(big)
        txt = "—" if self.value is None else f"{int(round(self._shown))}"
        r = QRectF(0, self.diameter * 0.2, self.diameter, self.diameter * 0.44)
        p.drawText(r, Qt.AlignHCenter | Qt.AlignBottom, txt)
        p.setPen(muted(self))
        p.setFont(font(self.diameter * 0.085, QFont.DemiBold))
        p.drawText(QRectF(0, self.diameter * 0.64, self.diameter, self.diameter * 0.2), Qt.AlignHCenter | Qt.AlignTop, "%")


class FlowTile(QFrame):
    def __init__(self, title, icon, tint, parent=None):
        super().__init__(parent)
        self.tint, self.icon, self.watts = QColor(tint), icon, None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.icon_label = QLabel()
        head.addWidget(self.icon_label)
        self.title = QLabel(title)
        self.title.setFont(font(None, QFont.Medium))
        head.addWidget(self.title)
        head.addStretch(1)
        lay.addLayout(head)
        val = QHBoxLayout()
        val.setSpacing(4)
        self.value = QLabel("—")
        self.value.setFont(font(QApplication.font().pointSizeF() * 2.2, QFont.DemiBold))
        self.unit = QLabel("W")
        self.unit.setFont(font(QApplication.font().pointSizeF() * 1.15, QFont.DemiBold))
        val.addWidget(self.value, 0, Qt.AlignBottom)
        val.addWidget(self.unit, 0, Qt.AlignBottom)
        val.addStretch(1)
        lay.addLayout(val)
        self.detail = QLabel(" ")
        lay.addWidget(self.detail)
        self.setMinimumWidth(180)
        self._restyle()

    def _restyle(self):
        active = (self.watts or 0) > 0
        self.icon_label.setPixmap(glyph(self.icon, 22, self.tint if active else muted(self, 0.55)))
        for lab, a in ((self.title, 0.7), (self.unit, 0.6), (self.detail, 0.6)):
            pal = lab.palette()
            pal.setColor(QPalette.WindowText, muted(self, a))
            lab.setPalette(pal)

    def set(self, watts, detail=None):
        self.watts = watts
        self.value.setText("—" if watts is None else f"{int(round(watts))}")
        self.detail.setText(detail or " ")
        self._restyle()
        self.update()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.Type.PaletteChange:
            self._restyle()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        active = (self.watts or 0) > 0
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(card_colour(self))
        border = QColor(self.palette().color(QPalette.WindowText))
        border.setAlphaF(0.10)
        p.setPen(QPen(border, 1))
        p.drawRoundedRect(r, 12, 12)
        if active:
            fill = QColor(self.tint)
            fill.setAlphaF(0.13)
            edge = QColor(self.tint)
            edge.setAlphaF(0.45)
            p.setBrush(fill)
            p.setPen(QPen(edge, 1))
            p.drawRoundedRect(r, 12, 12)


class StatusPill(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.text, self.colour = "Starting", QColor(ORANGE)
        self.setMinimumHeight(26)
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)

    def set(self, text, colour):
        self.text, self.colour = text, QColor(colour)
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.text) + 46, 26)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bg = QColor(self.palette().color(QPalette.WindowText))
        bg.setAlphaF(0.08)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setBrush(self.colour)
        p.drawEllipse(QPointF(14, r.center().y()), 4.5, 4.5)
        p.setPen(self.palette().color(QPalette.WindowText))
        p.setFont(tabular(QFont(self.font())))
        p.drawText(r.adjusted(26, 0, -10, 0), Qt.AlignVCenter | Qt.AlignLeft, self.text)


class SignalBars(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rssi = None
        self.setFixedSize(22, 18)

    def set(self, rssi):
        self.rssi = rssi
        self.setToolTip(f"Signal {rssi} dBm" if rssi is not None else "No signal reading")
        self.update()

    def paintEvent(self, _):
        if self.rssi is None:
            return
        level = 4 if self.rssi >= -60 else 3 if self.rssi >= -70 else 2 if self.rssi >= -80 else 1
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        for i in range(4):
            h = 4 + i * 4
            c = QColor(self.palette().color(QPalette.WindowText)) if i < level else muted(self, 0.25)
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(2 + i * 5, 17 - h, 3, h), 1, 1)


class TimeChart(QWidget):
    """Minimal line/area chart for time series (no QtCharts dependency)."""

    def __init__(self, y_fixed=None, unit="", area=False, parent=None):
        super().__init__(parent)
        self.y_fixed, self.unit, self.area = y_fixed, unit, area
        self.series = []                    # [(name, colour, [(t, v), ...])]
        self.window = 3600
        self.setMinimumHeight(170)

    def set(self, series, window):
        self.series, self.window = series, window
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        fm = self.fontMetrics()
        small = font(QApplication.font().pointSizeF() * 0.85)
        p.setFont(small)
        legend_h = 20 if len(self.series) > 1 else 0
        left, right, top, bottom = 54, 10, 8 + legend_h, 22
        W, H = self.width() - left - right, self.height() - top - bottom
        now = time.time()
        t0 = now - self.window
        vals = [v for _, _, pts in self.series for t, v in pts if t >= t0]
        if self.y_fixed:
            y0, y1 = self.y_fixed
        else:
            y0, y1 = 0, max(vals + [10])
            step = 10 ** math.floor(math.log10(max(y1, 1)))
            for m in (1, 2, 2.5, 5, 10):
                if y1 <= step * m * 4:
                    y1 = step * m * 4
                    break
        grid = muted(self, 0.10)
        lab = muted(self, 0.55)
        for i in range(5):
            v = y0 + (y1 - y0) * i / 4
            y = top + H - H * i / 4
            p.setPen(QPen(grid, 1))
            p.drawLine(QPointF(left, y), QPointF(left + W, y))
            p.setPen(lab)
            txt = f"{int(v)}%" if self.unit == "%" else f"{int(v)} {self.unit}" if i == 4 else f"{int(v)}"
            p.drawText(QRectF(0, y - 8, left - 6, 16), Qt.AlignRight | Qt.AlignVCenter, txt)
        for i in range(4):
            t = t0 + self.window * i / 3
            x = left + W * i / 3
            p.setPen(lab)
            align = Qt.AlignLeft if i == 0 else Qt.AlignRight if i == 3 else Qt.AlignHCenter
            rx = x if i == 0 else x - 60 if i == 3 else x - 30
            p.drawText(QRectF(rx, top + H + 4, 60, 16), align | Qt.AlignTop, time.strftime("%H:%M", time.localtime(t)))

        def xy(t, v):
            return QPointF(left + (t - t0) / self.window * W, top + H - (v - y0) / max(y1 - y0, 1e-9) * H)

        for name, colour, pts in self.series:
            pts = [(t, v) for t, v in pts if t >= t0]
            if len(pts) < 2:
                continue
            path = QPainterPath(xy(*pts[0]))
            for t, v in pts[1:]:
                path.lineTo(xy(t, v))
            if self.area:
                fill = QPainterPath(path)
                fill.lineTo(xy(pts[-1][0], y0))
                fill.lineTo(xy(pts[0][0], y0))
                fill.closeSubpath()
                c = QColor(colour)
                c.setAlphaF(0.18)
                p.fillPath(fill, c)
            p.setPen(QPen(QColor(colour), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        if legend_h:
            x = left
            for name, colour, pts in self.series:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(colour))
                p.drawEllipse(QPointF(x + 5, 10), 4, 4)
                p.setPen(lab)
                p.drawText(QPointF(x + 13, 10 + fm.ascent() / 2 - 1), name)
                x += 24 + p.fontMetrics().horizontalAdvance(name)


class CellBars(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.volts = []
        self.setMinimumHeight(110)

    def set(self, volts):
        self.volts = volts
        self.setToolTip("\n".join(f"Cell {i + 1}: {v:.3f} V" for i, v in enumerate(volts)))
        self.update()

    def paintEvent(self, _):
        if not self.volts:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        n = len(self.volts)
        median = sorted(self.volts)[n // 2]
        gap, label_h = 5, 16
        w = (self.width() - gap * (n - 1)) / n
        h = self.height() - label_h
        p.setFont(font(QApplication.font().pointSizeF() * 0.8))
        for i, v in enumerate(self.volts):
            frac = max(0.05, min(1.0, (v - 2.5) / (3.65 - 2.5)))
            x = i * (w + gap)
            c = QColor(ORANGE if abs(v - median) > 0.03 else GREEN)
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(x, h - h * frac, w, h * frac), 3, 3)
            p.setPen(muted(self, 0.55))
            p.drawText(QRectF(x, h + 2, w, label_h - 2), Qt.AlignHCenter | Qt.AlignTop, str(i + 1))


class ReadingRow(QWidget):
    def __init__(self, field, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.label = QLabel(field["label"])
        lay.addWidget(self.label)
        if field["tentative"]:
            q = QLabel("?")
            q.setToolTip("Mapping taken from other V2 models; not yet confirmed on the Elite 200 V2.")
            q.setFont(font(QApplication.font().pointSizeF() * 0.8))
            pal = q.palette()
            pal.setColor(QPalette.WindowText, muted(self, 0.4))
            q.setPalette(pal)
            lay.addWidget(q)
        lay.addStretch(1)
        self.value = QLabel("—")
        self.value.setFont(tabular(QFont(self.value.font())))
        self.value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.value)
        pal = self.label.palette()
        pal.setColor(QPalette.WindowText, muted(self, 0.65))
        self.label.setPalette(pal)


class GroupCard(Card):
    ICONS = {"Battery": "battery", "Mains input": "plug", "AC output": "outlet", "DC outputs": "cable",
             "Solar input": "sun", "Energy totals": "chart", "Settings": "gear", "Status": "info", "Device": "chip"}

    def __init__(self, group, parent=None):
        super().__init__(group, self.ICONS.get(group), parent)
        self.group = group
        self.rows = {}
        self.keys = ()

    def update_values(self, regs) -> bool:
        present = [(f, R.fmt(f, regs)) for f in R.FIELDS if f["group"] == self.group]
        present = [(f, v) for f, v in present if v is not None]
        keys = tuple(f["key"] for f, _ in present)
        if keys != self.keys:
            for row in self.rows.values():
                row.setParent(None)
            self.rows = {}
            for f, _ in present:
                row = ReadingRow(f)
                self.rows[f["key"]] = row
                self.body.addWidget(row)
            self.keys = keys
        for f, v in present:
            self.rows[f["key"]].value.setText(v)
        return bool(present)


class CellsCard(Card):
    def __init__(self, parent=None):
        super().__init__("Cells", "cells", parent)
        self.bars = CellBars()
        self.body.addWidget(self.bars)
        row = QHBoxLayout()
        self.low, self.spread, self.high = QLabel(), QLabel(), QLabel()
        self.spread.setAlignment(Qt.AlignCenter)
        self.high.setAlignment(Qt.AlignRight)
        for lab in (self.low, self.spread, self.high):
            lab.setFont(font(QApplication.font().pointSizeF() * 0.9))
            row.addWidget(lab, 1)
        self.body.addLayout(row)
        self.temps = QLabel()
        self.temps.setFont(font(QApplication.font().pointSizeF() * 0.9))
        self.body.addWidget(self.temps)

    def update_values(self, regs) -> bool:
        c = R.cells(regs)
        if not c:
            return False
        v = c["voltages"]
        self.title_label.setText(f"Cells ({len(v)} in series)")
        self.bars.set(v)
        self.low.setText(f"Low {min(v):.3f} V")
        self.spread.setText(f"Spread {round((max(v) - min(v)) * 1000)} mV")
        self.high.setText(f"High {max(v):.3f} V")
        t = c["temperatures"]
        self.temps.setText("Temperature sensors: " + " · ".join(f"{int(x)} °C" for x in t) if t else "")
        self.temps.setVisible(bool(t))
        for lab in (self.low, self.high, self.temps):
            pal = lab.palette()
            pal.setColor(QPalette.WindowText, muted(self, 0.6))
            lab.setPalette(pal)
        return True


class ResponsiveGrid(QWidget):
    """Masonry layout: as many ~320 px columns as fit; each card goes to the shortest column."""

    def __init__(self, cards, min_width=320, parent=None):
        super().__init__(parent)
        self.cards, self.min_width = cards, min_width
        self.visible_cards, self._sig = list(cards), None
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(16)
        self.columns = []

    def set_visible_cards(self, visible, force=False):
        self.visible_cards = visible
        self._relayout(force)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout()

    def _relayout(self, force=False):
        cols = max(1, (self.width() + 16) // (self.min_width + 16))
        sig = (cols, tuple(id(c) for c in self.visible_cards), tuple(c.sizeHint().height() // 40 for c in self.visible_cards))
        if sig == self._sig and not force:
            return
        self._sig = sig
        for c in self.cards:
            c.setParent(None)
        while self.row.count():
            item = self.row.takeAt(0)
            if item.layout():
                item.layout().deleteLater()
        self.columns = []
        heights = [0] * cols
        for _ in range(cols):
            col = QVBoxLayout()
            col.setSpacing(16)
            self.columns.append(col)
            self.row.addLayout(col, 1)
        for c in self.visible_cards:
            k = heights.index(min(heights))
            self.columns[k].addWidget(c)
            c.setVisible(True)
            heights[k] += c.sizeHint().height() + 16
        for col in self.columns:
            col.addStretch(1)


# ----------------------------------------------------------------------------- pages

class OverviewPage(QScrollArea):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        self.setWidget(content)
        lay = QVBoxLayout(content)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(16)

        self.connection = ConnectionPanel(win)
        lay.addWidget(self.connection)

        # Hero: battery card + four flow tiles
        self.hero = QWidget()
        hero = QHBoxLayout(self.hero)
        hero.setContentsMargins(0, 0, 0, 0)
        hero.setSpacing(16)
        bat = Card()
        bl = QHBoxLayout()
        bl.setSpacing(22)
        self.ring = BatteryRing()
        bl.addWidget(self.ring)
        info = QVBoxLayout()
        info.setSpacing(6)
        self.name = QLabel("Bluetti")
        self.name.setFont(font(QApplication.font().pointSizeF() * 1.55, QFont.DemiBold))
        self.state = QLabel(" ")
        self.state.setFont(font(QApplication.font().pointSizeF() * 1.2))
        info.addWidget(self.name)
        info.addWidget(self.state)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Plain)
        line.setStyleSheet("color: rgba(127,127,127,60);")
        info.addWidget(line)
        self.stats = {}
        for key in ("Battery", "Net flow", "Health", "Temperature"):
            row = QHBoxLayout()
            k = QLabel(key)
            k.setMinimumWidth(92)
            v = QLabel("—")
            v.setFont(tabular(QFont(v.font())))
            row.addWidget(k)
            row.addWidget(v, 1)
            info.addLayout(row)
            self.stats[key] = (k, v)
        info.addStretch(1)
        bl.addLayout(info, 1)
        bat.body.addLayout(bl)
        bat.setMinimumWidth(420)
        hero.addWidget(bat, 5)
        tiles = QGridLayout()
        tiles.setSpacing(12)
        self.t_solar = FlowTile("Solar in", "sun", ORANGE)
        self.t_mains = FlowTile("Mains in", "plug", BLUE)
        self.t_ac = FlowTile("AC out", "outlet", PURPLE)
        self.t_dc = FlowTile("DC out", "bolt", TEAL)
        tiles.addWidget(self.t_solar, 0, 0)
        tiles.addWidget(self.t_mains, 0, 1)
        tiles.addWidget(self.t_ac, 1, 0)
        tiles.addWidget(self.t_dc, 1, 1)
        hero.addLayout(tiles, 4)
        lay.addWidget(self.hero)

        # History
        self.history = Card("Since the app opened", "chart")
        self.period = QPushButton()
        self.period.setFlat(True)
        menu = QMenu(self.period)
        for label, secs in (("15 minutes", 900), ("1 hour", 3600), ("6 hours", 21600), ("24 hours", 86400)):
            act = menu.addAction(label)
            act.triggered.connect(lambda _=False, s=secs, l=label: self._set_window(s, l))
        self.period.setMenu(menu)
        self.history.header.addWidget(self.period)
        charts = QHBoxLayout()
        charts.setSpacing(20)
        self.soc_chart = TimeChart(y_fixed=(0, 100), unit="%", area=True)
        self.soc_chart.setMaximumWidth(380)
        self.power_chart = TimeChart(unit="W")
        charts.addWidget(self.soc_chart, 2)
        charts.addWidget(self.power_chart, 3)
        self.history.body.addLayout(charts)
        lay.addWidget(self.history)
        secs = int(win.settings.value("historyWindow", 3600))
        self._set_window(secs, {900: "15 minutes", 3600: "1 hour", 21600: "6 hours", 86400: "24 hours"}.get(secs, "1 hour"))

        # Detail cards
        self.cells = CellsCard()
        self.groups = [GroupCard(g) for g in ("Battery", "Mains input", "AC output", "DC outputs", "Solar input",
                                              "Energy totals", "Settings", "Status", "Device")]
        self.grid = ResponsiveGrid([self.cells] + self.groups)
        lay.addWidget(self.grid)
        lay.addStretch(1)

    def _set_window(self, secs, label):
        self.window = secs
        self.period.setText(label)
        self.win.settings.setValue("historyWindow", secs)
        self.refresh_history()

    def refresh_history(self):
        h = self.win.history
        self.soc_chart.set([("Battery", GREEN, [(s[0], s[1]) for s in h if s[1] is not None])], self.window)
        series = []
        for name, idx, colour in (("AC out", 2, PURPLE), ("DC out", 3, TEAL), ("Solar in", 4, ORANGE), ("Mains in", 5, BLUE)):
            pts = [(s[0], s[idx]) for s in h if s[idx] is not None]
            if any(v > 0 for _, v in pts):
                series.append((name, colour, pts))
        self.power_chart.set(series, self.window)

    def refresh(self):
        w = self.win
        regs = w.registers
        has = bool(regs)
        self.connection.setVisible(not has)
        self.hero.setVisible(has)
        self.history.setVisible(has)
        self.grid.setVisible(has)
        self.connection.refresh()
        if not has:
            return
        n = lambda k: R.number(k, regs)
        t = lambda k: R.text(k, regs)
        soc = n("soc")
        self.ring.set_value(soc)
        self.name.setText(w.station_name())
        self.state.setText(w.state_line())
        pal = self.state.palette()
        pal.setColor(QPalette.WindowText, soc_colour(soc))
        self.state.setPalette(pal)
        net = w.battery_watts()
        vals = {"Battery": " · ".join(x for x in (t("packVoltage"), t("packCurrent")) if x) or "—",
                "Net flow": f"{net:+.0f} W" if net is not None else "—",
                "Health": t("packSoh") or "—", "Temperature": t("packTemp") or t("cellMaxT") or "—"}
        for key, (k, v) in self.stats.items():
            v.setText(vals[key])
            p2 = k.palette()
            p2.setColor(QPalette.WindowText, muted(self, 0.6))
            k.setPalette(p2)
        j = lambda *xs: " · ".join(x for x in xs if x) or None
        self.t_solar.set(n("pvInPower"), j(t("pv1Voltage"), t("pv1Current")) if (n("pvInPower") or 0) > 0 else None)
        self.t_mains.set(n("gridInPower"), j(t("gridVoltage"), t("gridFrequency")) if (n("gridFrequency") or 0) > 0 else None)
        self.t_ac.set(n("acOutPower"), j(t("acOutVoltage"), t("acOutFrequency")) if (n("acOutVoltage") or 0) > 50 else None)
        dc = [f"{name} {int(v)} W" for name, v in (("USB", n("usbPower")), ("12 V", n("dc12Power"))) if v]
        self.t_dc.set(n("dcOutPower"), " · ".join(dc) or None)
        visible = [c for c in [self.cells] + self.groups if c.update_values(regs)]
        self.grid.set_visible_cards(visible)
        self.refresh_history()


class ConnectionPanel(QWidget):
    """Shown until the first readings arrive: status, advice, and the stations in range."""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self._sig = None
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 40, 0, 0)
        outer.addStretch(1)
        col = QWidget()
        col.setFixedWidth(560)
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.icon)
        self.title = QLabel()
        self.title.setFont(font(QApplication.font().pointSizeF() * 1.5, QFont.DemiBold))
        self.title.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.title)
        self.help = QLabel()
        self.help.setWordWrap(True)
        self.help.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        lay.addWidget(self.help)
        lay.addSpacing(6)
        self.list_card = Card("Nearby BLUETTI stations", "antenna")
        self.rows = QGridLayout()
        self.rows.setHorizontalSpacing(10)
        self.rows.setVerticalSpacing(10)
        self.rows.setColumnStretch(1, 1)
        self.list_card.body.addLayout(self.rows)
        lay.addWidget(self.list_card)
        lay.addStretch(1)
        outer.addWidget(col)
        outer.addStretch(1)

    def refresh(self):
        w = self.win
        self.icon.setPixmap(glyph("antenna", 56, muted(self, 0.5)))
        self.title.setText(w.phase_text.rstrip("."))
        phase = w.phase_text.lower()
        if "choose" in phase:
            msg = ("Pick your power station below; Bluetti Monitor remembers it. If yours isn't listed, keep "
                   "this computer within about 10 m of it and make sure the BLUETTI phone app isn't connected "
                   "to it over Bluetooth (it takes one connection at a time).")
        elif "missing" in phase:
            msg = ("The Bluetooth library (bleak) isn't installed. Install the RPM, or run "
                   "\u2018pip install --user bleak\u2019, then restart. Demo mode works meanwhile (Options menu).")
        elif "paused" in phase:
            msg = "The connection is paused so the BLUETTI phone app can use Bluetooth."
        elif "not in range" in phase or "no bluetti" in phase or "scanning" in phase:
            msg = ("Keep this computer within about 10 m of the power station. If the BLUETTI phone app "
                   "is connected over Bluetooth, disconnect it: the station only takes one Bluetooth "
                   "connection at a time.")
        elif "disconnected" in phase:
            msg = ("The connection dropped or the station refused it. Usually another device (the phone "
                   "app, or the Mac app) is connected to it. Retrying every few seconds.")
        else:
            msg = "Setting up the encrypted link. This takes a few seconds."
        self.help.setText(msg)
        pal = self.help.palette()
        pal.setColor(QPalette.WindowText, muted(self, 0.65))
        self.help.setPalette(pal)

        sig = tuple((st["address"], st["name"], st.get("rssi")) for st in w.stations[:10])
        if sig == self._sig:
            return
        self._sig = sig
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        sub = font(QApplication.font().pointSizeF() * 0.85)
        for r, st in enumerate(w.stations[:10]):
            ic = QLabel()
            ic.setPixmap(glyph("battery", 22, GREEN))
            name = QLabel(f"<b>{R.friendly_name(st['name'])}</b><br>"
                          f"<span style='font-size:{sub.pointSizeF():.1f}pt; opacity:0.6'>{st['name']}</span>")
            bars = SignalBars()
            bars.set(st.get("rssi"))
            btn = QPushButton("Connect")
            btn.clicked.connect(lambda _=False, addr=st["address"]: w.connect_to(addr))
            self.rows.addWidget(ic, r, 0)
            self.rows.addWidget(name, r, 1)
            self.rows.addWidget(bars, r, 2)
            self.rows.addWidget(btn, r, 3)
        self.list_card.setVisible(bool(w.stations))


class ReadingsPage(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter readings…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self.refresh)
        lay.addWidget(self.filter)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Reading", "Value", "Register"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Interactive)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.setColumnWidth(0, 340)
        lay.addWidget(self.tree)
        note = QLabel("?  = mapped from other V2 models (Elite 30/100 V2, AC180…), not yet confirmed on this one. "
                      "The Registers tab shows every raw value.")
        note.setWordWrap(True)
        pal = note.palette()
        pal.setColor(QPalette.WindowText, muted(self, 0.6))
        note.setPalette(pal)
        lay.addWidget(note)

    def refresh(self):
        regs = self.win.registers
        q = self.filter.text().strip().lower()
        self.tree.clear()
        bold = font(None, QFont.DemiBold)
        for g in R.GROUPS:
            rows = []
            for f in R.FIELDS:
                if f["group"] != g:
                    continue
                if q and q not in f["label"].lower() and q not in str(f["reg"]):
                    continue
                v = R.fmt(f, regs)
                if v is not None:
                    rows.append((f, v))
            if not rows:
                continue
            head = QTreeWidgetItem([g, "", ""])
            head.setFont(0, bold)
            head.setFirstColumnSpanned(True)
            self.tree.addTopLevelItem(head)
            for f, v in rows:
                item = QTreeWidgetItem(["    " + f["label"] + ("  ?" if f["tentative"] else ""), v, str(f["reg"])])
                item.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
                if f["tentative"]:
                    item.setToolTip(0, "Mapping taken from other V2 models; not yet confirmed on the Elite 200 V2.")
                item.setForeground(2, muted(self, 0.5))
                self.tree.addTopLevelItem(item)


class RegistersPage(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        bar = QHBoxLayout()
        self.hide_zero = QCheckBox("Hide zero registers")
        self.hide_zero.setChecked(win.settings.value("hideZero", "true") == "true")
        self.hide_zero.toggled.connect(lambda v: (win.settings.setValue("hideZero", "true" if v else "false"), self.refresh()))
        bar.addWidget(self.hide_zero)
        self.count = QLabel()
        bar.addWidget(self.count)
        bar.addStretch(1)
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(200)
        self.progress.setVisible(False)
        bar.addWidget(self.progress)
        self.scan_btn = QPushButton("Deep scan…")
        self.scan_btn.setToolTip("Reads every register from 0 to 19,999 (read-only). Takes several minutes.")
        self.scan_btn.clicked.connect(win.deep_scan)
        bar.addWidget(self.scan_btn)
        lay.addLayout(bar)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Register", "Hex", "Decimal", "Signed", "ASCII", "Meaning"])
        self.tree.setUniformRowHeights(True)
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        self.tree.setFont(mono)
        fm = self.tree.fontMetrics()
        for i, chars in enumerate((10, 8, 8, 8, 6)):
            self.tree.header().setSectionResizeMode(i, QHeaderView.Fixed)
            self.tree.setColumnWidth(i, fm.horizontalAdvance("0" * chars) + 24)
        self.tree.header().setSectionResizeMode(5, QHeaderView.Stretch)
        lay.addWidget(self.tree)

    def set_scan(self, data):
        self.progress.setVisible(not data["done"])
        self.progress.setMaximum(data["total"])
        self.progress.setValue(data["position"])
        self.scan_btn.setEnabled(data["done"])
        if data["done"]:
            self.count.setText(f"{len(self.win.registers)} registers read · deep scan found {data['found']} extra ranges")

    def refresh(self):
        regs, changed = self.win.registers, self.win.changed_at
        self.count.setText(f"{len(regs)} registers read")
        self.scan_btn.setEnabled(self.win.live)
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        hl = QColor("#ffd60a")
        hl.setAlphaF(0.20)
        now = time.time()
        covered = set()
        sections = []
        for b in R.BLOCKS:
            rng = range(b["start"], b["start"] + b["count"])
            covered.update(rng)
            addrs = [a for a in rng if a in regs]
            if addrs:
                sections.append((f"{b['name']}  ({rng.start}–{rng.stop - 1})", addrs))
        extra = sorted(a for a in regs if a not in covered)
        if extra:
            sections.append((f"Other (deep scan)  ({len(extra)} registers)", extra))
        bold = font(None, QFont.DemiBold)
        for title, addrs in sections:
            head = QTreeWidgetItem([title])
            head.setFont(0, bold)
            head.setFirstColumnSpanned(True)
            self.tree.addTopLevelItem(head)
            for a in addrs:
                v = regs[a]
                if self.hide_zero.isChecked() and v == 0:
                    continue
                if R.is_private(a):
                    item = QTreeWidgetItem([str(a), "hidden", "", "", "", "password"])
                else:
                    asc = "".join(chr(c) if 32 <= c < 127 else "·" for c in (v >> 8, v & 0xFF))
                    item = QTreeWidgetItem([str(a), f"0x{v:04X}", str(v), str(v - 0x10000 if v >= 0x8000 else v),
                                            asc, R.label_for_register(a, regs)])
                    if now - changed.get(a, 0) < 10:
                        for c in range(6):
                            item.setBackground(c, hl)
                for c in range(5):
                    item.setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
                head.addChild(item)
            head.setExpanded(True)
        self.tree.setUpdatesEnabled(True)


class LogPage(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        bar = QHBoxLayout()
        self.security = QLabel(" ")
        bar.addWidget(self.security)
        bar.addStretch(1)
        save = QPushButton("Save log…")
        save.clicked.connect(self.save)
        bar.addWidget(save)
        lay.addLayout(bar)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.text.setMaximumBlockCount(2000)
        lay.addWidget(self.text)

    def add(self, ts, msg):
        self.text.appendPlainText(f"{time.strftime('%H:%M:%S', time.localtime(ts))}  {msg}")

    def save(self):
        fn, _ = QFileDialog.getSaveFileName(self, "Save log", "bluetti-monitor.log", "Log files (*.log *.txt)")
        if fn:
            with open(fn, "w") as fh:
                fh.write(self.text.toPlainText())


# ----------------------------------------------------------------------------- main window

class MainWindow(QMainWindow):
    def __init__(self, engine, start_demo=False):
        super().__init__()
        self.engine = engine
        self.settings = QSettings("BluettiMonitor", "BluettiMonitor")
        self.registers, self.changed_at = {}, {}
        self.history = deque()
        self.stations, self.device_name, self.rssi = [], None, None
        self.phase_text, self.live, self.last_update = "Starting", False, None
        self.quitting = False
        self.setWindowTitle("Bluetti Monitor")
        self.setWindowIcon(app_icon())
        self.resize(1120, 860)
        self.setMinimumSize(900, 640)
        if self.settings.value("geometry"):
            self.restoreGeometry(self.settings.value("geometry"))

        self._build_toolbar()
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.overview = OverviewPage(self)
        self.readings = ReadingsPage(self)
        self.regs_page = RegistersPage(self)
        self.log_page = LogPage(self)
        self.tabs.addTab(self.overview, "Overview")
        self.tabs.addTab(self.readings, "All readings")
        self.tabs.addTab(self.regs_page, "Registers")
        self.tabs.addTab(self.log_page, "Log")
        self.tabs.currentChanged.connect(lambda _: self.refresh_tabs())
        self.setCentralWidget(self.tabs)
        self._build_tray()

        os.makedirs(DATA_DIR, exist_ok=True)
        self.pump = QTimer(self)
        self.pump.timeout.connect(self._drain)
        self.pump.start(100)
        self.clock = QTimer(self)
        self.clock.timeout.connect(self._tick)
        self.clock.start(1000)
        self.autosave = QTimer(self)
        self.autosave.timeout.connect(self._autosave)
        self.autosave.start(60_000)

        engine.set_poll_interval(float(self.settings.value("poll", 3)))
        if start_demo:
            engine.start_demo()
        else:
            engine.start_live(device_address=self.settings.value("station") or None)
        self.overview.refresh()

    # --- chrome
    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setIconSize(QSize(18, 18))
        self.addToolBar(tb)
        self.station_btn = QToolButton()
        self.station_btn.setText("Station")
        self.station_btn.setIcon(QIcon(glyph("battery", 18, self.palette().color(QPalette.WindowText))))
        self.station_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.station_btn.setPopupMode(QToolButton.InstantPopup)
        self.station_menu = QMenu(self)
        self.station_menu.aboutToShow.connect(self._fill_station_menu)
        self.station_btn.setMenu(self.station_menu)
        tb.addWidget(self.station_btn)
        tb.addSeparator()
        self.pill = StatusPill()
        tb.addWidget(self.pill)
        self.bars = SignalBars()
        tb.addWidget(self.bars)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.pause_act = QAction("Pause", self)
        self.pause_act.setToolTip("Disconnect so the BLUETTI phone app can use Bluetooth")
        self.pause_act.triggered.connect(self.toggle_pause)
        tb.addAction(self.pause_act)
        opts = QToolButton()
        opts.setText("Options")
        opts.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self)
        sub = menu.addMenu("Update every")
        grp = QActionGroup(self)
        cur = float(self.settings.value("poll", 3))
        for secs in (2, 3, 5, 10, 30):
            a = sub.addAction(f"{secs} seconds")
            a.setCheckable(True)
            a.setChecked(abs(cur - secs) < 0.01)
            grp.addAction(a)
            a.triggered.connect(lambda _=False, s=secs: self._set_poll(s))
        self.demo_act = menu.addAction("Demo mode")
        self.demo_act.setCheckable(True)
        self.demo_act.triggered.connect(self.toggle_demo)
        menu.addSeparator()
        menu.addAction("Export snapshot…", self.export_snapshot)
        menu.addAction("Open data folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(DATA_DIR)))
        menu.addSeparator()
        menu.addAction("Forget this station", self.forget_station)
        menu.addSeparator()
        menu.addAction("About Bluetti Monitor", self.show_about)
        menu.addAction("Quit", self.quit)
        opts.setMenu(menu)
        tb.addWidget(opts)

    def show_about(self):
        box = QMessageBox(self)
        box.setWindowTitle("About Bluetti Monitor")
        box.setIconPixmap(app_icon().pixmap(64, 64))
        box.setTextFormat(Qt.RichText)
        box.setText(f"<b>Bluetti Monitor {__version__}</b><br>An unofficial, read-only monitor for BLUETTI power stations.")
        box.setInformativeText(
            "<p>A hobby project, provided as is with no warranty. Use at your own risk.</p>"
            "<p><b>Unofficial.</b> BLUETTI is a trademark of its owner. This app is not affiliated with, "
            "endorsed by or supported by BLUETTI.</p>"
            "<p><b>Read-only.</b> It only reads values and never changes settings, switches outputs "
            "or alters charging.</p>"
            "<p><b>Encryption keys.</b> The Bluetooth encryption keys it uses are the same for every unit "
            "and come from the open-source bluetti-bt-lib project, where they are already public. They only "
            "work within Bluetooth range and give no access to any BLUETTI account, cloud service or Wi-Fi.</p>"
            "<p><b>AI-assisted.</b> Much of this code was written with the help of AI tools.</p>"
            "<p>MIT licence. Bundles bleak (MIT).</p>")
        box.exec()

    def _build_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(app_icon(), self)
        m = QMenu()
        m.addAction("Open Bluetti Monitor", self.show_window)
        self.tray_pause = m.addAction("Pause", self.toggle_pause)
        m.addSeparator()
        m.addAction("Quit", self.quit)
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda r: self.toggle_window() if r == QSystemTrayIcon.Trigger else None)
        self.tray.setToolTip("Bluetti Monitor")
        self.tray.show()

    def _fill_station_menu(self):
        m = self.station_menu
        m.clear()
        if not self.stations:
            m.addAction("No stations heard yet").setEnabled(False)
        cur = self.engine.current_address or self.engine.selected_device
        for st in self.stations:
            label = f"{R.friendly_name(st['name'])}   ·   {st.get('rssi', '?')} dBm"
            a = m.addAction(label)
            a.setCheckable(True)
            a.setChecked(st["address"] == cur)
            a.setToolTip(st["name"])
            a.triggered.connect(lambda _=False, addr=st["address"]: self.connect_to(addr))

    # --- actions
    def connect_to(self, address):
        self.settings.setValue("station", address)
        self.registers, self.changed_at = {}, {}
        self.history.clear()
        self.demo_act.setChecked(False)
        self.engine.switch_to(address)
        self.refresh_tabs()

    def forget_station(self):
        self.settings.remove("station")
        self.engine.selected_device = None
        self.engine.switch_to(None)
        self.registers, self.changed_at = {}, {}
        self.history.clear()
        self.refresh_tabs()

    def toggle_pause(self):
        paused = not self.engine.paused
        self.engine.set_paused(paused)
        for a in (self.pause_act, getattr(self, "tray_pause", None)):
            if a:
                a.setText("Reconnect" if paused else "Pause")

    def toggle_demo(self, on):
        self.registers, self.changed_at = {}, {}
        self.history.clear()
        if on:
            self.engine.start_demo()
        else:
            self.engine.start_live(device_address=self.settings.value("station") or None)

    def _set_poll(self, secs):
        self.settings.setValue("poll", secs)
        self.engine.set_poll_interval(secs)

    def deep_scan(self):
        self.engine.request_deep_scan()
        self.regs_page.set_scan({"position": 0, "total": 20000, "found": 0, "done": False})

    def export_snapshot(self):
        fn, _ = QFileDialog.getSaveFileName(self, "Export snapshot",
                                            time.strftime("bluetti-%Y-%m-%d_%H%M.json"), "JSON (*.json)")
        if fn:
            with open(fn, "w") as fh:
                fh.write(self.snapshot_json())

    def snapshot_json(self) -> str:
        regs = self.registers
        return json.dumps({
            "app": "Bluetti Monitor (Linux)",
            "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "device": self.device_name or "",
            "decoded": [{"key": f["key"], "label": f["label"], "group": f["group"], "register": f["reg"],
                         "value": R.fmt(f, regs), "confirmed": not f["tentative"]}
                        for f in R.FIELDS if R.fmt(f, regs) is not None],
            "cells": R.cells(regs) or {},
            "registers": {str(k): v for k, v in sorted(regs.items()) if not R.is_private(k)},
        }, indent=1)

    def _autosave(self):
        if self.registers and not self.engine.demo_mode:
            try:
                with open(os.path.join(DATA_DIR, "latest.json"), "w") as fh:
                    fh.write(self.snapshot_json())
            except OSError:
                pass

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def toggle_window(self):
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.show_window()

    def quit(self):
        self.quitting = True
        self.close()
        QApplication.quit()

    def closeEvent(self, e):
        self.settings.setValue("geometry", self.saveGeometry())
        if self.tray and not self.quitting:
            self.hide()                      # keep running in the tray, like the Mac menu-bar app
            if not self.settings.value("trayHintShown"):
                self.tray.showMessage("Bluetti Monitor", "Still running in the system tray. Quit from its menu.",
                                      QSystemTrayIcon.Information, 4000)
                self.settings.setValue("trayHintShown", "1")
            e.ignore()
            return
        self.engine.stop()
        e.accept()

    # --- derived values (same rules as the Mac app)
    def station_name(self):
        model = R.text("model", self.registers)
        if model and model != "—":
            return R.friendly_model(model)
        return R.friendly_name(self.device_name) if self.device_name else "Bluetti"

    def battery_watts(self):
        n = lambda k: R.number(k, self.registers) or 0
        if R.number("soc", self.registers) is None:
            return None
        return n("gridInPower") + n("pvInPower") - n("acOutPower") - n("dcOutPower")

    def state_line(self):
        w = self.battery_watts()
        if w is None:
            return " "
        full = int(R.number("timeToFull", self.registers) or 0)
        empty = int(R.number("timeToEmpty", self.registers) or 0)
        if w > 5:
            return f"Charging · full in {R.format_minutes(full)}" if full > 0 else "Charging"
        if w < -5:
            return f"Discharging · {R.format_minutes(empty)} left" if empty > 0 else "Discharging"
        return "Idle"

    # --- event handling
    def _drain(self):
        changed = False
        while True:
            try:
                ev = self.engine.queue.get_nowait()
            except queue.Empty:
                break
            kind, data = ev["type"], ev["data"]
            if kind == "log":
                self.log_page.add(ev["time"], data)
                self._log_to_file(ev["time"], data)
            elif kind == "phase":
                self.phase_text = data
                self.live = data.startswith("Live") or data.startswith("Demo")
                changed = True
            elif kind == "device_info":
                self.device_name = data.get("name")
            elif kind == "discovered":
                self.stations = data
                cur = self.engine.current_address or self.engine.selected_device
                self.rssi = next((s.get("rssi") for s in data if s["address"] == cur), self.rssi)
                changed = True
            elif kind == "security":
                if data.get("encrypted"):
                    sig = "verified" if data.get("signature") else "not verified"
                    self.log_page.security.setText(f"Encrypted link · station signature {sig}")
                else:
                    self.log_page.security.setText("Unencrypted link")
            elif kind == "deepscan":
                self.regs_page.set_scan(data)
            elif kind == "telemetry":
                now = time.time()
                new = data.get("registers", {})
                for a, v in new.items():
                    if a in self.registers and self.registers[a] != v:
                        self.changed_at[a] = now
                self.registers = dict(new)
                self.last_update = now
                n = lambda k: R.number(k, self.registers)
                self.history.append((now, n("soc"), n("acOutPower"), n("dcOutPower"), n("pvInPower"), n("gridInPower")))
                while self.history and self.history[0][0] < now - 86400:
                    self.history.popleft()
                if not self.engine.demo_mode and self.engine.current_address:
                    self.settings.setValue("station", self.engine.current_address)
                changed = True
        if changed:
            self.refresh_tabs()
            self._tick()

    def _log_to_file(self, ts, msg):
        try:
            with open(os.path.join(DATA_DIR, "bluetti-monitor.log"), "a") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))}  {msg}\n")
        except OSError:
            pass

    def _tick(self):
        p = self.phase_text
        if self.live and self.last_update:
            s = int(time.time() - self.last_update)
            label = ("Demo" if self.engine.demo_mode else "Live") + (" · just now" if s < 2 else f" · {s}s ago")
            colour = BLUE if self.engine.demo_mode else GREEN
        else:
            label = p.rstrip(".")
            pl = p.lower()
            colour = "#8e8e93" if "paused" in pl else RED if ("missing" in pl or "disconnected" in pl) else ORANGE
        self.pill.set(label, colour)
        self.bars.set(self.rssi if self.live and not self.engine.demo_mode else None)
        name = self.station_name() if self.registers else "Bluetti Monitor"
        self.setWindowTitle(name if name == "Bluetti Monitor" else f"{name} — Bluetti Monitor")
        soc = R.number("soc", self.registers)
        if self.tray:
            self.tray.setIcon(tray_icon(soc))
            self.tray.setToolTip(f"{name}\n{int(soc)} % · {self.state_line()}" if soc is not None else f"Bluetti Monitor\n{p}")
        self.demo_act.setChecked(self.engine.demo_mode)

    def refresh_tabs(self):
        i = self.tabs.currentIndex()
        self.overview.refresh()
        if i == 1:
            self.readings.refresh()
        elif i == 2:
            self.regs_page.refresh()


def app_icon() -> QIcon:
    icon = QIcon.fromTheme(APP_ID)
    if not icon.isNull():
        return icon
    for base in ("/app/share/icons/hicolor/256x256/apps/%s.png" % APP_ID,
                 "/usr/share/icons/hicolor/256x256/apps/bluetti-monitor.png",
                 os.path.join(os.path.dirname(__file__), "..", "icon.png")):
        if os.path.exists(base):
            return QIcon(base)
    return QIcon(glyph("battery", 64, GREEN))


def tray_icon(soc) -> QIcon:
    """Battery ring with the percentage, readable at tray size."""
    size = 64
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    rect = QRectF(5, 5, size - 10, size - 10)
    ink = QApplication.palette().color(QPalette.WindowText)   # follows the Plasma colour scheme
    track = QColor(ink)
    track.setAlpha(60)
    p.setPen(QPen(track, 7))
    p.drawEllipse(rect)
    if soc is not None:
        p.setPen(QPen(soc_colour(soc), 7, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(rect, 90 * 16, int(-soc / 100 * 360 * 16))
        f = QFont(QApplication.font())
        f.setPixelSize(26 if soc < 100 else 21)
        f.setBold(True)
        p.setFont(f)
        p.setPen(ink)
        p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, str(int(soc)))
    p.end()
    return QIcon(pm)


def run(engine, start_demo=False) -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Bluetti Monitor")
    app.setDesktopFileName(APP_ID)
    app.setQuitOnLastWindowClosed(False)
    win = MainWindow(engine, start_demo=start_demo)
    win.show()
    code = app.exec()
    engine.stop()
    return code
