"""The combiners get what the PICA200 gives them: the third UV set of the meshes (atr_tx2, read by #FIX_MDL,
#FIX_IMG_T2M1 and the archive programs) and texture samples filtered on the stored 8 bit values, without colour space
conversion. A threshold combiner (BleuRusse's BR_THR_*: alpha = (mask x A + noise - 0.5) x 4 ...) drew a uniform oval
when the noise of unit 2 was read at one point (atr_tx2 left at zero)."""
import os
import sys
import tomllib

import moderngl
import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import eleven
from model import Texture
from renderer import GpuMesh, Renderer
from scene import Scene

AURA = "effect._310_inbody_aura_a3"

FULL_SCREEN = """#version 330 core
void main() {
    vec2 corners[3] = vec2[](vec2(-1.0, -1.0), vec2(3.0, -1.0), vec2(-1.0, 3.0));
    gl_Position = vec4(corners[gl_VertexID], 0.0, 1.0);
}
"""


# With the archives of tests/fixtures.local.toml [files]: threshold_fire, whd0001.pck of Danse ardente converted by
# BleuRusse (whd0001_ef1.xc: three auras drawn by a threshold combiner of the archive, BR_THR_C7306A83).

def _files(*keys):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures.local.toml")
    if not os.path.isfile(path):
        pytest.skip("tests/fixtures.local.toml is missing")
    with open(path, "rb") as stream:
        files = tomllib.load(stream).get("files", {})
    missing = [key for key in keys if not (files.get(key) and os.path.isfile(files[key]))]
    if missing:
        pytest.skip(f"fixtures {', '.join(missing)} are needed")
    return [files[key] for key in keys]


def _aura():
    (path,) = _files("threshold_fire")
    if eleven.engine_state("IE4") == "missing":
        pytest.skip("engine IE4 is missing")
    scene = Scene()
    scene.install_missing = True
    scene.add_file(path, "IE4")
    for entry in scene.entries:
        for mesh in entry.model.meshes:
            if mesh.name == AURA:
                return mesh
    pytest.fail(f"{AURA} not found")


def test_third_uv_set_is_read():
    mesh = _aura()
    uv = mesh.uv[2]
    assert uv is not None and uv.shape == (len(mesh.positions), 2)
    # v = 1 - v of the file, like the other sets
    assert np.allclose(uv.min(axis=0), (-0.486, 0.452), atol=0.01)
    assert np.allclose(uv.max(axis=0), (1.305, 2.146), atol=0.01)
    assert not np.allclose(uv, mesh.uv[1])


def test_third_uv_set_reaches_the_vertex_stage():
    mesh = _aura()
    ctx = moderngl.create_standalone_context()
    try:
        gpu_mesh = GpuMesh(ctx, mesh)
        buffer, layout = gpu_mesh.buffers["atr_tx2"]
        assert layout == "2f"
        assert np.array_equal(np.frombuffer(buffer.read(), np.float32).reshape(-1, 2), mesh.uv[2])
        gpu_mesh.release()
    finally:
        ctx.release()


class _Binding:
    """What Renderer._mesh_options reads of a MaterialBinding."""
    texture_units = (0,)
    fragment_lighting = False

    class state:
        alpha_test = False
        alpha_func = None


def test_bilinear_filter_mixes_the_stored_values():
    # A black and a white texel, sampled half way: the PICA200 gives 0.5 (filtering in linear light gave 0.735)
    rgba = np.array([[[0, 0, 0, 255], [255, 255, 255, 255]]], np.uint8)
    sampler = {"wrap_s": 0, "wrap_t": 0, "mag_filter": 1, "min_filter": 1, "mip_filter": 0}
    shaders = eleven.shaders()
    ctx = moderngl.create_standalone_context()
    try:
        renderer = Renderer(ctx)
        options = renderer._mesh_options(_Binding(), 0)
        fragment = "\n".join([
            "#version 330 core", "out vec4 fragColor;", shaders.sampler_uniforms((0,)), shaders.SAMPLER_GLSL,
            shaders.GAMMA_GLSL,
            "void main() {",
            f"    fragColor = {shaders.texture_fetch(0, 'vec2(0.5, 0.5)', options.gamma_correct)};",
            "}"])
        program = ctx.program(vertex_shader=FULL_SCREEN, fragment_shader=fragment)
        renderer.texture(Texture("ramp", rgba, sampler), False).use(0)
        program["unf_frg_txt_2d_0"].value = 0
        program["unf_frg_smp_0"].value = (0, 0, 1 | (1 << 1), 0)
        fbo = ctx.framebuffer(color_attachments=[ctx.renderbuffer((1, 1), 4)])
        fbo.use()
        ctx.vertex_array(program, []).render(moderngl.TRIANGLES, vertices=3)
        value = np.frombuffer(fbo.read(components=4), np.uint8)
        assert abs(int(value[0]) - 128) <= 1 and value[3] == 255
        renderer.release_scene()
    finally:
        ctx.release()
