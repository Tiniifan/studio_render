"""Windows opened by a double click on an asset: a player of that asset alone, an editor of a Squirrel script, a
viewer of an image (a text opens its own window, text_dialog)."""
import math
import re

from PySide6 import QtCore, QtGui, QtWidgets

import assets as assets_module
import offscreen
import squirrel
from scene import Entry, Scene

FPS = 30


def renderable(asset):
    if asset.kind == assets_module.CUSTOM:
        return bool(asset.members)
    return asset.model is not None and any(part.meshes for part in asset.model.walk())


class RenderView(QtWidgets.QWidget):
    """The 3D view of the asset window, drawn by the offscreen context of the main window.

    A QOpenGLWidget per dialog would create and destroy an OpenGL context each time, which moderngl does not survive."""

    def __init__(self, player):
        super().__init__()
        from scene import View
        self.player = player
        self.view = View()
        self.outlines = True
        self.lighting = True
        self._last = None
        self.setMinimumSize(100, 60)
        self.setFocusPolicy(QtCore.Qt.ClickFocus)

    def paintEvent(self, event):
        ratio = self.devicePixelRatioF()
        width, height = max(int(self.width() * ratio), 2), max(int(self.height() * ratio), 2)
        painter = QtGui.QPainter(self)
        try:
            self.view.width, self.view.height = width, height
            self.view.update_orbit()
            pixels = self.player.offscreen.render(self.player.scene, self.view, self.player.frame, width, height,
                                                  outlines=self.outlines, lighting=self.lighting)
            image = QtGui.QImage(pixels.data, width, height, width * 3, QtGui.QImage.Format_RGB888)
            image.setDevicePixelRatio(ratio)
            painter.drawImage(0, 0, image)
        except Exception as error:
            painter.fillRect(self.rect(), QtGui.QColor("#525761"))
            painter.setPen(QtGui.QColor("#ffffff"))
            painter.drawText(self.rect(), QtCore.Qt.AlignCenter, f"Render failed: {error}")

    def mousePressEvent(self, event):
        if event.button() in (QtCore.Qt.LeftButton, QtCore.Qt.RightButton):
            self._last = event.position()

    def mouseMoveEvent(self, event):
        if self._last is None:
            return
        delta = event.position() - self._last
        self._last = event.position()
        if event.buttons() & QtCore.Qt.LeftButton:
            self.view.orbit(delta.x(), delta.y())
        elif event.buttons() & QtCore.Qt.RightButton:
            self.view.update_orbit()
            self.view.pan(delta.x(), delta.y())
        self.update()

    def mouseReleaseEvent(self, event):
        self._last = None

    def wheelEvent(self, event):
        self.view.zoom(event.angleDelta().y() / 120.0)
        self.update()


