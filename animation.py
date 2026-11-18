"""Evaluation of the animations studio_eleven reads (AnimationManager), the pose of the nodes and the cameras.

Rules (studio_eleven/.docs/formats/animations.md and mbn.md, also what IEGORender reproduces):
- keys are linear, the group 3 of a track (UVMove, BoneBool) holds its key;
- after the last key the next key is the first one again, at first key + frame count;
- a V1 node has a frame -> key table, the game reads it at the truncated frame instead of searching the keys;
- every frame a node starts from its rest TRS, a track replaces its channel, world = parent world x local;
- skinning palette = world x inverse bind of the .mbn.
Animations are bound to nodes, texture projections and materials by hash, so an animation of one archive (a player
animation of a technique) can drive the model of another archive.
"""
import math

import numpy as np

BONE_TRACKS = {"BoneLocation": "location", "BoneRotation": "rotation", "BoneScale": "scale", "BoneBool": "visible"}
UV_TRACKS = {"UVMove": "move", "UVRotate": "rotate", "UVScale": "scale"}
MATERIAL_TRACKS = {"MaterialTransparency": "transparency", "MaterialAttribute": "attribute"}
HELD_GROUP = 3


def quaternion_matrix(x, y, z, w):
    n = x * x + y * y + z * z + w * w
    s = 2.0 / n if n > 1e-12 else 0.0
    return np.array([
        [1 - s * (y * y + z * z), s * (x * y - z * w), s * (x * z + y * w)],
        [s * (x * y + z * w), 1 - s * (x * x + z * z), s * (y * z - x * w)],
        [s * (x * z - y * w), s * (y * z + x * w), 1 - s * (x * x + y * y)],
    ])


def trs(location, rotation3, scale):
    matrix = np.identity(4)
    matrix[:3, :3] = rotation3 * np.asarray(scale, dtype=np.float64)[None, :]
    matrix[:3, 3] = location
    return matrix


def _value(frame_value):
    data = vars(frame_value) if hasattr(frame_value, "__dict__") else {"value": frame_value}
    if "transparency" in data:
        return [float(data["transparency"])]
    order = [key for key in ("X", "Y", "Z", "W") if key in data]
    if order:
        return [float(data[key]) for key in order]
    return [float(value) for value in data.values()]


def _blend(a, b, t, quaternion):
    if quaternion:
        if np.dot(a, b) < 0.0:
            b = -b
        q = a + (b - a) * t
        return q / (np.linalg.norm(q) or 1.0)
    return a + (b - a) * t


class Channel:
    def __init__(self, keys, values, hold, table=None, period=None):
        self.keys = np.asarray(keys, dtype=np.float64)
        self.values = np.asarray(values, dtype=np.float64)
        self.hold = hold
        self.table = list(table) if table is not None else None
        self.period = period

    def evaluate(self, frame, frame_count, quaternion=False):
        keys, values = self.keys, self.values
        if len(keys) == 1:
            return values[0]

        if self.table:
            position = min(max(int(frame), 0), len(self.table) - 1)
            index = min(int(self.table[position]), len(keys) - 1)
            if self.hold:
                return values[index]
            following = index + 1 if index + 1 < len(keys) else min(int(self.table[0]), len(keys) - 1)
            start, end = keys[index], keys[following]
            if start == end:
                return values[index]
            if end < start:
                end += self.period if self.period is not None else frame_count + 1
            t = (frame - start) / (end - start)
            return _blend(values[index], values[following], t, quaternion)

        index = int(np.searchsorted(keys, frame, side="right")) - 1
        if index < 0:
            return values[0]
        if index >= len(keys) - 1:
            start, end, a, b = keys[-1], keys[0] + frame_count, values[-1], values[0]
        else:
            start, end, a, b = keys[index], keys[index + 1], values[index], values[index + 1]
        if self.hold or end <= start:
            return a
        t = min(max((frame - start) / (end - start), 0.0), 1.0)
        return _blend(a, b, t, quaternion)


