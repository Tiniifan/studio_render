"""Builds studio_render.exe with PyInstaller: python tools/build_exe.py (or build_exe.bat).

Writes out/<version>/studio_render/ (studio_render.exe and the files it needs) and out/<version>/studio_render.zip
(that folder, zipped). The version is the one of version.py; a build of the same version is replaced.

The build is a folder, not a single exe: a single exe unpacks itself in a temporary folder at every start, the engines
installed from the window and studio_render.ini would be lost. What the application reads from disk is copied next to
its modules (_internal): studio_eleven/ (with the zips of the engines, not their unpacked folders nor the default
games), icons/, fonts/, tools/.
"""
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from version import VERSION  # noqa: E402

NAME = "studio_render"
OUT = os.path.join(ROOT, "out")
# Loaded from files at run time (eleven.py imports studio_eleven by path): PyInstaller cannot see what they import
STUDIO_ELEVEN_IMPORTS = ("bisect", "collections", "enum", "functools", "hashlib", "heapq", "json", "operator",
                         "platform", "random", "shutil", "struct", "tempfile", "warnings", "weakref", "zipfile", "zlib")
# Not shipped: what the installer unpacks and the caches
SKIPPED = ("__pycache__", "installed.json")


def studio_eleven_copy(target):
    """studio_eleven/ without the unpacked engines (rendering/games/<id>/ next to <id>.zip), the default games
    (studio_render never draws with them, eleven.ensure_game) and the caches."""
    source = os.path.join(ROOT, "studio_eleven")
    games = os.path.join(source, "rendering", "games")
    zips = {name[:-4].lower() for name in os.listdir(games) if name.lower().endswith(".zip")}

    def ignore(folder, names):
        skipped = {name for name in names if name in SKIPPED}
        if os.path.normcase(folder) == os.path.normcase(games):
            skipped |= {name for name in names if os.path.isdir(os.path.join(folder, name))
                        and (name.lower() in zips or name.lower().startswith("default_v"))}
        return skipped

    shutil.copytree(source, target, ignore=ignore)


def main():
    release = os.path.join(OUT, VERSION)
    work = os.path.join(OUT, ".build")
    for folder in (release, work):
        if os.path.isdir(folder):
            shutil.rmtree(folder)
    os.makedirs(release)

    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onedir",
               "--name", NAME, "--distpath", release, "--workpath", work, "--specpath", work,
               "--paths", ROOT, "--collect-submodules", "glcontext", "--collect-data", "imageio_ffmpeg",
               "--copy-metadata", "imageio"]
    for name in STUDIO_ELEVEN_IMPORTS:
        command += ["--hidden-import", name]
    command.append(os.path.join(ROOT, "main.py"))
    print(" ".join(command))
    subprocess.run(command, check=True, cwd=ROOT)

    application = os.path.join(release, NAME)
    internal = os.path.join(application, "_internal")
    studio_eleven_copy(os.path.join(internal, "studio_eleven"))
    for folder in ("icons", "fonts", "tools"):
        shutil.copytree(os.path.join(ROOT, folder), os.path.join(internal, folder),
                        ignore=shutil.ignore_patterns("__pycache__", "*.py"))
    for file_name in ("README.md",):
        shutil.copy2(os.path.join(ROOT, file_name), application)
    shutil.copy2(os.path.join(ROOT, "studio_eleven", "LICENSE"), os.path.join(application, "LICENSE_studio_eleven"))

    archive = os.path.join(release, NAME + ".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as stream:
        for folder, _, files in os.walk(application):
            for file_name in files:
                path = os.path.join(folder, file_name)
                stream.write(path, os.path.relpath(path, release))
    shutil.rmtree(work, ignore_errors=True)
    print(f"{application}\n{archive}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