class Screen(QtWidgets.QWidget):
    """Keeps the view at the 5:3 ratio of the 3DS screen."""

    def __init__(self, view):
        super().__init__()
        self.view = view
        view.setParent(self)

    def resizeEvent(self, event):
        width, height = max(self.width() - 28, 10), max(self.height() - 28, 6)
        width = min(width, int(height * 5 / 3))
        height = int(width * 3 / 5)
        self.view.setGeometry((self.width() - width) // 2, (self.height() - height) // 2, width, height)


class AssetPlayer(QtWidgets.QDialog):
    """Renders one asset (or custom object) alone with its animations, around a free camera."""

    def __init__(self, window, asset):
        super().__init__(window)
        import viewer
        self.setWindowTitle(f"{asset.name} - studio_render")
        self.resize(960, 680)
        self.asset = asset
        self.offscreen = window.offscreen()
        self.frame = 0
        self.playing = False
        self.scene = Scene()
        self.scene.engine_id = window.project.engine_id
        project = window.project
        animation = project.animation_of(asset) if project.animatable(asset) else None
        sources = [e for e in project.scene.owned(animation) if e.own_animations] if animation is not None else []
        members = asset.members if asset.kind == assets_module.CUSTOM else [asset]
        for member in members:
            for part in member.model.walk():
                if part.meshes or part.bones or part.animations:
                    entry = Entry(part, self.scene.engine_id)
                    entry.bound = list(sources)
                    self.scene.entries.append(entry)
        if asset.kind == assets_module.CUSTOM:
            for entry in self.scene.entries:
                entry.skin_group = self.scene.entries
        self.last_frame = max((a.frame_count for entry in self.scene.entries for a in entry.animations), default=0)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        head = QtWidgets.QWidget(objectName="panelHead")
        head.setAttribute(QtCore.Qt.WA_StyledBackground)
        row = QtWidgets.QHBoxLayout(head)
        row.setContentsMargins(12, 6, 12, 6)
        row.setSpacing(10)
        title = QtWidgets.QLabel(asset.name + (f"  ▶ {animation.name}" if animation is not None else ""))
        title.setFont(viewer.mono_font(12))
        row.addWidget(title)
        row.addStretch(1)
        self.follow = QtWidgets.QCheckBox("Follow")
        self.follow.setToolTip("Keep the camera on the object while it moves")
        self.follow.setChecked(True)
        self.follow.setVisible(bool(sources))
        self.follow.setFocusPolicy(QtCore.Qt.NoFocus)
        self.follow.toggled.connect(lambda on: self.seek(self.frame))
        row.addWidget(self.follow)
        self.outlines = QtWidgets.QCheckBox("Outlines")
        self.lighting = QtWidgets.QCheckBox("Fragment lighting")
        for box in (self.outlines, self.lighting):
            box.setChecked(True)
            box.setFocusPolicy(QtCore.Qt.NoFocus)
            box.toggled.connect(self._options)
            row.addWidget(box)
        frame_button = QtWidgets.QPushButton("Frame all", objectName="importButton")
        frame_button.setFocusPolicy(QtCore.Qt.NoFocus)
        frame_button.clicked.connect(self.frame_all)
        row.addWidget(frame_button)
        layout.addWidget(head)

        self.view = RenderView(self)
        layout.addWidget(Screen(self.view), 1)

        bar = QtWidgets.QWidget(objectName="transport")
        bar.setAttribute(QtCore.Qt.WA_StyledBackground)
        controls = QtWidgets.QHBoxLayout(bar)
        controls.setContentsMargins(14, 8, 14, 10)
        controls.setSpacing(10)
        self.play = QtWidgets.QToolButton(objectName="play")
        self.play.setFixedSize(38, 38)
        self.play.setIconSize(QtCore.QSize(15, 15))
        self.play.setFocusPolicy(QtCore.Qt.NoFocus)
        self.play.clicked.connect(self.toggle_play)
        controls.addWidget(self.play)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(0, self.last_frame)
        self.slider.setEnabled(self.last_frame > 0)
        self.slider.valueChanged.connect(self.seek)
        controls.addWidget(self.slider, 1)
        self.label = QtWidgets.QLabel()
        self.label.setFont(viewer.mono_font(11, QtGui.QFont.Normal))
        self.label.setMinimumWidth(170)
        controls.addWidget(self.label)
        layout.addWidget(bar)
        self._icons = (viewer.icon("play", "#002033", 15), viewer.icon("pause", "#002033", 15))
        self.play.setIcon(self._icons[0])
        self.play.setEnabled(self.last_frame > 0)

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(int(1000 / FPS))
        self.timer.timeout.connect(self._tick)
        QtGui.QShortcut(QtGui.QKeySequence("Space"), self, self.toggle_play)
        QtGui.QShortcut(QtGui.QKeySequence("F"), self, self.frame_all)

        drawn = [e for e in self.scene.entries if e.has_meshes]
        self.animated = bool(sources)
        self.frame = offscreen._busiest_frame(self.scene, drawn)[0]
        self.frame_all()
        self.slider.setValue(self.frame)
        self._update_label()

    def frame_all(self):
        low, high = self.scene.bounds(float(self.frame), [e for e in self.scene.entries if e.has_meshes])
        view = self.view.view
        view.frame_bounds(low, high)
        view.yaw, view.pitch, view.roll = 0.0, math.radians(8.0), 0.0
        self.view.update()

    def _options(self):
        self.view.outlines = self.outlines.isChecked()
        self.view.lighting = self.lighting.isChecked()
        self.view.update()

    def seek(self, frame):
        self.frame = int(frame)
        if self.animated and self.follow.isChecked():
            low, high = self.scene.bounds(float(self.frame), [e for e in self.scene.entries if e.has_meshes])
            self.view.view.target = (low + high) * 0.5
        if self.slider.value() != self.frame:
            self.slider.blockSignals(True)
            self.slider.setValue(self.frame)
            self.slider.blockSignals(False)
        self._update_label()
        self.view.update()

    def _update_label(self):
        self.label.setText(f"frame {self.frame} / {self.last_frame} · 30 fps")

    def toggle_play(self):
        if self.last_frame <= 0:
            return
        self.playing = not self.playing
        self.play.setIcon(self._icons[1 if self.playing else 0])
        self.timer.start() if self.playing else self.timer.stop()

    def _tick(self):
        self.slider.setValue((self.frame + 1) % (self.last_frame + 1))

    def done(self, result):
        self.timer.stop()
        self.offscreen.release_scene()
        super().done(result)


class SquirrelHighlighter(QtGui.QSyntaxHighlighter):
    KEYWORDS = ("base break case catch class clone const constructor continue default delete else enum extends false "
                "for foreach function if in instanceof local null resume return static switch this throw true try "
                "typeof while yield").split()

    def __init__(self, document):
        super().__init__(document)

        def style(color, bold=False, italic=False):
            text_format = QtGui.QTextCharFormat()
            text_format.setForeground(QtGui.QColor(color))
            if bold:
                text_format.setFontWeight(QtGui.QFont.DemiBold)
            text_format.setFontItalic(italic)
            return text_format

        self.rules = [
            (re.compile(r"\b(" + "|".join(self.KEYWORDS) + r")\b"), style("#4cc2ff", bold=True)),
            (re.compile(r"\b\d+(\.\d+)?\b"), style("#e0b46c")),
            (re.compile(r"\b[A-Za-z_]\w*(?=\s*\()"), style("#c8a2ff")),
            (re.compile(r'"(\\.|[^"\\])*"' + r"|'(\\.|[^'\\])*'"), style("#9bd88a")),
            (re.compile(r"//[^\n]*|#[^\n]*"), style("#8a8a8a", italic=True)),
        ]
        self.comment = style("#8a8a8a", italic=True)

    def highlightBlock(self, text):
        for pattern, text_format in self.rules:
            for match in pattern.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), text_format)
        # Block comments across lines
        self.setCurrentBlockState(0)
        start = 0 if self.previousBlockState() == 1 else text.find("/*")
        while start >= 0:
            end = text.find("*/", start + (0 if self.previousBlockState() == 1 and start == 0 else 2))
            if end < 0:
                self.setCurrentBlockState(1)
                self.setFormat(start, len(text) - start, self.comment)
                break
            self.setFormat(start, end + 2 - start, self.comment)
            start = text.find("/*", end + 2)


