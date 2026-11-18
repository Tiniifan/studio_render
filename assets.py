"""The files of a project (assets): what they hold decides their kind and how the asset panel shows them.

An archive that holds other archives (a .pck) is a package: it shows as a folder of its archives, one level deep.
"""
import itertools
import os

import eleven
import squirrel

ARCHIVE_EXTENSIONS = (".xc", ".xv", ".xk", ".pck")
SCRIPT_EXTENSIONS = (".nut", ".nutb")
# Pictures imported on their own: shown in the player through the timeline
IMAGE_EXTENSIONS = (".xi", ".png", ".jpg", ".jpeg")
# A text asset: its text, font and color (JSON), drawn as a picture
TEXT_EXTENSION = ".text"
IMPORT_EXTENSIONS = ARCHIVE_EXTENSIONS + SCRIPT_EXTENSIONS + IMAGE_EXTENSIONS
ARCHIVE_MAGICS = (b"XPCK", b"XFSP")

MODEL, SKELETON, ANIMATION, CAMERA, LIGHT, IMAGE, OTHER, SQUIRREL, PACKAGE, CUSTOM, TEXT = (
    "model", "skeleton", "animation", "camera", "light", "image", "other", "squirrel", "package", "custom", "text")
KINDS = (MODEL, SKELETON, ANIMATION, CAMERA, LIGHT, IMAGE, OTHER, SQUIRREL, PACKAGE, CUSTOM, TEXT)

ANIMATION_EXTENSIONS = (".mtn2", ".imm2", ".mtm2", ".mtn3", ".imm3", ".mtm3")
# Tables every archive has, they say nothing of its content
NEUTRAL_EXTENSIONS = (".bin", ".cmn")

# Locks a template puts on its assets and custom objects
LOCKED, REPLACE_ONLY = "locked", "replace-only"
LOCKS = (LOCKED, REPLACE_ONLY)

_uids = itertools.count(1)


def is_archive(data):
    return data[:4] in ARCHIVE_MAGICS


def is_script(name):
    return name.lower().endswith(SCRIPT_EXTENSIONS)


def is_image_file(name):
    return name.lower().endswith(IMAGE_EXTENSIONS)


def is_text_file(name):
    return name.lower().endswith(TEXT_EXTENSION)


def archive_files(data):
    """{name: bytes} of an archive, read by studio_eleven's XPCK reader."""
    return eleven.xpck().open_file(data)


def nested_archives(files):
    return [name for name, content in files.items()
            if name.lower().endswith(ARCHIVE_EXTENSIONS) and isinstance(content, (bytes, bytearray)) and is_archive(content)]


def kind_of_files(names):
    """Kind of an archive from the names of its files, first rule that applies."""
    extensions = [os.path.splitext(name)[1].lower() for name in names]
    if ".prm" in extensions:
        return MODEL
    if ".mbn" in extensions:
        return SKELETON
    if any(extension in ANIMATION_EXTENSIONS for extension in extensions):
        return ANIMATION
    if ".cmr2" in extensions or ".cmr" in extensions:
        return CAMERA
    if ".lgt" in extensions:
        return LIGHT
    meaningful = [extension for extension in extensions if extension not in NEUTRAL_EXTENSIONS]
    if meaningful and all(extension == ".xi" for extension in meaningful):
        return IMAGE
    return OTHER


def classify(name, data):
    """(kind, archive files or None)."""
    if is_script(name):
        return SQUIRREL, None
    if is_text_file(name):
        return TEXT, None
    if is_image_file(name) and not is_archive(data):
        return IMAGE, None
    if not is_archive(data):
        return OTHER, None
    try:
        files = archive_files(data)
    except Exception:
        return OTHER, None
    if nested_archives(files):
        return PACKAGE, files
    return kind_of_files(files), files


def pack(files):
    """Bytes of an XPCK archive, written by studio_eleven's writer."""
    return eleven.xpck().pack_archive_bytes(dict(files))


class Asset:
    def __init__(self, name, data, kind, parent=None):
        self.uid = next(_uids)
        self.name = name
        self.data = data
        self.kind = kind
        self.parent = parent
        self.children = []          # archives of a package
        self.extra_files = {}       # files of a package that are not archives, kept when it is packed again
        self.thumbnail = None       # PNG bytes of the render of a model
        self.error = None           # why a script does not read or compile
        self.source_path = None     # file it was imported from, a script edited in the window is written back there
        self.model = None
        self.visible = True
        self.expanded = True
        self.lock = None            # LOCKED or REPLACE_ONLY in a template project
        self.role = None            # what it is for the script of a template: "map", "gol", "bal1"...
        self.image = None           # RGBA pixels (top row first) of a picture: an image file or a text
        self.text = None            # {"text", "font", "color"} of a text asset

    def __repr__(self):
        return f"<Asset {self.path} {self.kind}>"

    @property
    def is_picture(self):
        """An image file or a text: it can be put in the timeline and drawn over the player."""
        return self.image is not None

    @property
    def is_package(self):
        return self.kind == PACKAGE

    @property
    def path(self):
        return f"{self.parent.name}/{self.name}" if self.parent is not None else self.name

    @property
    def stem(self):
        return os.path.splitext(self.name)[0]

    @property
    def extension(self):
        return os.path.splitext(self.name)[1].lower()

    def packed(self):
        """Bytes of the asset as saved: a package is packed again from its archives (renamed or removed ones included),
        a .nutb is compiled again from its source (kept as source when it does not compile)."""
        if self.kind == SQUIRREL and self.extension == ".nutb" and not squirrel.is_compiled(self.data):
            try:
                return squirrel.compile(self.data, self.name)
            except squirrel.SquirrelError:
                return self.data
        if not self.is_package:
            return self.data
        files = dict(self.extra_files)
        for child in self.children:
            files[child.name] = child.data
        return pack(files)


class CustomObject(Asset):
    """Several assets drawn together as one object (head + body + studs), without copying them; one animation drives it."""

    def __init__(self, name, members):
        super().__init__(name, b"", CUSTOM)
        self.members = list(members)

    @property
    def stem(self):
        return self.name

    @property
    def extension(self):
        return ""


MOTION_EXTENSIONS = (".mtn2", ".mtn3")


def drives_armature(asset):
    """An animation archive with a motion (.mtn2/.mtn3, the nodes of an armature), not only UV or material tracks."""
    if asset.kind != ANIMATION:
        return False
    try:
        return any(name.lower().endswith(MOTION_EXTENSIONS) for name in archive_files(asset.data))
    except Exception:
        return False


def has_armature(asset):
    return asset.model is not None and any(part.bones for part in asset.model.walk())


def unique_name(name, taken):
    taken = {n.lower() for n in taken}
    if name.lower() not in taken:
        return name
    stem, extension = os.path.splitext(name)
    for index in itertools.count(2):
        candidate = f"{stem}_{index}{extension}"
        if candidate.lower() not in taken:
            return candidate


def valid_name(name):
    return bool(name) and name.strip() == name and not any(c in name for c in '\\/:*?"<>|') and name not in (".", "..")
