"""Loads studio_eleven's format readers and the Blender-free part of its StudioRender core, read-only.

studio_eleven (https://github.com/Tiniifan/studio_eleven) is a Blender addon: its package __init__ files register Blender
operators. The packages are created empty here and only the pure Python modules are imported, so neither bpy nor
Blender is needed. Blender's mathutils, imported at the top of a few readers, is replaced by compat/mathutils.py.

Where studio_eleven is taken from, first match:
1. the STUDIO_ELEVEN environment variable,
2. the studio_eleven folder next to this file (the copy shipped with the repository).
"""
import importlib
import os
import sys
import types

ROOT = os.path.dirname(os.path.abspath(__file__))

_PACKAGES = (
    "", "compression", "formats", "formats/archive", "formats/model", "formats/texture", "formats/texture/pixel_formats",
    "formats/material", "formats/animation", "formats/animation/tracks", "formats/camera",
    "vendor", "vendor/trianglestrip", "rendering", "rendering/studio_render",
)

# Packages whose __init__ only re-exports pure modules, run so `from .tracks import *` keeps working
_RUN_INIT = ("compression", "formats/texture/pixel_formats", "formats/animation/tracks")

PACKAGE = "studio_eleven"

# Engines of studio_render made of two of studio_eleven's: each archive is drawn by the first one that knows all its
# render programs. IE4 + IE5 is GO with the Miximax heads of IE5 (#FIX_MIXI_MAX and its color palette).
COMPOSITE_ENGINES = {
    "IE4+IE5": ("Inazuma Eleven Go Extended", ("IE4", "IE5")),
}

_path = None


def location():
    path = os.environ.get("STUDIO_ELEVEN") or os.path.join(ROOT, "studio_eleven")
    return os.path.abspath(path)


def _install_mathutils():
    try:
        import mathutils  # noqa: F401  (Blender's own when running inside Blender)
    except ImportError:
        from compat import mathutils as shim
        sys.modules["mathutils"] = shim


def install():
    global _path
    if _path is not None:
        return _path

    path = location()
    if not os.path.isfile(os.path.join(path, "formats", "archive", "xpck.py")):
        raise FileNotFoundError(
            f"studio_eleven not found in {path}. Set STUDIO_ELEVEN or run sync_studio_eleven.bat (see README.md).")

    _install_mathutils()

    for package in _PACKAGES:
        name = PACKAGE + ("." + package.replace("/", ".") if package else "")
        folder = os.path.join(path, *package.split("/")) if package else path
        if package in _RUN_INIT and os.path.isfile(os.path.join(folder, "__init__.py")):
            continue
        module = types.ModuleType(name)
        module.__path__ = [folder]
        module.__package__ = name
        sys.modules[name] = module

    for package in _RUN_INIT:
        name = PACKAGE + "." + package.replace("/", ".")
        if name not in sys.modules:
            importlib.import_module(name)

    # formats/__init__.py re-exports every format module (`from ..formats import mtr`)
    formats = sys.modules[PACKAGE + ".formats"]
    for category in ("archive", "model", "texture", "material", "animation", "camera"):
        for file_name in sorted(os.listdir(os.path.join(path, "formats", category))):
            if file_name.endswith(".py") and file_name != "__init__.py":
                name = file_name[:-3]
                setattr(formats, name, importlib.import_module(f"{PACKAGE}.formats.{category}.{name}"))

    _path = path
    return path


def module(name):
    """studio_eleven.<name>, e.g. module("formats.archive.xpck")."""
    install()
    return importlib.import_module(PACKAGE + "." + name)


def xpck():
    return module("formats.archive.xpck")


def res():
    return module("formats.archive.res")


def xmpr():
    return module("formats.model.xmpr")


def mbn():
    return module("formats.model.mbn")


def imgc():
    return module("formats.texture.imgc")


def txp():
    return module("formats.texture.txp")


def atr():
    return module("formats.material.atr")


def mtr():
    return module("formats.material.mtr")


def cmb():
    return module("formats.material.cmb")


def xcsl():
    return module("formats.material.xcsl")


def animation_manager():
    return module("formats.animation.animation_manager")


def minf():
    return module("formats.animation.minf")


def xcma():
    return module("formats.camera.xcma")


def xcmt():
    return module("formats.camera.xcmt")


def engines():
    return module("rendering.engines")


def game_manager():
    return module("rendering.game_manager")


