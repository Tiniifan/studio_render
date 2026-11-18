"""The window of a text asset: its text, its font and its color (a color wheel)."""
import colorsys
import math

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

import images

ACCENT = "#4cc2ff"
BG = "#1c1c1c"
LINE = "#3a3a3a"
FAINT = "#8a8a8a"
WARNING = "#f0b44c"


def hsv_to_rgb(h, s, v):
    """Vectorised HSV (0..1) to RGB (0..1)."""
    i = np.floor(h * 6.0).astype(int) % 6
    f = h * 6.0 - np.floor(h * 6.0)
    p, q, t = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
    choices = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)]
    channels = []
    for channel in range(3):
        channels.append(np.select([i == k for k in range(6)], [np.broadcast_to(c[channel], h.shape) for c in choices]))
    return np.stack(channels, axis=-1)


class ColorWheel(QtWidgets.QWidget):
    """Hue around the wheel, saturation from the centre; the brightness comes from the slider next to it."""
    changed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hue, self.saturation, self.value = 0.0, 1.0, 1.0
        self._image = None
        self._image_key = None
        self.setMinimumSize(180, 180)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.setCursor(QtCore.Qt.CrossCursor)

    def color(self):
        r, g, b = colorsys.hsv_to_rgb(self.hue, self.saturation, self.value)
        return QtGui.QColor(int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))

    def set_color(self, color):
        h, s, v = colorsys.rgb_to_hsv(color.redF(), color.greenF(), color.blueF())
        # A grey keeps the hue it had
        if s > 0:
            self.hue = h
        self.saturation, self.value = s, v
        self.update()

    def set_value(self, value):
        self.value = value
        self.update()
        self.changed.emit()

    def _geometry(self):
        side = min(self.width(), self.height()) - 8
        return QtCore.QPointF(self.width() / 2, self.height() / 2), side / 2

    def _wheel(self, radius):
        size = max(int(radius * 2 * self.devicePixelRatioF()), 2)
        key = (size, round(self.value, 3))
        if key != self._image_key:
            coords = (np.arange(size) + 0.5) / size * 2 - 1
            x, y = np.meshgrid(coords, coords)
            distance = np.sqrt(x * x + y * y)
            hue = (np.arctan2(-y, x) / (2 * math.pi)) % 1.0
            rgb = hsv_to_rgb(hue, np.clip(distance, 0, 1), np.full_like(hue, self.value))
            alpha = np.clip((1.0 - distance) * size / 2 + 0.5, 0, 1)    # smooth edge
            rgba = np.dstack([rgb, alpha]) * 255
            self._pixels = np.ascontiguousarray(rgba.astype(np.uint8))
            image = QtGui.QImage(self._pixels.data, size, size, size * 4, QtGui.QImage.Format_RGBA8888)
            image.setDevicePixelRatio(self.devicePixelRatioF())
            self._image, self._image_key = image, key
        return self._image

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        center, radius = self._geometry()
        painter.drawImage(QtCore.QPointF(center.x() - radius, center.y() - radius), self._wheel(radius))
        painter.setPen(QtGui.QPen(QtGui.QColor(LINE), 1))
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawEllipse(center, radius, radius)
        angle = self.hue * 2 * math.pi
        marker = QtCore.QPointF(center.x() + math.cos(angle) * self.saturation * radius,
                                center.y() - math.sin(angle) * self.saturation * radius)
        painter.setPen(QtGui.QPen(QtGui.QColor("#000000"), 3))
        painter.drawEllipse(marker, 6, 6)
        painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 1.5))
        painter.setBrush(self.color())
        painter.drawEllipse(marker, 6, 6)

    def _pick(self, position):
        center, radius = self._geometry()
        dx, dy = position.x() - center.x(), center.y() - position.y()
        self.hue = (math.atan2(dy, dx) / (2 * math.pi)) % 1.0
        self.saturation = min(1.0, math.hypot(dx, dy) / max(radius, 1))
        self.update()
        self.changed.emit()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._pick(event.position())

    def mouseMoveEvent(self, event):
        if event.buttons() & QtCore.Qt.LeftButton:
            self._pick(event.position())


class ValueSlider(QtWidgets.QWidget):
    """Brightness of the wheel: from black (bottom) to the full color (top)."""
    changed = QtCore.Signal(float)

    def __init__(self, wheel):
        super().__init__()
        self.wheel = wheel
        self.setFixedWidth(22)
        self.setMinimumHeight(180)
        self.setCursor(QtCore.Qt.PointingHandCursor)

    def _bar(self):
        return QtCore.QRectF(5, 6, self.width() - 10, self.height() - 12)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        bar = self._bar()
        r, g, b = colorsys.hsv_to_rgb(self.wheel.hue, self.wheel.saturation, 1.0)
        gradient = QtGui.QLinearGradient(bar.topLeft(), bar.bottomLeft())
        gradient.setColorAt(0, QtGui.QColor.fromRgbF(r, g, b))
        gradient.setColorAt(1, QtGui.QColor("#000000"))
        painter.setPen(QtGui.QPen(QtGui.QColor(LINE), 1))
        painter.setBrush(gradient)
        painter.drawRoundedRect(bar, 4, 4)
        y = bar.bottom() - self.wheel.value * bar.height()
        painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 2))
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawRoundedRect(QtCore.QRectF(1, y - 3, self.width() - 2, 6), 2, 2)

    def _pick(self, position):
        bar = self._bar()
        self.changed.emit(min(1.0, max(0.0, (bar.bottom() - position.y()) / bar.height())))

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._pick(event.position())

    def mouseMoveEvent(self, event):
        if event.buttons() & QtCore.Qt.LeftButton:
            self._pick(event.position())


