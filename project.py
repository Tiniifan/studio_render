"""A project: the ordered assets, the engine, the reference script and the animation bindings.

Saved as a .srp (Studio Render Project), a zip:
    assets/        the files of the project, as imported (with their name after renaming)
    preview/       the thumbnails of the assets (PNG)
    project.json

The pictures (image files and texts) put in the timeline are its layers: drawn over the player from their first
frame, for their duration, at their place on the 3DS top screen (images.SCREEN).

A template is a .srp whose project.json has a "template" section (see Template): its assets and custom objects can
be locked, the files it accepts and the animations it links on import are listed there.
"""
import fnmatch
import io
import json
import os
import tempfile
import zipfile

import assets as assets_module
import eleven
import images
import model as model_module
import skill_script
import squirrel
from assets import Asset, CustomObject
from scene import Entry, Scene
from version import VERSION, parse

EXTENSION = ".srp"
FORMAT = "studio_render project"


class ProjectError(Exception):
    pass


class ProjectVersionError(ProjectError):
    pass


def _migrate_nothing(data):
    return data


def _migrate_1_0(data):
    """1.1 adds templates: the projects of 1.0 have no template section and no lock."""
    data.setdefault("template", None)
    return data


def _migrate_1_1(data):
    """1.2 adds pictures (image files, texts) and the layers of the timeline."""
    data.setdefault("layers", [])
    return data


# (written by versions up to, migration): applied in order to the project.json of older versions
MIGRATIONS = [
    ("0.0.0", _migrate_nothing),
    ("1.0.0.0", _migrate_1_0),
    ("1.1.0.0", _migrate_1_1),
]


def migrate(data):
    written = parse(data.get("version", "0.0.0"))
    for up_to, migration in MIGRATIONS:
        if written <= parse(up_to):
            data = migration(data)
    data["version"] = VERSION
    return data


def apply_command_line(project, objects=(), animations=()):
    """--object NAME=A,B,C groups archives into a custom object, --animate TARGET=ANIMATION links an animation."""
    for text in objects:
        name, _, members = text.partition("=")
        found = [(member.strip(), project.find(member.strip())) for member in members.split(",") if member.strip()]
        missing = [member for member, asset in found if asset is None]
        if not name.strip() or not found or missing:
            raise ProjectError(f"--object {text}: " + (f"unknown archive {', '.join(missing)}" if missing else "expected NAME=A,B,C")
                               + f" (loaded: {', '.join(a.name for a in project.items)})")
        project.create_custom(name, [asset for _, asset in found])
    for text in animations:
        target_name, _, animation_name = text.partition("=")
        target, animation = project.find(target_name.strip()), project.find(animation_name.strip())
        if target is None or animation is None:
            raise ProjectError(f"--animate {text}: unknown " + ("object" if target is None else "animation")
                               + f" (loaded: {', '.join(a.name for a in project.walk())})")
        if not project.animatable(target):
            raise ProjectError(f"--animate {text}: {target.name} is neither a custom object nor an archive with an armature")
        if animation.kind != assets_module.ANIMATION:
            raise ProjectError(f"--animate {text}: {animation.name} is not an animation")
        project.set_animation(target, animation)


class Template:
    """The "template" section of a project.json: what makes a project a template.

    {
      "name": "HissatsuScene",
      "official": true,                # the file handed out: it opens as a new untitled project
      "imports": [                     # the only files that can be imported, each rule holds one asset
        {"label": "one .pck archive", "patterns": ["*.pck"]},
        {"label": "one script (.nut or .nutb)", "patterns": ["*.nut", "*.nutb"]},
        {"label": "one camera (.xv)", "patterns": ["*.xv"], "kind": "camera"}   # "kind": what the file must hold
      ],
      "auto_bind": {                   # animations of the imported .pck linked to custom objects (or top level assets)
        "swap_label": "Swap aa / ad",
        "hide_unbound": true,          # a slot without its archive in the .pck is not drawn
        "slots": [{"target": "Arion", "archive": "*_aa1.xc", "swapped": "*_ad1.xc",
                   "role": "atk1", "swapped_role": "def1"}, ...]
      },
      "script_visibility": true,       # the reference script shows and hides the objects cut by cut (skill_script)
      "archive_roles": [{"pattern": "*_ef1.xc", "role": "eff1"}, ...],   # roles of the archives of the .pck
      "swapped": false                 # state of the swap switch, saved with the project
    }

    The locks are not here but on the assets and custom objects themselves ("lock": "locked" or "replace-only"):
    they follow an asset through Replace… and a rename. So do the roles of the assets ("role": "map", "gol", "bal1").
    """

    def __init__(self, data):
        self.name = str(data.get("name") or "Template")
        self.official = bool(data.get("official"))
        self.imports = data.get("imports")      # None: any file can be imported
        auto_bind = data.get("auto_bind") or {}
        self.slots = [slot for slot in auto_bind.get("slots") or [] if slot.get("target")]
        self.swap_label = str(auto_bind.get("swap_label") or "Swap")
        self.hide_unbound = bool(auto_bind.get("hide_unbound"))
        self.script_visibility = bool(data.get("script_visibility"))
        self.archive_roles = [item for item in data.get("archive_roles") or [] if item.get("pattern") and item.get("role")]
        self.swapped = bool(data.get("swapped"))

    def to_json(self):
        data = {"name": self.name}
        if self.official:
            data["official"] = True
        if self.imports is not None:
            data["imports"] = self.imports
        if self.slots:
            data["auto_bind"] = {"swap_label": self.swap_label, "slots": self.slots}
            if self.hide_unbound:
                data["auto_bind"]["hide_unbound"] = True
        if self.script_visibility:
            data["script_visibility"] = True
        if self.archive_roles:
            data["archive_roles"] = self.archive_roles
        data["swapped"] = self.swapped
        return data

    @staticmethod
    def matches(rule, name):
        return any(fnmatch.fnmatch(name.lower(), pattern.lower()) for pattern in rule.get("patterns", []))

    def rule_of(self, name):
        """The import rule a file name falls under, None when the template does not take it."""
        return next((rule for rule in self.imports or [] if self.matches(rule, name)), None)

    def accepted(self):
        return ", ".join(rule.get("label") or " / ".join(rule.get("patterns", [])) for rule in self.imports or [])