class ScriptEditor(QtWidgets.QDialog):
    """A quick editor of a script. OK compiles it: the source of a .nut, or the compiled .nutb, is written to its file."""

    def __init__(self, window, asset):
        super().__init__(window)
        import viewer
        self.window_ = window
        self.asset = asset
        self.setWindowTitle(f"{asset.name} - studio_render")
        self.resize(900, 680)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        head = QtWidgets.QWidget(objectName="panelHead")
        head.setAttribute(QtCore.Qt.WA_StyledBackground)
        row = QtWidgets.QHBoxLayout(head)
        row.setContentsMargins(12, 6, 12, 6)
        row.setSpacing(8)
        title = QtWidgets.QLabel(asset.name)
        title.setFont(viewer.mono_font(12))
        row.addWidget(title)
        row.addStretch(1)
        target = QtWidgets.QLabel(asset.source_path or "Not linked to a file: the changes stay in the project")
        target.setObjectName("muted")
        target.setToolTip("File written when OK is pressed" if asset.source_path else "")
        row.addWidget(target)
        layout.addWidget(head)

        self.editor = QtWidgets.QPlainTextEdit()
        self.editor.setFont(viewer.mono_font(13, QtGui.QFont.Normal))
        self.editor.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.editor.setTabStopDistance(QtGui.QFontMetricsF(self.editor.font()).horizontalAdvance(" ") * 4)
        self.editor.setStyleSheet(f"QPlainTextEdit {{ background: {viewer.BG}; color: {viewer.FG}; border: 0; "
                                  f"selection-background-color: #264f78; padding: 6px; }}")
        self.highlighter = SquirrelHighlighter(self.editor.document())
        self.compiled = squirrel.is_compiled(asset.data)
        self.editor.setPlainText(self._source())
        self.editor.setReadOnly(self.compiled)
        self.editor.document().setModified(False)
        self.editor.document().modificationChanged.connect(self._title)
        layout.addWidget(self.editor, 1)

        bottom = QtWidgets.QHBoxLayout()
        bottom.setContentsMargins(12, 6, 12, 10)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.status.setFont(viewer.mono_font(11, QtGui.QFont.Normal))
        bottom.addWidget(self.status, 1)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        bottom.addWidget(buttons)
        layout.addLayout(bottom)
        self._show_error(asset.error)

    def _source(self):
        data = self.asset.data
        for encoding in ("utf-8", "shift-jis", "latin-1"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode("latin-1", "replace")

    def _title(self, modified):
        self.setWindowTitle(f"{self.asset.name}{'*' if modified else ''} - studio_render")

    def _show_error(self, error):
        import viewer
        self.status.setStyleSheet(f"color: {viewer.DANGER if error else viewer.MUTED};")
        self.status.setText(error or "")

    def accept(self):
        """Compiles the script: written when it compiles, otherwise back to the code or the changes are dropped."""
        if self.compiled:
            super().accept()
            return
        source = self.editor.toPlainText().encode("utf-8")
        try:
            self.window_.project.write_script(self.asset, source)
        except squirrel.SquirrelUnavailable as error:
            QtWidgets.QMessageBox.warning(self, "studio_render", f"{error}\nThe changes are kept in the project only.")
            self.asset.data = source
            self.window_.project.touch()
        except squirrel.SquirrelError as error:
            self._show_error(str(error))
            box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Warning, "studio_render",
                                        f"{self.asset.name} does not compile:\n\n{error}", parent=self)
            back = box.addButton("Back to the code", QtWidgets.QMessageBox.AcceptRole)
            box.addButton("Discard the changes", QtWidgets.QMessageBox.DestructiveRole)
            box.setDefaultButton(back)
            box.exec()
            if box.clickedButton() is back:
                self._go_to_error(str(error))
                return
            super().reject()
            return
        except OSError as error:
            QtWidgets.QMessageBox.warning(self, "studio_render", f"{self.asset.source_path} could not be written: {error}")
            return
        self.window_.scene_changed()
        super().accept()

    def _go_to_error(self, message):
        match = re.search(r"line = \((\d+)\) column = \((\d+)\)", message)
        if match:
            block = self.editor.document().findBlockByNumber(int(match.group(1)) - 1)
            cursor = QtGui.QTextCursor(block)
            cursor.movePosition(QtGui.QTextCursor.Right, n=max(int(match.group(2)) - 1, 0))
            self.editor.setTextCursor(cursor)
        self.editor.setFocus()

    def reject(self):
        if self.editor.document().isModified():
            answer = QtWidgets.QMessageBox.question(self, "studio_render", f"Discard the changes of {self.asset.name}?",
                                                    QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel,
                                                    QtWidgets.QMessageBox.Cancel)
            if answer != QtWidgets.QMessageBox.Discard:
                return
        super().reject()


