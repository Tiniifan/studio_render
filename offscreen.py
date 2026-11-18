"""Rendering without a window (thumbnails of the assets, .mp4 export) with the renderer of the viewer."""
import io
import math

import moderngl
import numpy as np
from PIL import Image

from overlays import OverlayRenderer
from renderer import Renderer
from scene import Entry, Scene, View

THUMBNAIL_SIZE = (250, 150)
THUMBNAIL_BACKGROUND = (0x1c / 255.0, 0x1c / 255.0, 0x1c / 255.0)
THUMBNAIL_SAMPLES = 24


class Offscreen:
    """A standalone OpenGL context and its renderer, made current only while it draws."""

    def __init__(self):
        self.ctx = moderngl.create_standalone_context()
        self.renderer = Renderer(self.ctx)
        self.overlays = OverlayRenderer(self.ctx)
        self._targets = {}

    def _target(self, width, height):
        key = (width, height)
        if key not in self._targets:
            samples = min(4, self.ctx.max_samples)
            msaa = self.ctx.framebuffer(
                color_attachments=[self.ctx.renderbuffer((width, height), 4, samples=samples)],
                depth_attachment=self.ctx.depth_renderbuffer((width, height), samples=samples))
            plain = self.ctx.framebuffer(color_attachments=[self.ctx.renderbuffer((width, height), 4)])
            self._targets[key] = (msaa, plain)
        return self._targets[key]

    def render(self, scene, view, frame, width, height, background=None, outlines=None, lighting=None, layers=()):
        """(height, width, 3) uint8 image, top row first; the layers (pictures of the timeline) are drawn over it."""
        with self.ctx:
            msaa, plain = self._target(width, height)
            view.width, view.height = width, height
            renderer = self.renderer
            previous = renderer.background, renderer.draw_outlines, renderer.fragment_lighting
            if background is not None:
                renderer.background = background
            if outlines is not None:
                renderer.draw_outlines = outlines
            if lighting is not None:
                renderer.fragment_lighting = lighting
            try:
                renderer.render(msaa, scene, view, float(frame))
                self.overlays.draw(msaa, layers)
            finally:
                renderer.background, renderer.draw_outlines, renderer.fragment_lighting = previous
            self.ctx.copy_framebuffer(plain, msaa)
            pixels = np.frombuffer(plain.read(components=3), dtype=np.uint8).reshape(height, width, 3)
            return pixels[::-1].copy()

    def release_scene(self):
        with self.ctx:
            self.renderer.release_scene()
            self.overlays.release_textures()

    def release(self):
        with self.ctx:
            self.renderer.release_scene()
            self.overlays.release_textures()
            for msaa, plain in self._targets.values():
                msaa.release()
                plain.release()
            self._targets.clear()


def _busiest_frame(scene, entries):
    """Frame where the most meshes are drawn: effects are often invisible at frame 0."""
    last = max((entry.frame_count for entry in entries), default=0)
    frames = sorted({int(round(f)) for f in np.linspace(0, last, min(last + 1, THUMBNAIL_SAMPLES))}) if last > 0 else [0]
    best, best_count = 0, -1
    for frame in frames:
        count = sum(len(scene.evaluate(entry, float(frame), None)) for entry in entries)
        if count > best_count:
            best, best_count = frame, count
    return best, best_count


def thumbnail(offscreen, models, engine_id, size=THUMBNAIL_SIZE):
    """PNG bytes of an orbit view framed on one model or several drawn together, None when nothing is drawn."""
    scene = Scene()
    scene.engine_id = engine_id
    for model in models if isinstance(models, (list, tuple)) else [models]:
        for part in model.walk():
            if part.meshes:
                scene.entries.append(Entry(part, engine_id))
    if not scene.entries:
        return None
    if isinstance(models, (list, tuple)) and len(models) > 1:
        # A custom object: its body takes the skin of its head
        for entry in scene.entries:
            entry.skin_group = scene.entries
    frame, count = _busiest_frame(scene, scene.entries)
    if count <= 0:
        return None

    width, height = size[0] * 2, size[1] * 2
    view = View()
    view.width, view.height = width, height
    view.frame_bounds(*scene.bounds(float(frame)))
    view.pitch = math.radians(8.0)
    # The bounding sphere fits the height, a 5:3 image has room for a closer view
    view.zoom(2.5)
    view.update_orbit()
    try:
        pixels = offscreen.render(scene, view, frame, width, height, THUMBNAIL_BACKGROUND)
    finally:
        offscreen.release_scene()
    image = Image.fromarray(pixels).resize(size, Image.LANCZOS)
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()
