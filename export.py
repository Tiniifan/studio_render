"""Export of assets to common formats: models and skeletons to .dae / .fbx / .obj, pictures to .png.

No Qt here (the window only asks for the path and shows the result), no dependency beyond numpy and Pillow.
asset_formats(asset) says what an asset offers, export_asset(asset, path) writes it; export_models() and
export_picture() / export_pictures() work from decoded archives and pixels.

What is exported, from the decoded archives (model.Model, children archives included):
- every mesh with its normals, its first UV layer (uv0) and its material; each material references the first texture
  of its texture units, written as a .png next to the exported file (unique, sanitized names);
- the nodes (.mbn) as one skeleton: each joint has its rest transform (location, rotation, scale) relative to its parent,
  so the world rest matrices are the ones studio_render poses from (world = parent world x T x R x S);
- skinned meshes with their weights, single-bind meshes (a whole mesh on one node) as meshes skinned to that node with a
  weight of 1, so that animating the node moves them in every format; the other meshes are static.

Bind pose. The inverse bind matrices of the .mbn are not always the inverse of the rest pose (effects often differ), and
a joint can have a scale (even 0, for effects revealed by an animation). As studio_eleven does when it imports into
Blender, the exported bind pose of a joint is its world rest matrix without scale (world rest location, product of the
rest rotations), the inverse bind matrices are its inverse, and the vertices are moved from the bind pose of the file to
that one (each vertex by the blend of its nodes). The rest pose of the joints (their transforms) keeps the scale, so an
importer shows the model as studio_render does at rest (exactly for single-bind meshes and unscaled skeletons).
- .fbx: that bind pose (cluster TransformLink, BindPose), joints with their rest scale (Blender puts it on the pose bones,
  as studio_eleven does);
- .dae: Blender's Collada importer ignores the rest transform of the joints when it differs from the bind pose, so the
  rest scale is baked: the bind pose is the world rest matrix itself (scale included, the scale-free one for a node
  scaled to 0) and the vertices are written as drawn at rest. Each joint also gets Blender's <extra> tip and roll,
  without which Blender's importer loses the rest rotations (and the bones of nodes scaled to 0);
- .obj has no skeleton: it holds the static geometry as studio_render draws it at rest.

Several archives (children of an archive, members of a custom object) share one skeleton, as one animation drives them
all in the game: nodes of the same hash (same name) are one joint, with the rest pose of the first archive that has it
(the body before the head of a player); the vertices of the others are bound to it from their own inverse binds.
Joint, mesh and material names are made unique.

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
import re
import struct
import zlib
from xml.sax.saxutils import escape, quoteattr

import numpy as np
from PIL import Image

import animation
import assets
import images

MODEL_FORMATS = (".dae", ".fbx", ".obj")
SKELETON_FORMATS = (".dae", ".fbx")
PICTURE_FORMATS = (".png",)
FORMAT_NAMES = {".dae": "Collada", ".fbx": "FBX (binary)", ".obj": "Wavefront OBJ", ".png": "PNG image"}

_EPSILON = 1e-9


class ExportError(Exception):
    pass


def _models(models):
    return [part for model in models if model is not None for part in model.walk()]


# Assets of a project (assets.Asset): what the Export entry offers and does


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
    parts = _models(models)
    if any(len(part.meshes) for part in parts):
        return MODEL_FORMATS
    if any(part.bones for part in parts):
        return SKELETON_FORMATS
    return ()


def file_filter(extensions):
    """Filter string of a save dialog for these extensions."""
    return ";;".join(f"{FORMAT_NAMES.get(e, e)} (*{e})" for e in extensions)


# Names


def file_name(name, fallback="file"):
    """A name usable as a file name on every system."""
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(name)).strip(" .")
    return cleaned or fallback


class _Names:
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


def _identifier(name):
    """An XML NCName (Collada ids and sids)."""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    if not cleaned or not (cleaned[0].isalpha() or cleaned[0] == "_"):
        cleaned = "_" + cleaned
    return cleaned


def _object_name(name):
    """A name for FBX (no control characters, the \\x00\\x01 separator of the format)."""
    return re.sub(r"[\x00-\x1f]", "_", str(name)) or "unnamed"


# Pictures


def write_png(rgba, path, bottom_row_first=False):
    """Writes RGBA uint8 pixels (height, width, 4) to a .png file."""
    pixels = np.ascontiguousarray(np.asarray(rgba, dtype=np.uint8)[::-1] if bottom_row_first else np.asarray(rgba, dtype=np.uint8))
    try:
        Image.fromarray(pixels, "RGBA").save(path, "PNG")
    except OSError as error:
        raise ExportError(f"{os.path.basename(path)} could not be written: {error}")
    return path


def export_picture(rgba, path):
    """A picture of the player (image file, text), RGBA top row first, as a .png. Returns [path]."""
    if rgba is None:
        raise ExportError("the picture could not be read")
    if not path.lower().endswith(".png"):
        raise ExportError("a picture is exported as .png")
    return [write_png(rgba, path)]


def export_pictures(pictures, path):
    """(name, RGBA top row first) pictures, an image archive, as .png: the first one at path, the others next to it as
    <stem>_<name>.png. Returns the written paths."""
    if not path.lower().endswith(".png"):
        raise ExportError("a picture is exported as .png")
    if not pictures:
        raise ExportError("the archive holds no readable image")
    folder, stem = os.path.dirname(path), os.path.splitext(os.path.basename(path))[0]
    names = _Names()
    names.get(stem)
    written = [write_png(pictures[0][1], path)]
    for name, rgba in pictures[1:]:
        other = names.get(f"{stem}_{file_name(os.path.splitext(name)[0], 'image')}")
        written.append(write_png(rgba, os.path.join(folder, other + ".png")))
    return written


# The scene to export, the same for every format


class _Joint:
    def __init__(self, name, parent, local, world, bind):
        self.name = name
        self.parent = parent        # index of the parent joint or None
        self.local = local          # rest transform relative to the parent (4x4, column vectors)
        self.world = world          # world rest matrix
        self.bind = bind            # world bind matrix: world rest without scale
        # world rest matrix when it can be inverted (a node scaled to 0 cannot), the bind pose of the .dae
        self.rest_bind = world if abs(np.linalg.det(world[:3, :3])) > _EPSILON else bind
        self.children = []
        self.location = local[:3, 3].copy()
        self.rotation = np.identity(3)
        self.scale = np.ones(3)


class _Part:
    """A mesh ready to write."""

    def __init__(self, name):
        self.name = name
        self.positions = None       # in the bind pose (skin formats)
        self.normals = None
        self.rest_positions = None  # as drawn at rest (.obj)
        self.rest_normals = None
        self.uv = None
        self.triangles = None
        self.rest_triangles = None  # turned the other way when the rest pose mirrors the mesh
        self.material = None        # index in scene.materials
        self.joints = None          # (n, k) joint indices, None for a static mesh
        self.weights = None         # (n, k)


class _Material:
    def __init__(self, name, texture):
        self.name = name
        self.texture = texture      # index in scene.textures or None


class _Texture:
    def __init__(self, name, rgba, file):
        self.name = name
        self.rgba = rgba            # bottom row first
        self.file = file            # file name next to the export


def _normalized(vectors):
    vectors = np.asarray(vectors, dtype=np.float64)
    lengths = np.linalg.norm(vectors, axis=1, keepdims=True)
    lengths[lengths < _EPSILON] = 1.0
    return vectors / lengths


def _transform(matrix, positions, normals):
    positions = positions @ matrix[:3, :3].T + matrix[:3, 3]
    linear = matrix[:3, :3]
    if abs(np.linalg.det(linear)) > _EPSILON:
        normals = normals @ np.linalg.inv(linear)
    else:
        normals = normals @ linear.T
    return positions, _normalized(normals)


def _blend(matrices, joints, weights, positions, normals):
    """Each vertex moved by the weighted blend of its matrices (linear blend skinning)."""
    totals = weights.sum(axis=1, keepdims=True)
    totals[totals < _EPSILON] = 1.0
    blended = np.einsum("nk,nkij->nij", weights / totals, matrices[joints])
    moved = np.einsum("nij,nj->ni", blended[:, :3, :3], positions) + blended[:, :3, 3]
    turned = np.einsum("nij,nj->ni", blended[:, :3, :3], normals)
    return moved, _normalized(turned)


class Scene:
    """Joints, meshes, materials and textures of the archives to export, shared by the writers."""

    def __init__(self, models, name):
        self.name = name
        self.joints = []
        self.parts = []
        self.materials = []
        self.textures = []
        self._texture_index = {}
        self._material_index = {}
        self._joint_names = _Names()
        self._part_names = _Names()
        self._material_names = _Names()
        self._texture_files = _Names()
        by_hash = {}
        for model in _models(models):
            mapping = self._add_bones(model, by_hash)
            for mesh in model.meshes:
                self._add_mesh(model, mesh, mapping)
        for index, joint in enumerate(self.joints):
            if joint.parent is not None:
                self.joints[joint.parent].children.append(index)

    @property
    def roots(self):
        return [index for index, joint in enumerate(self.joints) if joint.parent is None]

    @property
    def skinned(self):
        return any(part.joints is not None for part in self.parts)

    def _add_bones(self, model, by_hash):
        """Joint index of every node of the archive; nodes already there (same hash, same rest place) are shared."""
        bones = model.bones
        mapping = {}

        def visit(index, chain=()):
            if index in mapping:
                return mapping[index]
            bone = bones[index]
            if bone.hash in by_hash:
                # Same node as in an archive before (one animation drives them all): its joint, its rest pose
                mapping[index] = by_hash[bone.hash]
                return mapping[index]
            parent = bone.parent if bone.parent is not None and bone.parent not in chain else None
            parent_joint = visit(parent, chain + (index,)) if parent is not None else None
            rotation = animation.quaternion_matrix(*bone.rotation[1:], bone.rotation[0])
            local = animation.trs(bone.location, rotation, bone.scale)
            world, turn = local, rotation
            if parent_joint is not None:
                world = self.joints[parent_joint].world @ local
                turn = self.joints[parent_joint].bind[:3, :3] @ rotation
            bind = np.identity(4)
            bind[:3, :3] = turn
            bind[:3, 3] = world[:3, 3]
            joint = _Joint(self._joint_names.get(_object_name(bone.name or f"node_{index}")), parent_joint, local, world, bind)
            joint.rotation = np.array(bone.rotation, dtype=np.float64)     # w, x, y, z
            joint.scale = np.array(bone.scale, dtype=np.float64)
            self.joints.append(joint)
            mapping[index] = by_hash[bone.hash] = len(self.joints) - 1
            return mapping[index]

        for bone in bones:
            visit(bone.index)
        return mapping

    def _texture(self, texture):
        if texture is None:
            return None
        key = id(texture)
        if key not in self._texture_index:
            file = self._texture_files.get(file_name(texture.name, "texture")) + ".png"
            self.textures.append(_Texture(texture.name, texture.rgba, file))
            self._texture_index[key] = len(self.textures) - 1
        return self._texture_index[key]

    def _material(self, model, name):
        key = (id(model), name)
        if key not in self._material_index:
            material = model.materials.get(name)
            texture = None
            if material is not None:
                texture = next((t for t, _ in material.textures if t is not None), None)
            self.materials.append(_Material(self._material_names.get(_object_name(name or "material")), self._texture(texture)))
            self._material_index[key] = len(self.materials) - 1
        return self._material_index[key]

    def _add_mesh(self, model, mesh, mapping):
        if mesh.positions is None or not len(mesh.positions) or mesh.triangles is None or not len(mesh.triangles):
            return
        part = _Part(self._part_names.get(_object_name(mesh.name or f"mesh_{mesh.index}")))
        positions = np.asarray(mesh.positions, dtype=np.float64)
        normals = _normalized(mesh.normals if mesh.normals is not None else np.tile((0.0, 1.0, 0.0), (len(positions), 1)))
        triangles = np.asarray(mesh.triangles, dtype=np.int64)
        count = len(positions)
        triangles = triangles[(triangles < count).all(axis=1)]
        part.uv = np.asarray(mesh.uv[0], dtype=np.float64) if mesh.uv[0] is not None and len(mesh.uv[0]) == count else None
        part.material = self._material(model, mesh.material_name)
        bones = model.bones

        if mesh.bone_indices is not None and mesh.weights is not None and bones:
            local = np.clip(np.asarray(mesh.bone_indices, dtype=np.int64), 0, len(bones) - 1)
            weights = np.asarray(mesh.weights, dtype=np.float64)
            # Bind pose of the file -> exported bind pose; game rest pose (world x inverse bind of the file) for .obj
            to_bind = np.stack([self.joints[mapping[i]].bind @ bones[i].inverse_bind for i in range(len(bones))])
            to_rest = np.stack([self.joints[mapping[i]].world @ bones[i].inverse_bind for i in range(len(bones))])
            part.positions, part.normals = _blend(to_bind, local, weights, positions, normals)
            part.rest_positions, part.rest_normals = _blend(to_rest, local, weights, positions, normals)
            part.joints = np.vectorize(lambda i: mapping[int(i)], otypes=[np.int64])(local)
            part.weights = weights
        elif mesh.single_bind is not None and bones:
            joint_index = mapping[mesh.single_bind]
            joint = self.joints[joint_index]
            part.positions, part.normals = _transform(joint.bind, positions, normals)
            # A node scaled to 0 at rest draws nothing, .obj and .dae keep the mesh at the size of the bind pose
            part.rest_positions, part.rest_normals = _transform(joint.rest_bind, positions, normals)
            if np.linalg.det(joint.rest_bind[:3, :3]) < 0:
                part.rest_triangles = triangles[:, ::-1]
            part.joints = np.full((count, 1), joint_index, dtype=np.int64)
            part.weights = np.ones((count, 1))
        else:
            part.positions, part.normals = positions, normals
            part.rest_positions, part.rest_normals = positions, normals
        part.triangles = triangles
        if part.rest_triangles is None:
            part.rest_triangles = triangles
        if len(triangles):
            self.parts.append(part)

    def used_joints(self, part):
        """Sorted joints with a weight in the mesh."""
        if part.joints is None:
            return []
        return sorted({int(j) for j, w in zip(part.joints.ravel(), part.weights.ravel()) if w > 0.0})

    def write_textures(self, folder):
        paths = []
        for texture in self.textures:
            paths.append(write_png(texture.rgba, os.path.join(folder, texture.file), bottom_row_first=True))
        return paths


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
            written = _write_obj(scene, path)
        elif extension == ".dae":
            written = _write_dae(scene, path)
        else:
            written = _write_fbx(scene, path)
    except OSError as error:
        raise ExportError(f"{os.path.basename(path)} could not be written: {error}")
    return written + scene.write_textures(folder)


# Wavefront OBJ


def _number(value):
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _write_obj(scene, path):
    stem = os.path.splitext(os.path.basename(path))[0]
    mtl_path = os.path.join(os.path.dirname(path), stem + ".mtl")
    lines = ["# studio_render export", f"mtllib {os.path.basename(mtl_path)}"]
    offset = 1
    for part in scene.parts:
        lines.append(f"o {part.name}")
        lines.extend(f"v {_number(x)} {_number(y)} {_number(z)}" for x, y, z in part.rest_positions)
        if part.uv is not None:
            lines.extend(f"vt {_number(u)} {_number(v)}" for u, v in part.uv)
        lines.extend(f"vn {_number(x)} {_number(y)} {_number(z)}" for x, y, z in part.rest_normals)
        if part.material is not None:
            lines.append(f"usemtl {scene.materials[part.material].name.replace(' ', '_')}")
        for triangle in part.rest_triangles + offset:
            if part.uv is not None:
                lines.append("f " + " ".join(f"{i}/{i}/{i}" for i in triangle))
            else:
                lines.append("f " + " ".join(f"{i}//{i}" for i in triangle))
        offset += len(part.rest_positions)
    with open(path, "w", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines) + "\n")

    used = sorted({part.material for part in scene.parts if part.material is not None})
    materials = ["# studio_render export"]
    for index in used:
        material = scene.materials[index]
        materials += ["", f"newmtl {material.name.replace(' ', '_')}", "Ka 0 0 0", "Kd 1 1 1", "Ks 0 0 0", "d 1", "illum 1"]
        if material.texture is not None:
            materials.append(f"map_Kd {scene.textures[material.texture].file}")
    with open(mtl_path, "w", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(materials) + "\n")
    return [path, mtl_path]


# Collada 1.4.1


def _floats(values):
    return " ".join(_number(v) for v in np.asarray(values, dtype=np.float64).ravel())


def _matrix_text(matrix):
    # Collada matrices are written row by row, for column vectors like numpy here
    return " ".join(repr(float(v)) for v in np.asarray(matrix, dtype=np.float64).ravel())


def _bone_lengths(scene):
    """A display length per joint: the distance to its children, else half the one of its parent."""
    heads = np.array([joint.bind[:3, 3] for joint in scene.joints]) if scene.joints else np.zeros((0, 3))
    extent = float(np.ptp(heads, axis=0).max()) if len(heads) > 1 else 1.0
    default = max(extent * 0.05, 1e-3)
    lengths = [None] * len(scene.joints)

    def visit(index, inherited):
        joint = scene.joints[index]
        distances = [np.linalg.norm(heads[child] - heads[index]) for child in joint.children]
        distances = [d for d in distances if d > 1e-4]
        lengths[index] = float(np.mean(distances)) if distances else inherited
        for child in joint.children:
            visit(child, max(lengths[index] * 0.5, 1e-3))

    for root in scene.roots:
        visit(root, default)
    return lengths


def _blender_roll(rotation):
    """Roll of a Blender bone whose matrix is this rotation (its Y axis is the bone direction): the angle around Y
    between the matrix Blender makes for that direction with roll 0 (vec_roll_to_mat3) and this one."""
    x, y, z = rotation[:, 1] / (np.linalg.norm(rotation[:, 1]) or 1.0)
    theta, theta_alt = 1.0 + y, x * x + z * z
    if theta > 6.1e-3 or theta_alt > 2.5e-4 ** 2:
        if theta <= 6.1e-3:
            theta = theta_alt * 0.5 + theta_alt * theta_alt * 0.125
        base = np.array([[1 - x * x / theta, x, -x * z / theta],
                         [-x, y, -z],
                         [-x * z / theta, z, 1 - z * z / theta]])
    else:
        base = np.diag((-1.0, -1.0, 1.0))
    roll = base.T @ rotation
    return float(np.arctan2(roll[0, 2], roll[2, 2]))


def _write_dae(scene, path):
    out = ['<?xml version="1.0" encoding="utf-8"?>',
           '<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">',
           "  <asset>", "    <contributor><authoring_tool>studio_render</authoring_tool></contributor>",
           "    <created>1970-01-01T00:00:00</created>", "    <modified>1970-01-01T00:00:00</modified>",
           '    <unit name="meter" meter="1"/>', "    <up_axis>Y_UP</up_axis>", "  </asset>"]

    sids = _Names()
    joint_ids = [sids.get(_identifier(joint.name)) for joint in scene.joints]

    if scene.textures:
        out.append("  <library_images>")
        for index, texture in enumerate(scene.textures):
            out.append(f'    <image id="image-{index}" name={quoteattr(texture.name)}><init_from>{escape(texture.file)}</init_from></image>')
        out.append("  </library_images>")

    used_materials = sorted({part.material for part in scene.parts if part.material is not None})
    if used_materials:
        out.append("  <library_effects>")
        for index in used_materials:
            material = scene.materials[index]
            out.append(f'    <effect id="effect-{index}"><profile_COMMON>')
            if material.texture is not None:
                image = f"image-{material.texture}"
                out += [f'      <newparam sid="{image}-surface"><surface type="2D"><init_from>{image}</init_from></surface></newparam>',
                        f'      <newparam sid="{image}-sampler"><sampler2D><source>{image}-surface</source></sampler2D></newparam>',
                        '      <technique sid="common"><lambert>',
                        f'        <diffuse><texture texture="{image}-sampler" texcoord="UVMap"/></diffuse>',
                        "      </lambert></technique>"]
            else:
                out += ['      <technique sid="common"><lambert>',
                        "        <diffuse><color>1 1 1 1</color></diffuse>", "      </lambert></technique>"]
            out.append("    </profile_COMMON></effect>")
        out.append("  </library_effects>")
        out.append("  <library_materials>")
        for index in used_materials:
            out.append(f'    <material id="material-{index}" name={quoteattr(scene.materials[index].name)}>'
                       f'<instance_effect url="#effect-{index}"/></material>')
        out.append("  </library_materials>")

    if scene.parts:
        out.append("  <library_geometries>")
        for index, part in enumerate(scene.parts):
            geometry = f"geometry-{index}"
            count = len(part.rest_positions)
            out += [f'    <geometry id="{geometry}" name={quoteattr(part.name)}><mesh>',
                    f'      <source id="{geometry}-positions"><float_array id="{geometry}-positions-array" count="{count * 3}">'
                    f'{_floats(part.rest_positions)}</float_array>',
                    f'        <technique_common><accessor source="#{geometry}-positions-array" count="{count}" stride="3">'
                    '<param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/>'
                    "</accessor></technique_common></source>",
                    f'      <source id="{geometry}-normals"><float_array id="{geometry}-normals-array" count="{count * 3}">'
                    f'{_floats(part.rest_normals)}</float_array>',
                    f'        <technique_common><accessor source="#{geometry}-normals-array" count="{count}" stride="3">'
                    '<param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/>'
                    "</accessor></technique_common></source>"]
            if part.uv is not None:
                out += [f'      <source id="{geometry}-uv"><float_array id="{geometry}-uv-array" count="{count * 2}">'
                        f'{_floats(part.uv)}</float_array>',
                        f'        <technique_common><accessor source="#{geometry}-uv-array" count="{count}" stride="2">'
                        '<param name="S" type="float"/><param name="T" type="float"/></accessor></technique_common></source>']
            out.append(f'      <vertices id="{geometry}-vertices"><input semantic="POSITION" source="#{geometry}-positions"/></vertices>')
            material = ' material="material"' if part.material is not None else ""
            out += [f'      <triangles{material} count="{len(part.rest_triangles)}">',
                    f'        <input semantic="VERTEX" source="#{geometry}-vertices" offset="0"/>',
                    f'        <input semantic="NORMAL" source="#{geometry}-normals" offset="0"/>']
            if part.uv is not None:
                out.append(f'        <input semantic="TEXCOORD" source="#{geometry}-uv" offset="0" set="0"/>')
            out += [f"        <p>{' '.join(str(int(i)) for i in part.rest_triangles.ravel())}</p>", "      </triangles>",
                    "    </mesh></geometry>"]
        out.append("  </library_geometries>")

    skins = [index for index, part in enumerate(scene.parts) if part.joints is not None]
    if skins:
        out.append("  <library_controllers>")
        for index in skins:
            part = scene.parts[index]
            controller = f"controller-{index}"
            used = scene.used_joints(part)
            local = {joint: slot for slot, joint in enumerate(used)}
            binds = " ".join(_matrix_text(np.linalg.inv(scene.joints[j].rest_bind)) for j in used)
            weights, counts, pairs = [], [], []
            for joints_row, weights_row in zip(part.joints, part.weights):
                kept = [(int(j), float(w)) for j, w in zip(joints_row, weights_row) if w > 0.0]
                counts.append(len(kept))
                for joint, weight in kept:
                    pairs += [local[joint], len(weights)]
                    weights.append(weight)
            out += [f'    <controller id="{controller}" name={quoteattr(part.name)}><skin source="#geometry-{index}">',
                    f"      <bind_shape_matrix>{_matrix_text(np.identity(4))}</bind_shape_matrix>",
                    f'      <source id="{controller}-joints"><Name_array id="{controller}-joints-array" count="{len(used)}">'
                    f"{' '.join(joint_ids[j] for j in used)}</Name_array>",
                    f'        <technique_common><accessor source="#{controller}-joints-array" count="{len(used)}" stride="1">'
                    '<param name="JOINT" type="name"/></accessor></technique_common></source>',
                    f'      <source id="{controller}-binds"><float_array id="{controller}-binds-array" count="{16 * len(used)}">'
                    f"{binds}</float_array>",
                    f'        <technique_common><accessor source="#{controller}-binds-array" count="{len(used)}" stride="16">'
                    '<param name="TRANSFORM" type="float4x4"/></accessor></technique_common></source>',
                    f'      <source id="{controller}-weights"><float_array id="{controller}-weights-array" count="{len(weights)}">'
                    f"{_floats(weights)}</float_array>",
                    f'        <technique_common><accessor source="#{controller}-weights-array" count="{len(weights)}" stride="1">'
                    '<param name="WEIGHT" type="float"/></accessor></technique_common></source>',
                    f'      <joints><input semantic="JOINT" source="#{controller}-joints"/>'
                    f'<input semantic="INV_BIND_MATRIX" source="#{controller}-binds"/></joints>',
                    f'      <vertex_weights count="{len(counts)}">',
                    f'        <input semantic="JOINT" source="#{controller}-joints" offset="0"/>',
                    f'        <input semantic="WEIGHT" source="#{controller}-weights" offset="1"/>',
                    f"        <vcount>{' '.join(map(str, counts))}</vcount>",
                    f"        <v>{' '.join(map(str, pairs))}</v>",
                    "      </vertex_weights>", "    </skin></controller>"]
        out.append("  </library_controllers>")

    out += ["  <library_visual_scenes>", f'    <visual_scene id="scene" name={quoteattr(scene.name)}>']

    lengths = _bone_lengths(scene)

    def joint_node(index, depth):
        joint = scene.joints[index]
        pad = "  " * depth
        # Blender's importer builds a bone from the head of the node, a tip and a roll: without them it keeps neither
        # the rest rotation nor the bones of a node scaled to 0. The bone points along the Y axis of the bind pose.
        tip = joint.bind[:3, 1] * lengths[index]
        lines = [f'{pad}<node id="{joint_ids[index]}" name={quoteattr(joint.name)} sid="{joint_ids[index]}" type="JOINT">',
                 f'{pad}  <matrix sid="transform">{_matrix_text(joint.local)}</matrix>']
        for child in joint.children:
            lines += joint_node(child, depth + 1)
        lines.append(f'{pad}  <extra><technique profile="blender"><connect sid="connect" type="bool">0</connect>'
                     f'<roll sid="roll" type="float">{repr(_blender_roll(joint.bind[:3, :3]))}</roll>'
                     f'<tip_x sid="tip_x" type="float">{repr(float(tip[0]))}</tip_x>'
                     f'<tip_y sid="tip_y" type="float">{repr(float(tip[1]))}</tip_y>'
                     f'<tip_z sid="tip_z" type="float">{repr(float(tip[2]))}</tip_z></technique></extra>')
        lines.append(f"{pad}</node>")
        return lines

    if scene.joints:
        armature = sids.get(_identifier(scene.name or "Armature"))
        out.append(f'      <node id="{armature}" name={quoteattr(scene.name or "Armature")} type="NODE">')
        for root in scene.roots:
            out += joint_node(root, 4)
        out.append("      </node>")
    for index, part in enumerate(scene.parts):
        node = sids.get(_identifier(part.name))
        out.append(f'      <node id="{node}" name={quoteattr(part.name)} type="NODE">')
        bind = ""
        if part.material is not None:
            bind = ('<bind_material><technique_common><instance_material symbol="material" '
                    f'target="#material-{part.material}"><bind_vertex_input semantic="UVMap" input_semantic="TEXCOORD" '
                    'input_set="0"/></instance_material></technique_common></bind_material>')
        if part.joints is not None:
            skeletons = "".join(f"<skeleton>#{joint_ids[root]}</skeleton>" for root in scene.roots)
            out.append(f'        <instance_controller url="#controller-{index}">{skeletons}{bind}</instance_controller>')
        else:
            out.append(f'        <instance_geometry url="#geometry-{index}">{bind}</instance_geometry>')
        out.append("      </node>")
    out += ["    </visual_scene>", "  </library_visual_scenes>",
            '  <scene><instance_visual_scene url="#scene"/></scene>', "</COLLADA>"]
    with open(path, "w", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(out) + "\n")
    return [path]


# Binary FBX 7.4


class _Property:
    def __init__(self, code, value):
        self.code = code
        self.value = value


def _i(value):
    return _Property("I", int(value))


def _l(value):
    return _Property("L", int(value))


def _d(value):
    return _Property("D", float(value))


def _c(value):
    return _Property("C", bool(value))


def _r(value):
    return _Property("R", bytes(value))


def _array(code, values):
    dtype = {"d": "<f8", "i": "<i4", "f": "<f4", "l": "<i8"}[code]
    return _Property(code, np.ascontiguousarray(np.asarray(values).ravel(), dtype=dtype))


def _encode_property(prop):
    if isinstance(prop, str):
        prop = _Property("S", prop)
    elif isinstance(prop, bytes):
        prop = _Property("S", prop)
    elif isinstance(prop, bool):
        prop = _c(prop)
    elif isinstance(prop, int):
        prop = _i(prop)
    elif isinstance(prop, float):
        prop = _d(prop)
    code, value = prop.code, prop.value
    if code == "S" or code == "R":
        data = value.encode("utf-8") if isinstance(value, str) else value
        return code.encode() + struct.pack("<I", len(data)) + data
    if code == "I":
        return b"I" + struct.pack("<i", value)
    if code == "L":
        return b"L" + struct.pack("<q", value)
    if code == "D":
        return b"D" + struct.pack("<d", value)
    if code == "C":
        return b"C" + struct.pack("<B", 1 if value else 0)
    data = value.tobytes()
    if len(data) > 256:
        packed = zlib.compress(data)
        return code.encode() + struct.pack("<III", len(value), 1, len(packed)) + packed
    return code.encode() + struct.pack("<III", len(value), 0, len(data)) + data


class _Node:
    def __init__(self, name, *props, children=None):
        self.name = name
        self.props = props
        self.children = list(children or [])

    def add(self, name, *props, children=None):
        node = _Node(name, *props, children=children)
        self.children.append(node)
        return node

    def encode(self, offset):
        name = self.name.encode("ascii")
        props = b"".join(_encode_property(p) for p in self.props)
        position = offset + 13 + len(name) + len(props)
        blocks = []
        for child in self.children:
            data = child.encode(position)
            blocks.append(data)
            position += len(data)
        if self.children or not self.props:
            blocks.append(b"\0" * 13)
            position += 13
        head = struct.pack("<IIIB", position, len(self.props), len(props), len(name))
        return head + name + props + b"".join(blocks)


_FBX_MAGIC = b"Kaydara FBX Binary  \x00\x1a\x00"
_FBX_VERSION = 7400
# Fixed values every exporter writes (the file id goes with this creation time)
_FBX_TIME = "1970-01-01 10:00:00:000"
_FBX_FILE_ID = b"\x28\xb3\x2a\xeb\xb6\x24\xcc\xc2\xbf\xc8\xb0\x2a\xa9\x2b\xfc\xf1"
_FBX_FOOT_ID = b"\xfa\xbc\xab\x09\xd0\xc8\xd4\x66\xb1\x76\xfb\x83\x1c\xf7\x26\x7e"
_FBX_FOOT_MAGIC = b"\xf8\x5a\x8c\x6a\xde\xf5\xd9\x7e\xec\xe9\x0c\xe3\x75\x8f\x29\x0b"


def _fbx_name(name, kind):
    return _object_name(name) + "\x00\x01" + kind


def _p(name, kind, label, flags, *values):
    return _Node("P", name, kind, label, flags, *values)


def _p_vector(name, kind, values, flags="A"):
    return _p(name, kind, "", flags, *(_d(v) for v in values))


def _euler_xyz(rotation):
    """FBX Euler XYZ angles (degrees) of a rotation matrix: R = Rz x Ry x Rx."""
    sy = -rotation[2, 0]
    sy = min(1.0, max(-1.0, sy))
    y = np.arcsin(sy)
    if abs(sy) < 0.999999:
        x = np.arctan2(rotation[2, 1], rotation[2, 2])
        z = np.arctan2(rotation[1, 0], rotation[0, 0])
    else:
        x = np.arctan2(-rotation[1, 2], rotation[1, 1])
        z = 0.0
    return np.degrees((x, y, z))


def _fbx_matrix(matrix):
    # FBX stores matrices column by column
    return _array("d", np.asarray(matrix, dtype=np.float64).T)


def _write_fbx(scene, path):
    ids = iter(range(1_000_000, 1 << 40))
    objects = _Node("Objects")
    connections = _Node("Connections")
    counts = {}

    def count(kind, n=1):
        counts[kind] = counts.get(kind, 0) + n

    def connect(child, parent, prop=None):
        if prop is None:
            connections.add("C", "OO", _l(child), _l(parent))
        else:
            connections.add("C", "OP", _l(child), _l(parent), prop)

    def model_node(uid, name, kind, location=(0, 0, 0), rotation=(0, 0, 0), scale=(1, 1, 1)):
        properties = _Node("Properties70", children=[
            _p("InheritType", "enum", "", "", _i(0)),
            _p("DefaultAttributeIndex", "int", "Integer", "", _i(0)),
            _p_vector("Lcl Translation", "Lcl Translation", location),
            _p_vector("Lcl Rotation", "Lcl Rotation", rotation),
            _p_vector("Lcl Scaling", "Lcl Scaling", scale)])
        node = objects.add("Model", _l(uid), _fbx_name(name, "Model"), kind, children=[
            _Node("Version", _i(232)), properties, _Node("Shading", _c(True)), _Node("Culling", "CullingOff")])
        count("Model")
        return node

    folder = os.path.dirname(os.path.abspath(path))

    # Textures and materials
    video_ids, texture_ids = {}, {}
    for index, texture in enumerate(scene.textures):
        video, tex = next(ids), next(ids)
        absolute = os.path.join(folder, texture.file)
        objects.add("Video", _l(video), _fbx_name(texture.name, "Video"), "Clip", children=[
            _Node("Type", "Clip"),
            _Node("Properties70", children=[_p("Path", "KString", "XRefUrl", "", absolute)]),
            _Node("UseMipMap", _i(0)), _Node("Filename", absolute), _Node("RelativeFilename", texture.file)])
        objects.add("Texture", _l(tex), _fbx_name(texture.name, "Texture"), "", children=[
            _Node("Type", "TextureVideoClip"), _Node("Version", _i(202)),
            _Node("TextureName", _fbx_name(texture.name, "Texture")),
            _Node("Properties70", children=[_p("UseMaterial", "bool", "", "", _i(1))]),
            _Node("Media", _fbx_name(texture.name, "Video")), _Node("FileName", absolute),
            _Node("RelativeFilename", texture.file), _Node("ModelUVTranslation", _d(0), _d(0)),
            _Node("ModelUVScaling", _d(1), _d(1)), _Node("Texture_Alpha_Source", "None"),
            _Node("Cropping", _i(0), _i(0), _i(0), _i(0))])
        connect(video, tex)
        video_ids[index], texture_ids[index] = video, tex
        count("Video")
        count("Texture")

    material_ids = {}
    for index in sorted({part.material for part in scene.parts if part.material is not None}):
        material = scene.materials[index]
        uid = next(ids)
        objects.add("Material", _l(uid), _fbx_name(material.name, "Material"), "", children=[
            _Node("Version", _i(102)), _Node("ShadingModel", "lambert"), _Node("MultiLayer", _i(0)),
            _Node("Properties70", children=[
                _p_vector("DiffuseColor", "Color", (1.0, 1.0, 1.0)),
                _p("DiffuseFactor", "Number", "", "A", _d(1.0)),
                _p_vector("AmbientColor", "Color", (0.0, 0.0, 0.0)),
                _p_vector("EmissiveColor", "Color", (0.0, 0.0, 0.0))])])
        if material.texture is not None:
            connect(texture_ids[material.texture], uid, "DiffuseColor")
        material_ids[index] = uid
        count("Material")

    # Skeleton: a null (the armature) holding the joints
    joint_ids = []
    if scene.joints:
        armature = next(ids)
        model_node(armature, scene.name or "Armature", "Null")
        attribute = next(ids)
        objects.add("NodeAttribute", _l(attribute), _fbx_name(scene.name or "Armature", "NodeAttribute"), "Null",
                    children=[_Node("TypeFlags", "Null")])
        count("NodeAttribute")
        connect(attribute, armature)
        connect(armature, 0)
        joint_ids = [next(ids) for _ in scene.joints]
        for index, joint in enumerate(scene.joints):
            rotation = animation.quaternion_matrix(*joint.rotation[1:], joint.rotation[0])
            model_node(joint_ids[index], joint.name, "LimbNode", joint.location, _euler_xyz(rotation), joint.scale)
            attribute = next(ids)
            objects.add("NodeAttribute", _l(attribute), _fbx_name(joint.name, "NodeAttribute"), "LimbNode", children=[
                _Node("Properties70", children=[_p("Size", "double", "Number", "", _d(1.0))]),
                _Node("TypeFlags", "Skeleton")])
            count("NodeAttribute")
            connect(attribute, joint_ids[index])
            connect(joint_ids[index], joint_ids[joint.parent] if joint.parent is not None else armature)

    # Meshes
    mesh_ids = []
    for part in scene.parts:
        geometry, model = next(ids), next(ids)
        mesh_ids.append(model)
        corners = part.triangles.reshape(-1)
        polygon = part.triangles.astype(np.int64).copy()
        polygon[:, 2] = -polygon[:, 2] - 1
        layers = [_Node("Version", _i(100)),
                  _Node("LayerElement", children=[_Node("Type", "LayerElementNormal"), _Node("TypedIndex", _i(0))])]
        children = [
            _Node("Properties70"), _Node("GeometryVersion", _i(124)),
            _Node("Vertices", _array("d", part.positions)),
            _Node("PolygonVertexIndex", _array("i", polygon)),
            _Node("LayerElementNormal", _i(0), children=[
                _Node("Version", _i(102)), _Node("Name", ""), _Node("MappingInformationType", "ByPolygonVertex"),
                _Node("ReferenceInformationType", "Direct"), _Node("Normals", _array("d", part.normals[corners]))])]
        if part.uv is not None:
            children.append(_Node("LayerElementUV", _i(0), children=[
                _Node("Version", _i(101)), _Node("Name", "UVMap"), _Node("MappingInformationType", "ByPolygonVertex"),
                _Node("ReferenceInformationType", "IndexToDirect"), _Node("UV", _array("d", part.uv)),
                _Node("UVIndex", _array("i", corners))]))
            layers.append(_Node("LayerElement", children=[_Node("Type", "LayerElementUV"), _Node("TypedIndex", _i(0))]))
        if part.material is not None:
            children.append(_Node("LayerElementMaterial", _i(0), children=[
                _Node("Version", _i(101)), _Node("Name", ""), _Node("MappingInformationType", "AllSame"),
                _Node("ReferenceInformationType", "IndexToDirect"), _Node("Materials", _array("i", [0]))]))
            layers.append(_Node("LayerElement", children=[_Node("Type", "LayerElementMaterial"), _Node("TypedIndex", _i(0))]))
        children.append(_Node("Layer", _i(0), children=layers))
        objects.add("Geometry", _l(geometry), _fbx_name(part.name, "Geometry"), "Mesh", children=children)
        count("Geometry")
        model_node(model, part.name, "Mesh")
        connect(geometry, model)
        connect(model, 0)
        if part.material is not None:
            connect(material_ids[part.material], model)

        if part.joints is not None:
            skin = next(ids)
            objects.add("Deformer", _l(skin), _fbx_name(part.name, "Deformer"), "Skin", children=[
                _Node("Version", _i(101)), _Node("Link_DeformAcuracy", _d(50.0))])
            count("Deformer")
            connect(skin, geometry)
            flat_joints, flat_weights = part.joints, part.weights
            vertices = np.repeat(np.arange(len(flat_joints)), flat_joints.shape[1])
            flat_joints, flat_weights = flat_joints.ravel(), flat_weights.ravel()
            for joint in scene.used_joints(part):
                chosen = (flat_joints == joint) & (flat_weights > 0.0)
                cluster = next(ids)
                bind = scene.joints[joint].bind
                objects.add("Deformer", _l(cluster), _fbx_name("", "SubDeformer"), "Cluster", children=[
                    _Node("Version", _i(100)), _Node("UserData", "", ""),
                    _Node("Indexes", _array("i", vertices[chosen])), _Node("Weights", _array("d", flat_weights[chosen])),
                    _Node("Transform", _fbx_matrix(np.linalg.inv(bind))), _Node("TransformLink", _fbx_matrix(bind))])
                count("Deformer")
                connect(cluster, skin)
                connect(joint_ids[joint], cluster)

    if scene.joints:
        pose = next(ids)
        nodes = [_Node("PoseNode", children=[_Node("Node", _l(joint_ids[index])), _Node("Matrix", _fbx_matrix(joint.bind))])
                 for index, joint in enumerate(scene.joints)]
        nodes += [_Node("PoseNode", children=[_Node("Node", _l(uid)), _Node("Matrix", _fbx_matrix(np.identity(4)))])
                  for uid, part in zip(mesh_ids, scene.parts) if part.joints is not None]
        objects.add("Pose", _l(pose), _fbx_name("BIND_POSES", "Pose"), "BindPose", children=[
            _Node("Type", "BindPose"), _Node("Version", _i(100)), _Node("NbPoseNodes", _i(len(nodes)))] + nodes)
        count("Pose")

    definitions = _Node("Definitions", children=[_Node("Version", _i(100)), _Node("Count", _i(sum(counts.values()) + 1)),
                                                  _Node("ObjectType", "GlobalSettings", children=[_Node("Count", _i(1))])])
    for kind, number in counts.items():
        definitions.add("ObjectType", kind, children=[_Node("Count", _i(number))])

    document = next(ids)
    root = [
        _Node("FBXHeaderExtension", children=[
            _Node("FBXHeaderVersion", _i(1003)), _Node("FBXVersion", _i(_FBX_VERSION)), _Node("EncryptionType", _i(0)),
            _Node("CreationTimeStamp", children=[_Node("Version", _i(1000)), _Node("Year", _i(1970)), _Node("Month", _i(1)),
                                                 _Node("Day", _i(1)), _Node("Hour", _i(10)), _Node("Minute", _i(0)),
                                                 _Node("Second", _i(0)), _Node("Millisecond", _i(0))]),
            _Node("Creator", "studio_render")]),
        _Node("FileId", _r(_FBX_FILE_ID)),
        _Node("CreationTime", _FBX_TIME),
        _Node("Creator", "studio_render"),
        _Node("GlobalSettings", children=[_Node("Version", _i(1000)), _Node("Properties70", children=[
            _p("UpAxis", "int", "Integer", "", _i(1)), _p("UpAxisSign", "int", "Integer", "", _i(1)),
            _p("FrontAxis", "int", "Integer", "", _i(2)), _p("FrontAxisSign", "int", "Integer", "", _i(1)),
            _p("CoordAxis", "int", "Integer", "", _i(0)), _p("CoordAxisSign", "int", "Integer", "", _i(1)),
            _p("OriginalUpAxis", "int", "Integer", "", _i(1)), _p("OriginalUpAxisSign", "int", "Integer", "", _i(1)),
            _p("UnitScaleFactor", "double", "Number", "", _d(100.0)),
            _p("OriginalUnitScaleFactor", "double", "Number", "", _d(100.0)),
            _p_vector("AmbientColor", "ColorRGB", (0.0, 0.0, 0.0), flags="Color"),
            _p("DefaultCamera", "KString", "", "", "Producer Perspective"),
            _p("TimeMode", "enum", "", "", _i(6)),
            _p("TimeSpanStart", "KTime", "Time", "", _l(0)), _p("TimeSpanStop", "KTime", "Time", "", _l(46186158000)),
            _p("CustomFrameRate", "double", "Number", "", _d(30.0))])]),
        _Node("Documents", children=[_Node("Count", _i(1)), _Node("Document", _l(document), "Scene", "Scene", children=[
            _Node("Properties70", children=[_p("SourceObject", "object", "", ""),
                                            _p("ActiveAnimStackName", "KString", "", "", "")]),
            _Node("RootNode", _l(0))])]),
        _Node("References"),
        definitions,
        objects,
        connections,
        _Node("Takes", children=[_Node("Current", "")]),
    ]

    data = bytearray(_FBX_MAGIC + struct.pack("<I", _FBX_VERSION))
    for node in root:
        data += node.encode(len(data))
    data += b"\0" * 13
    data += _FBX_FOOT_ID + b"\0" * 4
    padding = ((len(data) + 15) & ~15) - len(data)
    data += b"\0" * (padding or 16)
    data += struct.pack("<I", _FBX_VERSION) + b"\0" * 120 + _FBX_FOOT_MAGIC
    with open(path, "wb") as stream:
        stream.write(bytes(data))
    return [path]
