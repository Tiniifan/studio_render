"""The scene to export, the same for every format: joints, meshes, materials and textures of decoded archives.

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
"""
import os

import numpy as np

import animation

from .common import EPSILON, Names, file_name, object_name, write_png


def models_of(models):
    return [part for model in models if model is not None for part in model.walk()]


class _Joint:
    def __init__(self, name, parent, local, world, bind):
        self.name = name
        self.parent = parent        # index of the parent joint or None
        self.local = local          # rest transform relative to the parent (4x4, column vectors)
        self.world = world          # world rest matrix
        self.bind = bind            # world bind matrix: world rest without scale
        # world rest matrix when it can be inverted (a node scaled to 0 cannot), the bind pose of the .dae
        self.rest_bind = world if abs(np.linalg.det(world[:3, :3])) > EPSILON else bind
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
    lengths[lengths < EPSILON] = 1.0
    return vectors / lengths


def _transform(matrix, positions, normals):
    positions = positions @ matrix[:3, :3].T + matrix[:3, 3]
    linear = matrix[:3, :3]
    if abs(np.linalg.det(linear)) > EPSILON:
        normals = normals @ np.linalg.inv(linear)
    else:
        normals = normals @ linear.T
    return positions, _normalized(normals)


def _blend(matrices, joints, weights, positions, normals):
    """Each vertex moved by the weighted blend of its matrices (linear blend skinning)."""
    totals = weights.sum(axis=1, keepdims=True)
    totals[totals < EPSILON] = 1.0
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
        self._joint_names = Names()
        self._part_names = Names()
        self._material_names = Names()
        self._texture_files = Names()
        by_hash = {}
        for model in models_of(models):
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
            joint = _Joint(self._joint_names.get(object_name(bone.name or f"node_{index}")), parent_joint, local, world, bind)
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
            self.materials.append(_Material(self._material_names.get(object_name(name or "material")), self._texture(texture)))
            self._material_index[key] = len(self.materials) - 1
        return self._material_index[key]

    def _add_mesh(self, model, mesh, mapping):
        if mesh.positions is None or not len(mesh.positions) or mesh.triangles is None or not len(mesh.triangles):
            return
        part = _Part(self._part_names.get(object_name(mesh.name or f"mesh_{mesh.index}")))
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
