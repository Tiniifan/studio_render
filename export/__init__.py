"""Export of assets to common formats: models and skeletons to .dae / .fbx / .obj, pictures to .png.

No Qt here (the window only asks for the path and shows the result), no dependency beyond numpy and Pillow.
asset_formats(asset) says what an asset offers, export_asset(asset, path) writes it; export_models() and
export_picture() / export_pictures() work from decoded archives and pixels.

Formats: one module each (dae.py, fbx.py, obj.py with its .mtl, png.py), each with a write() entry point; scene.py
gathers the joints, meshes, materials and textures of the archives (bind pose included), common.py holds the rest.

Conventions: positions are written as stored, Y up, right-handed (+X right, +Y up, +Z front, like OpenGL), 1 unit = 1 m
(this is what studio_eleven gives Blender, where it turns the armature by +90° around X to make Z up).
- .dae: <up_axis>Y_UP</up_axis>, <unit meter="1"/>;
- .fbx: binary FBX 7.4 (Blender does not read ASCII FBX), UpAxis = +Y, FrontAxis = +Z, CoordAxis = +X,
  UnitScaleFactor = 100 (FBX counts in centimeters);
- importers that convert to Z up (Blender) turn the model like studio_eleven does.
UVs: studio_eleven gives them with v going up (v = 1 - v of the file) and Texture.rgba bottom row first; the .png files
are written top row first, so the UVs are written unchanged (v up, origin at the bottom left, as .obj, Collada and FBX
expect).
"""
import os

import assets
import images

from . import dae, fbx, obj, png
from .common import (FORMAT_NAMES, MODEL_FORMATS, PICTURE_FORMATS, SKELETON_FORMATS, ExportError, file_name,
                     write_png)
from .scene import Scene, models_of

__all__ = ["MODEL_FORMATS", "SKELETON_FORMATS", "PICTURE_FORMATS", "FORMAT_NAMES", "ExportError", "Scene",
           "archive_pictures", "asset_formats", "asset_models", "export_asset", "export_models", "export_picture",
           "export_pictures", "file_filter", "file_name", "formats_for", "write_png"]


def archive_pictures(data):
    """(name, RGBA top row first) of the .xi files of an image archive, in file order."""
    try:
        files = assets.archive_files(data)
    except Exception:
        return []
    pictures = []
    for name in sorted(n for n in files if n.lower().endswith(".xi")):
        try:
            pictures.append((name, images.decode(name, files[name])))
        except images.ImageError:
            pass
    return pictures


def _image_archive(asset):
    return asset.kind == assets.IMAGE and not asset.is_picture and assets.is_archive(asset.data)


def asset_models(asset):
    """The decoded archives an asset exports: its own, or those of the members of a custom object."""
    if asset.kind == assets.CUSTOM:
        return [member.model for member in asset.members if member.model is not None]
    return [asset.model] if asset.model is not None else []


def asset_formats(asset):
    """Extensions the Export entry offers for an asset, () when it has none:
    - a picture (image file, text) or an image archive: .png;
    - an archive with meshes (a model): .dae, .fbx, .obj; with nodes but no mesh (a skeleton): .dae, .fbx;
    - a custom object: the same, its members merged on one skeleton;
    - a package: none (its archives are assets of their own and have the entry), nor scripts, animations, cameras."""
    if asset.is_package:
        return ()
    if asset.is_picture or _image_archive(asset):
        return PICTURE_FORMATS
    if asset.kind in (assets.SQUIRREL, assets.TEXT, assets.IMAGE):
        return ()
    return formats_for(asset_models(asset))


def export_asset(asset, path):
    """Exports an asset to path, its format chosen by the extension. Returns the written paths (textures included)."""
    offered = asset_formats(asset)
    extension = os.path.splitext(path)[1].lower()
    if not offered:
        raise ExportError(f"{asset.name} has nothing to export")
    if extension not in offered:
        raise ExportError(f"{asset.name} cannot be exported as {extension or 'a file without extension'}: "
                          f"use {', '.join(offered)}")
    if asset.is_picture:
        return export_picture(asset.image, path)
    if offered == PICTURE_FORMATS:
        return export_pictures(archive_pictures(asset.data), path)
    return export_models(asset_models(asset), path, asset.stem)


def formats_for(models):
    """Extensions a model export offers for these decoded archives: meshes -> .dae/.fbx/.obj, nodes only ->
    .dae/.fbx, nothing -> ()."""
    parts = models_of(models)
    if any(len(part.meshes) for part in parts):
        return MODEL_FORMATS
    if any(part.bones for part in parts):
        return SKELETON_FORMATS
    return ()


def file_filter(extensions):
    """Filter string of a save dialog for these extensions."""
    return ";;".join(f"{FORMAT_NAMES.get(e, e)} (*{e})" for e in extensions)


def export_picture(rgba, path):
    """A picture of the player (image file, text), RGBA top row first, as a .png. Returns [path]."""
    if rgba is None:
        raise ExportError("the picture could not be read")
    if not path.lower().endswith(".png"):
        raise ExportError("a picture is exported as .png")
    return png.write([("", rgba)], path)


def export_pictures(pictures, path):
    """(name, RGBA top row first) pictures, an image archive, as .png: the first one at path, the others next to it as
    <stem>_<name>.png. Returns the written paths."""
    return png.write(pictures, path)


def export_models(models, path, name=None):
    """Writes decoded archives (model.Model, their children included) to path (.dae, .fbx or .obj) and the textures
    next to it. Returns the written paths."""
    extension = os.path.splitext(path)[1].lower()
    offered = formats_for(models)
    if not offered:
        raise ExportError("nothing to export: no mesh and no node")
    if extension not in offered:
        if extension in MODEL_FORMATS:
            raise ExportError(f"{extension} cannot hold a skeleton without mesh, use {' or '.join(offered)}")
        raise ExportError(f"unknown format {extension or '(none)'}, use {', '.join(offered)}")
    scene = Scene(models, name or os.path.splitext(os.path.basename(path))[0])
    folder = os.path.dirname(os.path.abspath(path))
    try:
        if extension == ".obj":
            written = obj.write(scene, path)
        elif extension == ".dae":
            written = dae.write(scene, path)
        else:
            written = fbx.write(scene, path)
    except OSError as error:
        raise ExportError(f"{os.path.basename(path)} could not be written: {error}")
    return written + scene.write_textures(folder)
