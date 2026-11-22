"""Draw order of the meshes over every model of the scene: the game sorts one queue by (bucket, rank in the bucket,
order of the model), so an archive loaded after another does not cover it whatever their buckets."""
import os
import sys
import zlib
from unittest import mock

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import assets as assets_module
import eleven
import renderer as renderer_module
from assets import Asset
from model import Mesh, Model
from project import Project, ProjectError
from scene import Entry, Scene


def _mesh(name, bucket, alpha=1.0):
    mesh = Mesh()
    mesh.name = name
    mesh.material_name = name + "_mat"
    mesh.positions = np.zeros((3, 3), np.float32)
    mesh.normals = np.zeros((3, 3), np.float32)
    mesh.triangles = np.array([[0, 1, 2]], np.uint32)
    mesh.draw_priority = bucket
    mesh.base_alpha = alpha
    return mesh


def _model(name, meshes, outlined=()):
    model = Model(name)
    model.meshes = list(meshes)
    for mesh in model.meshes:
        if mesh.name in outlined:
            model.outlines.append({"mesh_hashes": [zlib.crc32(mesh.name.encode("shift-jis"))]})
    return model


def _scene(*models):
    scene = Scene()
    for model in models:
        scene.entries.append(Entry(model, "IE4"))
    return scene


def _names(scene, frame=0.0):
    return [item.mesh.name for item in scene.draw_list(frame, None)]


def test_lower_bucket_of_a_later_model_is_drawn_first():
    first = _model("whs0001_ef2", [_mesh("line", 42)])
    second = _model("whs0001_ef3", [_mesh("ball", 38)])
    assert _names(_scene(first, second)) == ["ball", "line"]


def test_same_bucket_by_rank_then_model_order():
    first = _model("a", [_mesh("a0", 10), _mesh("a1", 10)])
    second = _model("b", [_mesh("b0", 10), _mesh("b1", 10)])
    assert _names(_scene(first, second)) == ["a0", "b0", "a1", "b1"]


def test_rank_follows_the_file_order_inside_a_bucket():
    first = _model("a", [_mesh("a_late", 20), _mesh("a0", 5), _mesh("a1", 20)])
    second = _model("b", [_mesh("b0", 20)])
    assert _names(_scene(first, second)) == ["a0", "a_late", "b0", "a1"]


def test_skipped_meshes_and_hidden_models():
    first = _model("a", [_mesh("past_buckets", 64), _mesh("invisible", 3, alpha=0.0), _mesh("kept", 3)])
    second = _model("b", [_mesh("hidden", 1)])
    scene = _scene(first, second)
    scene.hidden_at = lambda frame: [scene.entries[1]]
    assert _names(scene) == ["kept"]


def _project(*models):
    project = Project()
    for model in models:
        asset = Asset(model.name + ".xc", b"", assets_module.MODEL)
        asset.model = model
        project.items.append(asset)
        project.scene.entries.append(Entry(model, "IE4", owner=asset))
    return project


def test_map_is_drawn_before_the_queue():
    effect = _model("whs0087_ef1", [_mesh("glow", 0)])
    stadium = _model("mv01b01", [_mesh("sky", 0), _mesh("base", 0), _mesh("base01", 0), _mesh("cloud", 1)])
    head = _model("cp0008a", [_mesh("hair", 0)], outlined=("hair",))
    project = _project(effect, stadium, head)
    assert _names(project.scene) == ["glow", "sky", "hair", "base", "base01", "cloud"]
    project.set_map(project.items[1])
    assert _names(project.scene) == ["sky", "base", "base01", "cloud", "glow", "hair"]


def test_one_map_label_per_project():
    project = _project(_model("mv01b01", [_mesh("sky", 0)]), _model("mf01b01", [_mesh("field", 0)]))
    stadium, field = project.items
    project.set_map(stadium)
    assert project.to_json()["map"] == "mv01b01.xc"
    project.set_map(field)
    assert project.map is field and project.to_json()["map"] == "mf01b01.xc"
    project.set_map(None)
    assert project.map is None and project.to_json()["map"] is None


def test_map_label_needs_meshes_and_leaves_with_its_asset():
    project = _project(_model("mv01b01", [_mesh("sky", 0)]), _model("whs0087_aa1", []))
    stadium, animation = project.items
    with pytest.raises(ProjectError):
        project.set_map(animation)
    project.set_map(stadium)
    project.remove(stadium)
    assert project.map is None and project.map_entries() == set()


def test_outlines_of_a_model_before_its_first_queued_mesh():
    first = _model("body", [_mesh("body_a", 30), _mesh("body_b", 40)], outlined=("body_a", "body_b"))
    second = _model("effect", [_mesh("effect_a", 20), _mesh("effect_b", 35)])
    scene = _scene(first, second)

    calls = []
    renderer = renderer_module.Renderer.__new__(renderer_module.Renderer)
    renderer.ctx = mock.MagicMock()
    renderer.background = (0.0, 0.0, 0.0)
    renderer.draw_outlines = True
    renderer._size_ndc = lambda items, view: None
    renderer._draw_outline = lambda fbo, entry, item, view, size: calls.append(("outline", item.mesh.name))
    renderer._draw_mesh = lambda fbo, entry, item, view, lights, ambient: calls.append(("mesh", item.mesh.name))
    view = mock.MagicMock()
    view.camera_rotation.return_value = None
    with mock.patch.object(eleven, "lighting"), mock.patch.object(eleven, "engines"):
        renderer.render(mock.MagicMock(), scene, view, 0.0)

    assert calls == [("mesh", "effect_a"), ("outline", "body_a"), ("outline", "body_b"), ("mesh", "body_a"),
                     ("mesh", "effect_b"), ("mesh", "body_b")]