def render_defaults():
    return module("rendering.render_defaults")


def game_material():
    return module("rendering.game_material")


def combiner():
    return module("rendering.studio_render.combiner")


def shaders():
    return module("rendering.studio_render.shaders")


def state():
    return module("rendering.studio_render.state")


def lighting():
    return module("rendering.studio_render.lighting")


def resources():
    return module("rendering.studio_render.resources")


def vertex_binary():
    return module("rendering.studio_render.vertex_binary")


def engine_parts(engine_id):
    """The studio_eleven engines an engine is made of: itself, or the two of a composite engine."""
    return COMPOSITE_ENGINES[engine_id][1] if engine_id in COMPOSITE_ENGINES else (engine_id,)


def _program_known(engine_id, model, program_hash):
    library = model.library or {}
    if any(res().get_name_hash(program["name"]) == program_hash for program in library.get("render_programs", [])):
        return True
    return render_defaults().find_render_default_by_hash(engine_id, program_hash) is not None


def drawing_engine(engine_id, model):
    """Engine an archive (a part of a model) is drawn with: the engine itself, or for a composite engine the first of
    its engines that knows every render program of the archive (the first one when none does)."""
    parts = engine_parts(engine_id)
    if len(parts) == 1:
        return engine_id
    for part in parts:
        if all(_program_known(part, model, mesh.render_program_hash) for mesh in model.meshes):
            return part
    return parts[0]


class EngineUnavailable(RuntimeError):
    """An engine that is not installed: studio_render never draws with studio_eleven's default games."""


def ensure_game(engine_id, install_missing=False):
    """The engine, installed: studio_render does not use the default games (default_v1/v2) that studio_eleven falls
    back on, it does not ship them.

    With install_missing, a bundled engine that is not installed yet is unpacked from its zip without asking (command line).
    The window asks first and calls install_game itself. A composite engine ensures each of its engines.
    Raises EngineUnavailable when the engine is still not installed."""
    if engine_id in COMPOSITE_ENGINES:
        for part in engine_parts(engine_id):
            ensure_game(part, install_missing)
        return engine_id
    manager = game_manager()
    if install_missing and not manager.is_usable(engine_id) and manager.is_bundled(engine_id):
        install_game(engine_id)
    if not manager.is_usable(engine_id) or engines().is_default_engine(engine_id):
        hint = "install it (Engine list of the window)" if manager.is_bundled(engine_id) else "its zip is missing from studio_eleven/rendering/games"
        raise EngineUnavailable(f"The engine {engine_id} is not installed: {hint}.")
    return engine_id


def install_game(engine_id):
    """Unpacks (or unpacks again) the game data of an engine with studio_eleven's installer.

    A composite engine installs those of its engines that are not installed yet."""
    if engine_id in COMPOSITE_ENGINES:
        versions = [install_game(part) for part in engine_parts(engine_id) if engine_state(part) != "ready"]
        return versions[-1] if versions else None
    version = game_manager().install(engine_id)
    render_defaults().invalidate()
    resources().clear_cache()
    return version


def engine_state(engine_id):
    """'ready' (usable), 'not installed' (zip only) or 'missing' (neither zip nor folder).

    A composite engine is ready when all its engines are, missing when one of them is."""
    if engine_id in COMPOSITE_ENGINES:
        states = [engine_state(part) for part in engine_parts(engine_id)]
        return "missing" if "missing" in states else "ready" if all(s == "ready" for s in states) else "not installed"
    manager = game_manager()
    if manager.is_usable(engine_id):
        return "ready"
    return "not installed" if manager.is_bundled(engine_id) else "missing"


def pending_updates():
    """Installed engines whose zip differs from the one they were installed from."""
    manager = game_manager()
    return [engine.id for engine in engines().GAME_ENGINES if manager.update_available(engine.id)]


def engine_ids():
    return [engine.id for engine in engines().GAME_ENGINES] + [engine.id for engine in engines().DEFAULT_ENGINES]


def game_engines():
    """(id, name) of the game engines, in the order of studio_eleven's engines.py; a composite engine follows the
    first of its engines."""
    result = []
    for engine in engines().GAME_ENGINES:
        result.append((engine.id, engine.name))
        result += [(engine_id, name) for engine_id, (name, parts) in COMPOSITE_ENGINES.items() if parts[0] == engine.id]
    return result
