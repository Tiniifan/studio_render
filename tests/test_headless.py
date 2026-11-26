"""studio_render_cli headless: renders of a technique without a window, as JSON lines on stdout.

The renders need tests/fixtures.local.toml [files]: project (the HissatsuScene template), technique_package and
technique_script (the .pck and the .nut of a technique with a success and a failure branch). The smoke test of the
built exe runs on the folder of STUDIO_RENDER_BUILD, or on out/<version>/studio_render when it exists.
"""
import hashlib
import json
import os
import random
import subprocess
import sys
import tomllib

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import headless
from version import VERSION

CLI = [sys.executable, os.path.join(ROOT, "cli.py")]


def _files(*keys):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures.local.toml")
    if not os.path.isfile(path):
        pytest.skip("tests/fixtures.local.toml is missing")
    with open(path, "rb") as stream:
        files = tomllib.load(stream).get("files", {})
    missing = [key for key in keys if not (files.get(key) and os.path.isfile(files[key]))]
    if missing:
        pytest.skip(f"fixtures not configured or not found: {', '.join(missing)}")
    return [files[key] for key in keys]


def _hash(path):
    with open(path, "rb") as stream:
        return hashlib.sha256(stream.read()).hexdigest()


def _run(arguments, command=CLI, cwd=ROOT, timeout=600):
    """(exit code, [JSON lines of stdout])."""
    done = subprocess.run(command + ["headless"] + arguments, capture_output=True, cwd=cwd, timeout=timeout)
    lines = [json.loads(line) for line in done.stdout.decode("utf-8").splitlines() if line.strip()]
    return done.returncode, lines


@pytest.fixture
def technique():
    project, package, script = _files("project", "technique_package", "technique_script")
    before = _hash(project)
    yield project, package, script
    assert _hash(project) == before, "the project was changed"


def test_spread_frames():
    frames = headless.spread_frames(84, 10, random.Random(1))
    assert len(frames) == 10 and frames == sorted(set(frames)) and 0 <= frames[0] and frames[-1] <= 84
    assert frames == headless.spread_frames(84, 10, random.Random(1))
    assert headless.spread_frames(4, 10, random.Random(1)) == [0, 1, 2, 3, 4]


def test_capabilities():
    code, lines = _run(["--capabilities"])
    assert code == 0
    assert lines == [{"headless": 1, "operations": ["frames", "mp4"], "version": VERSION}]


@pytest.mark.parametrize("arguments", [[], ["frames", "a.srp"], ["frames", "a.srp", "--frames-dir", "x", "--count",
                                                                 "2", "--frames", "1", "b.pck"]])
def test_usage(arguments):
    code, lines = _run(arguments)
    assert code == 2
    assert lines[-1]["step"] == "error" and lines[-1]["code"] == "USAGE"


def test_missing_project(tmp_path):
    code, lines = _run(["frames", str(tmp_path / "missing.srp"), "--frames-dir", str(tmp_path), "--frames", "0",
                        "a.pck"])
    assert code == 3 and lines == [{"step": "error", "code": "FILE_NOT_FOUND", "message": lines[0]["message"]}]


def test_frames(technique, tmp_path):
    project, package, script = technique
    folder = tmp_path / "frames"
    code, lines = _run(["frames", project, "--frames-dir", str(folder), "--frames", "5,0,5", package, script])
    assert code == 0, lines
    assert [line["step"] for line in lines] == ["import", "frames", "frame", "frame"]
    assert lines[1]["frames"] == [0, 5] and lines[1]["last"] > 5
    for index, (line, frame) in enumerate(zip(lines[2:], (0, 5))):
        assert line["index"] == index and line["frame"] == frame
        assert line["path"] == str(folder / f"frame_{frame:03d}.png")
        with Image.open(line["path"]) as image:
            assert image.size == (400, 240)


