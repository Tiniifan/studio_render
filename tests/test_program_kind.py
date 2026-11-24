"""A mesh drawn with a vertex program of the other kind than the mesh is not drawn: the game shows nothing of a skinned
mesh under #FIX_MDL / #FIX_SPR nor of a rigid mesh under #FIX_MDS. Other vertex programs are drawn as before."""
import os
import sys
import tomllib

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import eleven
from model import Mesh, Model
from scene import Entry, Scene, program_mismatch


def _mesh(name, program, skinned):
    mesh = Mesh()
    mesh.name = name
    mesh.material_name = name + "_mat"
    mesh.positions = np.zeros((3, 3), np.float32)
    mesh.normals = np.zeros((3, 3), np.float32)
    mesh.triangles = np.array([[0, 1, 2]], np.uint32)
    mesh.render_program_hash = eleven.res().get_name_hash(program)
    if skinned:
        mesh.weights = np.ones((3, 4), np.float32) * 0.25
        mesh.bone_indices = np.zeros((3, 4), np.int32)
    else:
        mesh.single_bind = None
    return mesh


def _library_model(meshes):
    """A model whose render library (RES.bin) holds its own programs, one per vertex program."""
    model = Model("effect")
    model.meshes = list(meshes)
    model.library = {"render_programs": [
        {"name": "LIB_SPR", "vertex_program": "#FIX_SPR", "combiner": "cmb0"},
        {"name": "LIB_MDL", "vertex_program": "#FIX_MDL", "combiner": "cmb0"},
        {"name": "LIB_MDS", "vertex_program": "#FIX_MDS", "combiner": "cmb0"},
        {"name": "LIB_SIL", "vertex_program": "#FIX_SIL_VTX", "combiner": "cmb0"},
    ]}
    return model


def _drawn(model, engine_id="IE4"):
    scene = Scene()
    scene.entries.append(Entry(model, engine_id))
    return [item.mesh.name for item in scene.draw_list(0.0, None)]


def test_mismatched_library_programs_are_not_drawn():
    model = _library_model([
        _mesh("skinned_spr", "LIB_SPR", True), _mesh("skinned_mdl", "LIB_MDL", True),
        _mesh("rigid_mds", "LIB_MDS", False),
        _mesh("skinned_mds", "LIB_MDS", True), _mesh("rigid_spr", "LIB_SPR", False), _mesh("rigid_mdl", "LIB_MDL", False),
        _mesh("rigid_sil", "LIB_SIL", False), _mesh("skinned_sil", "LIB_SIL", True),
        _mesh("unknown", "NOT_A_PROGRAM", True),
    ])
    assert _drawn(model) == ["skinned_mds", "rigid_spr", "rigid_mdl", "rigid_sil", "skinned_sil", "unknown"]
    entry = Entry(model, "IE4")
    assert entry.hidden_meshes == {
        0: "program LIB_SPR (#FIX_SPR, rigid), skinned mesh: not drawn in the game",
        1: "program LIB_MDL (#FIX_MDL, rigid), skinned mesh: not drawn in the game",
        2: "program LIB_MDS (#FIX_MDS, skinned), rigid mesh: not drawn in the game",
    }


def test_single_bind_mesh_is_rigid():
    mesh = _mesh("bound", "LIB_MDS", True)
    mesh.single_bind = 0
    assert program_mismatch(_library_model([mesh]), mesh, "IE4") is not None


def test_engine_render_defaults_give_the_kind():
    # #FIX_IMG draws with #FIX_SPR, #FIX_MDS_IMG0 with #FIX_MDS, #FIX_TON_12_MDL_SIL with #FIX_SIL_VTX (flagged
    # skinned, the game draws rigid effect meshes with it: not judged)
    model = Model("effect")
    model.meshes = [_mesh("skinned_img", "#FIX_IMG", True), _mesh("rigid_mds_img0", "#FIX_MDS_IMG0", False),
                    _mesh("skinned_mds_img0", "#FIX_MDS_IMG0", True), _mesh("rigid_img", "#FIX_IMG", False),
                    _mesh("rigid_sil", "#FIX_TON_12_MDL_SIL", False)]
    assert _drawn(model) == ["skinned_mds_img0", "rigid_img", "rigid_sil"]


# With the archives of tests/fixtures.local.toml [files]: mismatch_chaos / mismatch_galactic, whs0001.pck of Tir
# chaotique / Tir galactique converted with a vertex program of the other kind (wings, fist); mismatch_chaos_fixed /
# mismatch_galactic_fixed, the same archives with the matching programs; package_alt (whs0001.pck of GO), go_wht0012 and
# go_whd0016 (wht0012.pck, whd0016.pck of GO).

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


def _scene(path):
    if eleven.engine_state("IE4") == "missing":
        pytest.skip("engine IE4 is missing")
    scene = Scene()
    scene.install_missing = True
    scene.add_file(path, "IE4")
    return scene


def _hidden(scene):
    return sorted(entry.model.meshes[index].name for entry in scene.entries for index in entry.hidden_meshes)


def _frame_meshes(scene, frame):
    return {(item.entry.model.name, item.mesh.name) for item in scene.draw_list(float(frame), None)}


@pytest.mark.parametrize("key, fixed_key, archive, meshes, frame", [
    ("mismatch_chaos", "mismatch_chaos_fixed", "whs0001_ef3.xc", ["effect.wing_10"], 32),
    ("mismatch_galactic", "mismatch_galactic_fixed", "whs0001_ef3.xc",
     ["effect._300_handCulling_Noskin", "effect._310_hand_Noskin", "effect._320_handOutline_Noskin"], 145),
])
def test_converted_techniques(key, fixed_key, archive, meshes, frame):
    path, fixed_path = _files(key, fixed_key)
    scene, fixed = _scene(path), _scene(fixed_path)
    assert _hidden(scene) == meshes
    assert _hidden(fixed) == []
    expected = {(archive, name) for name in meshes}
    assert not expected & _frame_meshes(scene, frame)
    assert expected <= _frame_meshes(fixed, frame)


@pytest.mark.parametrize("key", ["package_alt", "go_wht0012", "go_whd0016"])
def test_game_archives_hide_nothing(key):
    (path,) = _files(key)
    assert _hidden(_scene(path)) == []
