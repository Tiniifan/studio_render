"""Pictures as .png: an image file, a text, the images of an image archive."""
import os

from .common import ExportError, Names, file_name, write_png


def write(pictures, path):
    """(name, RGBA top row first) pictures, an image archive, as .png: the first one at path, the others next to it as
    <stem>_<name>.png. Returns the written paths."""
    if not path.lower().endswith(".png"):
        raise ExportError("a picture is exported as .png")
    if not pictures:
        raise ExportError("the archive holds no readable image")
    folder, stem = os.path.dirname(path), os.path.splitext(os.path.basename(path))[0]
    names = Names()
    names.get(stem)
    written = [write_png(pictures[0][1], path)]
    for name, rgba in pictures[1:]:
        other = names.get(f"{stem}_{file_name(os.path.splitext(name)[0], 'image')}")
        written.append(write_png(rgba, os.path.join(folder, other + ".png")))
    return written