def test_frames_count_is_reproducible(technique, tmp_path):
    project, package, script = technique
    runs = []
    for name in ("a", "b"):
        code, lines = _run(["frames", project, "--frames-dir", str(tmp_path / name), "--count", "10", "--seed", "1",
                            "--size", "200x120", package, script])
        assert code == 0, lines
        runs.append(lines[1]["frames"])
        assert len(lines[1]["frames"]) == 10 == len(set(lines[1]["frames"]))
        assert sorted(os.listdir(tmp_path / name)) == [f"frame_{f:03d}.png" for f in lines[1]["frames"]]
    assert runs[0] == runs[1]


def test_file_not_taken(technique, tmp_path):
    project, package, _ = technique
    other = tmp_path / "whd0001.xc"
    other.write_bytes(b"not an archive")
    code, lines = _run(["frames", project, "--frames-dir", str(tmp_path), "--frames", "0", package, str(other)])
    assert code == 3
    assert lines[0]["step"] == "import" and lines[0]["result"]["not_taken"]
    assert lines[-1]["step"] == "error" and lines[-1]["code"] == "IMPORT_REFUSED"
    assert not list(tmp_path.glob("*.png"))


def test_mp4_both_branches(technique, tmp_path):
    project, package, script = technique
    success, failure = tmp_path / "success.mp4", tmp_path / "failure.mp4"
    success.write_bytes(b"old")     # replaced
    code, lines = _run(["mp4", project, "--mp4", str(success), "--failure", str(failure), package, script])
    assert code == 0, lines
    assert lines[0]["step"] == "import"
    for branch, path in (("success", success), ("failure", failure)):
        progress = [line for line in lines if line["step"] == "progress" and line["branch"] == branch]
        exported = [line for line in lines if line["step"] == "exported" and line["branch"] == branch]
        assert progress and progress[-1]["done"] == progress[-1]["total"]
        assert len(exported) == 1 and exported[0]["result"]["path"] == str(path)
        assert exported[0]["result"]["frames"] == progress[-1]["total"]
        assert path.stat().st_size > 1000
    steps = [line["step"] for line in lines]
    assert steps.index("exported") < len(steps) - 1 and steps[-1] == "exported"


def _built_folder():
    folder = os.environ.get("STUDIO_RENDER_BUILD") or os.path.join(ROOT, "out", VERSION, "studio_render")
    if not os.path.isfile(os.path.join(folder, "studio_render_cli.exe")):
        pytest.skip(f"no built studio_render_cli.exe in {folder} (python tools/build_exe.py, or STUDIO_RENDER_BUILD)")
    return folder


def test_built_exe(tmp_path):
    folder = _built_folder()
    assert os.path.isfile(os.path.join(folder, "studio_render.exe"))
    assert os.path.isdir(os.path.join(folder, "_internal"))
    command = [os.path.join(folder, "studio_render_cli.exe")]
    code, lines = _run(["--capabilities"], command, cwd=str(tmp_path), timeout=60)
    assert code == 0 and set(lines[0]["operations"]) >= {"frames", "mp4"}


def test_built_exe_renders(technique, tmp_path):
    folder = _built_folder()
    project, package, script = technique
    command = [os.path.join(folder, "studio_render_cli.exe")]
    code, lines = _run(["frames", project, "--frames-dir", "frames", "--frames", "0,5", package, script], command,
                       cwd=str(tmp_path))
    assert code == 0, lines
    assert [line["frame"] for line in lines if line["step"] == "frame"] == [0, 5]
    assert (tmp_path / "frames" / "frame_005.png").is_file()
    code, lines = _run(["mp4", project, "--mp4", "out.mp4", "--failure", "fail.mp4", package, script], command,
                       cwd=str(tmp_path))
    assert code == 0, lines
    assert [line["branch"] for line in lines if line["step"] == "exported"] == ["success", "failure"]
    assert (tmp_path / "out.mp4").stat().st_size > 1000 and (tmp_path / "fail.mp4").stat().st_size > 1000