class Animation:
    """One animation file: channels by (target hash, channel name)."""

    def __init__(self, manager, source):
        self.name = manager.AnimationName
        self.source = source
        self.frame_count = int(manager.FrameCount or 0)
        self.kind = None
        self.channels = {}

        for track in manager.Tracks:
            for table, kind in ((BONE_TRACKS, "bone"), (UV_TRACKS, "uv"), (MATERIAL_TRACKS, "material")):
                if track.Name in table:
                    self.kind = kind
                    channel_name = table[track.Name]
                    break
            else:
                continue

            for node in track.Nodes:
                if not node.Frames:
                    continue
                keys = [frame.Key for frame in node.Frames]
                values = [_value(frame.Value) for frame in node.Frames]
                self.channels[(node.Name, channel_name)] = Channel(
                    keys, values, track.Index == HELD_GROUP, getattr(node, "FrameTable", None), getattr(node, "TableLength", None))

    def value(self, target_hash, channel, frame):
        found = self.channels.get((target_hash, channel))
        if found is None:
            return None
        return found.evaluate(frame, self.frame_count, quaternion=channel == "rotation")

    def targets(self):
        return {target for target, _ in self.channels}


POSE_CACHE_SIZE = 1024


class Pose:
    def __init__(self, model):
        self.model = model
        count = len(model.bones)
        self.world = np.tile(np.identity(4), (count, 1, 1))
        self.visible = np.ones(count, dtype=bool)
        self.order = self._parents_first()
        self.rest_rotation = [quaternion_matrix(*bone.rotation[1:], bone.rotation[0]) for bone in model.bones]
        self.rest_local = [trs(bone.location, self.rest_rotation[bone.index], bone.scale) for bone in model.bones]
        self.inverse_binds = np.stack([bone.inverse_bind for bone in model.bones]) if count else None
        # A billboard node turns to the camera: its pose depends on the view and is not kept
        self.camera_dependent = any(bone.billboard == 1 for bone in model.bones)
        self._plans = {}
        self._frames = {}
        self._palette = None
        self._slot = [self.world, self.visible, None]

    def _parents_first(self):
        order, placed = [], set()
        bones = self.model.bones
        while len(order) < len(bones):
            progress = False
            for bone in bones:
                if bone.index not in placed and (bone.parent is None or bone.parent in placed):
                    order.append(bone.index)
                    placed.add(bone.index)
                    progress = True
            if not progress:
                order.extend(b.index for b in bones if b.index not in placed)
                break
        return order

    def _plan(self, animations):
        """Per node, the channels that drive it: the last animation that has a channel wins, as it replaces the others."""
        key = tuple(id(animation) for animation in animations)
        plan = self._plans.get(key)
        if plan is None:
            plan = []
            for bone in self.model.bones:
                channels = {}
                for animation in animations:
                    for name in ("location", "rotation", "scale", "visible"):
                        channel = animation.channels.get((bone.hash, name))
                        if channel is not None:
                            channels[name] = (channel, animation.frame_count)
                plan.append(channels)
            self._plans = {key: plan}
            self._frames.clear()
        return key, plan

    def update(self, animations, frame, camera_rotation=None):
        bones = self.model.bones
        if not bones:
            return self.world
        key, plan = self._plan(animations)
        kept = not self.camera_dependent or camera_rotation is None
        if kept:
            found = self._frames.get((key, frame))
            if found is not None:
                self.world, self.visible, self._palette = found
                self._slot = found
                return self.world

        world = np.tile(np.identity(4), (len(bones), 1, 1))
        visible = np.ones(len(bones), dtype=bool)
        for index in self.order:
            bone = bones[index]
            channels = plan[index]
            if channels:
                location, rotation, scale = bone.location, self.rest_rotation[index], bone.scale
                found = channels.get("location")
                if found is not None:
                    location = found[0].evaluate(frame, found[1])[:3]
                found = channels.get("rotation")
                if found is not None:
                    rotation = quaternion_matrix(*found[0].evaluate(frame, found[1], quaternion=True)[:4])
                found = channels.get("scale")
                if found is not None:
                    scale = found[0].evaluate(frame, found[1])[:3]
                found = channels.get("visible")
                if found is not None:
                    visible[index] = found[0].evaluate(frame, found[1])[0] != 0
                local = trs(location, rotation, scale)
            else:
                location, rotation, scale = bone.location, self.rest_rotation[index], bone.scale
                local = self.rest_local[index]

            if bone.billboard == 1 and camera_rotation is not None:
                turn = np.identity(4)
                turn[:3, :3] = camera_rotation
                local = trs(location, np.identity(3), (1.0, 1.0, 1.0)) @ turn @ trs((0.0, 0.0, 0.0), rotation, scale)
            world[index] = world[bone.parent] @ local if bone.parent is not None else local

        self.world, self.visible, self._palette = world, visible, None
        self._slot = [world, visible, None]
        if kept:
            if len(self._frames) >= POSE_CACHE_SIZE:
                self._frames.pop(next(iter(self._frames)))
            self._frames[(key, frame)] = self._slot
        return self.world

    def palette(self):
        if not self.model.bones:
            return None
        if self._palette is None:
            self._palette = np.matmul(self.world, self.inverse_binds)
            self._slot[2] = self._palette
        return self._palette


