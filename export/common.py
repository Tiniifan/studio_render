"""What the format modules share: the formats, the error, names valid for files and formats, numbers, .png files."""
import os
import re

import numpy as np
from PIL import Image

MODEL_FORMATS = (".dae", ".fbx", ".obj")
SKELETON_FORMATS = (".dae", ".fbx")
PICTURE_FORMATS = (".png",)
FORMAT_NAMES = {".dae": "Collada", ".fbx": "FBX (binary)", ".obj": "Wavefront OBJ", ".png": "PNG image"}

EPSILON = 1e-9


class ExportError(Exception):
    pass


def file_name(name, fallback="file"):
    """A name usable as a file name on every system."""
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(name)).strip(" .")
    return cleaned or fallback


class Names:
    """Unique names, case-insensitive (file systems, Blender data)."""

    def __init__(self):
        self.taken = set()

    def get(self, name):
        candidate, index = name, 2
        while candidate.lower() in self.taken:
            candidate = f"{name}_{index}"
            index += 1
        self.taken.add(candidate.lower())
        return candidate


def identifier(name):
    """An XML NCName (Collada ids and sids)."""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    if not cleaned or not (cleaned[0].isalpha() or cleaned[0] == "_"):
        cleaned = "_" + cleaned
    return cleaned


def object_name(name):
    """A name for FBX (no control characters, the \\x00\\x01 separator of the format)."""
    return re.sub(r"[\x00-\x1f]", "_", str(name)) or "unnamed"


def number(value):
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def write_png(rgba, path, bottom_row_first=False):
    """Writes RGBA uint8 pixels (height, width, 4) to a .png file."""
    pixels = np.ascontiguousarray(np.asarray(rgba, dtype=np.uint8)[::-1] if bottom_row_first else np.asarray(rgba, dtype=np.uint8))
    try:
        Image.fromarray(pixels, "RGBA").save(path, "PNG")
    except OSError as error:
        raise ExportError(f"{os.path.basename(path)} could not be written: {error}")
    return path