def squirrel_source(asset):
    """Source text of a script asset ("" when it is still compiled)."""
    data = asset.data
    if squirrel.is_compiled(data):
        return ""
    return bytes(data).decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else str(data or "")


def archives_to_read(name, data):
    """(name, bytes) of the archives add() reads for a file: the file itself, or the archives of a package."""
    if assets_module.is_script(name) or not assets_module.is_archive(data):
        return []
    kind, files = assets_module.classify(name, data)
    if kind != assets_module.PACKAGE:
        return [(name, data)]
    return [(child, files[child]) for child in assets_module.nested_archives(files)]


def project_archives(path):
    """The archives opening a project reads."""
    result = []
    try:
        with zipfile.ZipFile(path) as archive:
            data = json.loads(archive.read("project.json").decode("utf-8"))
            names = set(archive.namelist())
            for item in data.get("assets", []):
                member = "assets/" + item.get("name", "")
                if member in names:
                    result += archives_to_read(item["name"], archive.read(member))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return []
    return result


# Length of a picture put in the timeline, in frames (3 s at 30 fps)
DEFAULT_LAYER_DURATION = 90


class Layer:
    """A picture in the timeline: from frame start, for duration frames, in a rectangle of the 3DS top screen."""

    def __init__(self, asset, start=0, duration=DEFAULT_LAYER_DURATION, x=0.0, y=0.0, width=1.0, height=1.0):
        self.asset = asset
        self.start = int(start)
        self.duration = max(1, int(duration))
        self.x, self.y, self.width, self.height = float(x), float(y), float(width), float(height)
        self.visible = True

    @property
    def end(self):
        """Last frame drawn."""
        return self.start + self.duration - 1

    def shown_at(self, frame):
        return self.visible and self.start <= frame <= self.end

    def fit(self):
        """Native size (one pixel of the picture is one pixel of the screen), shrunk to fit, centred on the screen."""
        height, width = self.asset.image.shape[:2]
        scale = min(1.0, images.SCREEN[0] / width, images.SCREEN[1] / height)
        self.width, self.height = width * scale, height * scale
        self.x = (images.SCREEN[0] - self.width) / 2
        self.y = (images.SCREEN[1] - self.height) / 2

    def to_json(self):
        return {"asset": self.asset.path, "start": self.start, "duration": self.duration, "x": round(self.x, 3),
                "y": round(self.y, 3), "width": round(self.width, 3), "height": round(self.height, 3),
                "visible": self.visible}


def _preview_path(asset):
    return f"preview/custom/{asset.name}.png" if asset.kind == assets_module.CUSTOM else f"preview/{asset.path}.png"


