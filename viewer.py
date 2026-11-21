"""The window: assets, player (OpenGL view drawn by moderngl) and timeline, in Qt."""
import io
import os
import sys
import threading
import time
import traceback

import moderngl
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtSvg import QSvgRenderer

import asset_windows
import assets as assets_module
import eleven
import export as export_module
import images
import model as model_module
import offscreen as offscreen_module
import project as project_module
import squirrel
import text_dialog
from overlays import OverlayRenderer
from preferences import Preferences, SCREENSHOT_CLIPBOARD, SCREENSHOT_FILE
from project import Project, ProjectError, ProjectVersionError
from renderer import Renderer
from scene import View
from version import VERSION

APP = "studio_render"
FPS = 30
SPEEDS = (0.25, 0.5, 1.0, 1.5, 2.0)
SCREEN_RATIO = 5 / 3
EXPORT_SIZE = (800, 480)
ASSET_MIME = "application/x-studio-render-asset"
ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")

BG = "#1c1c1c"
PANEL = "#202020"
PANEL_2 = "#2b2b2b"
RAISED = "#373737"
LINE = "#3a3a3a"
FG = "#ffffff"
MUTED = "#c5c5c5"
FAINT = "#8a8a8a"
ACCENT = "#4cc2ff"
ICON_REST = "#9aa3b0"
DANGER = "#e85c5c"
WARNING = "#f0b44c"
# Badge of each template lock: icon, text, color
LOCK_BADGES = {
    assets_module.LOCKED: ("lock", "locked", DANGER),
    assets_module.REPLACE_ONLY: ("lock_replace", "replace only", WARNING),
}
CLIP_ANIM = "#2f6a8f"
CLIP_FX = "#2c7d8c"
CLIP_CAM = ("#5a5f6b", "#4f5460")
CLIP_PLAN = ("#3d3d3d", "#343434")
CLIP_IMAGE = "#7a55a8"
CLIP_TEXT = "#9a6e2a"

KIND_ICONS = {
    assets_module.ANIMATION: "animation", assets_module.SKELETON: "skeleton", assets_module.IMAGE: "image",
    assets_module.CAMERA: "camera", assets_module.SQUIRREL: "squirrel", assets_module.LIGHT: "light",
    assets_module.OTHER: "other", assets_module.MODEL: "other", assets_module.PACKAGE: "other",
    assets_module.CUSTOM: "skeleton", assets_module.TEXT: "text",
}

STYLE = f"""
QMainWindow, QWidget#root {{ background: {BG}; }}
QMenuBar {{ background: {PANEL}; border-bottom: 1px solid {LINE}; padding: 4px 6px; }}
QMenuBar::item {{ padding: 5px 9px; border-radius: 4px; color: {MUTED}; background: transparent; }}
QMenuBar::item:selected, QMenuBar::item:pressed {{ background: {RAISED}; color: {FG}; }}
QMenu {{ background: {PANEL_2}; border: 1px solid {LINE}; border-radius: 6px; padding: 4px; }}
QMenu::item {{ padding: 6px 28px 6px 26px; border-radius: 4px; color: {FG}; }}
QMenu::item:selected {{ background: {RAISED}; }}
QMenu::item:disabled {{ color: {FAINT}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 4px 2px; }}
QMenu::indicator {{ width: 14px; height: 14px; left: 6px; }}
QSplitter::handle {{ background: {PANEL}; }}
QSplitter::handle:horizontal {{ border-left: 1px solid {LINE}; width: 5px; }}
QSplitter::handle:vertical {{ border-top: 1px solid {LINE}; height: 5px; }}
QSplitter::handle:hover {{ background: {ACCENT}; }}
QWidget#panelHead {{ background: {PANEL}; border-bottom: 1px solid {LINE}; }}
QWidget#transport {{ background: {PANEL}; border-top: 1px solid {LINE}; }}
QLabel#panelTitle {{ font-size: 8pt; font-weight: 600; letter-spacing: 1px; color: {MUTED}; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#version {{ color: {MUTED}; font-size: 11px; }}
QLineEdit {{ background: {BG}; border: 1px solid {LINE}; border-radius: 4px; padding: 3px 8px; color: {FG}; }}
QLineEdit:focus {{ border-color: {ACCENT}; }}
QLineEdit#rename {{ border: 1px solid {ACCENT}; border-radius: 3px; padding: 1px 4px; }}
QPushButton#importButton, QToolButton#speed {{ background: {PANEL_2}; border: 1px solid {LINE}; border-radius: 4px;
    padding: 3px 9px; color: {FG}; }}
QPushButton#importButton:hover, QToolButton#speed:hover {{ background: {RAISED}; }}
QToolButton#speed::menu-indicator {{ image: none; width: 0; }}
QPushButton#seg {{ background: {PANEL_2}; border: 1px solid {LINE}; padding: 3px 10px; color: {MUTED}; }}
QPushButton#seg:checked {{ background: {RAISED}; color: {FG}; }}
QPushButton#segLeft {{ background: {PANEL_2}; border: 1px solid {LINE}; border-top-left-radius: 4px;
    border-bottom-left-radius: 4px; padding: 3px 10px; color: {MUTED}; }}
QPushButton#segRight {{ background: {PANEL_2}; border: 1px solid {LINE}; border-left: 0; border-top-right-radius: 4px;
    border-bottom-right-radius: 4px; padding: 3px 10px; color: {MUTED}; }}
QPushButton#segLeft:checked, QPushButton#segRight:checked {{ background: {RAISED}; color: {FG}; }}
QCheckBox {{ color: {MUTED}; spacing: 6px; }}
QToolButton#tool {{ border: 1px solid transparent; border-radius: 5px; background: transparent; color: {MUTED}; padding: 0 6px; }}
QToolButton#tool:hover {{ background: {RAISED}; border-color: {LINE}; color: {FG}; }}
QToolButton#head {{ border: 0; border-radius: 4px; background: transparent; }}
QToolButton#head:hover {{ background: {RAISED}; }}
QToolButton#play {{ border: 0; border-radius: 19px; background: {ACCENT}; }}
QToolButton#play:hover {{ background: #7fd3ff; }}
QComboBox#engine {{ background: {PANEL_2}; border: 1px solid {LINE}; border-radius: 4px; padding: 3px 8px; color: {FG}; min-width: 230px; }}
QScrollArea {{ border: 0; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QSlider::groove:horizontal {{ height: 4px; background: {LINE}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 12px; margin: -5px 0; border-radius: 6px; }}
QLabel#toast {{ background: {RAISED}; border: 1px solid {LINE}; border-radius: 6px; padding: 7px 12px; color: {FG}; }}
QLabel#overlay {{ background: rgba(10, 12, 16, 158); padding: 2px 6px; border-radius: 3px; color: #e8edf5; }}
QToolTip {{ background: {PANEL_2}; color: {FG}; border: 1px solid {LINE}; }}
QWidget#templateBar {{ background: {PANEL_2}; border-top: 1px solid {LINE}; border-left: 3px solid {ACCENT}; }}
QLabel#templateName {{ color: {FG}; font-weight: 600; }}
QLabel#templateTag {{ color: {ACCENT}; font-size: 8pt; font-weight: 600; letter-spacing: 1px; }}
"""


def mono_font(pixel_size=11, weight=QtGui.QFont.Medium):
    font = QtGui.QFont()
    font.setFamilies(["Cascadia Mono", "Consolas", "Courier New"])
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    font.setStyleHint(QtGui.QFont.Monospace)
    return font


def two(value):
    return f"{int(value):02d}"


def timecode(frame):
    seconds, sub = divmod(int(frame), FPS)
    minutes, seconds = divmod(seconds, 60)
    return f"{two(minutes)}:{two(seconds)}:{two(sub)}"


# Icons

_svg_cache = {}
_pixmap_cache = {}


def svg_source(name):
    if name not in _svg_cache:
        with open(os.path.join(ICON_FOLDER, name + ".svg"), "r", encoding="utf-8") as stream:
            _svg_cache[name] = stream.read()
    return _svg_cache[name]


def svg_renderer(name, color):
    key = (name, color)
    if key not in _pixmap_cache:
        _pixmap_cache[key] = QSvgRenderer(QtCore.QByteArray(svg_source(name).replace("currentColor", color).encode("utf-8")))
    return _pixmap_cache[key]


def draw_icon(painter, name, color, rect):
    """Draws an icon in rect, keeping its aspect ratio."""
    renderer = svg_renderer(name, color)
    box = renderer.viewBoxF()
    if box.width() <= 0 or box.height() <= 0:
        return
    scale = min(rect.width() / box.width(), rect.height() / box.height())
    width, height = box.width() * scale, box.height() * scale
    target = QtCore.QRectF(rect.x() + (rect.width() - width) / 2, rect.y() + (rect.height() - height) / 2, width, height)
    renderer.render(painter, target)


def icon(name, color=FG, size=16):
    result = QtGui.QIcon()
    for scale in (1, 2):
        pixmap = QtGui.QPixmap(size * scale, size * scale)
        pixmap.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        draw_icon(painter, name, color, QtCore.QRectF(0, 0, size * scale, size * scale))
        painter.end()
        pixmap.setDevicePixelRatio(scale)
        result.addPixmap(pixmap)
    return result


def themed_icon(name, rest=MUTED, hover=FG, size=16):
    result = icon(name, rest, size)
    active = icon(name, hover, size)
    for pixmap_size in (QtCore.QSize(size, size), QtCore.QSize(size * 2, size * 2)):
        result.addPixmap(active.pixmap(pixmap_size), QtGui.QIcon.Active)
    return result


def tool_button(name, tip, size=16, object_name="tool", text=None, color=MUTED, hover=FG):
    button = QtWidgets.QToolButton()
    button.setObjectName(object_name)
    button.setIcon(themed_icon(name, color, hover, size))
    button.setIconSize(QtCore.QSize(size, size))
    button.setToolTip(tip)
    button.setCursor(QtCore.Qt.PointingHandCursor)
    button.setFocusPolicy(QtCore.Qt.NoFocus)
    if text:
        button.setText(text)
        button.setFont(mono_font(11))
    return button


# Player


# Longest stretch of idle work (GPU uploads, poses) between two events of the window, in seconds
PREPARE_SLICE = 0.02


class GLView(QOpenGLWidget):
    """The 3D view: the game camera of the scene, or the free camera."""
    gesture_started = QtCore.Signal()
    drawn = QtCore.Signal()

    def __init__(self, window):
        super().__init__()
        self.window_ = window
        self.view = View()
        self.renderer = None
        self.overlays = None
        self.ctx = None
        self.game_camera = True
        self.draw_outlines = True
        self.fragment_lighting = True
        self._last = None
        self._prepare = []
        self._prepare_timer = QtCore.QTimer(self, singleShot=True, interval=0)
        self._prepare_timer.timeout.connect(self._prepare_step)
        self.setFocusPolicy(QtCore.Qt.ClickFocus)
        self.setMinimumSize(100, 60)

    @property
    def scene(self):
        return self.window_.scene

    @property
    def frame(self):
        return self.window_.frame

    def initializeGL(self):
        self.ctx = moderngl.create_context()
        self.renderer = Renderer(self.ctx)
        self.overlays = OverlayRenderer(self.ctx)

    def release_scene(self):
        if self.renderer is None:
            return
        self.makeCurrent()
        self.renderer.release_scene()
        self.overlays.release_textures()
        self.doneCurrent()

    def queue_prepare(self):
        """In idle time, what the scene needs to be drawn (programs, buffers, textures), then the poses of every
        frame: the first playback runs as fast as the next ones."""
        scene = self.scene
        work = []
        for entry in scene.entries:
            for _, mesh, _, _, outline, _ in entry.draw_order:
                work.append(("mesh", entry, mesh, outline is not None))
        frames = max(scene.frame_count, 0) + 1
        for entry in scene.entries:
            if entry.model.bones and entry.animations:
                work.append(("poses", entry, 0, frames))
        self._prepare = work
        self._prepare_timer.start()

    def _prepare_step(self):
        if not self._prepare:
            return
        scene = self.scene
        deadline = time.perf_counter() + PREPARE_SLICE
        current = False
        try:
            while self._prepare and time.perf_counter() < deadline:
                work = self._prepare[0]
                if work[1] not in scene.entries:
                    self._prepare.pop(0)
                    continue
                if work[0] == "mesh":
                    if self.renderer is None:
                        break
                    if not current:
                        self.makeCurrent()
                        current = True
                    self._prepare.pop(0)
                    self.renderer.draw_outlines = self.draw_outlines
                    self.renderer.fragment_lighting = self.fragment_lighting
                    self.renderer.prepare(work[1], work[2], work[3])
                else:
                    if getattr(self.window_, "playing", False):
                        # The playback evaluates the poses itself, the window keeps its time for the frames
                        break
                    _, entry, frame, frames = work
                    mapped = scene.frame_map(entry, float(frame)) if scene.frame_map is not None else float(frame)
                    entry.pose.update(entry.animations, mapped)
                    if frame + 1 < frames:
                        self._prepare[0] = ("poses", entry, frame + 1, frames)
                    else:
                        self._prepare.pop(0)
        except Exception:
            traceback.print_exc()
            self._prepare = []
        finally:
            if current:
                self.doneCurrent()
        if self._prepare:
            waiting = self.renderer is None or (self._prepare[0][0] == "poses" and getattr(self.window_, "playing", False))
            self._prepare_timer.start(250 if waiting else 0)

    def game_pose(self):
        return self.scene.camera_at(self.frame) if self.game_camera else None

    def apply_camera(self, view, width, height):
        view.width, view.height = width, height
        pose = self.scene.camera_at(self.frame) if self.game_camera else None
        if pose is not None:
            view.update_game(*pose)
        else:
            view.update_orbit()

    def paintGL(self):
        if self.ctx is None:
            return
        ratio = self.devicePixelRatioF()
        width, height = max(int(self.width() * ratio), 1), max(int(self.height() * ratio), 1)
        fbo = self.ctx.detect_framebuffer(self.defaultFramebufferObject())
        fbo.viewport = (0, 0, width, height)
        self.apply_camera(self.view, width, height)
        self.renderer.draw_outlines = self.draw_outlines
        self.renderer.fragment_lighting = self.fragment_lighting
        try:
            self.renderer.render(fbo, self.scene, self.view, float(self.frame))
            self.overlays.draw(fbo, self.window_.project.layers_at(self.frame))
        except Exception:
            traceback.print_exc()
        self.drawn.emit()

    def _free(self):
        """The first gesture in game camera mode goes free, from where the game camera is."""
        if self.game_camera:
            pose = self.scene.camera_at(self.frame)
            if pose is not None:
                self.view.set_pose(*pose)
            self.gesture_started.emit()
            self.view.width, self.view.height = max(self.width(), 1), max(self.height(), 1)
            self.view.update_orbit()

    def mousePressEvent(self, event):
        if event.button() in (QtCore.Qt.LeftButton, QtCore.Qt.RightButton):
            self._last = event.position()
            self._free()

    def mouseMoveEvent(self, event):
        if self._last is None:
            return
        delta = event.position() - self._last
        self._last = event.position()
        if event.buttons() & QtCore.Qt.LeftButton:
            self.view.orbit(delta.x(), delta.y())
        elif event.buttons() & QtCore.Qt.RightButton:
            self.view.pan(delta.x(), delta.y())
        self.update()

    def mouseReleaseEvent(self, event):
        self._last = None

    def contextMenuEvent(self, event):
        event.accept()

    def wheelEvent(self, event):
        self._free()
        self.view.zoom(event.angleDelta().y() / 120.0)
        self.update()


