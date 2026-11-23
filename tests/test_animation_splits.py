"""Splits (plans) of a scene driven by a technique script: the bones play the range of the .mtninf, the UV and material
animations the range of their own .imminf / .mtminf, which the game shifts one frame later. The last frame of a cut must
show the last key of its split, not the first key of the next one."""
import os
import sys
import tomllib
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from model import Model
from project import Project


def _split(name, start, end):
    return {"name": name, "start": start, "end": end, "speed": 0.5}


def _scripted_project(index, local, motions=("01", "02", "03", "04")):
    project = Project("IE4")
    project.scene.scripted = lambda: True
    project.scene.camera_index_at = lambda frame: (index, local)
    project._scripted = lambda: SimpleNamespace(motions=list(motions))
    return project


def _model(bone=None, uv=None, material=None, first=None):
    model = Model("whs0001_aa1.xc")
    for kind, splits in (("bone", bone), ("uv", uv), ("material", material)):
        if splits:
            model.kind_splits[kind][1] = splits
    model.splits = {1: bone or first or uv or material or []}
    return model


def test_bones_play_the_mtninf_range_and_uv_the_imminf_range():
    model = _model(bone=[_split("01", 0, 30), _split("02", 31, 70)], uv=[_split("01", 0, 31), _split("02", 32, 71)],
                   material=[_split("02", 33, 72)])
    entry = SimpleNamespace(model=model, bound=[])
    # Last frame of cut 02 (local frame 39): the key "end" of the .mtninf, not the first of cut 03
    frames = _scripted_project(1, 39).frame_map(entry, 70.0)
    assert frames == {"bone": 70.0, "uv": 71.0, "material": 72.0}
    # The local frame is bounded by the end of each split
    frames = _scripted_project(1, 45).frame_map(entry, 76.0)
    assert frames == {"bone": 70.0, "uv": 71.0, "material": 72.0}


def test_without_a_split_of_its_own_a_kind_follows_the_bones():
    model = _model(bone=[_split("02", 31, 70)])
    frames = _scripted_project(1, 10).frame_map(SimpleNamespace(model=model, bound=[]), 41.0)
    assert frames == {"bone": 41.0, "uv": 41.0, "material": 41.0}


def test_without_mtninf_the_first_split_of_the_name_is_kept():
    model = _model(uv=[_split("02", 32, 71)])
    frames = _scripted_project(1, 39).frame_map(SimpleNamespace(model=model, bound=[]), 70.0)
    assert frames == {"bone": 71.0, "uv": 71.0, "material": 71.0}


def test_outside_a_script_every_kind_plays_the_frame():
    project = Project("IE4")
    frames = project.frame_map(SimpleNamespace(model=Model("a"), bound=[]), 12.0)
    assert frames == {"bone": 12.0, "uv": 12.0, "material": 12.0}


# With the game's archives (tests/fixtures.local.toml)

def _fixtures():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures.local.toml")
    if not os.path.isfile(path):
        pytest.skip("tests/fixtures.local.toml is missing")
    with open(path, "rb") as stream:
        files = tomllib.load(stream).get("files", {})
    wanted = {key: files.get(key) for key in ("package_alt", "project")}
    if not all(value and os.path.isfile(value) for value in wanted.values()):
        pytest.skip("the package_alt (whs0001.pck) and project (HissatsuScene.srp) fixtures are needed")
    return wanted


@pytest.fixture(scope="module")
def scripted_scene():
    files = _fixtures()
    project = Project.load(files["project"])
    with open(files["package_alt"], "rb") as stream:
        project.add("whs0001.pck", stream.read())
    project.auto_bind()
    project.reference = project.add("whs0001.nut", b"function Setup() {\n    SetupNum(4, 1, 0, 1, 1);\n}\n")
    assert project.scene.scripted()
    return project


def test_archive_keeps_the_splits_of_each_kind(scripted_scene):
    archive = next(a for a in scripted_scene.walk() if a.name == "whs0001_aa1.xc")
    model = scripted_scene.scene.owned(archive)[0].model
    bone = [(s["name"], s["start"], s["end"]) for splits in model.kind_splits["bone"].values() for s in splits]
    uv = [(s["name"], s["start"], s["end"]) for splits in model.kind_splits["uv"].values() for s in splits]
    assert bone == [("01", 0, 30), ("02", 31, 70), ("03", 71, 111), ("04", 112, 139)]
    assert uv == [("01", 0, 31), ("02", 32, 71), ("03", 72, 112), ("04", 113, 140)]
    assert [split for splits in model.splits.values() for split in splits] == \
        [split for splits in model.kind_splits["bone"].values() for split in splits]


def test_last_frame_of_a_cut_shows_the_end_key_of_the_mtninf(scripted_scene):
    project = scripted_scene
    scene = project.scene
    entry = next(e for e in scene.entries if any(s.model.name == "whs0001_aa1.xc" for s in e.bound))
    assert [(name, a, b) for name, a, b in project.splits()] == [("01", 0, 30), ("02", 31, 70), ("03", 71, 111),
                                                                  ("04", 112, 139)]
    for last, end, uv_end in ((30, 30, 30), (70, 70, 71), (111, 111, 112)):
        frames = project.frame_map(entry, float(last))
        assert frames["bone"] == float(end)
        assert frames["uv"] == float(uv_end)
        scene.evaluate(entry, float(last), None)
        drawn = entry.pose.world.copy()
        entry.pose.update(entry.animations, float(end))
        assert np.allclose(drawn, entry.pose.world)
        entry.pose.update(entry.animations, float(end + 1))
        if last != 30:
            assert not np.allclose(drawn, entry.pose.world)
