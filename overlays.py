"""Draws the layers of the timeline (pictures) over the 3D render, with moderngl."""
import struct

import moderngl

import images

VERTEX = """
#version 330
in vec2 in_position;
in vec2 in_uv;
out vec2 uv;
void main() {
    gl_Position = vec4(in_position, 0.0, 1.0);
    uv = in_uv;
}
"""

FRAGMENT = """
#version 330
uniform sampler2D picture;
in vec2 uv;
out vec4 color;
void main() {
    color = texture(picture, uv);
}
"""


class OverlayRenderer:
    """One per OpenGL context: the textures of the pictures are kept until their pixels change."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.program = ctx.program(vertex_shader=VERTEX, fragment_shader=FRAGMENT)
        self.buffer = ctx.buffer(reserve=16 * 4)
        self.vao = ctx.vertex_array(self.program, [(self.buffer, "2f 2f", "in_position", "in_uv")])
        self.textures = {}      # asset uid: (pixels, texture)

    def texture(self, asset):
        pixels = asset.image
        cached = self.textures.get(asset.uid)
        if cached is not None and cached[0] is pixels:
            return cached[1]
        if cached is not None:
            cached[1].release()
        height, width = pixels.shape[:2]
        # Row 0 of the pixels is the top of the picture: v = 0 at the top
        texture = self.ctx.texture((width, height), 4, pixels.tobytes())
        texture.filter = (moderngl.LINEAR, moderngl.LINEAR)
        texture.repeat_x = texture.repeat_y = False
        self.textures[asset.uid] = (pixels, texture)
        return texture

    def draw(self, fbo, layers):
        if not layers:
            return
        ctx = self.ctx
        fbo.use()
        ctx.disable(moderngl.DEPTH_TEST | moderngl.CULL_FACE)
        ctx.enable(moderngl.BLEND)
        # The alpha of the framebuffer stays opaque: screenshots and videos are not see-through
        ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA, moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA)
        ctx.blend_equation = moderngl.FUNC_ADD
        screen_width, screen_height = images.SCREEN
        for layer in layers:
            left = layer.x / screen_width * 2.0 - 1.0
            right = (layer.x + layer.width) / screen_width * 2.0 - 1.0
            top = 1.0 - layer.y / screen_height * 2.0
            bottom = 1.0 - (layer.y + layer.height) / screen_height * 2.0
            self.buffer.write(struct.pack("16f", left, top, 0.0, 0.0, right, top, 1.0, 0.0,
                                          left, bottom, 0.0, 1.0, right, bottom, 1.0, 1.0))
            self.texture(layer.asset).use(0)
            self.program["picture"].value = 0
            self.vao.render(moderngl.TRIANGLE_STRIP)
        ctx.disable(moderngl.BLEND)
        ctx.enable(moderngl.DEPTH_TEST)

    def release_textures(self):
        for _, texture in self.textures.values():
            texture.release()
        self.textures.clear()
