"""Squirrel scripts of the games: .nutb (compiled) <-> .nut (source), through the Windows tools of tools/."""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
DECOMPILER = os.path.join(ROOT, "tools", "NutDecompiler", "NutDecompiler.exe")
COMPILER = os.path.join(ROOT, "tools", "SquirrelCompiler", "SquirrelCompiler.exe")
TIMEOUT = 60


class SquirrelError(Exception):
    pass


class SquirrelUnavailable(SquirrelError):
    pass


def is_compiled(data):
    """Compiled Squirrel bytecode starts with 0xFAFA then RIQS."""
    return data[:6] == b"\xfa\xfaRIQS"


def available():
    return sys.platform == "win32" and os.path.isfile(DECOMPILER) and os.path.isfile(COMPILER)


def _check(tool):
    if sys.platform != "win32":
        raise SquirrelUnavailable(f"{os.path.basename(tool)} is a Windows program, Squirrel scripts can't be converted on {sys.platform}.")
    if not os.path.isfile(tool):
        raise SquirrelUnavailable(f"{os.path.relpath(tool, ROOT)} is missing.")


def _run(arguments, folder):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(arguments, cwd=folder, capture_output=True, timeout=TIMEOUT, creationflags=flags)
    except subprocess.TimeoutExpired:
        raise SquirrelError(f"{os.path.basename(arguments[0])} did not answer within {TIMEOUT} s.")
    output = (result.stdout or b"").decode("utf-8", "replace") + (result.stderr or b"").decode("utf-8", "replace")
    return result.returncode, output.replace(folder + os.sep, "").replace(folder, "").strip()


def decompile(data, name="script.nutb"):
    """Source text (bytes) of a compiled script."""
    _check(DECOMPILER)
    folder = tempfile.mkdtemp(prefix="studio_render_nut_")
    try:
        stem = os.path.splitext(os.path.basename(name))[0] or "script"
        source = os.path.join(folder, stem + ".nutb")
        target = os.path.join(folder, stem + ".nut")
        with open(source, "wb") as stream:
            stream.write(data)
        code, output = _run([DECOMPILER, source], folder)
        if not os.path.isfile(target) or os.path.getsize(target) == 0:
            raise SquirrelError(output or f"{os.path.basename(name)} could not be decompiled (exit code {code}).")
        with open(target, "rb") as stream:
            return stream.read()
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def compile(data, name="script.nut"):
    """Compiled bytes of a source script, SquirrelError holds the compiler message."""
    _check(COMPILER)
    folder = tempfile.mkdtemp(prefix="studio_render_nut_")
    try:
        stem = os.path.splitext(os.path.basename(name))[0] or "script"
        source = os.path.join(folder, stem + ".nut")
        target = os.path.join(folder, stem + ".cnut")
        with open(source, "wb") as stream:
            stream.write(data)
        code, output = _run([COMPILER, "-o", target, "-c", source], folder)
        # The compiler prints nothing when it succeeds
        if output or not os.path.isfile(target):
            raise SquirrelError(output or f"{os.path.basename(name)} could not be compiled (exit code {code}).")
        with open(target, "rb") as stream:
            return stream.read()
    finally:
        shutil.rmtree(folder, ignore_errors=True)
