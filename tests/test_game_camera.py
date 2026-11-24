"""Far plane of the game cameras: a .cmr2 holds none, Inazuma Eleven GO draws to 6000 units (a space background placed
at 6400 to 8000 is not shown in the game); the other engines keep 10000, the free cameras their own."""
import os
import sys
import tomllib

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import eleven
from scene import Scene, View, game_camera_far


def _clip_depth(view, depth):
    """Normalized device depth of a point straight ahead of the camera."""
    point = view.projection @ view.view @ np.array((0.0, 0.0, -depth, 1.0))
    return point[2] / point[3]


def test_far_plane_per_engine():
    assert game_camera_far("IE4") == 6000.0
    assert game_camera_far("IE4+IE5") == 6000.0
    assert game_camera_far("IE5") == game_camera_far("IE6") == game_camera_far("YW1") == 10000.0
    scene = Scene()
    assert scene.game_camera_far == 6000.0
    scene.engine_id = "IE6"
    assert scene.game_camera_far == 10000.0


def test_game_view_clips_past_the_far_plane():
    view = View()
    view.update_game((0.0, 0.0, 0.0), (0.0, 0.0, -1.0), 0.7854, 0.0, far=Scene().game_camera_far)
    assert _clip_depth(view, 5934.0) < 1.0      # the sky of the stadium
    assert _clip_depth(view, 6400.0) > 1.0      # the space background of a converted technique


# With the archives of tests/fixtures.local.toml [files]: space_galactic, whs0001.pck of Tir galactique converted with
# its space background at 6400 to 8000 units; mismatch_galactic_fixed, the same technique with it brought under 3000.

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


def _nearest_depth(path, mesh_name, frame):
    """Depth of the nearest vertex of a mesh drawn at a frame inside the view of the game camera (in front of it and in
    its field of view)."""
    if eleven.engine_state("IE4") == "missing":
        pytest.skip("engine IE4 is missing")
    scene = Scene()
    scene.install_missing = True
    scene.add_file(path, "IE4")
    view = View()
    view.update_game(*scene.camera_at(frame), far=scene.game_camera_far)
    (item,) = [item for item in scene.draw_list(float(frame), None) if item.mesh.name == mesh_name]
    positions = item.positions if item.positions is not None else item.mesh.positions
    world = positions @ item.matrix[:3, :3].T + item.matrix[:3, 3]
    eye = world @ view.view[:3, :3].T + view.view[:3, 3]
    clip = np.c_[eye, np.ones(len(eye))] @ view.projection.T
    inside = (clip[:, 3] > 0) & (np.abs(clip[:, 0]) <= clip[:, 3]) & (np.abs(clip[:, 1]) <= clip[:, 3])
    assert inside.any()
    return float(-eye[inside, 2].min()), scene.game_camera_far


def test_space_background_of_a_converted_technique():
    path, fixed = _files("space_galactic", "mismatch_galactic_fixed")
    depth, far = _nearest_depth(path, "effect._000_space_new", 180)
    assert depth > far
    depth, far = _nearest_depth(fixed, "effect._000_space_new", 180)
    assert depth < far
