"""What is on screen: the loaded archives (entries), the animations that drive them and the cameras.

Each archive with nodes, meshes, animations or cameras becomes an entry; a .pck gives one entry per archive it holds.
An entry plays its own animations and, optionally, the animations of another entry (a technique keeps the player
animations in archives without meshes: whs0001_aa1.xc drives the model of the attacker).
"""
import math
import os
import zlib

import numpy as np

import animation, eleven
import model as model_module


class DrawItem:
    __slots__ = ("model", "mesh", "matrix", "positions", "normals", "tint", "uv_rows", "outline", "entry", "bucket",
                 "bucket_index", "model_index")


SKIN_TEXTURE_PREFIX = "uas"     # grey skin texture of the bodies (uas0001), tinted by PRIMARY_COLOR (#FIX_TON_12)
PALETTE_SCALE = 0.25            # the mask palette of a head is RGB / 255 x 0.25 (renderer.PALETTE_SCALE)


def is_skin_material(material):
    return material is not None and any(texture is not None and texture.name.lower().startswith(SKIN_TEXTURE_PREFIX)
                                        for texture, _ in material.textures)


class Entry:
    def __init__(self, model, engine_id, path=None, owner=None):
        self.model = model
        self.engine_id = eleven.drawing_engine(engine_id, model)
        self.path = path
        self.owner = owner       # what added the entry (an asset of the window), None from the command line
        self.visible = True
        self.skin_group = None   # entries of one character (head + body + studs): the body takes the skin of the head
        self.skin_meshes = {index for index, mesh in enumerate(model.meshes)
                            if is_skin_material(model.materials.get(mesh.material_name))}
        self.pose = animation.Pose(model)
        self.own_animations = [animation.Animation(manager, model.name)
                               for managers in model.animations.values() for manager in managers]
        self.bound = []          # entries whose animations also drive this one
        self.material_index = {h: i for i, h in enumerate(model.material_order)}
        self.texprojs = {}
        for texproj_hash, material_name, uv_index in model.texprojs:
            self.texprojs.setdefault((material_name, uv_index), texproj_hash)
        self.outline_of = {}
        for outline in model.outlines:
            for mesh_hash in outline["mesh_hashes"]:
                self.outline_of.setdefault(mesh_hash, outline)
        self.bucket_index = []
        counts = {}
        for mesh in model.meshes:
            self.bucket_index.append(counts.get(mesh.bucket, 0))
            counts[mesh.bucket] = counts.get(mesh.bucket, 0) + 1
        # What evaluate needs of each mesh, in draw order: the game files the meshes in 64 draw priority buckets,
        # a mesh past them is never drawn
        self.draw_order = []
        for index in sorted(range(len(model.meshes)), key=lambda i: (model.meshes[i].bucket, self.bucket_index[i])):
            mesh = model.meshes[index]
            if mesh.bucket >= 64:
                continue
            node = mesh.single_bind if mesh.single_bind is not None else mesh.parent_node
            outline = self.outline_of.get(zlib.crc32(mesh.name.encode("shift-jis")))
            texprojs = [self.texprojs.get((mesh.material_name, unit)) for unit in range(3)]
            self.draw_order.append((index, mesh, node, zlib.crc32(mesh.material_name.encode("shift-jis")), outline, texprojs))

    @property
    def name(self):
        return self.model.name

    @property
    def animations(self):
        result = list(self.own_animations)
        for entry in self.bound:
            result.extend(entry.own_animations)
        return result

    @property
    def has_meshes(self):
        return len(self.model.meshes) > 0

    @property
    def frame_count(self):
        return max((a.frame_count for a in self.own_animations), default=0)