class LayerEditor(QtWidgets.QWidget):
    """Over the 3D view: click a picture of the timeline to select it (frame and corner handles), drag it to move it,
    drag a corner to resize it (Shift: without keeping its proportions). Elsewhere, the mouse drives the camera."""
    HANDLE = 9
    MINIMUM = 4.0       # smallest side, in pixels of the 3DS screen

    def __init__(self, window, gl):
        super().__init__()
        self.window_ = window
        self.gl = gl
        self._drag = None           # (mode, layer, press in screen pixels, rectangle at the press)
        self._camera = False        # the press went to the 3D view
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.ClickFocus)

    def scale(self):
        return self.width() / images.SCREEN[0]

    def rect_of(self, layer):
        scale = self.scale()
        return QtCore.QRectF(layer.x * scale, layer.y * scale, layer.width * scale, layer.height * scale)

    def shown(self):
        return self.window_.project.layers_at(self.window_.frame)

    @staticmethod
    def corners(rect):
        return [rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft()]

    def hit(self, position):
        """(mode, layer): a corner (0 to 3) of the selected layer, else "move" and the top layer under the mouse."""
        shown = self.shown()
        selected = self.window_.selected_layer
        if selected in shown:
            for index, corner in enumerate(self.corners(self.rect_of(selected))):
                if abs(position.x() - corner.x()) <= self.HANDLE and abs(position.y() - corner.y()) <= self.HANDLE:
                    return index, selected
        for layer in reversed(shown):
            if self.rect_of(layer).contains(position):
                return "move", layer
        return None, None

    def paintEvent(self, event):
        layer = self.window_.selected_layer
        if layer is None or layer not in self.shown():
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = self.rect_of(layer)
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 150), 3))
        painter.drawRect(rect)
        painter.setPen(QtGui.QPen(QtGui.QColor(ACCENT), 1.5, QtCore.Qt.DashLine))
        painter.drawRect(rect)
        painter.setPen(QtGui.QPen(QtGui.QColor(ACCENT), 1.5))
        painter.setBrush(QtGui.QColor(FG))
        half = self.HANDLE / 2
        for corner in self.corners(rect):
            painter.drawRect(QtCore.QRectF(corner.x() - half, corner.y() - half, half * 2, half * 2))

    @staticmethod
    def _cursor(mode):
        if mode == "move":
            return QtCore.Qt.SizeAllCursor
        if mode in (0, 2):
            return QtCore.Qt.SizeFDiagCursor
        if mode in (1, 3):
            return QtCore.Qt.SizeBDiagCursor
        return QtCore.Qt.ArrowCursor

    def mousePressEvent(self, event):
        position = event.position()
        if event.button() == QtCore.Qt.LeftButton:
            mode, layer = self.hit(position)
            if layer is not None:
                self.window_.select_layer(layer)
                self._drag = (mode, layer, position / self.scale(), (layer.x, layer.y, layer.width, layer.height))
                self.setCursor(QtCore.Qt.ClosedHandCursor if mode == "move" else self._cursor(mode))
                return
            if self.window_.selected_layer is not None:
                self.window_.select_layer(None)
        self._camera = True
        self.gl.mousePressEvent(event)

    def mouseMoveEvent(self, event):
        position = event.position()
        if self._drag is not None:
            self._drag_to(position / self.scale(), bool(event.modifiers() & QtCore.Qt.ShiftModifier))
            return
        if self._camera:
            self.gl.mouseMoveEvent(event)
            return
        self.setCursor(self._cursor(self.hit(position)[0]))

    def _drag_to(self, point, free):
        mode, layer, press, (x, y, width, height) = self._drag
        if mode == "move":
            layer.x, layer.y = x + point.x() - press.x(), y + point.y() - press.y()
        else:
            # The opposite corner stays where it is
            corners = [(x, y), (x + width, y), (x + width, y + height), (x, y + height)]
            anchor_x, anchor_y = corners[(mode + 2) % 4]
            new_width = max(self.MINIMUM, abs(point.x() - anchor_x))
            new_height = max(self.MINIMUM, abs(point.y() - anchor_y))
            if not free:
                factor = max(new_width / width, new_height / height)
                new_width, new_height = max(self.MINIMUM, width * factor), max(self.MINIMUM, height * factor)
            layer.x = anchor_x - new_width if mode in (0, 3) else anchor_x
            layer.y = anchor_y - new_height if mode in (0, 1) else anchor_y
            layer.width, layer.height = new_width, new_height
        self.window_.layers_changed(timeline=False)

    def mouseReleaseEvent(self, event):
        if self._drag is not None:
            self._drag = None
            self.setCursor(self._cursor(self.hit(event.position())[0]))
            self.window_.layers_changed()
            return
        if self._camera:
            self._camera = False
            self.gl.mouseReleaseEvent(event)

    def wheelEvent(self, event):
        self.gl.wheelEvent(event)

    def contextMenuEvent(self, event):
        _, layer = self.hit(QtCore.QPointF(event.pos()))
        event.accept()
        if layer is not None:
            self.window_.select_layer(layer)
            self.window_.layer_menu(layer, event.globalPos())

    def keyPressEvent(self, event):
        layer = self.window_.selected_layer
        if layer is not None and event.key() in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            self.window_.remove_layer(layer)
            return
        super().keyPressEvent(event)


class Stage(QtWidgets.QWidget):
    """Keeps the 3D view at the 5:3 ratio of the 3DS screen, centred and as large as possible."""

    def __init__(self, gl, window):
        super().__init__()
        self.gl = gl
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(QtGui.QPalette.Window, QtGui.QColor(BG))
        self.setPalette(palette)
        gl.setParent(self)
        self.editor = LayerEditor(window, gl)
        self.editor.setParent(self)
        self.split_label = QtWidgets.QLabel(self, objectName="overlay")
        self.camera_label = QtWidgets.QLabel(self, objectName="overlay")
        for label in (self.split_label, self.camera_label):
            label.setFont(mono_font(11))
            label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)

    def resizeEvent(self, event):
        width, height = max(self.width() - 28, 10), max(self.height() - 28, 6)
        width = min(width, int(height * SCREEN_RATIO))
        height = int(width / SCREEN_RATIO)
        self.gl.setGeometry((self.width() - width) // 2, (self.height() - height) // 2, width, height)
        self.editor.setGeometry(self.gl.geometry())
        self.place_overlays()

    def set_overlays(self, split, camera):
        for label, text in ((self.split_label, split), (self.camera_label, camera)):
            label.setText(text or "")
            label.setVisible(bool(text))
            label.adjustSize()
        self.place_overlays()

    def place_overlays(self):
        rect = self.gl.geometry()
        self.split_label.move(rect.x() + 10, rect.y() + 8)
        self.camera_label.move(rect.right() - 10 - self.camera_label.width(), rect.y() + 8)
        self.editor.raise_()
        self.split_label.raise_()
        self.camera_label.raise_()


class Transport(QtWidgets.QWidget):
    def __init__(self, window):
        super().__init__()
        self.setObjectName("transport")
        self.setAttribute(QtCore.Qt.WA_StyledBackground)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 10)
        layout.setSpacing(4)

        info = QtWidgets.QVBoxLayout()
        info.setSpacing(0)
        self.timecode = QtWidgets.QLabel("00:00:00")
        self.timecode.setFont(mono_font(15))
        self.frame_label = QtWidgets.QLabel("frame 0 / 0 · 30 fps", objectName="muted")
        self.frame_label.setFont(mono_font(11, QtGui.QFont.Normal))
        info.addWidget(self.timecode)
        info.addWidget(self.frame_label)
        left = QtWidgets.QWidget()
        left.setLayout(info)
        layout.addWidget(left, 1)

        def button(name, tip, slot, text=None, text_after=False):
            widget = tool_button(name, tip, text=text)
            widget.setFixedHeight(30)
            if not text:
                widget.setFixedWidth(34)
            if text_after:
                widget.setLayoutDirection(QtCore.Qt.RightToLeft)
            if text:
                widget.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            widget.clicked.connect(slot)
            layout.addWidget(widget)
            return widget

        button("start", "Go to start (Home)", window.go_start)
        button("back10", "Back 10 s (J)", window.back_10, "10 s")
        button("previous", "Previous frame (←)", window.previous_frame)
        self.play = QtWidgets.QToolButton(objectName="play")
        self.play.setFixedSize(38, 38)
        self.play.setIconSize(QtCore.QSize(15, 15))
        self.play.setCursor(QtCore.Qt.PointingHandCursor)
        self.play.setFocusPolicy(QtCore.Qt.NoFocus)
        self.play.clicked.connect(window.toggle_play)
        layout.addSpacing(6)
        layout.addWidget(self.play)
        layout.addSpacing(6)
        self.set_playing(False)
        button("next", "Next frame (→)", window.next_frame)
        button("forward10", "Forward 10 s (L)", window.forward_10, "10 s", text_after=True)
        button("end", "Go to end (End)", window.go_end)

        right = QtWidgets.QHBoxLayout()
        right.setSpacing(8)
        right.addStretch(1)
        self.loop = QtWidgets.QToolButton(objectName="tool")
        self.loop.setCheckable(True)
        self.loop.setChecked(True)
        self.loop.setFixedSize(34, 30)
        self.loop.setIconSize(QtCore.QSize(16, 16))
        self.loop.setToolTip("Loop playback")
        self.loop.setFocusPolicy(QtCore.Qt.NoFocus)
        self.loop.toggled.connect(self._loop_icon)
        self._loop_icon(True)
        right.addWidget(self.loop)

        self.speed = QtWidgets.QToolButton(objectName="speed")
        self.speed.setFont(mono_font(12))
        self.speed.setToolTip("Playback speed")
        self.speed.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.speed.setMinimumWidth(62)
        self.speed.setFocusPolicy(QtCore.Qt.NoFocus)
        menu = QtWidgets.QMenu(self.speed)
        group = QtGui.QActionGroup(menu)
        for value in SPEEDS:
            action = menu.addAction(f"{value:g}×")
            action.setCheckable(True)
            action.setChecked(value == 1.0)
            action.setFont(mono_font(12, QtGui.QFont.Normal))
            group.addAction(action)
            action.triggered.connect(lambda checked=False, v=value: window.set_speed(v))
        self.speed.setMenu(menu)
        self.speed.setText("1×")
        right.addWidget(self.speed)
        right_widget = QtWidgets.QWidget()
        right_widget.setLayout(right)
        layout.addWidget(right_widget, 1)

    def _loop_icon(self, on):
        self.loop.setIcon(icon("loop", ACCENT if on else FAINT))

    def set_playing(self, playing):
        self.play.setIcon(icon("pause" if playing else "play", "#002033", 15))
        self.play.setToolTip("Pause (Space)" if playing else "Play (Space)")


class PlayerPanel(QtWidgets.QWidget):
    def __init__(self, window):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        head = QtWidgets.QWidget(objectName="panelHead")
        head.setAttribute(QtCore.Qt.WA_StyledBackground)
        row = QtWidgets.QHBoxLayout(head)
        row.setContentsMargins(12, 6, 12, 6)
        row.setSpacing(10)
        row.addWidget(QtWidgets.QLabel("PLAYER", objectName="panelTitle"))
        segment = QtWidgets.QHBoxLayout()
        segment.setSpacing(0)
        self.game_button = QtWidgets.QPushButton("Game camera", objectName="segLeft")
        self.free_button = QtWidgets.QPushButton("Free", objectName="segRight")
        group = QtWidgets.QButtonGroup(self)
        for button in (self.game_button, self.free_button):
            button.setCheckable(True)
            button.setCursor(QtCore.Qt.PointingHandCursor)
            button.setFocusPolicy(QtCore.Qt.NoFocus)
            group.addButton(button)
            segment.addWidget(button)
        self.game_button.setChecked(True)
        self.game_button.clicked.connect(lambda: window.set_game_camera(True))
        self.free_button.clicked.connect(lambda: window.set_game_camera(False))
        row.addLayout(segment)
        row.addStretch(1)
        self.outlines = QtWidgets.QCheckBox("Outlines")
        self.outlines.setChecked(True)
        # _success of the technique script: the game plays another branch when the technique fails
        success = QtWidgets.QHBoxLayout()
        success.setSpacing(0)
        self.success_button = QtWidgets.QPushButton("Success", objectName="segLeft")
        self.failure_button = QtWidgets.QPushButton("Failure", objectName="segRight")
        outcome = QtWidgets.QButtonGroup(self)
        for button in (self.success_button, self.failure_button):
            button.setCheckable(True)
            button.setCursor(QtCore.Qt.PointingHandCursor)
            button.setFocusPolicy(QtCore.Qt.NoFocus)
            button.setToolTip("_success of the technique script (SkillFunc): the if (_success) / else it runs")
            outcome.addButton(button)
            success.addWidget(button)
        self.success_button.setChecked(True)
        self.success_button.clicked.connect(lambda: window.set_success(True))
        self.failure_button.clicked.connect(lambda: window.set_success(False))
        row.addLayout(success)
        row.addSpacing(6)
        self.lighting = QtWidgets.QCheckBox("Fragment lighting")
        self.lighting.setChecked(True)
        for box in (self.outlines, self.lighting):
            box.setFocusPolicy(QtCore.Qt.NoFocus)
            row.addWidget(box)
        self.shot = tool_button("screenshot", "Take a screenshot of the player")
        self.shot.setFixedSize(30, 26)
        self.shot.clicked.connect(window.screenshot)
        row.addWidget(self.shot)
        layout.addWidget(head)

        self.gl = GLView(window)
        self.stage = Stage(self.gl, window)
        layout.addWidget(self.stage, 1)
        self.transport = Transport(window)
        layout.addWidget(self.transport)

    def set_game_camera(self, on):
        (self.game_button if on else self.free_button).setChecked(True)


# Assets


