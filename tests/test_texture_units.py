"""Texture unit 1 of the expression meshes of an IE5 / IE6 head (eyes, mouth): it holds the colours of the mask of unit 0.
A GO character animation only moves the _texproj0 projections, Chrono Stone and Galaxy ones also move the _texproj1
with the same keys. When no animation moves unit 1, the preview gives it the UV values of unit 0, whatever the engine."""
import os
import sys
import tomllib
import zlib

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import eleven
from model import Mesh, Model
from project import Project
from scene import Entry, Scene

EYES = "DefaultLib.cp_20"


class _UVAnimation:
    """An animation built in memory: {(texproj hash, channel): value}, the same at every frame."""
    kind = "uv"
    frame_count = 30

    def __init__(self, channels):
        self.channels = channels

    def value(self, target_hash, channel, frame):
        found = self.channels.get((target_hash, channel))
        return None if found is None else np.asarray(found, np.float32)


def _head(units):
    mesh = Mesh()
    mesh.name = "eyes"
    mesh.material_name = EYES
    mesh.positions = np.zeros((3, 3), np.float32)
    mesh.normals = np.zeros((3, 3), np.float32)
    mesh.triangles = np.array([[0, 1, 2]], np.uint32)
    model = Model("cp0008a")
    model.meshes = [mesh]
    model.texprojs = [(zlib.crc32(f"{EYES}_texproj{unit}".encode()), EYES, unit) for unit in units]
    return model


def _rows(units, channels):
    scene = Scene()
    entry = Entry(_head(units), "IE4")
    entry.own_animations = [_UVAnimation(channels)]
    scene.entries.append(entry)
    (item,) = scene.evaluate(entry, 10.0, None)
    return [np.asarray(rows) for rows in item.uv_rows]


def _hash(unit):
    return zlib.crc32(f"{EYES}_texproj{unit}".encode())


def test_unit_1_without_track_takes_the_values_of_unit_0():
    rows = _rows((0, 1), {(_hash(0), "move"): (0.0, -0.125)})
    assert np.allclose(rows[1], rows[0])
    assert not np.allclose(rows[0], _rows((0, 1), {})[0])


def test_unit_1_with_its_own_track_keeps_its_values():
    rows = _rows((0, 1), {(_hash(0), "move"): (0.0, -0.125), (_hash(1), "move"): (0.25, 0.0)})
    alone = _rows((0, 1), {(_hash(1), "move"): (0.25, 0.0)})
    assert np.allclose(rows[1], alone[1])
    assert not np.allclose(rows[1], rows[0])


def test_unit_1_own_scale_track_counts_as_animated():
    rows = _rows((0, 1), {(_hash(0), "move"): (0.0, -0.125), (_hash(1), "scale"): (1.0, 1.0)})
    assert np.allclose(rows[1], _rows((0, 1), {})[1])


def test_head_without_unit_1_is_unchanged():
    rows = _rows((0,), {(_hash(0), "move"): (0.0, -0.125)})
    assert np.allclose(rows[1], _rows((0,), {})[1])
    assert np.allclose(rows[2], _rows((0,), {})[2])


# With the game's archives (tests/fixtures.local.toml): head_ie4 (cp0008a.xc of GO), head_ie5 (an IE5 head, e.g.
# cp0008a.xc of a Custom Miximax resources/waza folder), package_alt (whs0001.pck of GO), package_cs (whs0001.pck of
# Chrono Stone), package_galaxy (whs0001.pck of Galaxy)

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


def _engine(engine_id):
    for part in eleven.engine_parts(engine_id):
        state = eleven.engine_state(part)
        if state == "missing":
            pytest.skip(f"engine {part} is missing")
        if state != "ready":
            eleven.install_game(part)
    return engine_id


def _driven_head(engine_id, head, package):
    project = Project(_engine(engine_id))
    for path in (head, package):
        with open(path, "rb") as stream:
            project.add(os.path.basename(path), stream.read())
    target = project.find(os.path.basename(head))
    project.set_animation(target, project.find("whs0001_aa1.xc"))
    (entry,) = [entry for entry in project.scene.entries if entry.owner is target]
    return project, entry


def _eyes(scene, entry, frame):
    return next(item for item in scene.evaluate(entry, float(frame), None) if item.mesh.material_name == EYES)


ENGINES = ("IE4", "IE4+IE5", "IE5", "IE6")


@pytest.mark.parametrize("engine_id", ENGINES)
def test_go_motion_moves_unit_1_of_an_ie5_head_with_unit_0(engine_id):
    head, package = _files("head_ie5", "package_alt")
    project, entry = _driven_head(engine_id, head, package)
    assert (EYES, 1) in entry.texprojs
    for frame in (5, 10):
        rows = _eyes(project.scene, entry, frame).uv_rows
        assert np.allclose(np.asarray(rows[1]), np.asarray(rows[0]))
    # Eyes closed from frame 9 to 14: UVMove (0, -0.125)
    assert not np.allclose(np.asarray(_eyes(project.scene, entry, 10).uv_rows[1]),
                           np.asarray(_eyes(project.scene, entry, 5).uv_rows[1]))


def test_engine_change_keeps_unit_1_in_step():
    head, package = _files("head_ie5", "package_alt")
    project, entry = _driven_head(_engine("IE4"), head, package)
    for engine_id in ENGINES[1:]:
        project.set_engine(_engine(engine_id))
        rows = _eyes(project.scene, entry, 10).uv_rows
        assert np.allclose(np.asarray(rows[1]), np.asarray(rows[0]))


@pytest.mark.parametrize("engine_id", ENGINES)
def test_ie4_head_has_no_unit_1(engine_id):
    head, package = _files("head_ie4", "package_alt")
    project, entry = _driven_head(engine_id, head, package)
    assert (EYES, 1) not in entry.texprojs
    rows = _eyes(project.scene, entry, 10).uv_rows
    assert np.allclose(np.asarray(rows[1]), np.asarray(rows[2]))


@pytest.mark.parametrize("key, engine_id", [("package_cs", "IE5"), ("package_galaxy", "IE6")])
def test_motion_with_texproj1_tracks_drives_unit_1_itself(key, engine_id):
    head, package = _files("head_ie5", key)
    project, entry = _driven_head(engine_id, head, package)
    texproj = entry.texprojs[(EYES, 1)]
    assert any(anim.value(texproj, "move", 10.0) is not None for anim in entry.animations if anim.kind == "uv")
    rows = _eyes(project.scene, entry, 10).uv_rows
    assert np.allclose(np.asarray(rows[1]), np.asarray(rows[0]))