class Scene:
    def __init__(self):
        self.entries = []
        self.cameras = []
        self.engine_id = "IE4"
        self.install_missing = False   # unpack a bundled engine without asking (command line)
        self.revision = 0
        self.hidden_at = None          # frame -> entries not drawn at that frame (the script of a template)
        self.camera_order = None       # () -> cameras played in order, None for all of them as imported
        self.frame_map = None          # (entry, frame) -> {"bone", "uv", "material"} -> frame of its animations
        self.map_entries = None        # () -> entries drawn before the queue of the others (the asset labelled map)
        self._skins = {}

    # Loading

    def add_file(self, path, engine_id=None):
        model = model_module.load_file(path)
        return self.add_model(model, engine_id, path)

    def add_model(self, model, engine_id=None, path=None, owner=None):
        engine_id = engine_id or self.engine_id
        eleven.ensure_game(engine_id, self.install_missing)
        added = []
        for part in model.walk():
            for camera in part.cameras:
                track = animation.CameraTrack(camera)
                track.owner = owner
                track.archive = part.name
                self.cameras.append(track)
            if part.bones or part.meshes or part.animations:
                entry = Entry(part, engine_id, path, owner)
                self.entries.append(entry)
                added.append(entry)
        self.revision += 1
        return added

    def clear(self):
        self.entries.clear()
        self.cameras.clear()
        self.revision += 1

    def remove_owner(self, owner):
        """Removes the entries and cameras an owner added, and the bindings to its entries."""
        removed = [entry for entry in self.entries if entry.owner is owner]
        self.entries = [entry for entry in self.entries if entry.owner is not owner]
        self.cameras = [camera for camera in self.cameras if camera.owner is not owner]
        for entry in self.entries:
            entry.bound = [source for source in entry.bound if source not in removed]
        self.revision += 1
        return removed

    def owned(self, owner):
        return [entry for entry in self.entries if entry.owner is owner]

    def find(self, name):
        name = name.lower()
        for entry in self.entries:
            if entry.name.lower() == name or os.path.splitext(entry.name)[0].lower() == name:
                return entry
        return None

    def bind(self, target, source):
        """The animations of source also drive target (by node, texture projection and material hashes)."""
        if source is not None and source is not target and source not in target.bound:
            target.bound.append(source)
            self.revision += 1

    def unbind(self, target, source=None):
        if source is None:
            target.bound.clear()
        elif source in target.bound:
            target.bound.remove(source)
        self.revision += 1

    def set_engine(self, entry, engine_id):
        eleven.ensure_game(engine_id, self.install_missing)
        entry.engine_id = eleven.drawing_engine(engine_id, entry.model)
        self.revision += 1

    def set_engine_all(self, engine_id):
        eleven.ensure_game(engine_id, self.install_missing)
        self.engine_id = engine_id
        for entry in self.entries:
            entry.engine_id = eleven.drawing_engine(engine_id, entry.model)
        self.revision += 1

    def visible_entries(self, frame=None):
        hidden = self.hidden_at(frame) if frame is not None and self.hidden_at is not None else ()
        return [entry for entry in self.entries if entry.visible and entry.has_meshes and entry not in hidden]

    def scripted(self):
        """The cameras follow the script of a template: the scene lasts as long as its cuts."""
        return self.camera_order is not None and bool(self.camera_order())

    @property
    def frame_count(self):
        if self.scripted():
            return self.camera_frame_count
        counts = [entry.frame_count for entry in self.entries]
        counts.append(self.camera_frame_count)
        return max(counts, default=0)

    def played_cameras(self):
        """The cameras in the order they play: as imported, or as the script of a template orders its cuts."""
        order = self.camera_order() if self.camera_order is not None else None
        return order if order else self.cameras

    @property
    def camera_frame_count(self):
        played = self.played_cameras()
        if not played:
            return 0
        return sum(camera.frame_count + 1 for camera in played) - 1

    def camera_index_at(self, frame):
        """(position in the played cameras, frame inside it): each camera plays frame_count + 1 frames."""
        played = self.played_cameras()
        if not played:
            return None, 0
        local = frame
        for index, camera in enumerate(played):
            if local <= camera.frame_count:
                return index, local
            local -= camera.frame_count + 1
        return len(played) - 1, played[-1].frame_count

    def camera_track_at(self, frame):
        """(camera track, frame inside it) at a frame."""
        index, local = self.camera_index_at(frame)
        return (None, 0) if index is None else (self.played_cameras()[index], local)

    def camera_at(self, frame):
        """(location, aim, vertical fov, roll) of the game camera, None without camera or when its archive is hidden."""
        camera, local = self.camera_track_at(frame)
        if camera is None or not getattr(camera, "enabled", True):
            return None
        return camera.at(local)

    def camera_starts(self):
        """First frame of each camera."""
        starts, frame = [], 0
        for camera in self.played_cameras():
            starts.append(frame)
            frame += camera.frame_count + 1
        return starts

    def bounds(self, frame=0.0, entries=None):
        low, high = None, None
        for entry in self.visible_entries() if entries is None else entries:
            entry.pose.update(entry.animations, frame)
            for item in self.evaluate(entry, frame, None):
                positions = item.positions if item.positions is not None else item.mesh.positions
                if not len(positions):
                    continue
                points = np.concatenate([positions, np.ones((len(positions), 1), np.float32)], axis=1) @ item.matrix.T
                a, b = points[:, :3].min(axis=0), points[:, :3].max(axis=0)
                low = a if low is None else np.minimum(low, a)
                high = b if high is None else np.maximum(high, b)
        if low is None:
            return np.array((-1.0, -1.0, -1.0)), np.array((1.0, 1.0, 1.0))
        return low, high

    # Frame

    def skin_color(self, entry):
        """RGB of the skin of a character: the skin color (blue channel) of the mask palette of its head, x 4 (the
        palette holds RGB / 255 x 0.25, PRIMARY_COLOR takes RGB / 255). None when no part has a palette, the head is
        then drawn with its RGB mask (an engine without #FIX_MIXI_MAX, such as IE4)."""
        group = entry.skin_group
        if not group:
            return None
        key = tuple((id(e), e.engine_id) for e in group)
        if key not in self._skins:
            from renderer import MaterialBinding
            colors = []
            for each in group:
                for mesh in each.model.meshes:
                    try:
                        palette = MaterialBinding(each.engine_id, each.model, mesh).palette
                    except Exception:
                        palette = None
                    if palette and "b" in palette:
                        colors.append(tuple(min(1.0, c / PALETTE_SCALE) for c in palette["b"][:3]))
            self._skins[key] = colors[0] if colors else None
        return self._skins[key]

    def draw_list(self, frame, camera_rotation):
        """The draw items of every visible entry in one queue, as the game sorts it (sub_18680C): by bucket, rank in the
        bucket inside its model, then order of the model in the scene. An archive loaded after another no longer
        covers it whatever their buckets. The map is drawn first: the outlines of the characters, which write no
        depth, would otherwise be covered by the meshes of the stadium queued after them."""
        first = self.map_entries() if self.map_entries is not None else ()
        items = []
        for model_index, entry in enumerate(self.visible_entries(frame)):
            for item in self.evaluate(entry, frame, camera_rotation):
                item.model_index = model_index
                items.append(item)
        items.sort(key=lambda item: (item.entry not in first, item.bucket, item.bucket_index, item.model_index))
        return items

    def animation_frames(self, entry, frame):
        """{"bone", "uv", "material"} -> frame each kind of animation of an entry plays at a frame of the scene."""
        if self.frame_map is None:
            return dict.fromkeys(("bone", "uv", "material"), frame)
        return self.frame_map(entry, frame)

    def evaluate(self, entry, frame, camera_rotation):
        frames = self.animation_frames(entry, frame)
        model = entry.model
        animations = entry.animations
        pose = entry.pose
        pose.update(animations, frames["bone"], camera_rotation)
        palette = pose.palette()
        uv_animations = [a for a in animations if a.kind == "uv"]
        material_animations = [a for a in animations if a.kind == "material"]

        items = []
        skin_color = None
        for index, mesh, node, material_hash, outline, texprojs in entry.draw_order:
            if node is not None and not pose.visible[node]:
                continue

            tint = [1.0, 1.0, 1.0, mesh.base_alpha]
            if index in entry.skin_meshes:
                if skin_color is None:
                    skin_color = self.skin_color(entry) or ()
                if skin_color:
                    tint[:3] = skin_color
            for anim in material_animations:
                value = anim.value(material_hash, "transparency", frames["material"])
                if value is not None:
                    tint[3] = float(value[0])
                value = anim.value(material_hash, "attribute", frames["material"])
                if value is not None:
                    tint[:3] = [float(v) for v in value[:3]]
            if tint[3] < 0.001:
                continue

            item = DrawItem()
            item.model = model
            item.mesh = mesh
            item.entry = entry
            item.bucket, item.bucket_index = mesh.bucket, entry.bucket_index[index]
            item.model_index = 0
            item.tint = tint
            item.outline = outline
            item.positions, item.normals = None, None

            if mesh.bone_indices is not None and palette is not None:
                item.positions, item.normals = animation.skin(mesh, palette)
                item.matrix = np.identity(4)
            else:
                item.matrix = pose.world[mesh.single_bind].copy() if mesh.single_bind is not None else np.identity(4)
                if mesh.billboard == 1 and camera_rotation is not None:
                    scale = np.linalg.norm(item.matrix[:3, :3], axis=0)
                    item.matrix[:3, :3] = camera_rotation * scale[None, :]

            item.uv_rows = []
            for texproj in texprojs:
                move, scale, rotate = (0.0, 0.0), (1.0, 1.0), 0.0
                if texproj is not None:
                    for anim in uv_animations:
                        value = anim.value(texproj, "move", frames["uv"])
                        if value is not None:
                            move = (float(value[0]), float(value[1]))
                        value = anim.value(texproj, "scale", frames["uv"])
                        if value is not None:
                            scale = (float(value[0]), float(value[1]))
                        value = anim.value(texproj, "rotate", frames["uv"])
                        if value is not None:
                            rotate = float(value[0])
                item.uv_rows.append(animation.uv_rows(move, scale, rotate))
            items.append(item)
        return items