class ImageViewer(QtWidgets.QDialog):
    """An image at its size (enlarged without blur when small), on a checkerboard for its transparency."""

    def __init__(self, window, asset):
        super().__init__(window)
        height, width = asset.image.shape[:2]
        self.setWindowTitle(f"{asset.name} · {width}×{height}")
        pixels = asset.image.copy(order="C")
        image = QtGui.QImage(pixels.data, width, height, width * 4, QtGui.QImage.Format_RGBA8888).copy()
        scale = max(1, min(4, 360 // max(width, height, 1)))
        label = QtWidgets.QLabel()
        label.setPixmap(QtGui.QPixmap.fromImage(image).scaled(width * scale, height * scale, QtCore.Qt.KeepAspectRatio,
                                                              QtCore.Qt.FastTransformation))
        label.setAlignment(QtCore.Qt.AlignCenter)
        tile = QtGui.QPixmap(16, 16)
        tile.fill(QtGui.QColor("#3a3a3a"))
        painter = QtGui.QPainter(tile)
        painter.fillRect(0, 0, 8, 8, QtGui.QColor("#2b2b2b"))
        painter.fillRect(8, 8, 8, 8, QtGui.QColor("#2b2b2b"))
        painter.end()
        palette = label.palette()
        palette.setBrush(QtGui.QPalette.Window, QtGui.QBrush(tile))
        label.setPalette(palette)
        label.setAutoFillBackground(True)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(label)
        scroll.setWidgetResizable(True)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)
        screen = self.screen().availableGeometry() if self.screen() else QtCore.QRect(0, 0, 1280, 800)
        self.resize(min(width * scale + 40, screen.width() - 80), min(height * scale + 40, screen.height() - 120))


def open_asset(window, asset):
    """Shows the window of an asset, modal; False when the asset has nothing to show alone."""
    if asset.kind == assets_module.TEXT:
        window.edit_text(asset)
        return True
    if asset.kind == assets_module.SQUIRREL:
        dialog = ScriptEditor(window, asset)
    elif asset.is_picture:
        dialog = ImageViewer(window, asset)
    elif renderable(asset):
        dialog = AssetPlayer(window, asset)
    else:
        return False
    dialog.exec()
    return True


def can_open(asset):
    return asset.kind == assets_module.SQUIRREL or asset.is_picture or renderable(asset)