def skin(mesh, palette):
    indices = np.clip(mesh.bone_indices, 0, len(palette) - 1)
    # The four weighted node matrices are summed first, then each vertex is transformed once
    blended = np.einsum("nk,nkij->nij", mesh.weights, palette[indices][:, :, :3, :])
    skinned = np.einsum("nij,nj->ni", blended[:, :, :3], mesh.positions) + blended[:, :, 3]
    normals = np.einsum("nij,nj->ni", blended[:, :, :3], mesh.normals)
    return skinned.astype(np.float32), normals.astype(np.float32)


_uv_rows = {}


def uv_rows(move=(0.0, 0.0), scale=(1.0, 1.0), rotate=0.0):
    """Rows of unf_vtx_txt for a texture projection, in the V of studio_eleven (v = 1 - v of the file).

    The game (sub_478F8C): A = [[sx cos r, sx sin r], [-sy sin r, sy cos r]], uv' = ([u - mx, v - my - 1] . inv(A)) + [0, 1]
    in the V of the file.
    """
    key = (move[0], move[1], scale[0], scale[1], rotate)
    found = _uv_rows.get(key)
    if found is not None:
        return found
    mx, my = move
    sx = scale[0] if abs(scale[0]) > 1e-8 else 1e-8
    sy = scale[1] if abs(scale[1]) > 1e-8 else 1e-8
    cos, sin = math.cos(rotate), math.sin(rotate)
    a, b, c, d = sx * cos, sx * sin, -sy * sin, sy * cos
    determinant = a * d - b * c
    i00, i01, i10, i11 = d / determinant, -b / determinant, -c / determinant, a / determinant
    row_x = (i00, -i10, 0.0, -mx * i00 - my * i10)
    row_y = (-i01, i11, 0.0, mx * i01 + my * i11)
    if len(_uv_rows) > 65536:
        _uv_rows.clear()
    _uv_rows[key] = (row_x, row_y)
    return row_x, row_y


IDENTITY_UV = ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0))


class CameraTrack:
    """A baked camera (cmr2): location, aim, roll and vertical field of view (radians) per frame."""

    def __init__(self, data):
        self.hash = data.get("hash")
        self.frame_count = int(data.get("frame_count") or 0)
        values = data.get("values", {})
        self.location = values.get("location", {})
        self.aim = values.get("aim", {})
        self.fov = values.get("focal_length", {})
        self.roll = values.get("roll", {})

    @staticmethod
    def _sample(table, frame, default):
        if not table:
            return default
        keys = sorted(table)
        frame = min(max(frame, keys[0]), keys[-1])
        low = max(k for k in keys if k <= frame)
        high = min(k for k in keys if k >= frame)
        a, b = np.asarray(table[low], dtype=np.float64), np.asarray(table[high], dtype=np.float64)
        if high == low:
            return a
        return a + (b - a) * (frame - low) / (high - low)

    def at(self, frame):
        location = self._sample(self.location, frame, np.array((0.0, 0.0, 10.0)))
        aim = self._sample(self.aim, frame, np.zeros(3))
        fov = float(self._sample(self.fov, frame, np.array(0.6)))
        roll = float(self._sample(self.roll, frame, np.array(0.0)))
        return location, aim, fov, roll