class View:
    """Camera of the window: an orbit around a target, or the game camera of the scene."""

    def __init__(self):
        self.target = np.zeros(3)
        self.distance = 10.0
        self.yaw = 0.0
        self.pitch = 0.15
        self.fov = math.radians(40.0)
        self.roll = 0.0
        self.width = 400
        self.height = 240
        self.near = 0.1
        self.far = 5000.0
        self.view = np.identity(4)
        self.projection = np.identity(4)

    def frame_bounds(self, low, high):
        self.target = (np.asarray(low) + np.asarray(high)) * 0.5
        radius = max(float(np.linalg.norm(np.asarray(high) - np.asarray(low))) * 0.5, 0.01)
        self.distance = radius / math.sin(self.fov * 0.5) * 1.05
        self.near = max(self.distance / 1000.0, 0.01)
        self.far = self.distance * 100.0

    @staticmethod
    def look_at(eye, target, up):
        forward = np.asarray(target, dtype=np.float64) - np.asarray(eye, dtype=np.float64)
        forward /= np.linalg.norm(forward) or 1.0
        side = np.cross(forward, up)
        if np.linalg.norm(side) < 1e-8:
            side = np.cross(forward, (0.0, 0.0, 1.0))
        side /= np.linalg.norm(side)
        upward = np.cross(side, forward)
        matrix = np.identity(4)
        matrix[0, :3], matrix[1, :3], matrix[2, :3] = side, upward, -forward
        matrix[:3, 3] = -matrix[:3, :3] @ np.asarray(eye, dtype=np.float64)
        return matrix

    @staticmethod
    def perspective(fovy, aspect, near, far):
        f = 1.0 / math.tan(fovy * 0.5)
        matrix = np.zeros((4, 4))
        matrix[0, 0] = f / aspect
        matrix[1, 1] = f
        matrix[2, 2] = (far + near) / (near - far)
        matrix[2, 3] = 2.0 * far * near / (near - far)
        matrix[3, 2] = -1.0
        return matrix

    def eye(self):
        return self.target + self.distance * np.array((
            math.cos(self.pitch) * math.sin(self.yaw),
            math.sin(self.pitch),
            math.cos(self.pitch) * math.cos(self.yaw)))

    @staticmethod
    def _rolled(view, roll):
        if not roll:
            return view
        turn = np.identity(4)
        c, s = math.cos(roll), math.sin(roll)
        turn[0, 0], turn[0, 1], turn[1, 0], turn[1, 1] = c, -s, s, c
        return turn @ view

    def update_orbit(self):
        # Same as the world Y up below the poles, and still defined when looking straight down or up
        up = (-math.sin(self.pitch) * math.sin(self.yaw), math.cos(self.pitch), -math.sin(self.pitch) * math.cos(self.yaw))
        self.view = self._rolled(self.look_at(self.eye(), self.target, up), self.roll)
        self.projection = self.perspective(self.fov, self.width / max(self.height, 1), self.near, self.far)

    def update_game(self, location, aim, fov, roll, near=1.0, far=10000.0):
        self.view = self._rolled(self.look_at(location, aim, (0.0, 1.0, 0.0)), roll)
        self.projection = self.perspective(fov, self.width / max(self.height, 1), near, far)

    def set_pose(self, location, aim, fov, roll):
        """Orbit that starts where a game camera is."""
        location, aim = np.asarray(location, dtype=np.float64), np.asarray(aim, dtype=np.float64)
        offset = location - aim
        distance = float(np.linalg.norm(offset))
        if distance < 1e-6:
            offset, distance = np.array((0.0, 0.0, 1.0)), 1.0
        self.target = aim.copy()
        self.distance = distance
        self.pitch = math.asin(max(-1.0, min(1.0, offset[1] / distance)))
        self.yaw = math.atan2(offset[0], offset[2])
        self.fov = float(fov)
        self.roll = float(roll)
        self._clip_planes()

    def look_down(self, height=100.0):
        """From (0, height, 0) toward the origin, (0, 0, -1) as the top of the screen."""
        self.target = np.zeros(3)
        self.distance = height
        self.yaw, self.pitch, self.roll = 0.0, math.pi / 2, 0.0
        self.fov = math.radians(40.0)
        self._clip_planes()

    def _clip_planes(self):
        self.near = max(self.distance / 1000.0, 0.01)
        self.far = max(self.distance * 100.0, 10000.0)

    def camera_rotation(self):
        """Rotation that turns a billboard to the camera (inverse of the view rotation)."""
        return self.view[:3, :3].T.copy()

    def orbit(self, dx, dy):
        self.yaw -= dx * 0.01
        self.pitch = min(max(self.pitch + dy * 0.01, -math.pi / 2), math.pi / 2)

    def pan(self, dx, dy):
        right = self.view[0, :3]
        up = self.view[1, :3]
        scale = self.distance * 0.0015
        self.target = self.target - right * dx * scale + up * dy * scale

    def zoom(self, steps):
        self.distance = max(self.distance * (0.9 ** steps), 1e-3)
        self.near = max(self.distance / 1000.0, 0.01)
        self.far = max(self.distance * 100.0, 100.0)