class Switch(QtWidgets.QAbstractButton):
    """An on/off switch: a track with a knob, the accent color when on."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.NoFocus)

    def sizeHint(self):
        return QtCore.QSize(36, 20)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        track = QtCore.QRectF(1, 2, 34, 16)
        painter.setPen(QtGui.QPen(QtGui.QColor(ACCENT if self.isChecked() else "#5a5a5a"), 1))
        painter.setBrush(QtGui.QColor(ACCENT if self.isChecked() else BG))
        painter.drawRoundedRect(track, 8, 8)
        knob = 12
        x = track.right() - knob - 2 if self.isChecked() else track.left() + 2
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(BG if self.isChecked() else MUTED))
        painter.drawEllipse(QtCore.QRectF(x, track.top() + 2, knob, knob))


class Tile(QtWidgets.QWidget):
    """A card: thumbnail (render of a model, or the icon of its kind) and file name."""
    NAME_HEIGHT = 40        # file name, then what it is linked to (empty when nothing)

    def __init__(self, panel, asset):
        super().__init__()
        self.panel = panel
        self.asset = asset
        self.hover = False
        self.drop_target = False
        self._press = None
        self.editor = None
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setAcceptDrops(True)
        project = panel.window_.project
        tip = asset.name
        if asset.kind == assets_module.CUSTOM:
            tip += "\nCustom object: " + ", ".join(member.name for member in asset.members)
        users = project.users_of(asset) if asset.parent is None else []
        if users:
            tip += "\nDrawn by the custom object " + ", ".join(user.name for user in users)
        if project.animatable(asset):
            animation = project.animation_of(asset)
            tip += f"\nAnimation: {animation.name}" if animation else "\nDrop an animation here (or this card on an animation)"
        elif asset.kind == assets_module.ANIMATION:
            targets = project.bound_targets(asset)
            tip += "\nAnimates " + ", ".join(t.name for t in targets) if targets else "\nDrop it on a custom object or an archive with an armature"
        if asset.is_picture:
            height, width = asset.image.shape[:2]
            what = f"Text · {asset.text['font']} · {asset.text['color']}" if asset.kind == assets_module.TEXT else "Image"
            tip += f"\n{what} · {width}×{height}"
            tip += ("\nIn the timeline" if project.layer_of(asset) is not None
                    else "\nDrag it into the timeline (or right click > Add to the timeline)")
        if asset.error:
            tip += "\n\n" + asset.error
        if project.reference is asset:
            tip += "\nReference script"
        lock = project.lock_text(asset)
        if lock:
            tip += "\n\n" + lock[0].upper() + lock[1:]
        self.setToolTip(tip)

    @property
    def selected(self):
        return self.asset in self.panel.selection

    def thumb_height(self, width=None):
        return int(round(((width or self.width()) - 2) * 3 / 5))

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QtGui.QPainterPath()
        path.addRoundedRect(rect, 6, 6)
        painter.fillPath(path, QtGui.QColor(PANEL_2))

        thumb = QtCore.QRectF(rect.x(), rect.y(), rect.width(), self.thumb_height())
        painter.save()
        painter.setClipPath(path)
        painter.fillRect(thumb, QtGui.QColor(BG))
        pixmap = self.panel.thumbnail(self.asset)
        member = self.asset.parent is None and bool(self.panel.window_.project.users_of(self.asset))
        if member:
            # Drawn by its custom object, not on its own
            painter.setOpacity(0.45)
        if pixmap is not None:
            scale = max(thumb.width() / pixmap.width(), thumb.height() / pixmap.height())
            width, height = pixmap.width() * scale, pixmap.height() * scale
            painter.drawPixmap(QtCore.QRectF(thumb.center().x() - width / 2, thumb.center().y() - height / 2, width, height),
                               pixmap, QtCore.QRectF(pixmap.rect()))
        else:
            name = KIND_ICONS.get(self.asset.kind, "other")
            color = ACCENT if self.hover or self.selected else ICON_REST
            share = 0.30 if name == "squirrel" else 0.46
            width = thumb.width() * share
            box = svg_renderer(name, color).viewBoxF()
            height = width * box.height() / box.width()
            area = QtCore.QRectF(thumb.center().x() - width / 2, thumb.center().y() - height / 2, width, height)
            if name == "squirrel":
                painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
            draw_icon(painter, name, color, area)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setOpacity(1.0)
        painter.setPen(QtGui.QPen(QtGui.QColor(LINE), 1))
        painter.drawLine(QtCore.QPointF(rect.left(), thumb.bottom() + 0.5), QtCore.QPointF(rect.right(), thumb.bottom() + 0.5))
        project = self.panel.window_.project
        right = rect.right() - 4
        lock = self.asset.lock or (self.asset.parent.lock if self.asset.parent is not None else None)
        if lock in LOCK_BADGES:
            right = self._lock_badge(painter, rect, right, *LOCK_BADGES[lock]) - 4
        if project.reference is self.asset:
            badge = QtCore.QRectF(right - 20, rect.top() + 4, 20, 18)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(0, 0, 0, 153))
            painter.drawRoundedRect(badge, 3, 3)
            draw_icon(painter, "star", ACCENT, badge.adjusted(3, 2, -3, -2))
        if self.asset.kind == assets_module.CUSTOM:
            self._flag(painter, rect, "object", MUTED)
        elif self.asset.parent is None:
            users = project.users_of(self.asset)
            if users:
                self._flag(painter, rect, "in " + ", ".join(user.name for user in users), FAINT)
        painter.restore()

        if self.editor is None:
            painter.setFont(mono_font(11))
            painter.setPen(QtGui.QColor(FG))
            text_rect = QtCore.QRectF(rect.x() + 7, thumb.bottom() + 4, rect.width() - 14, 16)
            text = QtGui.QFontMetrics(painter.font()).elidedText(self.asset.name, QtCore.Qt.ElideRight, int(text_rect.width()))
            painter.drawText(text_rect, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, text)
            link = self.panel.link_text(self.asset)
            if link:
                painter.setFont(mono_font(10, QtGui.QFont.Normal))
                painter.setPen(QtGui.QColor(ACCENT))
                link_rect = text_rect.translated(0, 16)
                painter.drawText(link_rect, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                                 QtGui.QFontMetrics(painter.font()).elidedText(link, QtCore.Qt.ElideRight, int(link_rect.width())))

        if self.asset.error:
            pen = QtGui.QPen(QtGui.QColor(DANGER), 2 if self.selected else 1.5)
        elif self.panel.dragging is not None and self.panel.can_link(self.panel.dragging, self.asset) and not self.drop_target:
            # While a card is dragged, the cards it can be linked to are outlined
            pen = QtGui.QPen(QtGui.QColor(ACCENT), 1.5, QtCore.Qt.DashLine)
        elif self.selected or self.drop_target:
            pen = QtGui.QPen(QtGui.QColor(ACCENT), 2)
        elif self.hover:
            pen = QtGui.QPen(QtGui.QColor("#565656"), 1)
        else:
            pen = QtGui.QPen(QtGui.QColor(LINE), 1)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        inset = pen.widthF() / 2
        painter.drawRoundedRect(QtCore.QRectF(self.rect()).adjusted(inset, inset, -inset, -inset), 6, 6)

    def _lock_badge(self, painter, rect, right, icon_name, text, color):
        """Template lock in the top right corner: its icon only (the tooltip says what it allows). Returns its left."""
        badge = QtCore.QRectF(right - 22, rect.top() + 4, 22, 18)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(0, 0, 0, 170))
        painter.drawRoundedRect(badge, 3, 3)
        draw_icon(painter, icon_name, color, badge.adjusted(4, 2, -4, -2))
        return badge.left()

    def _flag(self, painter, rect, text, color, right=False):
        painter.setFont(mono_font(10))
        metrics = QtGui.QFontMetrics(painter.font())
        text = metrics.elidedText(text, QtCore.Qt.ElideRight, int(rect.width() * 0.6))
        width = metrics.horizontalAdvance(text) + 10
        x = rect.right() - 4 - width if right else rect.left() + 4
        badge = QtCore.QRectF(x, rect.top() + 4, width, 16)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(0, 0, 0, 160))
        painter.drawRoundedRect(badge, 3, 3)
        painter.setPen(QtGui.QColor(color))
        painter.drawText(badge, QtCore.Qt.AlignCenter, text)

    def enterEvent(self, event):
        self.hover = True
        self.update()

    def leaveEvent(self, event):
        self.hover = False
        self.update()

    def mousePressEvent(self, event):
        modifiers = event.modifiers()
        if event.button() == QtCore.Qt.LeftButton:
            if modifiers & QtCore.Qt.ControlModifier:
                self.panel.select(self.asset, "toggle")
            elif modifiers & QtCore.Qt.ShiftModifier:
                self.panel.select(self.asset, "range")
            else:
                self.panel.select(self.asset)
            self._press = event.position()
        elif event.button() == QtCore.Qt.RightButton:
            if not self.selected:
                self.panel.select(self.asset)
            self._press = None

    def mouseMoveEvent(self, event):
        if self._press is None or not (self.panel.linkable(self.asset) or self.asset.is_picture):
            return
        if (event.position() - self._press).manhattanLength() < QtWidgets.QApplication.startDragDistance():
            return
        self._press = None
        drag = QtGui.QDrag(self)
        mime = QtCore.QMimeData()
        mime.setData(ASSET_MIME, QtCore.QByteArray(str(self.asset.uid).encode()))
        drag.setMimeData(mime)
        pixmap = self.grab()
        drag.setPixmap(pixmap.scaledToWidth(min(pixmap.width(), 110), QtCore.Qt.SmoothTransformation))
        self.panel.set_dragging(self.asset)
        try:
            drag.exec(QtCore.Qt.LinkAction | QtCore.Qt.CopyAction)
        finally:
            self.panel.set_dragging(None)

    def mouseReleaseEvent(self, event):
        self._press = None

    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.panel.window_.open_asset(self.asset)

    def contextMenuEvent(self, event):
        self.panel.context_menu(self.asset, event.globalPos())

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        if event.mimeData().hasFormat(ASSET_MIME):
            source = self.panel.asset_of(event.mimeData())
            if source is not None and self.panel.can_link(source, self.asset):
                event.setDropAction(QtCore.Qt.LinkAction)
                event.accept()
                self.drop_target = True
                self.update()
                return
        event.ignore()

    def dragLeaveEvent(self, event):
        self.drop_target = False
        self.update()

    def dropEvent(self, event):
        self.drop_target = False
        self.update()
        if event.mimeData().hasUrls():
            self.panel.window_.dropEvent(event)
            return
        source = self.panel.asset_of(event.mimeData())
        if source is not None:
            event.setDropAction(QtCore.Qt.LinkAction)
            event.accept()
            self.panel.link(source, self.asset)

    def edit_name(self):
        self.editor = RenameEdit(self, self.asset, self.panel)
        top = self.thumb_height() + 2
        self.editor.setGeometry(4, top, self.width() - 8, 20)
        self.editor.show()
        self.editor.setFocus()
        self.editor.selectAll()
        self.update()


class RenameEdit(QtWidgets.QLineEdit):
    """Edits a name in place: Enter keeps it, Escape cancels, leaving the field keeps it."""

    def __init__(self, parent, asset, panel):
        super().__init__(asset.name, parent)
        self.setObjectName("rename")
        self.setFont(mono_font(11))
        self.asset = asset
        self.panel = panel
        self.done = False

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self.finish(True)
        elif event.key() == QtCore.Qt.Key_Escape:
            self.finish(False)
        else:
            super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.finish(True)

    def finish(self, commit):
        if self.done:
            return
        self.done = True
        name = self.text().strip()
        QtCore.QTimer.singleShot(0, lambda: self.panel.finish_rename(self.asset, name if commit else None))


class FolderHead(QtWidgets.QWidget):
    """Head of a package: arrow, name, rename and remove buttons, number of archives."""
    HEIGHT = 28

    def __init__(self, panel, asset):
        super().__init__()
        self.panel = panel
        self.asset = asset
        self.hover = False
        self.setFixedHeight(self.HEIGHT)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(6)
        self.caret = QtWidgets.QLabel("▾" if asset.expanded else "▸")
        self.caret.setFixedWidth(12)
        self.caret.setStyleSheet(f"color: {FAINT}; font-size: 10px;")
        layout.addWidget(self.caret)
        self.name = QtWidgets.QLabel(asset.name)
        self.name.setFont(mono_font(12))
        self.name.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Preferred)
        self.name_layout = layout
        layout.addWidget(self.name, 0)
        project = panel.window_.project
        rename = tool_button("pencil", "Rename", 13, "head", color=FAINT, hover=FG)
        rename.setFixedSize(22, 22)
        rename.clicked.connect(lambda: panel.start_rename(asset))
        rename.setEnabled(project.can_rename(asset))
        remove = tool_button("cross", "Remove", 13, "head", color=DANGER, hover="#ff6b6b")
        remove.setFixedSize(22, 22)
        remove.clicked.connect(lambda: panel.window_.remove_asset(asset))
        remove.setEnabled(project.can_remove(asset))
        if asset.lock:
            self.setToolTip(project.lock_text(asset))
        layout.addWidget(rename)
        layout.addWidget(remove)
        layout.addStretch(1)
        count = len(asset.children)
        self.count = QtWidgets.QLabel(f"{count} archive{'s' if count != 1 else ''}")
        self.count.setStyleSheet(f"color: {FAINT}; font-size: 8pt;")
        layout.addWidget(self.count)
        self.editor = None
        self._fit_name()

    def _fit_name(self):
        metrics = QtGui.QFontMetrics(self.name.font())
        self.name.setMinimumWidth(min(metrics.horizontalAdvance(self.asset.name) + 4, 160))
        self.name.setMaximumWidth(metrics.horizontalAdvance(self.asset.name) + 4)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        if self.hover or self.panel.selected is self.asset:
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(PANEL_2))
            painter.drawRoundedRect(QtCore.QRectF(self.rect()), 4, 4)

    def enterEvent(self, event):
        self.hover = True
        self.update()

    def leaveEvent(self, event):
        self.hover = False
        self.update()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.panel.select(self.asset, refresh=False)
            self.asset.expanded = not self.asset.expanded
            self.panel.refresh()
        elif event.button() == QtCore.Qt.RightButton:
            self.panel.select(self.asset)

    def contextMenuEvent(self, event):
        self.panel.context_menu(self.asset, event.globalPos())

    def edit_name(self):
        self.editor = RenameEdit(self, self.asset, self.panel)
        self.editor.setFont(mono_font(12))
        index = self.name_layout.indexOf(self.name)
        self.name.hide()
        self.name_layout.insertWidget(index, self.editor, 1)
        self.editor.setFocus()
        self.editor.selectAll()


class AssetCanvas(QtWidgets.QWidget):
    """Lays out the cards and the package folders, the number of columns follows the width."""
    PADDING = 10
    GAP = 8
    CARD = 125

    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.rows = []      # (widget, kind) in order: "tiles" lists or "head" widgets
        self.empty = QtWidgets.QLabel("No asset. Drop .xc, .pck, .nut or image files here.", self)
        self.empty.setStyleSheet(f"color: {FAINT};")

    def columns(self):
        return max(1, min(8, round(self.width() / self.CARD)))

    def set_rows(self, rows):
        for widgets, _ in self.rows:
            for widget in widgets if isinstance(widgets, list) else [widgets]:
                widget.hide()
                widget.deleteLater()
        self.rows = rows
        for widgets, _ in rows:
            for widget in widgets if isinstance(widgets, list) else [widgets]:
                widget.setParent(self)
                widget.show()
        self.empty.setVisible(not rows)
        self.relayout()

    def relayout(self):
        columns = self.columns()
        y = self.PADDING
        for widgets, indent in self.rows:
            left = self.PADDING + (indent and 26)
            width = self.width() - left - self.PADDING
            if isinstance(widgets, list):
                card = max(20, (width - self.GAP * (columns - 1)) / columns)
                height = 0
                for index, tile in enumerate(widgets):
                    row, column = divmod(index, columns)
                    height = tile.thumb_height(int(card)) + Tile.NAME_HEIGHT
                    tile.setGeometry(int(left + column * (card + self.GAP)), int(y + row * (height + self.GAP)), int(card), height)
                rows = (len(widgets) + columns - 1) // columns
                y += rows * height + max(rows - 1, 0) * self.GAP + (self.GAP if indent else 0) + self.GAP
            else:
                widgets.setGeometry(self.PADDING, y, self.width() - 2 * self.PADDING, FolderHead.HEIGHT)
                y += FolderHead.HEIGHT + 4
        self.empty.setGeometry(self.PADDING + 2, self.PADDING, self.width() - 2 * self.PADDING, 20)
        self.setMinimumHeight(y + self.PADDING)
        self.update()

    def resizeEvent(self, event):
        if event.oldSize().width() != event.size().width():
            self.relayout()

    def paintEvent(self, event):
        # The line along the archives of an open package
        painter = QtGui.QPainter(self)
        painter.setPen(QtGui.QPen(QtGui.QColor(LINE), 1))
        for widgets, indent in self.rows:
            if indent and isinstance(widgets, list) and widgets:
                top = widgets[0].y() - 4
                bottom = max(w.geometry().bottom() for w in widgets) + 8
                painter.drawLine(self.PADDING + 11, top, self.PADDING + 11, bottom)


class AssetPanel(QtWidgets.QWidget):
    def __init__(self, window):
        super().__init__()
        self.window_ = window
        self.selection = []         # selected assets, the last one clicked at the end
        self.dragging = None        # animation being dragged over the cards
        self._pixmaps = {}
        self.setMinimumWidth(200)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(QtGui.QPalette.Window, QtGui.QColor(PANEL))
        self.setPalette(palette)

        head = QtWidgets.QWidget(objectName="panelHead")
        head.setAttribute(QtCore.Qt.WA_StyledBackground)
        row = QtWidgets.QHBoxLayout(head)
        row.setContentsMargins(10, 7, 10, 7)
        row.setSpacing(8)
        row.addWidget(QtWidgets.QLabel("ASSETS", objectName="panelTitle"))
        row.addStretch(1)
        self.filter = QtWidgets.QLineEdit()
        self.filter.setPlaceholderText("Filter")
        self.filter.setClearButtonEnabled(True)
        # Files dropped on the field are imported by the window, not typed in
        self.filter.setAcceptDrops(False)
        # Keys like F, J, L and Space drive the player until the field is clicked
        self.filter.setFocusPolicy(QtCore.Qt.ClickFocus)
        self.filter.setMaximumWidth(200)
        self.filter.textChanged.connect(self.refresh)
        row.addWidget(self.filter, 1)
        self.import_button = QtWidgets.QPushButton("+ Import", objectName="importButton")
        self.import_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.import_button.clicked.connect(window.import_dialog)
        row.addWidget(self.import_button)
        self.text_button = QtWidgets.QPushButton("+ Add text", objectName="importButton")
        self.text_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.text_button.setToolTip("A text drawn with a font of the game, to put in the timeline")
        self.text_button.clicked.connect(window.add_text_dialog)
        row.addWidget(self.text_button)
        layout.addWidget(head)

        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.canvas = AssetCanvas(self)
        self.scroll.setWidget(self.canvas)
        layout.addWidget(self.scroll, 1)

        # Shown while several cards are selected: what can be done with them
        self.selection_bar = QtWidgets.QWidget(objectName="transport")
        self.selection_bar.setAttribute(QtCore.Qt.WA_StyledBackground)
        bar = QtWidgets.QHBoxLayout(self.selection_bar)
        bar.setContentsMargins(10, 6, 10, 6)
        bar.setSpacing(8)
        self.selection_label = QtWidgets.QLabel(objectName="muted")
        bar.addWidget(self.selection_label, 1)
        self.group_button = QtWidgets.QPushButton("Group as custom object…", objectName="importButton")
        self.group_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.group_button.setToolTip("Draw the selected models together as one object, animated as one (Ctrl+G)")
        self.group_button.clicked.connect(lambda: window.group_selection())
        bar.addWidget(self.group_button)
        layout.addWidget(self.selection_bar)
        self.selection_bar.hide()

        # Shown in a template project only: its name and, when it links animations, the swap switch
        self.template_bar = QtWidgets.QWidget(objectName="templateBar")
        self.template_bar.setAttribute(QtCore.Qt.WA_StyledBackground)
        bar = QtWidgets.QHBoxLayout(self.template_bar)
        bar.setContentsMargins(10, 7, 12, 7)
        bar.setSpacing(8)
        bar.addWidget(QtWidgets.QLabel("TEMPLATE", objectName="templateTag"))
        self.template_name = QtWidgets.QLabel(objectName="templateName")
        bar.addWidget(self.template_name)
        bar.addStretch(1)
        self.swap_label = QtWidgets.QLabel(objectName="muted")
        bar.addWidget(self.swap_label)
        self.swap = Switch()
        self.swap.toggled.connect(lambda on: window.set_swapped(on))
        self.swap_label.mousePressEvent = lambda event: self.swap.toggle()
        bar.addWidget(self.swap)
        layout.addWidget(self.template_bar)
        self.template_bar.hide()

        version = QtWidgets.QLabel(f"studio_render {VERSION}", objectName="version")
        version.setContentsMargins(12, 4, 12, 6)
        layout.addWidget(version)

        for keys, slot in (("F2", self._rename_selected), (QtGui.QKeySequence.Delete, self._remove_selected),
                           ("Return", self._open_selected), ("Enter", self._open_selected),
                           ("Ctrl+G", lambda: window.group_selection()), ("Ctrl+A", self._select_all)):
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(keys), self)
            shortcut.setContext(QtCore.Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)

    # Content

    def thumbnail(self, asset):
        if not asset.thumbnail:
            return None
        key = (asset.uid, id(asset.thumbnail))
        if key not in self._pixmaps:
            pixmap = QtGui.QPixmap()
            pixmap.loadFromData(asset.thumbnail, "PNG")
            self._pixmaps[key] = pixmap if not pixmap.isNull() else None
        return self._pixmaps[key]

    def visible_asset(self, asset):
        return self.window_.preferences.squirrel or asset.kind != assets_module.SQUIRREL

    def refresh(self):
        project = self.window_.project
        query = self.filter.text().strip().lower()

        def match(asset):
            return not query or query in asset.name.lower()

        # Custom objects first, then the files in import order
        top = [a for a in project.items if not a.is_package and self.visible_asset(a) and match(a)]
        top.sort(key=lambda a: a.kind != assets_module.CUSTOM)
        top = [Tile(self, a) for a in top]
        rows = [(top, 0)] if top else []
        for package in (a for a in project.items if a.is_package):
            children = [c for c in package.children if self.visible_asset(c) and (match(c) or match(package))]
            if not children and not match(package):
                continue
            rows.append((FolderHead(self, package), 0))
            if package.expanded and children:
                rows.append(([Tile(self, c) for c in children], 1))
        existing = set(map(id, project.walk()))
        self.selection = [a for a in self.selection if id(a) in existing]
        self.canvas.set_rows(rows)
        self._update_selection_bar()
        self._update_template_bar()

    def _update_template_bar(self):
        template = self.window_.project.template
        self.template_bar.setVisible(template is not None)
        if template is None:
            return
        self.template_name.setText(template.name)
        accepted = template.accepted()
        self.template_bar.setToolTip(f"Template project {template.name}" + (f"\nImports: {accepted}" if accepted else ""))
        swap = bool(template.slots)
        self.swap_label.setVisible(swap)
        self.swap.setVisible(swap)
        if swap:
            self.swap_label.setText(template.swap_label)
            tip = "Links the animations of the .pck the other way round:\n" + "\n".join(
                f"{slot['target']}: {slot.get('swapped' if not template.swapped else 'archive', '')}" for slot in template.slots)
            self.swap.setToolTip(tip)
            self.swap_label.setToolTip(tip)
            self.swap.blockSignals(True)
            self.swap.setChecked(template.swapped)
            self.swap.blockSignals(False)

    @property
    def selected(self):
        return self.selection[-1] if self.selection else None

    @selected.setter
    def selected(self, asset):
        self.selection = [asset] if asset is not None else []

    def repaint_tiles(self):
        for child in self.canvas.findChildren(Tile):
            child.update()

    def ordered_tiles(self):
        return sorted(self.canvas.findChildren(Tile), key=lambda tile: (tile.y(), tile.x()))

    def select(self, asset, mode="single", refresh=True):
        """single: only this asset; toggle (Ctrl+click): add or take it out; range (Shift+click): from the last one."""
        if mode == "toggle":
            if asset in self.selection:
                self.selection.remove(asset)
            else:
                self.selection.append(asset)
        elif mode == "range" and self.selection:
            order = [tile.asset for tile in self.ordered_tiles()]
            anchor = self.selection[-1]
            if anchor in order and asset in order:
                a, b = sorted((order.index(anchor), order.index(asset)))
                span = order[a:b + 1]
                self.selection = [x for x in self.selection if x not in span] + [x for x in span if x is not asset] + [asset]
            else:
                self.selection = [asset]
        else:
            self.selection = [asset]
        self.setFocus()
        self.repaint_tiles()
        for head in self.canvas.findChildren(FolderHead):
            head.update()
        self._update_selection_bar()

    def _select_all(self):
        self.selection = [tile.asset for tile in self.ordered_tiles()]
        self.repaint_tiles()
        self._update_selection_bar()

    def _update_selection_bar(self):
        count = len(self.selection)
        groupable = [a for a in self.selection if self.window_.project.groupable(a)]
        self.selection_bar.setVisible(count > 1)
        self.selection_label.setText(f"{count} selected")
        self.group_button.setEnabled(len(groupable) >= 2)

    def set_dragging(self, asset):
        self.dragging = asset
        self.repaint_tiles()

    def linkable(self, asset):
        """Cards that can be dragged to link: animations, and what an animation can drive."""
        return asset.kind == assets_module.ANIMATION or self.window_.project.animatable(asset)

    def can_link(self, dragged, target):
        project = self.window_.project
        if dragged.kind == assets_module.ANIMATION:
            return project.animatable(target)
        return project.animatable(dragged) and target.kind == assets_module.ANIMATION

    def link(self, dragged, target):
        """Either direction: an animation dropped on an object, or an object dropped on an animation."""
        if dragged.kind == assets_module.ANIMATION:
            self.window_.set_animation(target, dragged)
        else:
            self.window_.set_animation(dragged, target)

    def link_text(self, asset):
        """Second line of a card: the animation of an object, or the objects an animation drives."""
        project = self.window_.project
        if asset.kind == assets_module.ANIMATION:
            return ", ".join(target.stem for target in project.bound_targets(asset))
        if project.animatable(asset):
            animation = project.animation_of(asset)
            return animation.stem if animation is not None else ""
        return ""

    def targets_menu(self, menu, animation):
        """Animate ▸ the custom objects and archives with an armature, those it drives checked."""
        window = self.window_
        project = window.project
        submenu = menu.addMenu("Animate")
        targets = [a for a in project.items if project.animatable(a)]
        targets.sort(key=lambda a: a.kind != assets_module.CUSTOM)
        for target in targets:
            linked = project.animation_of(target) is animation
            other = project.animation_of(target)
            label = target.name + (f"   (now {other.stem})" if other is not None and not linked else "")
            action = submenu.addAction(label, lambda t=target, on=linked: window.set_animation(t, None if on else animation))
            action.setCheckable(True)
            action.setChecked(linked)
        if not targets:
            hint = submenu.addAction("Group models into a custom object first (Ctrl+G)")
            hint.setEnabled(False)

    def asset_of(self, mime):
        try:
            uid = int(bytes(mime.data(ASSET_MIME)).decode())
        except ValueError:
            return None
        return next((a for a in self.window_.project.walk() if a.uid == uid), None)

    def widget_of(self, asset):
        for widget in self.canvas.findChildren(Tile) + self.canvas.findChildren(FolderHead):
            if widget.asset is asset:
                return widget
        return None

    # Rename and remove

    def start_rename(self, asset):
        widget = self.widget_of(asset)
        if widget is None:
            return
        if not self.window_.project.can_rename(asset):
            self.window_.show_toast(f"<b style='color:{ACCENT}'>{asset.name}</b> keeps its name: the template needs it")
            return
        self.select(asset)
        widget.edit_name()

    def finish_rename(self, asset, name):
        if name:
            self.window_.rename_asset(asset, name)
        else:
            self.refresh()

    def _rename_selected(self):
        if self.selected is not None:
            self.start_rename(self.selected)

    def _open_selected(self):
        if self.selected is not None and not self.selected.is_package:
            self.window_.open_asset(self.selected)

    def _remove_selected(self):
        project = self.window_.project
        kept = [asset for asset in self.selection if not project.can_remove(asset)]
        for asset in [asset for asset in self.selection if asset not in kept]:
            self.window_.remove_asset(asset)
        if kept:
            names = ", ".join(asset.name for asset in kept)
            self.window_.show_toast(f"Locked by the template, not removed: <b style='color:{ACCENT}'>{names}</b>")

    def animation_menu(self, menu, target):
        """Animation ▸ None / the animations of the project, the current one checked."""
        window = self.window_
        project = window.project
        current = project.animation_of(target)
        submenu = menu.addMenu("Animation")
        group = QtGui.QActionGroup(submenu)
        none = submenu.addAction("None", lambda: window.set_animation(target, None))
        none.setCheckable(True)
        none.setChecked(current is None)
        group.addAction(none)
        animations = project.animations()
        if animations:
            submenu.addSeparator()
        for animation in animations:
            action = submenu.addAction(animation.path, lambda a=animation: window.set_animation(target, a))
            action.setCheckable(True)
            action.setChecked(animation is current)
            group.addAction(action)
        if not animations:
            hint = submenu.addAction("Import an archive with animations (.pck, .xc)")
            hint.setEnabled(False)

    def context_menu(self, asset, position):
        window = self.window_
        project = window.project
        menu = QtWidgets.QMenu(self)
        if len(self.selection) > 1 and asset in self.selection:
            count = len(self.selection)
            group = menu.addAction("Group as custom object…\tCtrl+G", window.group_selection)
            group.setEnabled(sum(1 for a in self.selection if project.groupable(a)) >= 2)
            menu.addSeparator()
            removable = sum(1 for a in self.selection if project.can_remove(a))
            if removable == count:
                menu.addAction(f"Remove {count} assets\tDel", self._remove_selected)
            else:
                remove = menu.addAction(f"Remove {removable} of {count} assets ({count - removable} locked)\tDel",
                                        self._remove_selected)
                remove.setEnabled(removable > 0)
            menu.exec(position)
            return
        if not asset.is_package:
            opened = menu.addAction("Edit text…\tEnter" if asset.kind == assets_module.TEXT else "Open\tEnter",
                                    lambda: window.open_asset(asset))
            opened.setEnabled(asset_windows.can_open(asset))
            menu.addSeparator()
        rename = menu.addAction("Rename\tF2", lambda: self.start_rename(asset))
        rename.setEnabled(project.can_rename(asset))
        if asset.kind != assets_module.CUSTOM:
            replace = menu.addAction("Replace…", lambda: window.replace_dialog(asset))
            replace.setEnabled(project.can_replace(asset))
        if project.can_remove(asset):
            menu.addAction("Remove\tDel" if asset.kind != assets_module.CUSTOM else "Remove (keeps its parts)\tDel",
                           lambda: window.remove_asset(asset))
        lock = project.lock_text(asset)
        if lock:
            # Says why the actions above are greyed out
            note = menu.addAction(icon("lock" if asset.lock == assets_module.LOCKED or asset.parent is not None else "lock_replace",
                                       LOCK_BADGES.get(asset.lock or (asset.parent.lock if asset.parent else None), ("", "", FAINT))[2]),
                                  lock[0].upper() + lock[1:])
            note.setEnabled(False)
        if export_module.asset_formats(asset):
            menu.addSeparator()
            menu.addAction("Export…", lambda: window.export_asset(asset))
        if project.animatable(asset):
            menu.addSeparator()
            self.animation_menu(menu, asset)
        elif asset.kind == assets_module.ANIMATION:
            menu.addSeparator()
            self.targets_menu(menu, asset)
        if asset.is_picture:
            menu.addSeparator()
            if project.layer_of(asset) is None:
                menu.addAction("Add to the timeline", lambda: window.add_layer(asset))
            else:
                menu.addAction("Remove from the timeline", lambda: window.remove_layer(project.layer_of(asset)))
        if asset.kind == assets_module.SQUIRREL and asset.parent is None:
            menu.addSeparator()
            reference = menu.addAction("Set as reference script", lambda: window.set_reference(asset))
            reference.setEnabled(window.project.reference is not asset)
            if asset.extension == ".nut":
                menu.addAction("Compile to .nutb", lambda: window.convert_script(asset, compile_=True))
            elif asset.extension == ".nutb":
                menu.addAction("Decompile to .nut", lambda: window.convert_script(asset, compile_=False))
        menu.exec(position)


# Timeline


class TimelineCanvas(QtWidgets.QWidget):
    """Track heads on the left, lanes with the ruler on the right (scrolled by offset)."""
    HEADS = 220
    RULER = 26
    TRACK = 34

    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.window_ = panel.window_
        self.tracks = []
        self.scale = 5.5
        self.offset = 0
        self._seeking = False
        self._drag = None       # editing a picture clip: (mode, layer, frame at the press, start, duration)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(QtCore.Qt.ClickFocus)

    # Tracks

    def build(self):
        window = self.window_
        project = window.project
        scene = project.scene
        tracks = []
        # The pictures on top, as in an editing software: the upper track is drawn over the lower ones
        for layer in reversed(project.layers):
            asset = layer.asset
            if asset.kind == assets_module.TEXT:
                sub = f"text · {asset.text['font']}"
            else:
                sub = f"image · {asset.image.shape[1]}×{asset.image.shape[0]}" if asset.image is not None else "image"
            tracks.append({"type": "text" if asset.kind == assets_module.TEXT else "image", "name": asset.name,
                           "sub": sub, "asset": asset, "layer": layer})
        splits = project.splits()
        if splits:
            tracks.append({"type": "plan", "name": "Splits", "sub": f"mtninf · {len(splits)} splits", "asset": None,
                           "clips": [(a, b, name) for name, a, b in splits]})
        starts = scene.camera_starts()
        played = scene.played_cameras()
        for asset in project.walk():
            cameras = [(i, c) for i, c in enumerate(played) if c.owner is asset]
            if cameras:
                clips = [(starts[i], starts[i] + c.frame_count, f"cam {n + 1}") for n, (i, c) in enumerate(cameras)]
                tracks.append({"type": "cam", "name": asset.name, "sub": f"cmr2 · {len(cameras)} cameras",
                               "asset": asset, "clips": clips})
        for asset in project.walk():
            animated = [entry for entry in scene.owned(asset) if entry.own_animations]
            if animated:
                kinds = sorted({a.kind for entry in animated for a in entry.own_animations if a.kind},
                               key=("bone", "uv", "material").index)
                label = " · ".join({"bone": "mtn", "uv": "imm", "material": "mtm"}[k] for k in kinds)
                targets = project.bound_targets(asset)
                sub = "→ " + ", ".join(t.stem for t in targets) if targets else label
                last = max(entry.frame_count for entry in animated)
                own = []
                for entry in animated:
                    for split_list in entry.model.splits.values():
                        own = sorted(((int(s["start"]), int(s["end"]), s["name"]) for s in split_list), key=lambda s: s[0])
                        break
                    if own:
                        break
                tracks.append({"type": "fx" if asset.kind == assets_module.MODEL else "anim", "name": asset.name, "sub": sub,
                               "asset": asset, "kinds": label, "end": last, "parts": own or [(a, b, n) for n, a, b in splits]})
        self.tracks = tracks
        self.setFixedHeight(self.RULER + self.TRACK * len(tracks) + 1)
        self.update()

    @property
    def last(self):
        return self.window_.last_frame

    def lanes_width(self):
        # Room after the end to lengthen a picture
        return int((self.last + 1) * self.scale + 40 + (300 if self.window_.project.layers else 0))

    def x_of(self, frame):
        return self.HEADS + frame * self.scale - self.offset

    def frame_at(self, x):
        return int(round((x - self.HEADS + self.offset) / self.scale - 0.5))

    @staticmethod
    def off(track):
        if "layer" in track:
            return not track["layer"].visible
        return track["asset"] is not None and not track["asset"].visible

    def clip_rect(self, index, layer):
        top = self.RULER + index * self.TRACK
        return QtCore.QRectF(self.x_of(layer.start) + 0.5, top + 4.5, layer.duration * self.scale - 2, self.TRACK - 9)

    # Drawing

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        width = self.width()
        frame = self.window_.frame
        split_index = self.window_.split_index(frame)
        painter.fillRect(self.rect(), QtGui.QColor(PANEL))

        # Lanes
        painter.save()
        painter.setClipRect(QtCore.QRect(self.HEADS, 0, width - self.HEADS, self.height()))
        painter.fillRect(QtCore.QRect(self.HEADS, 0, width - self.HEADS, self.RULER), QtGui.QColor(PANEL_2))
        painter.setFont(mono_font(10, QtGui.QFont.Normal))
        first = max(0, self.frame_at(self.HEADS) - 1)
        last_visible = min(self.last + 1, self.frame_at(width) + 2)
        for f in range(first, last_visible + 1):
            x = self.x_of(f)
            major = f % 15 == 0
            if major:
                painter.setPen(QtGui.QColor(FAINT))
                painter.drawLine(QtCore.QPointF(x, self.RULER - 10), QtCore.QPointF(x, self.RULER - 1))
                label = f"{two(f // FPS)}:00" if f % FPS == 0 else str(f)
                painter.drawText(QtCore.QPointF(x + 3, 13), label)
            elif (self.scale >= 6 and f % 5 == 0) or self.scale >= 10:
                painter.setPen(QtGui.QColor(LINE))
                painter.drawLine(QtCore.QPointF(x, self.RULER - 5), QtCore.QPointF(x, self.RULER - 1))
        painter.setPen(QtGui.QColor(LINE))
        painter.drawLine(self.HEADS, self.RULER - 0, width, self.RULER)

        for index, track in enumerate(self.tracks):
            top = self.RULER + index * self.TRACK
            off = self.off(track)
            painter.setOpacity(0.35 if off else 1.0)
            if "layer" in track:
                layer = track["layer"]
                rect = self.clip_rect(index, layer)
                selected = layer is self.window_.selected_layer
                self._clip(painter, rect, CLIP_TEXT if track["type"] == "text" else CLIP_IMAGE, layer.start <= frame <= layer.end)
                if selected:
                    painter.setPen(QtGui.QPen(QtGui.QColor(ACCENT), 2))
                    painter.setBrush(QtCore.Qt.NoBrush)
                    painter.drawRoundedRect(rect.adjusted(-0.5, -0.5, 0.5, 0.5), 4, 4)
                    # Grips of the two ends: drag them to change the duration
                    painter.setPen(QtCore.Qt.NoPen)
                    painter.setBrush(QtGui.QColor(255, 255, 255, 170))
                    for x in (rect.left() + 3, rect.right() - 5):
                        painter.drawRoundedRect(QtCore.QRectF(x, rect.top() + 6, 2, rect.height() - 12), 1, 1)
                self._clip_text(painter, rect.adjusted(4, 0, -4, 0), track["asset"].stem, f"{layer.duration} f")
            elif track["type"] in ("plan", "cam"):
                colors = CLIP_PLAN if track["type"] == "plan" else CLIP_CAM
                for n, (a, b, name) in enumerate(track["clips"]):
                    rect = QtCore.QRectF(self.x_of(a) + 0.5, top + 4.5, (b - a + 1) * self.scale - 2, self.TRACK - 9)
                    current = a <= frame <= b
                    self._clip(painter, rect, colors[n % 2], current)
                    text = name if track["type"] == "cam" else name
                    self._clip_text(painter, rect, text, f"{b - a + 1} f")
            else:
                color = CLIP_FX if track["type"] == "fx" else CLIP_ANIM
                rect = QtCore.QRectF(self.x_of(0) + 0.5, top + 4.5, (track["end"] + 1) * self.scale - 2, self.TRACK - 9)
                self._clip(painter, rect, color, False)
                painter.save()
                painter.setClipRect(rect.intersected(QtCore.QRectF(self.HEADS, 0, width, self.height())))
                for a, b, name in track["parts"]:
                    part = QtCore.QRectF(self.x_of(a), rect.top(), (b - a + 1) * self.scale, rect.height())
                    if a <= frame <= b:
                        painter.fillRect(part, QtGui.QColor(255, 255, 255, 30))
                    painter.setPen(QtGui.QColor(0, 0, 0, 90))
                    painter.drawLine(QtCore.QPointF(part.right(), part.top()), QtCore.QPointF(part.right(), part.bottom()))
                    if part.width() > 26:
                        painter.setFont(mono_font(10, QtGui.QFont.Medium))
                        painter.setPen(QtGui.QColor(255, 255, 255, 230 if a <= frame <= b else 200))
                        painter.drawText(part.adjusted(6, 0, 0, 0), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, name)
                painter.setFont(mono_font(10, QtGui.QFont.Normal))
                painter.setPen(QtGui.QColor(238, 243, 250, 190))
                kinds_rect = QtCore.QRectF(rect.left(), rect.top(), min(rect.width(), width - rect.left()) - 6, rect.height())
                painter.drawText(kinds_rect, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight, track["kinds"])
                painter.restore()
            painter.setOpacity(1.0)
            painter.setPen(QtGui.QColor(LINE))
            painter.drawLine(self.HEADS, top + self.TRACK, width, top + self.TRACK)

        # Play head
        x = self.x_of(frame + 0.5)
        painter.setPen(QtGui.QPen(QtGui.QColor(ACCENT), 1))
        painter.drawLine(QtCore.QPointF(x, 0), QtCore.QPointF(x, self.height()))
        knob = QtGui.QPolygonF([QtCore.QPointF(x - 6, 0), QtCore.QPointF(x + 6, 0), QtCore.QPointF(x + 6, 8.4),
                                QtCore.QPointF(x, 14), QtCore.QPointF(x - 6, 8.4)])
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(ACCENT))
        painter.drawPolygon(knob)
        painter.restore()

        # Heads
        painter.fillRect(QtCore.QRect(0, 0, self.HEADS, self.height()), QtGui.QColor(PANEL))
        painter.fillRect(QtCore.QRect(0, 0, self.HEADS, self.RULER), QtGui.QColor(PANEL_2))
        painter.setPen(QtGui.QColor(FAINT))
        painter.setFont(mono_font(11, QtGui.QFont.Normal))
        painter.drawText(QtCore.QRect(10, 0, self.HEADS - 20, self.RULER), QtCore.Qt.AlignVCenter,
                         f"30 fps · {self.last + 1} frames")
        painter.setPen(QtGui.QColor(LINE))
        painter.drawLine(0, self.RULER, self.HEADS, self.RULER)
        for index, track in enumerate(self.tracks):
            top = self.RULER + index * self.TRACK
            off = self.off(track)
            if "layer" in track and track["layer"] is self.window_.selected_layer:
                painter.fillRect(QtCore.QRect(0, top + 1, self.HEADS, self.TRACK - 1), QtGui.QColor(PANEL_2))
            if track["asset"] is not None:
                draw_icon(painter, "eye", FAINT if off else MUTED, QtCore.QRectF(14, top + 10, 14, 14))
            name_color = FAINT if off else FG
            painter.setPen(QtGui.QColor(name_color))
            painter.setFont(mono_font(12))
            metrics = QtGui.QFontMetrics(painter.font())
            painter.drawText(QtCore.QRect(40, top + 3, self.HEADS - 60, 16), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                             metrics.elidedText(track["name"], QtCore.Qt.ElideRight, self.HEADS - 60))
            small = QtGui.QFont()
            small.setPixelSize(10)
            painter.setFont(small)
            painter.setPen(QtGui.QColor(FAINT if off else MUTED))
            painter.drawText(QtCore.QRect(40, top + 18, self.HEADS - 60, 13), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                             QtGui.QFontMetrics(small).elidedText(track["sub"], QtCore.Qt.ElideRight, self.HEADS - 60))
            tag = {"plan": FAINT, "cam": CLIP_CAM[0], "anim": CLIP_ANIM, "fx": CLIP_FX, "image": CLIP_IMAGE,
                   "text": CLIP_TEXT}[track["type"]]
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(tag))
            painter.drawRoundedRect(QtCore.QRectF(self.HEADS - 14, top + 8, 4, 18), 2, 2)
            painter.setPen(QtGui.QColor(LINE))
            painter.drawLine(0, top + self.TRACK, self.HEADS, top + self.TRACK)
        painter.drawLine(self.HEADS, 0, self.HEADS, self.height())

    def _clip(self, painter, rect, color, current):
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 140 if current else 20), 1))
        painter.setBrush(QtGui.QColor(color))
        painter.drawRoundedRect(rect, 4, 4)

    def _clip_text(self, painter, rect, text, small):
        painter.save()
        painter.setClipRect(rect)
        painter.setFont(mono_font(11))
        painter.setPen(QtGui.QColor("#eef3fa"))
        metrics = QtGui.QFontMetrics(painter.font())
        painter.drawText(rect.adjusted(7, 0, 0, 0), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, text)
        painter.setFont(mono_font(11, QtGui.QFont.Normal))
        painter.setPen(QtGui.QColor(238, 243, 250, 180))
        painter.drawText(rect.adjusted(7 + metrics.horizontalAdvance(text) + 6, 0, 0, 0),
                         QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, small)
        painter.restore()

    # Mouse

    def track_at(self, y):
        index = int((y - self.RULER) // self.TRACK)
        return self.tracks[index] if y >= self.RULER and 0 <= index < len(self.tracks) else None

    GRIP = 6        # pixels at the ends of a picture clip that change its duration

    def clip_hit(self, position):
        """(mode, layer) of the picture clip under the mouse: "start" / "end" (its ends) or "move"."""
        track = self.track_at(position.y())
        if track is None or "layer" not in track or position.x() < self.HEADS:
            return None, None
        layer = track["layer"]
        rect = self.clip_rect(self.tracks.index(track), layer)
        if not rect.adjusted(-3, 0, 3, 0).contains(position):
            return None, None
        grip = min(self.GRIP, rect.width() / 3)
        if position.x() <= rect.left() + grip:
            return "start", layer
        if position.x() >= rect.right() - grip:
            return "end", layer
        return "move", layer

    def frame_float(self, x):
        return (x - self.HEADS + self.offset) / self.scale

    def mousePressEvent(self, event):
        position = event.position()
        if position.x() >= self.HEADS:
            if event.button() == QtCore.Qt.LeftButton:
                mode, layer = self.clip_hit(position)
                if layer is not None:
                    self.window_.select_layer(layer)
                    self._drag = (mode, layer, self.frame_float(position.x()), layer.start, layer.duration)
                    self.setCursor(QtCore.Qt.ClosedHandCursor if mode == "move" else QtCore.Qt.SizeHorCursor)
                    return
                self._seeking = True
                self.window_.seek(self.frame_at(position.x()))
            return
        track = self.track_at(position.y())
        if track is None or track["asset"] is None:
            return
        if "layer" in track and event.button() == QtCore.Qt.LeftButton:
            if 8 <= position.x() <= 34:
                self.window_.toggle_layer_visible(track["layer"])
            else:
                self.window_.select_layer(track["layer"])
            return
        if event.button() == QtCore.Qt.LeftButton and 8 <= position.x() <= 34:
            self.window_.toggle_visible(track["asset"])

    def mouseMoveEvent(self, event):
        position = event.position()
        if self._drag is not None:
            mode, layer, pressed, start, duration = self._drag
            delta = int(round(self.frame_float(position.x()) - pressed))
            if mode == "move":
                layer.start = max(0, start + delta)
            elif mode == "start":
                # The end stays: the clip gets longer or shorter from its beginning
                layer.start = max(0, min(start + duration - 1, start + delta))
                layer.duration = start + duration - layer.start
            else:
                layer.duration = max(1, duration + delta)
            self.window_.layers_changed(timeline=False)
            self.update()
            return
        if self._seeking:
            self.window_.seek(self.frame_at(position.x()))
        track = self.track_at(position.y())
        mode, _ = self.clip_hit(position)
        if mode == "move":
            self.setCursor(QtCore.Qt.OpenHandCursor)
        elif mode is not None:
            self.setCursor(QtCore.Qt.SizeHorCursor)
        else:
            over_eye = position.x() < self.HEADS and track is not None and track["asset"] is not None and 8 <= position.x() <= 34
            self.setCursor(QtCore.Qt.PointingHandCursor if over_eye or position.x() >= self.HEADS else QtCore.Qt.ArrowCursor)

    def mouseReleaseEvent(self, event):
        self._seeking = False
        if self._drag is not None:
            self._drag = None
            self.window_.layers_changed()

    def keyPressEvent(self, event):
        layer = self.window_.selected_layer
        if layer is not None and event.key() in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            self.window_.remove_layer(layer)
            return
        super().keyPressEvent(event)

    # A picture card dropped here goes into the timeline, from the frame under the mouse

    def _dropped_picture(self, event):
        if not event.mimeData().hasFormat(ASSET_MIME):
            return None
        asset = self.window_.assets_panel.asset_of(event.mimeData())
        return asset if asset is not None and asset.is_picture else None

    def dragEnterEvent(self, event):
        if self._dropped_picture(event) is not None:
            event.setDropAction(QtCore.Qt.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        asset = self._dropped_picture(event)
        if asset is None:
            event.ignore()
            return
        event.setDropAction(QtCore.Qt.CopyAction)
        event.accept()
        x = event.position().x()
        start = self.frame_at(x) if x >= self.HEADS else self.window_.frame
        self.window_.add_layer(asset, max(0, start))

    def contextMenuEvent(self, event):
        track = self.track_at(event.pos().y())
        if track is not None and "layer" in track:
            if event.pos().x() < self.HEADS or self.clip_hit(QtCore.QPointF(event.pos()))[1] is not None:
                self.window_.select_layer(track["layer"])
                self.window_.layer_menu(track["layer"], event.globalPos())
            return
        if track is None or event.pos().x() >= self.HEADS or track["asset"] is None:
            return
        asset = track["asset"]
        menu = QtWidgets.QMenu(self)
        visible = menu.addAction("Visible", lambda: self.window_.toggle_visible(asset))
        visible.setCheckable(True)
        visible.setChecked(asset.visible)
        targets = self.window_.project.bound_targets(asset)
        if targets:
            menu.addSeparator()
            menu.addAction("Unbind", lambda: self.window_.unbind(asset))
        menu.exec(event.globalPos())

    def wheelEvent(self, event):
        if event.modifiers() & QtCore.Qt.ControlModifier:
            self.panel.zoom.setValue(self.panel.zoom.value() + (1 if event.angleDelta().y() > 0 else -1))
            event.accept()
        elif event.modifiers() & QtCore.Qt.ShiftModifier:
            self.panel.hbar.setValue(self.panel.hbar.value() - event.angleDelta().y())
            event.accept()
        else:
            event.ignore()


class TimelinePanel(QtWidgets.QWidget):
    def __init__(self, window):
        super().__init__()
        self.window_ = window
        self.setMinimumHeight(100)
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(QtGui.QPalette.Window, QtGui.QColor(PANEL))
        self.setPalette(palette)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QtWidgets.QWidget(objectName="panelHead")
        bar.setAttribute(QtCore.Qt.WA_StyledBackground)
        bar.setFixedHeight(34)
        row = QtWidgets.QHBoxLayout(bar)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(12)
        row.addWidget(QtWidgets.QLabel("TIMELINE", objectName="panelTitle"))
        self.split_label = QtWidgets.QLabel()
        self.frames_label = QtWidgets.QLabel()
        for label in (self.split_label, self.frames_label):
            label.setTextFormat(QtCore.Qt.RichText)
            row.addWidget(label)
        row.addStretch(1)
        row.addWidget(QtWidgets.QLabel("Zoom", objectName="muted"))
        self.zoom = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.zoom.setRange(6, 28)
        self.zoom.setValue(11)
        self.zoom.setFixedWidth(110)
        self.zoom.setFocusPolicy(QtCore.Qt.NoFocus)
        self.zoom.valueChanged.connect(self._zoom)
        row.addWidget(self.zoom)
        layout.addWidget(bar)

        self.canvas = TimelineCanvas(self)
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.canvas)
        layout.addWidget(self.scroll, 1)
        self.hbar = QtWidgets.QScrollBar(QtCore.Qt.Horizontal)
        self.hbar.valueChanged.connect(self._scrolled)
        layout.addWidget(self.hbar)

    def _zoom(self, value):
        center = self.window_.frame
        self.canvas.scale = value / 2.0
        self.update_range()
        self.hbar.setValue(int(center * self.canvas.scale - (self.canvas.width() - self.canvas.HEADS) / 2))
        self.canvas.update()

    def _scrolled(self, value):
        self.canvas.offset = value
        self.canvas.update()

    def update_range(self):
        visible = max(self.canvas.width() - self.canvas.HEADS, 1)
        self.hbar.setRange(0, max(0, self.canvas.lanes_width() - visible))
        self.hbar.setPageStep(visible)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QtCore.QTimer.singleShot(0, self.update_range)

    def rebuild(self):
        self.canvas.build()
        self.update_range()
        self.update_frame()

    def update_frame(self):
        window = self.window_
        frame = window.frame
        index = window.split_index(frame)
        if index is not None:
            name, a, b = window.splits[index]
            self.split_label.setText(f"<span style='color:{MUTED}'>Current split</span> <b><code>{name}</code></b>")
            self.frames_label.setText(f"<span style='color:{MUTED}'>Frames</span> <b><code>{a} – {b}</code></b>")
        else:
            self.split_label.setText(f"<span style='color:{MUTED}'>Current split</span> <b>–</b>")
            self.frames_label.setText(f"<span style='color:{MUTED}'>Frames</span> <b><code>0 – {window.last_frame}</code></b>")
        # Keep the play head in sight while playing
        x = frame * self.canvas.scale - self.hbar.value()
        visible = self.canvas.width() - self.canvas.HEADS
        if window.playing and (x < 0 or x > visible - 20):
            self.hbar.setValue(int(frame * self.canvas.scale - 20))
        self.canvas.update()


# Window


class FirstEngineDialog(QtWidgets.QDialog):
    """First start: the default engine. Only OK: no Cancel, no close button, Escape does nothing."""

    def __init__(self, parent, labels, current):
        super().__init__(parent, QtCore.Qt.Dialog | QtCore.Qt.CustomizeWindowHint | QtCore.Qt.WindowTitleHint)
        self.setWindowTitle(APP)
        self.setModal(True)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        layout.addWidget(QtWidgets.QLabel("Welcome to studio_render.\nWhich engine do you want to use by default?\n"
                                          "It is installed now; Options > Default engine changes it."))
        self.combo = QtWidgets.QComboBox()
        self.combo.addItems(labels)
        self.combo.setCurrentIndex(current)
        layout.addWidget(self.combo)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        ok = QtWidgets.QPushButton("OK")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        layout.addLayout(row)

    def reject(self):
        pass

    def closeEvent(self, event):
        event.ignore()


class EngineBox(QtWidgets.QComboBox):
    pass


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, args):
        super().__init__()
        self.setAcceptDrops(True)
        self.resize(1440, 860)
        self.preferences = Preferences()
        self.project = Project(getattr(args, "engine", None) or self.preferred_engine())
        self.success = True
        self.frame = 0
        self.playing = False
        self.speed = 1.0
        self.splits = []
        self.last_frame = 0
        self.selected_layer = None      # picture of the timeline selected in the player and the timeline
        self._play_clock = QtCore.QElapsedTimer()
        self._play_position = 0.0
        self._offscreen = None
        self._thumbnail_queue = []
        self._toast_timer = QtCore.QTimer(self, singleShot=True, interval=2200)
        self._last_folder = ""
        self._last_export_folder = ""

        self.assets_panel = AssetPanel(self)
        self.player = PlayerPanel(self)
        self.gl = self.player.gl
        self.timeline = TimelinePanel(self)

        self.horizontal = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.horizontal.addWidget(self.assets_panel)
        self.horizontal.addWidget(self.player)
        self.horizontal.setStretchFactor(1, 1)
        self.horizontal.setChildrenCollapsible(False)
        self.horizontal.setHandleWidth(5)
        self.vertical = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.vertical.addWidget(self.horizontal)
        self.vertical.addWidget(self.timeline)
        self.vertical.setStretchFactor(0, 1)
        self.vertical.setChildrenCollapsible(False)
        self.vertical.setHandleWidth(5)
        self.setCentralWidget(self.vertical)
        self.horizontal.setSizes([520, 920])
        self.vertical.setSizes([600, 260])

        self.toast = QtWidgets.QLabel(self, objectName="toast")
        self.toast.setTextFormat(QtCore.Qt.RichText)
        self.toast.hide()
        self._toast_timer.timeout.connect(self.toast.hide)

        self._build_menus()
        self._build_shortcuts()

        self.player.outlines.toggled.connect(self._set_outlines)
        self.player.lighting.toggled.connect(self._set_lighting)
        self.player.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.player.setFocus()
        self.gl.gesture_started.connect(lambda: self.set_game_camera(False, keep_view=True))
        self.gl.drawn.connect(self._view_drawn)

        self.timer = QtCore.QTimer(self)
        self.timer.setTimerType(QtCore.Qt.PreciseTimer)
        self.timer.setInterval(8)
        self.timer.timeout.connect(self._tick)
        self.thumbnail_timer = QtCore.QTimer(self, singleShot=True, interval=0)
        self.thumbnail_timer.timeout.connect(self._render_next_thumbnail)

        self._apply_preferences()
        self._open_arguments(args)
        self.reset_free_camera(update=False)
        self.scene_changed()

    # Building

    def _build_menus(self):
        bar = self.menuBar()
        file_menu = bar.addMenu("&File")
        file_menu.addAction("New", self.new_project, QtGui.QKeySequence("Ctrl+N"))
        file_menu.addAction("Open…", self.open_project_dialog, QtGui.QKeySequence("Ctrl+O"))
        file_menu.addAction("Save", self.write_project, QtGui.QKeySequence("Ctrl+S"))
        file_menu.addAction("Save as…", self.write_project_as, QtGui.QKeySequence("Ctrl+Shift+S"))
        file_menu.addSeparator()
        export = file_menu.addMenu("Export")
        export.addAction(".mp4…", self.export_mp4)

        view = bar.addMenu("&View")
        self.assets_action = view.addAction("Assets", self._toggle_assets, QtGui.QKeySequence("Ctrl+1"))
        self.assets_action.setCheckable(True)
        self.timeline_action = view.addAction("Timeline", self._toggle_timeline, QtGui.QKeySequence("Ctrl+T"))
        self.timeline_action.setCheckable(True)
        view.addSeparator()
        view.addAction("Frame all", self.frame_all, QtGui.QKeySequence("F"))
        view.addSeparator()
        view.addAction("Reset game camera", self.reset_game_camera)
        view.addAction("Reset free camera", self.reset_free_camera)

        options = bar.addMenu("&Options")
        languages = options.addMenu("Languages")
        self.squirrel_action = languages.addAction("Squirrel", self._toggle_squirrel)
        self.squirrel_action.setCheckable(True)
        shot = options.addMenu("Screenshot")
        group = QtGui.QActionGroup(self)
        self.shot_file = shot.addAction("Save to a file…", lambda: self._set_screenshot(SCREENSHOT_FILE))
        self.shot_clipboard = shot.addAction("Copy to the clipboard", lambda: self._set_screenshot(SCREENSHOT_CLIPBOARD))
        for action in (self.shot_file, self.shot_clipboard):
            action.setCheckable(True)
            group.addAction(action)
        self.home_menu = options.addMenu("Home project")
        self.home_menu.aboutToShow.connect(self._fill_home_menu)
        self.default_engine_menu = options.addMenu("Default engine")
        self.default_engine_menu.aboutToShow.connect(self._fill_default_engine_menu)

        # Kept on the window: the menu bar does not keep the Python object alive
        self._corner = corner = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(corner)
        row.setContentsMargins(0, 0, 10, 0)
        row.setSpacing(8)
        label = QtWidgets.QLabel("Engine", objectName="muted")
        row.addWidget(label)
        self.engine_box = EngineBox(objectName="engine")
        self.engine_box.setFocusPolicy(QtCore.Qt.NoFocus)
        model = QtGui.QStandardItemModel(self.engine_box)
        for engine_id, name in eleven.game_engines():
            item = QtGui.QStandardItem(f"{engine_id} · {name}")
            item.setData(engine_id, QtCore.Qt.UserRole)
            if eleven.engine_state(engine_id) == "missing":
                item.setEnabled(False)
                item.setToolTip("Neither the zip nor the folder of this engine is there.")
            model.appendRow(item)
        self.engine_box.setModel(model)
        self.engine_box.activated.connect(self._engine_chosen)
        row.addWidget(self.engine_box)
        bar.setCornerWidget(corner, QtCore.Qt.TopRightCorner)
        self._show_engine(self.project.engine_id)

    def _build_shortcuts(self):
        for key, slot in (("Space", self.toggle_play), ("Left", self.previous_frame), ("Right", self.next_frame),
                          ("J", self.back_10), ("L", self.forward_10), ("Home", self.go_start), ("End", self.go_end)):
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(key), self)
            shortcut.activated.connect(slot)

    def _apply_preferences(self):
        preferences = self.preferences
        self.assets_action.setChecked(preferences.show_assets)
        self.assets_panel.setVisible(preferences.show_assets)
        self.timeline_action.setChecked(preferences.show_timeline)
        self.timeline.setVisible(preferences.show_timeline)
        self.squirrel_action.setChecked(preferences.squirrel)
        (self.shot_clipboard if preferences.screenshot == SCREENSHOT_CLIPBOARD else self.shot_file).setChecked(True)

    # Home project: opened at startup when nothing else is asked

    def _fill_home_menu(self):
        menu = self.home_menu
        menu.clear()
        home = self.preferences.home_project
        current = menu.addAction(os.path.basename(home) if home else "No home project")
        current.setEnabled(False)
        if home:
            current.setToolTip(home)
            if not os.path.isfile(home):
                current.setText(f"{os.path.basename(home)} (missing)")
        menu.addSeparator()
        enabled = menu.addAction("Open at startup", lambda: setattr(self.preferences, "home_enabled", not self.preferences.home_enabled))
        enabled.setCheckable(True)
        enabled.setChecked(self.preferences.home_enabled)
        enabled.setEnabled(bool(home))
        menu.addAction("Choose…", self.choose_home_project)
        use_current = menu.addAction("Use the current project", lambda: self.set_home_project(self.project.path))
        use_current.setEnabled(bool(self.project.path) and os.path.normcase(self.project.path or "") != os.path.normcase(home))
        open_home = menu.addAction("Open the home project", lambda: self.open_project_checked(home))
        open_home.setEnabled(bool(home) and os.path.isfile(home))
        menu.addAction("Remove the home project", lambda: self.set_home_project("")).setEnabled(bool(home))

    def choose_home_project(self):
        home = self.preferences.home_project
        folder = os.path.dirname(home) if home else self._last_folder
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Home project", folder, "Studio Render Project (*.srp)")
        if path:
            self.set_home_project(path)

    def set_home_project(self, path):
        self.preferences.home_project = os.path.abspath(path) if path else ""
        if path:
            self.preferences.home_enabled = True
            self.show_toast(f"<b style='color:{ACCENT}'>{os.path.basename(path)}</b> opens at startup")

    def _open_home_project(self):
        home = self.preferences.home_project
        if home and self.preferences.home_enabled and os.path.isfile(home):
            self.open_project(home)

    def _open_arguments(self, args):
        files = [item.partition("@")[0] for item in getattr(args, "files", [])]
        if not files and not getattr(args, "object", None) and not getattr(args, "animate", None):
            self._open_home_project()
        projects = [path for path in files if path.lower().endswith(project_module.EXTENSION)]
        if projects:
            self.open_project(projects[0])
        others = [path for path in files if not path.lower().endswith(project_module.EXTENSION)]
        if others:
            self.import_files(others)
        try:
            project_module.apply_command_line(self.project, getattr(args, "object", []), getattr(args, "animate", []))
        except ProjectError as error:
            message = str(error)
            QtCore.QTimer.singleShot(0, lambda: QtWidgets.QMessageBox.warning(self, APP, message))
        for custom in self.project.customs():
            self.queue_thumbnail(custom)

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_started", False):
            self._started = True
            QtCore.QTimer.singleShot(0, self._startup_questions)

    # Preferred engine: chosen at the first start, the engine of a new project

    def preferred_engine(self):
        """The engine of the .ini; without one, IE4 (or the first installed engine when IE4 is not)."""
        engine_id = self.preferences.engine
        if engine_id and eleven.engine_state(engine_id) != "missing":
            return engine_id
        if eleven.engine_state("IE4") == "ready":
            return "IE4"
        return next((e for e, _ in eleven.game_engines() if eleven.engine_state(e) == "ready"), "IE4")

    def _engine_choices(self):
        """(id, label) of the engines that can be installed or are, in the order of the Engine list."""
        return [(engine_id, f"{engine_id} · {name}") for engine_id, name in eleven.game_engines()
                if eleven.engine_state(engine_id) != "missing"]

    def choose_first_engine(self):
        """First start: which engine studio_render uses by default; it is installed at once and kept in the .ini."""
        choices = self._engine_choices()
        if not choices:
            return
        current = next((i for i, (engine_id, _) in enumerate(choices) if engine_id == "IE4"), 0)
        # An engine is needed to draw anything: the question comes back until one is installed
        while self.no_engine_installed():
            dialog = FirstEngineDialog(self, [label for _, label in choices], current)
            dialog.exec()
            self.set_default_engine(choices[dialog.combo.currentIndex()][0])

    def set_default_engine(self, engine_id):
        """Preferred engine: installed if it is not, kept in the .ini; an empty untitled project takes it at once."""
        if eleven.engine_state(engine_id) == "not installed" and not self._install(engine_id):
            return False
        self.preferences.engine = engine_id
        project = self.project
        if project.path is None and not project.items and project.engine_id != engine_id:
            project.set_engine(engine_id)
            project.dirty = False
            self._show_engine(engine_id)
            self.reload_engine()
        self.show_toast(f"<b style='color:{ACCENT}'>{engine_id}</b> is the default engine")
        return True

    def _fill_default_engine_menu(self):
        menu = self.default_engine_menu
        menu.clear()
        group = QtGui.QActionGroup(menu)
        preferred = self.preferences.engine
        for engine_id, label in self._engine_choices():
            state = eleven.engine_state(engine_id)
            action = menu.addAction(label + ("" if state == "ready" else "   (installed when chosen)"),
                                    lambda e=engine_id: self.set_default_engine(e))
            action.setCheckable(True)
            action.setChecked(engine_id == preferred)
            group.addAction(action)

    @staticmethod
    def no_engine_installed():
        return not any(eleven.engine_state(engine_id) == "ready" for engine_id, _ in eleven.game_engines()
                       if engine_id not in eleven.COMPOSITE_ENGINES)

    def _startup_questions(self):
        """First start (no engine installed yet): the default engine. Then the engines whose zip changed since they
        were installed: proposed at every start, nothing is remembered."""
        if self.no_engine_installed():
            self.choose_first_engine()
        for engine_id in eleven.pending_updates():
            answer = QtWidgets.QMessageBox.question(
                self, APP, f"A new version of {engine_id} is available. Update it now?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.Yes)
            if answer == QtWidgets.QMessageBox.Yes:
                self._install(engine_id)
        engine_id = self.project.engine_id
        if eleven.engine_state(engine_id) == "not installed":
            if self._ask_install(engine_id):
                self.reload_engine()

    @property
    def scene(self):
        return self.project.scene

    def open_asset(self, asset):
        """Double click: the asset alone in its own window (a model), or the script editor."""
        if self.playing:
            self.toggle_play()
        if not asset_windows.open_asset(self, asset):
            self.show_toast(f"<b style='color:{ACCENT}'>{asset.name}</b> has nothing to render on its own")

    # Title, toast

    def update_title(self):
        project = self.project
        template = f" [{project.template.name}]" if project.template is not None and project.path else ""
        self.setWindowTitle(f"{project.name}{'*' if project.dirty else ''}{template} - {APP}")

    def show_toast(self, html):
        self.toast.setText(html)
        self.toast.adjustSize()
        self.toast.move((self.width() - self.toast.width()) // 2, self.height() - self.toast.height() - 24)
        self.toast.raise_()
        self.toast.show()
        self._toast_timer.start()

    # Scene and frame

    def scene_changed(self, assets=True):
        self.gl.queue_prepare()
        self.splits = self.project.splits()
        self.last_frame = max(self.project.scene.frame_count, self.project.layers_end(), 0)
        self.frame = min(self.frame, self.last_frame)
        if self.selected_layer not in self.project.layers:
            self.selected_layer = None
        self.player.stage.editor.update()
        if assets:
            self.assets_panel.refresh()
        self.timeline.rebuild()
        self.update_frame()
        self.update_title()

    def split_index(self, frame):
        for index, (_, a, b) in enumerate(self.splits):
            if a <= frame <= b:
                return index
        return None

    def update_frame(self):
        frame = self.frame
        transport = self.player.transport
        transport.timecode.setText(timecode(frame))
        transport.frame_label.setText(f"frame {frame} / {self.last_frame} · 30 fps")
        index = self.split_index(frame)
        split = None
        if index is not None:
            name, a, b = self.splits[index]
            split = f"Split {name} · {frame - a}/{b - a}"
        camera_text = None
        scene = self.project.scene
        index, _ = scene.camera_index_at(frame)
        if index is not None:
            played = scene.played_cameras()
            track = played[index]
            archive = os.path.splitext(track.archive)[0]
            if played is not scene.cameras:
                # The script of a template orders the cameras: its cuts are counted
                camera_text = f"{archive} · cut {index + 1}/{len(played)}"
            else:
                same = [c for c in played if c.owner is track.owner]
                camera_text = f"{archive} · {same.index(track) + 1}/{len(same)}"
        self.player.stage.set_overlays(split, camera_text)
        if self.timeline.isVisible():
            self.timeline.update_frame()
        self.gl.update()

    def _view_drawn(self):
        pass

    def seek(self, frame):
        self.frame = int(max(0, min(self.last_frame, frame)))
        self._play_position = float(self.frame)
        self._play_clock.restart()
        self.update_frame()

    def go_start(self):
        self.seek(0)

    def go_end(self):
        self.seek(self.last_frame)

    def back_10(self):
        self.seek(self.frame - 10 * FPS)

    def forward_10(self):
        self.seek(self.frame + 10 * FPS)

    def previous_frame(self):
        self.seek(self.frame - 1)

    def next_frame(self):
        self.seek(self.frame + 1)

    def set_speed(self, speed):
        self.speed = speed
        self.player.transport.speed.setText(f"{speed:g}×")
        self._play_position = float(self.frame)
        self._play_clock.restart()

    def toggle_play(self):
        self.playing = not self.playing
        self.player.transport.set_playing(self.playing)
        if self.playing:
            if self.frame >= self.last_frame:
                self.frame = 0
            self._play_position = float(self.frame)
            self._play_clock.start()
            self.timer.start()
        else:
            self.timer.stop()

    def _tick(self):
        elapsed = self._play_clock.restart() / 1000.0
        self._play_position += elapsed * FPS * self.speed
        if self._play_position > self.last_frame:
            if self.player.transport.loop.isChecked() and self.last_frame > 0:
                self._play_position %= self.last_frame + 1
            else:
                self._play_position = float(self.last_frame)
                self.frame = self.last_frame
                self.update_frame()
                self.toggle_play()
                return
        frame = int(self._play_position)
        if frame != self.frame:
            self.frame = frame
            self.update_frame()

    # Cameras

    def set_game_camera(self, on, keep_view=False):
        self.gl.game_camera = on
        self.player.set_game_camera(on)
        self.gl.update()

    def reset_game_camera(self):
        """Back to the cameras of the animation, whatever was done with the mouse."""
        self.set_game_camera(True)

    def reset_free_camera(self, update=True):
        """First frame of the first camera imported, otherwise from above the origin."""
        view = self.gl.view
        scene = self.project.scene
        if scene.cameras:
            view.set_pose(*scene.cameras[0].at(0))
        else:
            view.look_down(100.0)
        if update:
            self.set_game_camera(False)

    def frame_all(self):
        scene = self.project.scene
        if scene.visible_entries():
            low, high = scene.bounds(float(self.frame))
            self.gl.view.frame_bounds(low, high)
            self.gl.view.roll = 0.0
            self.gl.view.fov = View().fov
        self.set_game_camera(False)

    def _set_outlines(self, on):
        self.gl.draw_outlines = on
        self.gl.update()

    def set_success(self, success):
        """The preview plays the technique as a success or a failure (the _success of its script)."""
        self.success = success
        self.project.success = success
        (self.player.success_button if success else self.player.failure_button).setChecked(True)
        self.scene.revision += 1
        self.scene_changed(assets=False)
        self.gl.update()

    def _set_lighting(self, on):
        self.gl.fragment_lighting = on
        self.gl.update()

    # View and options

    def _toggle_assets(self):
        on = self.assets_action.isChecked()
        self.assets_panel.setVisible(on)
        self.preferences.show_assets = on

    def _toggle_timeline(self):
        on = self.timeline_action.isChecked()
        self.timeline.setVisible(on)
        self.preferences.show_timeline = on
        if on:
            self.timeline.rebuild()

    def _toggle_squirrel(self):
        self.preferences.squirrel = self.squirrel_action.isChecked()
        self.assets_panel.refresh()

    def _set_screenshot(self, destination):
        self.preferences.screenshot = destination

    # Engines

    def _show_engine(self, engine_id):
        index = self.engine_box.findData(engine_id, QtCore.Qt.UserRole)
        if index >= 0:
            self.engine_box.setCurrentIndex(index)

    def _install(self, engine_id):
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            eleven.install_game(engine_id)
            return True
        except Exception as error:
            traceback.print_exc()
            QtWidgets.QMessageBox.warning(self, APP, f"{engine_id} could not be installed: {error}")
            return False
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def _ask_install(self, engine_id):
        answer = QtWidgets.QMessageBox.question(
            self, APP, f"{engine_id} is downloaded but not installed. Install it now?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.Yes)
        return answer == QtWidgets.QMessageBox.Yes and self._install(engine_id)

    def _engine_chosen(self, index):
        engine_id = self.engine_box.itemData(index, QtCore.Qt.UserRole)
        previous = self.project.engine_id
        if engine_id == previous:
            return
        state = eleven.engine_state(engine_id)
        if state == "missing" or (state == "not installed" and not self._ask_install(engine_id)):
            self._show_engine(previous)
            return
        self.project.set_engine(engine_id)
        self.reload_engine()

    def reload_engine(self):
        """Material bindings, programs and textures are made again, and the thumbnails of the models."""
        self.gl.release_scene()
        self.project.scene.set_engine_all(self.project.engine_id)
        for asset in self.project.walk():
            if (asset.kind == assets_module.MODEL and asset.model is not None) or asset.kind == assets_module.CUSTOM:
                self.queue_thumbnail(asset)
        self.scene_changed()

    # Assets

    def import_dialog(self):
        patterns = "*.xc *.xv *.xk *.pck *.xi *.png *.jpg *.jpeg" + (" *.nut *.nutb" if self.preferences.squirrel else "")
        title = "Level-5 files and images"
        template = self.project.template
        if template is not None and template.imports is not None:
            patterns = " ".join(pattern for rule in template.imports for pattern in rule.get("patterns", []))
            title = f"Files of {template.name}"
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Import", self._last_folder, f"{title} ({patterns});;All files (*)")
        if paths:
            self._last_folder = os.path.dirname(paths[0])
            self.import_files(paths)

    def read_archives(self, archives):
        """Reads the archives that are not cached yet in the worker processes (model.preload) while the window keeps
        running behind a progress dialog; adding them to the project afterwards is immediate."""
        pending = [(name, data) for name, data in archives if not model_module.is_cached(data, name)]
        if not pending:
            return
        state = {"done": 0, "total": len(pending), "finished": False}

        def progress(done, total):
            state["done"], state["total"] = done, total

        def work():
            try:
                model_module.preload(pending, progress)
            except Exception:
                traceback.print_exc()
            finally:
                state["finished"] = True

        thread = threading.Thread(target=work, name="read archives", daemon=True)
        thread.start()
        dialog = QtWidgets.QProgressDialog("Reading the archives…", None, 0, len(pending), self)
        dialog.setWindowTitle(APP)
        dialog.setWindowModality(QtCore.Qt.WindowModal)
        dialog.setMinimumDuration(400)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        loop = QtCore.QEventLoop()
        timer = QtCore.QTimer(interval=50)

        def poll():
            dialog.setMaximum(max(state["total"], 1))
            dialog.setValue(state["done"])
            dialog.setLabelText(f"Reading the archives… {state['done']} / {state['total']}")
            if state["finished"]:
                loop.quit()

        timer.timeout.connect(poll)
        timer.start()
        loop.exec()
        timer.stop()
        thread.join()
        dialog.close()
        dialog.deleteLater()

    def import_files(self, paths):
        refused, failed, not_taken, replaced = [], [], [], []
        archives = []
        for path in paths:
            if assets_module.is_script(path) or not self.project.import_rule(os.path.basename(path))[0]:
                continue
            try:
                with open(path, "rb") as stream:
                    archives += project_module.archives_to_read(os.path.basename(path), stream.read())
            except Exception:
                pass
        self.read_archives(archives)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            for path in paths:
                name = os.path.basename(path)
                lower = name.lower()
                allowed, rule = self.project.import_rule(name)
                if not allowed:
                    not_taken.append(name)
                    continue
                if assets_module.is_script(name) and not self.preferences.squirrel:
                    refused.append(name)
                    continue
                try:
                    with open(path, "rb") as stream:
                        data = stream.read()
                except OSError as error:
                    failed.append(f"{name}: {error}")
                    continue
                if not lower.endswith(assets_module.IMPORT_EXTENSIONS) and not assets_module.is_archive(data):
                    failed.append(f"{name}: not a Level-5 archive, an image or a Squirrel script")
                    continue
                refusal = self.project.rule_refusal(rule, name, data)
                if refusal:
                    not_taken.append(f"{name}: {refusal}")
                    continue
                try:
                    holder = self.project.holder(rule) if rule is not None else None
                    if holder is not None:
                        # A template holds one file per rule: the new one takes the place of the old one
                        if holder in self._thumbnail_queue:
                            self._thumbnail_queue.remove(holder)
                        asset, _ = self.project.replace(holder, name, data,
                                                        source_path=os.path.abspath(path) if assets_module.is_script(name) else None)
                        replaced.append((holder.name, asset.name))
                        if asset.kind == assets_module.SQUIRREL:
                            self.project.check_script(asset)
                        else:
                            self._queue_models(asset)
                    elif assets_module.is_script(name):
                        asset = self.project.add(name, data, source_path=os.path.abspath(path))
                        self.project.check_script(asset)
                    else:
                        asset = self.project.add(name, data)
                        if asset.error:
                            failed.append(asset.error)
                        self._queue_models(asset)
                        if asset.is_package:
                            self.project.auto_bind()
                except Exception as error:
                    traceback.print_exc()
                    failed.append(f"{name}: {error}")
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        if self.project.scene.cameras and not getattr(self, "_camera_placed", False):
            self._camera_placed = True
            self.reset_free_camera(update=False)
        self.gl.release_scene()
        self.scene_changed()
        if replaced:
            self.show_toast("<br>".join(f"<b style='color:{ACCENT}'>{old}</b> replaced by <b style='color:{ACCENT}'>{new}</b>"
                                        for old, new in replaced))
        if not_taken:
            template = self.project.template
            QtWidgets.QMessageBox.warning(
                self, APP, ("This file cannot be imported in this project:" if len(not_taken) == 1 else
                            "These files cannot be imported in this project:") + "\n\n" + "\n".join(not_taken)
                + f"\n\n{template.name} only takes {template.accepted()}.")
        if refused:
            QtWidgets.QMessageBox.information(
                self, APP, "Squirrel scripts are turned off (Options > Languages > Squirrel):\n" + "\n".join(refused))
        if failed:
            QtWidgets.QMessageBox.warning(self, APP, "\n".join(failed))

    def _queue_models(self, asset):
        for each in [asset] + asset.children:
            if each.kind == assets_module.MODEL and each.model is not None:
                self.queue_thumbnail(each)

    def queue_thumbnail(self, asset):
        if asset not in self._thumbnail_queue:
            self._thumbnail_queue.append(asset)
        self.thumbnail_timer.start(0)

    def _render_next_thumbnail(self):
        if self.playing:
            # A thumbnail takes the window for a moment: it waits for the playback to stop
            self.thumbnail_timer.start(500)
            return
        while self._thumbnail_queue:
            asset = self._thumbnail_queue.pop(0)
            if asset not in list(self.project.walk()):
                continue
            self._render_thumbnail(asset)
            self.assets_panel.repaint_tiles()
            break
        if self._thumbnail_queue:
            self.thumbnail_timer.start(0)

    def flush_thumbnails(self):
        playing, self.playing = self.playing, False
        while self._thumbnail_queue:
            self._render_next_thumbnail()
        self.playing = playing

    def offscreen(self):
        if self._offscreen is None:
            self._offscreen = offscreen_module.Offscreen()
        return self._offscreen

    def _render_thumbnail(self, asset):
        try:
            models = [m.model for m in asset.members] if asset.kind == assets_module.CUSTOM else asset.model
            asset.thumbnail = offscreen_module.thumbnail(self.offscreen(), models, self.project.engine_id)
        except Exception:
            traceback.print_exc()
            asset.thumbnail = None

    def remove_asset(self, asset):
        if not self.project.can_remove(asset):
            self.show_toast(f"<b style='color:{ACCENT}'>{asset.name}</b> cannot be removed: {self.project.lock_text(asset)}")
            return
        if asset in self._thumbnail_queue:
            self._thumbnail_queue.remove(asset)
        self.project.remove(asset)
        self.gl.release_scene()
        self.scene_changed()
        self.show_toast(f"<b style='color:{ACCENT}'>{asset.name}</b> removed from the assets")

    def replace_dialog(self, asset):
        """Replace…: another file takes the place of the asset; the links that still make sense are kept."""
        if not self.project.can_replace(asset):
            self.show_toast(f"<b style='color:{ACCENT}'>{asset.name}</b> cannot be replaced: {self.project.lock_text(asset)}")
            return
        folder = os.path.dirname(asset.source_path) if asset.source_path else self._last_folder
        if assets_module.is_script(asset.name):
            pattern = "Squirrel scripts (*.nut *.nutb)"
        elif asset.is_picture:
            pattern = "Images (*.xi *.png *.jpg *.jpeg)"
        else:
            pattern = "Level-5 archives (*.xc *.xv *.xk *.pck)"
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, f"Replace {asset.name}", folder, f"{pattern};;All files (*)")
        if path:
            self._last_folder = os.path.dirname(path)
            self.replace_asset(asset, path)

    def replace_asset(self, asset, path):
        name = os.path.basename(path)
        if assets_module.is_script(name) and not self.preferences.squirrel:
            QtWidgets.QMessageBox.information(self, APP, "Squirrel scripts are turned off (Options > Languages > Squirrel).")
            return None
        try:
            with open(path, "rb") as stream:
                data = stream.read()
        except OSError as error:
            QtWidgets.QMessageBox.warning(self, APP, f"{name}: {error}")
            return None
        if not (assets_module.is_script(name) or assets_module.is_image_file(name) or assets_module.is_archive(data)):
            QtWidgets.QMessageBox.warning(self, APP, f"{name} is not a Level-5 archive, an image or a Squirrel script.")
            return None
        error = self.project.check_replace(asset, name, data)
        if error:
            QtWidgets.QMessageBox.warning(self, APP, error)
            return None
        if asset in self._thumbnail_queue:
            self._thumbnail_queue.remove(asset)
        users = self.project.users_of(asset)
        self.read_archives(project_module.archives_to_read(name, data))
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            new, lost = self.project.replace(asset, name, data, source_path=os.path.abspath(path))
            if new.kind == assets_module.SQUIRREL:
                self.project.check_script(new)
            if new.error and new.kind == assets_module.IMAGE:
                QtWidgets.QMessageBox.warning(self, APP, new.error)
        except Exception as error:
            traceback.print_exc()
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, APP, f"{asset.name} could not be replaced: {error}")
            return None
        QtWidgets.QApplication.restoreOverrideCursor()
        self._queue_models(new)
        for custom in users:
            if custom in self.project.items:
                self.queue_thumbnail(custom)
        self.gl.release_scene()
        self.assets_panel.selected = new
        self.scene_changed()
        self.show_toast(f"<b style='color:{ACCENT}'>{asset.name}</b> replaced by <b style='color:{ACCENT}'>{new.name}</b>")
        if lost:
            QtWidgets.QMessageBox.information(self, APP, "These links could not be kept:\n\n" + "\n".join(lost))
        return new

    def rename_asset(self, asset, name):
        try:
            self.project.rename(asset, name)
        except ProjectError as error:
            QtWidgets.QMessageBox.warning(self, APP, str(error))
        self.scene_changed()

    def set_reference(self, asset):
        self.project.set_reference(asset)
        self.scene_changed()

    def toggle_visible(self, asset):
        self.project.set_visible(asset, not asset.visible)
        self.timeline.canvas.update()
        self.gl.update()

    def unbind(self, source):
        self.project.unbind(source)
        self.scene_changed()

    def set_swapped(self, swapped):
        """The swap switch of a template: its slots take the other archives of the .pck, linked at once."""
        linked = self.project.set_swapped(swapped)
        self.scene_changed()
        self.update_title()
        if linked:
            self.show_toast(", ".join(f"<b style='color:{ACCENT}'>{target.name}</b> {source.stem if source else '(none)'}"
                                      for target, source in linked))

    def set_animation(self, target, source):
        if self.project.set_animation(target, source):
            if source is not None:
                self.show_toast(f"<b style='color:{ACCENT}'>{source.name}</b> animates <b style='color:{ACCENT}'>{target.name}</b>")
            else:
                self.show_toast(f"<b style='color:{ACCENT}'>{target.name}</b> has no animation")
        self.scene_changed()

    def group_selection(self):
        """Ctrl+G: the selected models become one custom object, named by the user."""
        project = self.project
        members = [a for a in self.assets_panel.selection if project.groupable(a)]
        order = [a for a in project.items if a in members]
        if len(order) < 2:
            self.show_toast("Select at least two models of the top level (Ctrl+click) to group them")
            return
        suggested = order[0].stem
        taken = {a.name.lower() for a in project.items}
        name = assets_module.unique_name(suggested, taken)
        name, ok = QtWidgets.QInputDialog.getText(self, "Custom object", "Name of the object:", text=name)
        if not ok or not name.strip():
            return
        try:
            custom = project.create_custom(name, order)
        except ProjectError as error:
            QtWidgets.QMessageBox.warning(self, APP, str(error))
            return
        self.gl.release_scene()
        self.queue_thumbnail(custom)
        self.assets_panel.selected = custom
        self.scene_changed()
        self.show_toast(f"<b style='color:{ACCENT}'>{custom.name}</b> created: drop an animation on it")

    def convert_script(self, asset, compile_):
        if compile_:
            try:
                squirrel.compile(asset.data, asset.name)
            except squirrel.SquirrelUnavailable:
                pass
            except squirrel.SquirrelError as error:
                QtWidgets.QMessageBox.warning(self, APP, f"{asset.name} could not be compiled:\n\n{error}")
                return
        name = asset.stem + (".nutb" if compile_ else ".nut")
        # The asset keeps the source text either way, a .nutb is compiled again when it is written
        data = asset.data
        existing = next((a for a in self.project.items if a.name.lower() == name.lower()), None)
        allowed, rule = self.project.import_rule(name)
        if rule is not None and existing is None:
            # A template holds one script: the converted one takes its place
            existing = self.project.holder(rule)
        elif not allowed:
            QtWidgets.QMessageBox.warning(self, APP, f"{name} cannot be imported in this project.")
            return
        if existing is asset:
            added, _ = self.project.replace(existing, name, data)
        elif existing is not None:
            answer = QtWidgets.QMessageBox.question(self, APP, f"{name} already exists. Replace it?",
                                                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
            if answer != QtWidgets.QMessageBox.Yes:
                return
            added, _ = self.project.replace(existing, name, data)
        else:
            added = self.project.add(name, data)
        self.project.check_script(added)
        self.assets_panel.selected = added
        self.scene_changed()
        self.show_toast(f"<b style='color:{ACCENT}'>{name}</b> added to the assets")

    # Layers: pictures of the timeline drawn over the player

    def layers_changed(self, timeline=True):
        """A layer moved, changed size, duration or visibility: the frames, the player and the timeline follow."""
        self.project.touch()
        last = max(self.project.scene.frame_count, self.project.layers_end(), 0)
        if last != self.last_frame:
            self.last_frame = last
            self.timeline.update_range()
            transport = self.player.transport
            transport.frame_label.setText(f"frame {self.frame} / {self.last_frame} · 30 fps")
        if timeline:
            self.timeline.rebuild()
        else:
            self.timeline.canvas.update()
        self.player.stage.editor.update()
        self.gl.update()
        self.update_title()

    def select_layer(self, layer):
        if layer is not self.selected_layer:
            self.selected_layer = layer
            self.player.stage.editor.update()
            self.timeline.canvas.update()

    def add_layer(self, asset, start=None):
        """Puts a picture in the timeline at a frame (the current one by default); one already there moves to it."""
        project = self.project
        start = self.frame if start is None else start
        layer = project.layer_of(asset)
        if layer is None:
            layer = project.add_layer(asset, start)
            self.show_toast(f"<b style='color:{ACCENT}'>{asset.name}</b> added to the timeline")
        else:
            layer.start = max(0, int(start))
        self.selected_layer = layer
        if not self.timeline.isVisible():
            self.timeline_action.setChecked(True)
            self._toggle_timeline()
        self.layers_changed()
        self.assets_panel.refresh()

    def remove_layer(self, layer):
        if layer is None:
            return
        self.project.remove_layer(layer)
        if self.selected_layer is layer:
            self.selected_layer = None
        self.layers_changed()
        self.assets_panel.refresh()
        self.show_toast(f"<b style='color:{ACCENT}'>{layer.asset.name}</b> removed from the timeline")

    def toggle_layer_visible(self, layer):
        layer.visible = not layer.visible
        self.layers_changed(timeline=False)

    def move_layer(self, layer, step):
        self.project.move_layer(layer, step)
        self.layers_changed()

    def fit_layer(self, layer):
        layer.fit()
        self.layers_changed(timeline=False)

    def layer_menu(self, layer, position):
        """Right click on a picture in the timeline or in the player."""
        menu = QtWidgets.QMenu(self)
        visible = menu.addAction("Visible", lambda: self.toggle_layer_visible(layer))
        visible.setCheckable(True)
        visible.setChecked(layer.visible)
        if layer.asset.kind == assets_module.TEXT:
            menu.addAction("Edit text…", lambda: self.edit_text(layer.asset))
        menu.addSeparator()
        index = self.project.layers.index(layer)
        forward = menu.addAction("Bring forward", lambda: self.move_layer(layer, 1))
        forward.setEnabled(index < len(self.project.layers) - 1)
        backward = menu.addAction("Send backward", lambda: self.move_layer(layer, -1))
        backward.setEnabled(index > 0)
        menu.addAction("Native size, centred", lambda: self.fit_layer(layer))
        menu.addSeparator()
        menu.addAction("Remove from the timeline\tDel", lambda: self.remove_layer(layer))
        menu.exec(position)

    # Texts

    def add_text_dialog(self):
        dialog = text_dialog.TextDialog(self, title="Add text")
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        try:
            asset = self.project.add_text(dialog.spec())
        except Exception as error:
            traceback.print_exc()
            QtWidgets.QMessageBox.warning(self, APP, f"The text could not be made: {error}")
            return
        self.assets_panel.selected = asset
        self.scene_changed()
        self.show_toast(f"<b style='color:{ACCENT}'>{asset.name}</b> added: drag it into the timeline")

    def edit_text(self, asset):
        """The window of a text asset: OK draws it again with its new text, font and color."""
        dialog = text_dialog.TextDialog(self, asset.text, title=f"Edit text · {asset.name}")
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return None
        spec = dialog.spec()
        if spec != asset.text:
            self.project.set_text(asset, spec)
            self.layers_changed()
            self.assets_panel.refresh()
        return asset

    # Drag and drop of files

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        event.acceptProposedAction()
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        projects = [p for p in paths if p.lower().endswith(project_module.EXTENSION)]
        if projects:
            self.open_project_checked(projects[0])
            return
        if paths:
            self.import_files(paths)

    # Projects

    def confirm_discard(self):
        """True when the changes can go: nothing to lose, saved, or the user does not want them."""
        project = self.project
        if not project.dirty or (project.path is None and not project.items):
            return True
        answer = QtWidgets.QMessageBox.question(
            self, APP, f"Save the changes of {project.name}?",
            QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Save)
        if answer == QtWidgets.QMessageBox.Save:
            return self.write_project()
        return answer == QtWidgets.QMessageBox.Discard

    def new_project(self):
        """Empty project: no asset, nothing rendered."""
        if not self.confirm_discard():
            return
        self._set_project(Project(self.project.engine_id))

    def _set_project(self, project):
        if self.playing:
            self.toggle_play()
        self._thumbnail_queue.clear()
        self.gl.release_scene()
        self.project = project
        self.selected_layer = None
        project.success = self.success
        self._show_engine(project.engine_id)
        self.assets_panel.selected = None
        self.frame = 0
        self._camera_placed = bool(project.scene.cameras)
        self.reset_free_camera(update=False)
        self.set_game_camera(True)
        self.scene_changed()

    def open_project_dialog(self):
        if not self.confirm_discard():
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open project", self._last_folder,
                                                        "Studio Render Project (*.srp)")
        if path:
            self._last_folder = os.path.dirname(path)
            self.open_project(path)

    def open_project_checked(self, path):
        if self.confirm_discard():
            self.open_project(path)

    def open_project(self, path):
        # The engine first: a project is not read with an engine that is not installed (no default game)
        try:
            engine_id = Project.read_json(path).get("engine") or "IE4"
        except ProjectError as error:
            QtWidgets.QMessageBox.warning(self, APP, str(error))
            return False
        state = eleven.engine_state(engine_id)
        if state != "ready" and not (state == "not installed" and self._ask_install(engine_id)):
            QtWidgets.QMessageBox.warning(
                self, APP, f"{os.path.basename(path)} is not opened: its engine {engine_id} is not installed"
                + (" and its zip is missing." if state == "missing" else "."))
            return False
        self.read_archives(project_module.project_archives(path))
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            project = Project.load(path)
        except ProjectVersionError as error:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, APP, str(error))
            return False
        except Exception as error:
            traceback.print_exc()
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, APP, f"{os.path.basename(path)} could not be opened: {error}")
            return False
        QtWidgets.QApplication.restoreOverrideCursor()
        self._set_project(project)
        for asset in project.walk():
            if asset.kind == assets_module.MODEL and asset.model is not None and not asset.thumbnail:
                self.queue_thumbnail(asset)
        return True

    def export_asset(self, asset):
        """Export… of the asset menu: a save dialog with the formats of the asset (export.asset_formats)."""
        formats = export_module.asset_formats(asset)
        if not formats:
            return
        filters = export_module.file_filter(formats).split(";;")
        folder = self._last_export_folder or self._last_folder
        suggested = os.path.join(folder, export_module.file_name(asset.stem, "export") + formats[0])
        path, chosen = QtWidgets.QFileDialog.getSaveFileName(self, f"Export {asset.name}", suggested, ";;".join(filters))
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += formats[filters.index(chosen)] if chosen in filters else formats[0]
        self._last_export_folder = os.path.dirname(path)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            written = export_module.export_asset(asset, path)
        except export_module.ExportError as error:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, APP, f"{asset.name} was not exported: {error}.")
            return
        except Exception as error:
            traceback.print_exc()
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, APP, f"{asset.name} could not be exported: {error}")
            return
        QtWidgets.QApplication.restoreOverrideCursor()
        others = sum(1 for p in written[1:] if p.lower().endswith(".png"))
        noun = "image" if path.lower().endswith(".png") else "texture"
        extra = f" (+ {others} {noun}{'s' if others > 1 else ''})" if others else ""
        self.show_toast(f"<b style='color:{ACCENT}'>{asset.name}</b> exported to "
                        f"<b style='color:{ACCENT}'>{os.path.basename(path)}</b>{extra}")

    def write_project_as(self):
        return self.write_project(ask=True)

    def write_project(self, ask=False):
        path = self.project.path
        if not path or ask:
            stem = self.project.reference.stem if self.project.reference else "project"
            if self.project.template is not None:
                stem = self.project.template.name
            suggested = os.path.join(self._last_folder, stem + ".srp")
            if self.project.path:
                suggested = self.project.path
            path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save project", suggested, "Studio Render Project (*.srp)")
            if not path:
                return False
            if not path.lower().endswith(project_module.EXTENSION):
                path += project_module.EXTENSION
            self._last_folder = os.path.dirname(path)
        self.flush_thumbnails()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            self.project.save(path)
        except Exception as error:
            traceback.print_exc()
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, APP, f"{os.path.basename(path)} could not be saved: {error}")
            return False
        QtWidgets.QApplication.restoreOverrideCursor()
        self.update_title()
        self.show_toast(f"<b style='color:{ACCENT}'>{os.path.basename(path)}</b> saved")
        return True

    def closeEvent(self, event):
        if not self.confirm_discard():
            event.ignore()
            return
        self.timer.stop()
        model_module.shutdown_pool()
        event.accept()

    # Screenshot and export

    def screenshot(self):
        image = self.gl.grabFramebuffer()
        if self.preferences.screenshot == SCREENSHOT_CLIPBOARD:
            QtWidgets.QApplication.clipboard().setImage(image)
            self.show_toast(f"Frame <b style='color:{ACCENT}'>{self.frame}</b> copied to the clipboard")
            return
        suggested = os.path.join(self._last_folder, f"{self.project.name}_{self.frame:03d}.png")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save screenshot", suggested, "PNG (*.png)")
        if path:
            self._last_folder = os.path.dirname(path)
            if not image.save(path):
                QtWidgets.QMessageBox.warning(self, APP, f"{path} could not be written.")
            else:
                self.show_toast(f"<b style='color:{ACCENT}'>{os.path.basename(path)}</b> saved")

    def export_mp4(self):
        from video import Mp4Writer
        suggested = os.path.join(self._last_folder, f"{self.project.name}.mp4")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export .mp4", suggested, "MPEG-4 video (*.mp4)")
        if not path:
            return
        if not path.lower().endswith(".mp4"):
            path += ".mp4"
        self._last_folder = os.path.dirname(path)
        if self.playing:
            self.toggle_play()

        width, height = EXPORT_SIZE
        last = self.last_frame
        progress = QtWidgets.QProgressDialog("Rendering the video…", "Cancel", 0, last + 1, self)
        progress.setWindowTitle(APP)
        progress.setWindowModality(QtCore.Qt.WindowModal)
        progress.setMinimumDuration(0)
        offscreen = self.offscreen()
        renderer = offscreen.renderer
        renderer.draw_outlines, renderer.fragment_lighting = self.gl.draw_outlines, self.gl.fragment_lighting
        free = View()
        free.__dict__.update({k: (v.copy() if isinstance(v, np.ndarray) else v) for k, v in self.gl.view.__dict__.items()})
        scene = self.project.scene
        cancelled = False
        writer = None
        try:
            writer = Mp4Writer(path, width, height, FPS)
            for frame in range(last + 1):
                if progress.wasCanceled():
                    cancelled = True
                    break
                pose = scene.camera_at(frame) if self.gl.game_camera else None
                free.width, free.height = width, height
                if pose is not None:
                    free.update_game(*pose)
                else:
                    free.update_orbit()
                writer.append(offscreen.render(scene, free, frame, width, height,
                                                    layers=self.project.layers_at(frame)))
                progress.setValue(frame + 1)
                QtWidgets.QApplication.processEvents()
        except Exception as error:
            traceback.print_exc()
            cancelled = True
            QtWidgets.QMessageBox.warning(self, APP, f"The video could not be written: {error}")
        finally:
            if writer is not None:
                writer.close()
            offscreen.release_scene()
            progress.close()
        if cancelled:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            return
        self.show_toast(f"<b style='color:{ACCENT}'>{os.path.basename(path)}</b> exported")


def _palette():
    palette = QtGui.QPalette()
    colors = {
        QtGui.QPalette.Window: BG, QtGui.QPalette.WindowText: FG, QtGui.QPalette.Base: BG,
        QtGui.QPalette.AlternateBase: PANEL_2, QtGui.QPalette.Text: FG, QtGui.QPalette.Button: PANEL_2,
        QtGui.QPalette.ButtonText: FG, QtGui.QPalette.Highlight: ACCENT, QtGui.QPalette.HighlightedText: "#002033",
        QtGui.QPalette.ToolTipBase: PANEL_2, QtGui.QPalette.ToolTipText: FG, QtGui.QPalette.PlaceholderText: FAINT,
        QtGui.QPalette.Mid: LINE, QtGui.QPalette.Midlight: RAISED, QtGui.QPalette.Dark: PANEL, QtGui.QPalette.Light: RAISED,
        QtGui.QPalette.Link: ACCENT,
    }
    for role, color in colors.items():
        palette.setColor(role, QtGui.QColor(color))
    for role in (QtGui.QPalette.WindowText, QtGui.QPalette.Text, QtGui.QPalette.ButtonText):
        palette.setColor(QtGui.QPalette.Disabled, role, QtGui.QColor(FAINT))
    return palette


def run(args):
    import faulthandler
    # A crash in the GPU driver or in moderngl still prints where it happened
    faulthandler.enable()
    surface = QtGui.QSurfaceFormat()
    surface.setVersion(3, 3)
    surface.setProfile(QtGui.QSurfaceFormat.CoreProfile)
    surface.setDepthBufferSize(24)
    surface.setSamples(4)
    QtGui.QSurfaceFormat.setDefaultFormat(surface)

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP)
    app.setApplicationVersion(VERSION)
    try:
        app.styleHints().setColorScheme(QtCore.Qt.ColorScheme.Dark)
    except AttributeError:
        pass
    font = QtGui.QFont("Segoe UI")
    font.setPointSizeF(9.5)
    app.setFont(font)
    app.setPalette(_palette())
    app.setStyleSheet(STYLE)
    window = MainWindow(args)
    window.show()
    return app.exec()