class Project:
    def __init__(self, engine_id="IE4"):
        self.path = None
        self.dirty = False
        self.items = []             # top level assets, in import order
        self.reference = None       # reference script
        self.map = None             # the asset labelled map (a stadium): drawn before the other models
        self.bindings = []          # (animation asset, model asset or custom object)
        self.template = None        # Template of a template project
        self.layers = []            # pictures in the timeline, drawn in this order (the last one on top)
        self._script = (None, None)  # ((source, success), SkillScript) of the reference script
        self.success = True         # _success of the script in the preview (chosen in the player, not saved)
        self.scene = Scene()
        self.scene.hidden_at = self.hidden_at
        self.scene.camera_order = self.camera_order
        self.scene.frame_map = self.frame_map
        self.scene.map_entries = self.map_entries
        self.scene.engine_id = engine_id

    # Queries

    @property
    def engine_id(self):
        return self.scene.engine_id

    @property
    def name(self):
        if self.path:
            return os.path.splitext(os.path.basename(self.path))[0]
        return self.template.name if self.template is not None else "Untitled"

    def walk(self):
        for item in self.items:
            yield item
            yield from item.children

    def find(self, path):
        """Asset by path (package/archive) or by name, with or without extension."""
        lowered = path.lower()
        for asset in self.walk():
            if asset.path.lower() == lowered:
                return asset
        for asset in self.walk():
            if asset.name.lower() == lowered or asset.stem.lower() == lowered:
                return asset
        return None

    def scripts(self):
        return [asset for asset in self.items if asset.kind == assets_module.SQUIRREL]

    def customs(self):
        return [asset for asset in self.items if asset.kind == assets_module.CUSTOM]

    def users_of(self, asset):
        """Custom objects an asset is part of."""
        return [custom for custom in self.customs() if asset in custom.members]

    def animations(self):
        return [asset for asset in self.walk() if asset.kind == assets_module.ANIMATION]

    @staticmethod
    def groupable(asset):
        """Top level archives with something to draw can be put in a custom object."""
        return (asset.parent is None and asset.kind not in (assets_module.CUSTOM, assets_module.PACKAGE)
                and asset.model is not None and any(part.meshes for part in asset.model.walk()))

    def animatable(self, asset):
        """Custom objects and top level archives with an armature take an animation (not the parts of an object)."""
        if asset.parent is not None:
            return False
        return asset.kind == assets_module.CUSTOM or (assets_module.has_armature(asset) and not self.users_of(asset))

    def animation_of(self, target):
        return next((source for source, t in self.bindings if t is target), None)

    def siblings(self, asset):
        return asset.parent.children if asset.parent is not None else self.items

    def bound_targets(self, source):
        return [target for s, target in self.bindings if s is source]

    def touch(self):
        self.dirty = True

    # Template locks and rules

    @staticmethod
    def _locks(asset):
        """The lock of the asset and the one of its package: the archives of a locked package are locked too."""
        return asset.lock, asset.parent.lock if asset.parent is not None else None

    def can_remove(self, asset):
        return self._locks(asset) == (None, None)

    def can_replace(self, asset):
        own, package = self._locks(asset)
        return (own != assets_module.LOCKED and package is None
                and asset.kind not in (assets_module.CUSTOM, assets_module.TEXT))

    def can_rename(self, asset):
        """Locked assets and locked custom objects keep their name (the slots find the objects by name), and so do the
        archives of a package in a template that links them by name."""
        own, package = self._locks(asset)
        if own == assets_module.LOCKED or package is not None:
            return False
        if asset.kind == assets_module.CUSTOM and own is not None:
            return False
        if asset.parent is not None and self.template is not None and self.template.slots:
            return False
        return True

    def lock_text(self, asset):
        """What the lock of an asset allows, for its tooltip; empty when it has none."""
        own, package = self._locks(asset)
        template = self.template.name if self.template is not None else "the template"
        if package is not None:
            return f"part of {asset.parent.name}, locked by {template}"
        if own == assets_module.LOCKED:
            return f"locked by {template}, it cannot be removed, replaced or renamed"
        if own == assets_module.REPLACE_ONLY:
            return f"locked by {template}, it can be replaced (Replace…) but not removed"
        return ""

    def import_rule(self, name):
        """(allowed, rule): every file is allowed outside a template; in a template, only those of a rule."""
        if self.template is None or self.template.imports is None:
            return True, None
        rule = self.template.rule_of(name)
        return rule is not None, rule

    @staticmethod
    def rule_refusal(rule, name, data):
        """Why a file of the right extension still does not fit its rule (its content is not of the kind asked), or None."""
        wanted = (rule or {}).get("kind")
        if not wanted:
            return None
        kind = assets_module.SQUIRREL if assets_module.is_script(name) else assets_module.classify(name, data)[0]
        return None if kind == wanted else f"not a {wanted} archive (it holds: {kind})"

    def holder(self, rule):
        """The top level asset that fills an import rule of the template (the .pck, the script)."""
        return next((asset for asset in self.items if asset.kind != assets_module.CUSTOM
                     and self.template.matches(rule, asset.name)), None)

    def check_replace(self, old, name, data):
        """Why name cannot replace old (an error message), or None."""
        if not self.can_replace(old):
            return f"{old.name} cannot be replaced: {self.lock_text(old)}."
        if self.template is not None and self.template.imports is not None:
            rule = self.template.rule_of(old.name)
            if rule is not None and not self.template.matches(rule, name):
                return f"{name} cannot replace {old.name} in this project, it takes {rule.get('label') or ', '.join(rule['patterns'])}."
            refusal = self.rule_refusal(rule, name, data)
            if refusal:
                return f"{name} cannot replace {old.name}: {refusal}."
            if rule is None and old.lock is None and old.parent is None and not self.import_rule(name)[0]:
                return f"{name} cannot be imported in this project."
        if old.lock is not None:
            kind = assets_module.SQUIRREL if assets_module.is_script(name) else assets_module.classify(name, data)[0]
            if kind != old.kind:
                return f"{name} cannot replace {old.name}: the template needs a file of the same kind ({old.kind}, not {kind})."
        return None

    def auto_bind(self):
        """Links the animations of the packages to the custom objects of the slots, swapped or not.

        A slot whose archive is missing, or is not an animation of an armature (a motion), leaves its object without animation.
        Returns [(target, animation or None)]."""
        if self.template is None or not self.template.slots:
            return []
        archives = [child for package in self.items if package.is_package for child in package.children
                    if assets_module.drives_armature(child)]
        linked = []
        for slot in self.template.slots:
            target = self.slot_target(slot)
            if target is None:
                continue
            pattern = str(slot.get("swapped" if self.template.swapped else "archive") or "").lower()
            source = next((a for a in archives if pattern and fnmatch.fnmatch(a.name.lower(), pattern)), None)
            self.set_animation(target, source)
            linked.append((target, source))
        return linked

    def slot_target(self, slot):
        """The custom object of a slot, or a top level asset (the ball), by name."""
        name = str(slot["target"]).lower()
        found = [a for a in self.items if a.name.lower() == name or (a.kind != assets_module.CUSTOM and a.stem.lower() == name)]
        found.sort(key=lambda a: a.kind != assets_module.CUSTOM)
        return found[0] if found else None

    # Visibility of a template: slots without animation, and the script cut by cut

    def skill_script(self):
        """The SkillScript of the reference script, parsed again when its source changes."""
        script = self.reference
        if script is None or not squirrel_source(script):
            return None
        source = squirrel_source(script)
        if self._script[0] != (source, self.success):
            self._script = ((source, self.success), skill_script.SkillScript.parse(source, self.success))
        return self._script[1]

    def roles(self):
        """[(asset or custom object, role)] of a template: slots (swapped with the switch), assets, archives of the .pck."""
        template = self.template
        roles = []
        for slot in template.slots:
            target = self.slot_target(slot)
            role = slot.get("swapped_role" if template.swapped else "role")
            if target is not None and role:
                roles.append((target, role))
        roles += [(asset, asset.role) for asset in self.items if asset.role]
        for package in (a for a in self.items if a.is_package):
            for child in package.children:
                role = next((r["role"] for r in template.archive_roles if fnmatch.fnmatch(child.name.lower(), r["pattern"].lower())), None)
                if role:
                    roles.append((child, role))
        return roles

    def _scripted(self):
        """The SkillScript that drives the preview of a template, None otherwise."""
        if self.template is None or not self.template.script_visibility:
            return None
        script = self.skill_script()
        return script if script is not None and script.calls else None

    def camera_order(self):
        """Cameras of a template, cut by cut as its script plays them (SetupNum, SetupMotion); None to play them all.

        The cameras come from the .xv imported on its own when there is one, else from the .xv of the .pck."""
        script = self._scripted()
        cameras = self.scene.cameras
        if script is None or not script.cut_num or not cameras:
            return None
        owners = []
        for camera in cameras:
            if camera.owner not in owners:
                owners.append(camera.owner)
        owner = next((o for o in owners if o is not None and o.parent is None), owners[0])
        own = [camera for camera in cameras if camera.owner is owner]
        order = []
        for cut, wanted in enumerate(script.camera_hashes()):
            # A motion that is not in the .xv: the camera of the same rank, as in the file
            camera = next((c for c in own if c.hash == wanted), own[cut] if cut < len(own) else None)
            if camera is not None:
                order.append(camera)
        return order

    def frame_map(self, entry, frame):
        """Frame of an entry's animations per kind ({"bone", "uv", "material"} -> frame): in a cut of the script, the
        frame in the split named after its motion ("04a"), as the game sets the motion of every object of the cut. The
        bones play the split of the .mtninf, the UV and material animations that of their own .imminf / .mtminf (the
        game starts them one frame later), the bone frame without one; the frame itself outside a scripted template."""
        if not self.scene.scripted():
            return dict.fromkeys(("bone", "uv", "material"), frame)
        index, local = self.scene.camera_index_at(frame)
        motion = self._scripted().motions[index]
        models = [entry.model] + [source.model for source in entry.bound]

        def mapped(tables):
            for table in tables:
                for splits in table.values():
                    split = next((s for s in splits if s["name"] == motion), None)
                    if split is not None:
                        return float(split["start"]) + min(local, float(split["end"]) - float(split["start"]))
            return None

        # The splits of a model are those of its .mtninf, else the first file of each name (an archive without .mtninf)
        bone = mapped(model.splits for model in models)
        bone = frame if bone is None else bone
        frames = {"bone": bone}
        for kind in ("uv", "material"):
            own = mapped(model.kind_splits.get(kind, {}) for model in models)
            frames[kind] = bone if own is None else own
        return frames

    def cut_at(self, frame):
        """(cut from 0, frame in its camera) of the game camera at a frame: the cut of the script when it orders the
        cameras, else the rank of the camera in its archive."""
        index, local = self.scene.camera_index_at(frame)
        if index is None:
            return 0, frame
        if self.scene.camera_order is not None and self.camera_order():
            return index, local
        track = self.scene.played_cameras()[index]
        same = [camera for camera in self.scene.cameras if camera.owner is track.owner]
        return same.index(track), local

    def hidden_at(self, frame):
        """Entries a template does not draw at a frame (the scene asks it before drawing)."""
        template = self.template
        if template is None:
            return ()
        hidden = set()
        if template.hide_unbound:
            for slot in template.slots:
                target = self.slot_target(slot)
                if target is not None and self.animation_of(target) is None:
                    hidden.update(self.scene.owned(target))
        script = self.skill_script() if template.script_visibility else None
        if script is not None and script.calls:
            cut, local = self.cut_at(frame)
            for owner, role in self.roles():
                if not script.visible(role, cut, local):
                    hidden.update(self.scene.owned(owner))
        return hidden

    def set_swapped(self, swapped):
        if self.template is None:
            return []
        if self.template.swapped != swapped:
            self.template.swapped = swapped
            self.touch()
        return self.auto_bind()

    # Assets

    def add(self, name, data, parent=None, index=None, kind=None, files=None, source_path=None, engine=None):
        """New asset, loaded in the scene; the name is made unique among its siblings.

        A script keeps its source text: a .nutb is decompiled here, it is compiled again when written back."""
        siblings = parent.children if parent is not None else self.items
        name = assets_module.unique_name(name, [a.name for a in siblings])
        if kind is None:
            kind, files = assets_module.classify(name, data)
        picture = kind == assets_module.TEXT or (kind == assets_module.IMAGE and assets_module.is_image_file(name)
                                                  and not assets_module.is_archive(data))
        if kind != assets_module.SQUIRREL and parent is None and not picture:
            # Nothing is added with an engine that is not installed (studio_render has no default game)
            eleven.ensure_game(engine or self.engine_id, self.scene.install_missing)
        asset = Asset(name, data, kind, parent)
        asset.source_path = source_path
        asset.engine = engine       # forced on the command line ('path@IE5'), not saved
        if kind == assets_module.SQUIRREL and squirrel.is_compiled(data):
            try:
                asset.data = squirrel.decompile(data, name)
            except squirrel.SquirrelError as error:
                if not isinstance(error, squirrel.SquirrelUnavailable):
                    asset.error = f"{name} could not be decompiled: {error}"
        if picture:
            self._load_picture(asset)
        if index is None:
            siblings.append(asset)
        else:
            siblings.insert(index, asset)

        if asset.is_package:
            files = files if files is not None else assets_module.archive_files(data)
            nested = set(assets_module.nested_archives(files))
            asset.extra_files = {n: content for n, content in files.items() if n not in nested}
            for child_name in sorted(nested):
                child_kind = assets_module.kind_of_files(assets_module.archive_files(files[child_name]))
                self.add(child_name, files[child_name], asset, kind=child_kind)
        elif kind != assets_module.SQUIRREL and assets_module.is_archive(data):
            self._load(asset)

        if kind == assets_module.SQUIRREL and parent is None and self.reference is None:
            self.reference = asset
        self.touch()
        return asset

    def _load_picture(self, asset):
        try:
            if asset.kind == assets_module.TEXT:
                asset.text = images.read_text(asset.data)
                asset.image = images.render_text(asset.text)
            else:
                asset.image = images.decode(asset.name, asset.data)
            asset.thumbnail = images.thumbnail(asset.image)
        except Exception as error:
            asset.image = None
            asset.error = f"{asset.name} could not be read: {error}"

    def add_text(self, spec, index=None):
        return self.add(images.text_name(spec), images.text_data(spec), index=index)

    def set_text(self, asset, spec):
        """New text, font or color of a text asset; its layer keeps its place, its scale and its timing."""
        old = asset.image
        asset.data = images.text_data(spec)
        self._load_picture(asset)
        layer = self.layer_of(asset)
        if layer is not None and asset.image is not None and old is not None:
            scale = layer.height / old.shape[0]
            layer.width, layer.height = asset.image.shape[1] * scale, asset.image.shape[0] * scale
        self.touch()

    def _load(self, asset):
        try:
            asset.model = model_module.load(asset.data, asset.name)
        except Exception as error:
            asset.model = None
            asset.error = f"{asset.name} could not be read: {error}"
            return
        self.scene.add_model(asset.model, getattr(asset, "engine", None) or self.engine_id, owner=asset)
        for entry in self.scene.owned(asset):
            entry.visible = asset.visible

    def remove(self, asset):
        """Removes an asset; removing a custom object keeps its members, they are drawn on their own again."""
        if not self.can_remove(asset):
            raise ProjectError(f"{asset.name} cannot be removed: {self.lock_text(asset)}.")
        self._remove(asset)

    def _remove(self, asset):
        for child in list(asset.children):
            self._remove(child)
        for custom in self.users_of(asset):
            custom.members.remove(asset)
            if custom.members:
                self._build_custom(custom)
            else:
                self._remove(custom)
        self.scene.remove_owner(asset)
        self.bindings = [(s, t) for s, t in self.bindings if s is not asset and t is not asset]
        self.layers = [layer for layer in self.layers if layer.asset is not asset]
        siblings = self.siblings(asset)
        if asset in siblings:
            siblings.remove(asset)
        if self.reference is asset:
            self.reference = next(iter(self.scripts()), None)
        if self.map is asset:
            self.map = None
        self._member_visibility()
        self.touch()

    # Custom objects

    def create_custom(self, name, members, index=None, lock=None):
        members = [member for member in members if self.groupable(member)]
        if not members:
            raise ProjectError("A custom object needs models from the top level of the assets.")
        name = name.strip()
        if not assets_module.valid_name(name):
            raise ProjectError(f"'{name}' is not a valid name.")
        if any(other.name.lower() == name.lower() for other in self.items):
            raise ProjectError(f"An asset named {name} already exists.")
        # An animation of a part goes to the object, which draws the part from now on
        inherited = next((self.animation_of(member) for member in members if self.animation_of(member)), None)
        for member in members:
            for source, target in list(self.bindings):
                if target is member:
                    self.unbind(source, target)
        custom = CustomObject(name, members)
        custom.lock = lock
        if inherited is not None:
            self.bindings.append((inherited, custom))
        if index is None:
            index = len(self.items)
        self.items.insert(min(index, len(self.items)), custom)
        self._build_custom(custom)
        self.touch()
        return custom

    def _build_custom(self, custom):
        """Entries of its own for each member: the same archive can be in several objects with other animations."""
        source = self.animation_of(custom)
        self.scene.remove_owner(custom)
        for member in custom.members:
            for part in member.model.walk():
                if part.bones or part.meshes or part.animations:
                    entry = Entry(part, getattr(member, "engine", None) or self.engine_id, owner=custom)
                    entry.visible = custom.visible
                    self.scene.entries.append(entry)
        # One character: the body takes the skin color of the head
        group = self.scene.owned(custom)
        for entry in group:
            entry.skin_group = group
        if source is not None:
            self.bindings = [(s, t) for s, t in self.bindings if t is not custom]
            self.bind(source, custom)
        self._member_visibility()
        self.scene.revision += 1

    def _member_visibility(self):
        """An archive used by a custom object is drawn by that object only."""
        for asset in self.items:
            if asset.kind == assets_module.CUSTOM:
                continue
            visible = asset.visible and not self.users_of(asset)
            for entry in self.scene.owned(asset):
                entry.visible = visible

    def replace(self, old, name, data, source_path=None):
        """New file in place of old (same position); the links that still make sense are kept.

        Returns (new asset, [links that could not be kept])."""
        error = self.check_replace(old, name, data)
        if error:
            raise ProjectError(error)
        siblings = self.siblings(old)
        index = siblings.index(old)
        users = self.users_of(old)
        animation = self.animation_of(old)
        targets = self.bound_targets(old)
        child_targets = {child.name.lower(): (child.name, self.bound_targets(child)) for child in old.children}
        # A template links the archives of its package again from its slots
        relink = self.template is not None and bool(self.template.slots) and old.is_package
        if relink:
            child_targets = {}
        was_reference = self.reference is old
        was_map = self.map is old
        layer = self.layer_of(old)

        gone = [old] + old.children
        for each in gone:
            self.scene.remove_owner(each)
        self.bindings = [(s, t) for s, t in self.bindings if s not in gone and t not in gone]
        siblings.remove(old)
        if layer is not None:
            layer_index = self.layers.index(layer)
            self.layers.remove(layer)
        if was_reference:
            self.reference = None
        new = self.add(name, data, old.parent, index=index, source_path=source_path)
        new.lock = old.lock
        new.role = old.role
        if layer is not None and new.is_picture:
            # The new picture takes the place of the old one in the timeline, with its height
            layer.asset = new
            layer.width = new.image.shape[1] * layer.height / new.image.shape[0]
            self.layers.insert(layer_index, layer)

        lost = []
        for custom in users:
            position = custom.members.index(old)
            if self.groupable(new):
                custom.members[position] = new
            else:
                custom.members.pop(position)
                lost.append(f"{new.name} cannot be a part of {custom.name}")
        for custom in users:
            if custom.members:
                self._build_custom(custom)
            else:
                self._remove(custom)
                lost.append(f"{custom.name} was removed: it has no part left")
        if animation is not None:
            if self.animatable(new):
                self.set_animation(new, animation)
            else:
                lost.append(f"{new.name} cannot be animated by {animation.name}")
        if targets:
            if new.kind == assets_module.ANIMATION:
                for target in targets:
                    self.set_animation(target, new)
            else:
                lost.append(f"{new.name} is not an animation: {', '.join(t.name for t in targets)} lost it")
        # A package keeps the links of the archives it still has, by name
        for child in new.children:
            child_name, linked = child_targets.pop(child.name.lower(), (None, []))
            for target in linked:
                if child.kind == assets_module.ANIMATION:
                    self.set_animation(target, child)
                else:
                    lost.append(f"{child.name} is not an animation any more: {target.name} lost it")
        for child_name, linked in child_targets.values():
            for target in linked:
                lost.append(f"{child_name} is not in {new.name}: {target.name} lost its animation")
        if was_reference:
            self.reference = new if new.kind == assets_module.SQUIRREL else next(iter(self.scripts()), None)
        if was_map:
            self.map = new if self.can_be_map(new) else None
        if relink or (self.template is not None and new.is_package):
            self.auto_bind()
        self._member_visibility()
        self.touch()
        return new, lost

    def rename(self, asset, name):
        name = name.strip()
        if name == asset.name:
            return
        if not self.can_rename(asset):
            raise ProjectError(f"{asset.name} cannot be renamed: the template needs its name.")
        if not assets_module.valid_name(name):
            raise ProjectError(f"'{name}' is not a valid file name.")
        if any(other is not asset and other.name.lower() == name.lower() for other in self.siblings(asset)):
            raise ProjectError(f"An asset named {name} already exists.")
        if asset.kind == assets_module.CUSTOM and any(other.name.lower() == name.lower() for other in self.items if other is not asset):
            raise ProjectError(f"An asset named {name} already exists.")
        asset.name = name
        if asset.model is not None:
            asset.model.name = name
        self.scene.revision += 1
        self.touch()

    def set_visible(self, asset, visible):
        asset.visible = visible
        for entry in self.scene.owned(asset):
            entry.visible = visible
        self._member_visibility()
        for camera in self.scene.cameras:
            if camera.owner is asset:
                camera.enabled = visible
        self.scene.revision += 1

    def write_script(self, asset, source):
        """Keeps the new source of a script and writes its file: the text for a .nut, compiled for a .nutb.

        Raises SquirrelError (the compiler message) when it does not compile; nothing is changed then."""
        compiled = squirrel.compile(source, asset.name)
        if asset.source_path:
            with open(asset.source_path, "wb") as stream:
                stream.write(compiled if asset.extension == ".nutb" else source)
        asset.data = source
        asset.error = None
        self.touch()

    def set_reference(self, asset):
        if asset is not self.reference:
            self.reference = asset
            self.touch()

    def can_be_map(self, asset):
        """An asset with meshes of its own (an archive, a package): it can carry the map label."""
        return asset.kind != assets_module.CUSTOM and any(entry.has_meshes for each in [asset] + asset.children
                                                          for entry in self.scene.owned(each))

    def set_map(self, asset):
        """Labels an asset as the map, None clears the label: a project has one map, the label moves to the new one."""
        if asset is not None and not self.can_be_map(asset):
            raise ProjectError(f"{asset.name} has no model, it cannot be the map.")
        if asset is not self.map:
            self.map = asset
            self.touch()

    def map_entries(self):
        """Entries of the asset labelled map (of its archives for a package), drawn before the queue of the others."""
        if self.map is None:
            return set()
        return {entry for each in [self.map] + self.map.children for entry in self.scene.owned(each)}

    # Animation bindings

    def bind(self, source, target):
        """The animations of the source archive also drive the target archive (by node hashes)."""
        if source is target or (source, target) in self.bindings:
            return False
        sources = [entry for entry in self.scene.owned(source) if entry.own_animations]
        targets = self.scene.owned(target)
        if not sources or not targets:
            return False
        for entry in targets:
            for animated in sources:
                self.scene.bind(entry, animated)
        self.bindings.append((source, target))
        self.touch()
        return True

    def set_animation(self, target, source):
        """The one animation of a custom object or an archive with an armature; None removes it."""
        if self.animation_of(target) is source and (source is None or (source, target) in self.bindings):
            return False
        for s, t in list(self.bindings):
            if t is target:
                self.unbind(s, t)
        if source is not None:
            self.bind(source, target)
        self.touch()
        return True

    def unbind(self, source, target=None):
        sources = self.scene.owned(source)
        for s, t in list(self.bindings):
            if s is source and (target is None or t is target):
                for entry in self.scene.owned(t):
                    for animated in sources:
                        self.scene.unbind(entry, animated)
                self.bindings.remove((s, t))
                self.touch()

    # Layers: the pictures of the timeline

    def pictures(self):
        return [asset for asset in self.items if asset.is_picture]

    def layer_of(self, asset):
        return next((layer for layer in self.layers if layer.asset is asset), None)

    def add_layer(self, asset, start=0, duration=DEFAULT_LAYER_DURATION):
        """Puts a picture in the timeline (once): native size, centred on the screen."""
        if not asset.is_picture:
            raise ProjectError(f"{asset.name} is not a picture.")
        layer = self.layer_of(asset)
        if layer is not None:
            return layer
        layer = Layer(asset, max(0, int(start)), duration)
        layer.fit()
        self.layers.append(layer)
        self.touch()
        return layer

    def remove_layer(self, layer):
        if layer in self.layers:
            self.layers.remove(layer)
            self.touch()

    def move_layer(self, layer, step):
        """Draw order: +1 brings it one layer forward (drawn later), -1 sends it back."""
        index = self.layers.index(layer)
        target = max(0, min(len(self.layers) - 1, index + step))
        if target != index:
            self.layers.insert(target, self.layers.pop(index))
            self.touch()

    def layers_at(self, frame):
        """Layers drawn at a frame, the bottom one first."""
        return [layer for layer in self.layers if layer.shown_at(frame) and layer.asset.image is not None]

    def layers_end(self):
        return max((layer.end for layer in self.layers), default=0)

    # Engine

    def set_engine(self, engine_id):
        if engine_id != self.engine_id:
            self.scene.set_engine_all(engine_id)
            self.touch()

    # Scripts

    def check_script(self, asset):
        """Compiles the source of a script, the error is kept on the asset."""
        if squirrel.is_compiled(asset.data):
            return asset.error
        asset.error = None
        try:
            squirrel.compile(asset.data, asset.name)
        except squirrel.SquirrelUnavailable:
            pass
        except squirrel.SquirrelError as error:
            asset.error = str(error)
        return asset.error

    # Splits

    def splits(self):
        """[(name, first frame, last frame)] of the scene: those that start the earliest, first archive in import order.

        When the script of a template orders the cameras, its cuts, named after their motion."""
        if self.scene.scripted():
            motions = self._scripted().motions
            cameras = self.scene.played_cameras()
            return [(motions[i], start, start + camera.frame_count)
                    for i, (start, camera) in enumerate(zip(self.scene.camera_starts(), cameras))]
        best = None
        for asset in self.walk():
            for entry in self.scene.owned(asset):
                for splits in entry.model.splits.values():
                    if not splits:
                        continue
                    ordered = sorted(splits, key=lambda split: split["start"])
                    if best is None or ordered[0]["start"] < best[0]["start"]:
                        best = ordered
        return [(split["name"], int(split["start"]), int(split["end"])) for split in best or []]

    # Saving

    def to_json(self):
        def entry(asset):
            data = {"name": asset.name, "kind": asset.kind}
            if asset.lock:
                data["lock"] = asset.lock
            if asset.role:
                data["role"] = asset.role
            if asset.source_path:
                data["source"] = asset.source_path
            if asset.is_package:
                data["archives"] = [{"name": child.name, "kind": child.kind} for child in asset.children]
            return data

        files = [asset for asset in self.items if asset.kind != assets_module.CUSTOM]
        data = {
            "format": FORMAT,
            "version": VERSION,
            "engine": self.engine_id,
            "assets": [entry(asset) for asset in files],
            "custom_objects": [dict({"name": custom.name, "position": self.items.index(custom),
                                     "members": [member.path for member in custom.members]},
                                    **({"lock": custom.lock} if custom.lock else {})) for custom in self.customs()],
            "reference_script": self.reference.path if self.reference is not None else None,
            "map": self.map.path if self.map is not None else None,
            "bindings": [{"animation": source.path, "model": target.path} for source, target in self.bindings],
            "layers": [layer.to_json() for layer in self.layers],
        }
        if self.template is not None:
            data["template"] = self.template.to_json()
        return data

    def save(self, path):
        folder = os.path.dirname(os.path.abspath(path))
        handle, temporary = tempfile.mkstemp(prefix=".srp_", suffix=EXTENSION, dir=folder)
        os.close(handle)
        try:
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                for asset in self.items:
                    if asset.kind != assets_module.CUSTOM:
                        archive.writestr("assets/" + asset.name, asset.packed())
                for asset in self.walk():
                    if asset.thumbnail:
                        archive.writestr(_preview_path(asset), asset.thumbnail)
                archive.writestr("project.json", json.dumps(self.to_json(), indent=2))
            os.replace(temporary, path)
        except BaseException:
            if os.path.exists(temporary):
                os.remove(temporary)
            raise
        # The packages hold their renamed and removed archives from now on
        for asset in self.items:
            if asset.is_package:
                asset.data = asset.packed()
        self.path = path
        self.dirty = False

    @classmethod
    def read_json(cls, path):
        try:
            with zipfile.ZipFile(path) as archive:
                return json.loads(archive.read("project.json").decode("utf-8"))
        except (OSError, KeyError, ValueError, zipfile.BadZipFile) as error:
            raise ProjectError(f"{os.path.basename(path)} is not a studio_render project: {error}")

    @classmethod
    def load(cls, path, check_scripts=True):
        data = cls.read_json(path)
        written = str(data.get("version", "0.0.0"))
        if parse(written) > parse(VERSION):
            raise ProjectVersionError(f"This project was written by studio_render {written}, update studio_render to open it.")
        data = migrate(data)

        project = cls(data.get("engine") or "IE4")
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            for item in data.get("assets", []):
                name = item["name"]
                if "assets/" + name not in names:
                    print(f"[studio_render] {path}: assets/{name} is missing")
                    continue
                asset = project.add(name, archive.read("assets/" + name), source_path=item.get("source"))
                asset.lock = item.get("lock") if item.get("lock") in assets_module.LOCKS else None
                asset.role = item.get("role") if skill_script.parse_role(item.get("role")) else None
                if asset.is_package:
                    kinds = {a["name"]: a.get("kind") for a in item.get("archives", [])}
                    order = list(kinds)
                    asset.children.sort(key=lambda child: order.index(child.name) if child.name in order else len(order))
                    for child in asset.children:
                        if kinds.get(child.name) in assets_module.KINDS:
                            child.kind = kinds[child.name]
                for each in [asset] + asset.children:
                    preview = f"preview/{each.path}.png"
                    if preview in names:
                        each.thumbnail = archive.read(preview)
                if asset.kind == assets_module.SQUIRREL and check_scripts:
                    project.check_script(asset)

            for item in data.get("custom_objects", []):
                members = [project.find(path) for path in item.get("members", [])]
                try:
                    lock = item.get("lock") if item.get("lock") in assets_module.LOCKS else None
                    custom = project.create_custom(item["name"], [m for m in members if m is not None], item.get("position"), lock)
                except (ProjectError, KeyError) as error:
                    print(f"[studio_render] {path}: custom object skipped ({error})")
                    continue
                if _preview_path(custom) in names:
                    custom.thumbnail = archive.read(_preview_path(custom))

        map_path = data.get("map")
        project.map = project.find(map_path) if map_path else None
        if project.map is not None and not project.can_be_map(project.map):
            project.map = None
        reference = data.get("reference_script")
        project.reference = project.find(reference) if reference else None
        if project.reference is None or project.reference.kind != assets_module.SQUIRREL:
            project.reference = next(iter(project.scripts()), None)
        for binding in data.get("bindings", []):
            source, target = project.find(binding.get("animation", "")), project.find(binding.get("model", ""))
            if source is not None and target is not None:
                project.bind(source, target)
        for item in data.get("layers", []):
            asset = project.find(str(item.get("asset", "")))
            if asset is None or not asset.is_picture or project.layer_of(asset) is not None:
                continue
            try:
                layer = Layer(asset, item.get("start", 0), item.get("duration", DEFAULT_LAYER_DURATION))
                layer.fit()
                for key in ("x", "y", "width", "height"):
                    if key in item:
                        setattr(layer, key, float(item[key]))
                layer.visible = bool(item.get("visible", True))
            except (TypeError, ValueError) as error:
                print(f"[studio_render] {path}: layer of {asset.name} skipped ({error})")
                continue
            project.layers.append(layer)
        if isinstance(data.get("template"), dict):
            project.template = Template(data["template"])
        project.path = path
        if project.template is not None and project.template.official:
            # The template handed out stays as it is: what is made from it is saved as a new project
            project.template.official = False
            project.path = None
        project.dirty = False
        return project