class TextDialog(QtWidgets.QDialog):
    """Add text / Edit text: OK makes the text (spec()), Cancel leaves everything as it was."""

    def __init__(self, parent, spec=None, title="Add text"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(640, 460)
        spec = spec or images.text_spec("", images.DEFAULT_FONT, images.DEFAULT_COLOR)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        columns = QtWidgets.QHBoxLayout()
        columns.setSpacing(16)
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(QtWidgets.QLabel("Text"))
        self.text = QtWidgets.QPlainTextEdit(spec["text"])
        self.text.setPlaceholderText("Name of the technique")
        self.text.setTabChangesFocus(True)
        left.addWidget(self.text, 1)
        left.addWidget(QtWidgets.QLabel("Font"))
        self.font_box = QtWidgets.QComboBox()
        self.font_box.addItems(list(images.FONTS))
        self.font_box.setCurrentText(spec["font"])
        left.addWidget(self.font_box)
        columns.addLayout(left, 1)

        right = QtWidgets.QVBoxLayout()
        right.setSpacing(6)
        right.addWidget(QtWidgets.QLabel("Color"))
        wheel_row = QtWidgets.QHBoxLayout()
        self.wheel = ColorWheel()
        self.slider = ValueSlider(self.wheel)
        wheel_row.addWidget(self.wheel, 1)
        wheel_row.addWidget(self.slider)
        right.addLayout(wheel_row, 1)
        hex_row = QtWidgets.QHBoxLayout()
        self.swatch = QtWidgets.QLabel()
        self.swatch.setFixedSize(28, 22)
        hex_row.addWidget(self.swatch)
        self.hex = QtWidgets.QLineEdit()
        self.hex.setMaxLength(7)
        self.hex.setFixedWidth(90)
        self.hex.setValidator(QtGui.QRegularExpressionValidator(QtCore.QRegularExpression("#?[0-9A-Fa-f]{0,6}")))
        hex_row.addWidget(self.hex)
        hex_row.addStretch(1)
        right.addLayout(hex_row)
        columns.addLayout(right)
        layout.addLayout(columns, 1)

        layout.addWidget(QtWidgets.QLabel("Preview"))
        self.preview = QtWidgets.QLabel()
        self.preview.setAlignment(QtCore.Qt.AlignCenter)
        self.preview.setMinimumHeight(110)
        self.preview.setStyleSheet(f"background: #525761; border: 1px solid {LINE}; border-radius: 4px;")
        layout.addWidget(self.preview)
        self.note = QtWidgets.QLabel()
        self.note.setStyleSheet(f"color: {WARNING};")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.ok = buttons.button(QtWidgets.QDialogButtonBox.Ok)
        layout.addWidget(buttons)

        self.wheel.set_color(QtGui.QColor(spec["color"]))
        self.wheel.changed.connect(self._wheel_changed)
        self.slider.changed.connect(self.wheel.set_value)
        self.hex.textEdited.connect(self._hex_edited)
        self.text.textChanged.connect(self._update)
        self.font_box.currentTextChanged.connect(self._update)
        self._wheel_changed()
        self.text.setFocus()

    def color(self):
        return self.wheel.color().name()

    def spec(self):
        return images.text_spec(self.text.toPlainText(), self.font_box.currentText(), self.color())

    def _wheel_changed(self):
        self.hex.setText(self.color())
        self.slider.update()
        self._update()

    def _hex_edited(self, text):
        text = text if text.startswith("#") else "#" + text
        if len(text) == 7:
            self.wheel.set_color(QtGui.QColor(text))
            self.slider.update()
            self._update()

    def _update(self):
        color = self.color()
        self.swatch.setStyleSheet(f"background: {color}; border: 1px solid {LINE}; border-radius: 3px;")
        spec = self.spec()
        drawable = images.drawable(spec)
        self.ok.setEnabled(drawable)
        missing = images.missing_characters(spec)
        self.note.setText(f"Not in {spec['font']}, not drawn: {' '.join(missing)}" if missing else "")
        if not drawable:
            self.preview.setPixmap(QtGui.QPixmap())
            self.preview.setText(f"<span style='color:{FAINT}'>Type a text</span>")
            return
        pixels = np.ascontiguousarray(images.render_text(spec))
        height, width = pixels.shape[:2]
        image = QtGui.QImage(pixels.data, width, height, width * 4, QtGui.QImage.Format_RGBA8888).copy()
        pixmap = QtGui.QPixmap.fromImage(image)
        room = QtCore.QSize(max(self.preview.width() - 16, 50), max(self.preview.height() - 12, 40))
        if pixmap.width() > room.width() or pixmap.height() > room.height():
            pixmap = pixmap.scaled(room, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        self.preview.setPixmap(pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update()
