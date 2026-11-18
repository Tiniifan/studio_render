"""A Level-5 archive (.xc / .xv / .pck) turned into plain data the renderer draws.

Every file is read by studio_eleven's readers (eleven.py): XPCK, RES.bin, XMPR, MBN, IMGC, ATR, MTR, XCSL, TXP, CMB,
the animations and the cameras. This module only links what they return: meshes to materials, materials to textures,
render states and lighting, nodes to their parents.
"""
import concurrent.futures
import hashlib
import io
import os
import pickle
import struct
import zlib

import numpy as np

import eleven

ARCHIVE_MAGICS = (b"XPCK", b"XFSP")


def crc(name):
    return zlib.crc32(name.encode("shift-jis"))


class Bone:
    def __init__(self, index, data, name):
        self.index = index
        self.name = name
        self.hash = data["crc32"]
        self.parent_hash = data["parent_crc32"]
        self.flag = data["flag"]
        self.location = np.array(data["location"], dtype=np.float64)
        # studio_eleven gives the rest rotation as a Blender quaternion (w, x, y, z)
        self.rotation = np.array(tuple(data["quaternion_rotation"]), dtype=np.float64)
        self.scale = np.array(data["scale"], dtype=np.float64)
        self.inverse_bind = np.array(data["inverse_bind"].array, dtype=np.float64)
        self.parent = None

    @property
    def billboard(self):
        return self.flag & 3


class Texture:
    def __init__(self, name, rgba, sampler):
        self.name = name
        # (height, width, 4) uint8, bottom row first like the UVs of studio_eleven (v = 1 - v of the file)
        self.rgba = rgba
        self.sampler = sampler

    @property
    def size(self):
        return self.rgba.shape[1], self.rgba.shape[0]


class Material:
    def __init__(self, name):
        self.name = name
        self.hash = crc(name)
        self.textures = []      # (Texture or None, texture mode) per texture unit
        self.atr = None         # state dict of formats/material/atr.py
        self.lighting = None    # formats/material/mtr.py Material


class Mesh:
    def __init__(self):
        self.name = ""
        self.material_name = ""
        self.index = 0
        self.positions = None
        self.normals = None
        self.uv = [None, None]
        self.tint = None
        self.flags = None       # the Col layer: per vertex outline flags (atr_pr2)
        self.weights = None
        self.bone_indices = None
        self.triangles = None
        self.single_bind = None
        self.parent_node = None
        self.render_program_hash = 0
        self.draw_priority = 0
        self.mesh_type = 1
        self.base_alpha = 1.0

    @property
    def bucket(self):
        return self.draw_priority & 0xFF

    @property
    def billboard(self):
        return (self.draw_priority >> 8) & 3


class Model:
    def __init__(self, name):
        self.name = name
        self.bones = []
        self.bone_by_hash = {}
        self.meshes = []
        self.materials = {}
        self.material_order = []
        self.textures = {}
        self.texprojs = []          # (texproj hash, material name, uv index)
        self.outlines = []
        self.animations = {}        # animation name -> [AnimationManager]
        self.splits = {}            # animation hash -> [split dicts]
        self.cameras = []
        self.library = None         # render library of the archive (custom combiners)
        self.library_files = {"cmb": {}, "lut": {}, "shdc": {}}
        self.children = []
        self.engine = None          # engine id forced for this archive, None = the scene one

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    @property
    def has_geometry(self):
        return any(len(model.meshes) for model in self.walk())


def _decode_texture(data):
    decoded = eleven.imgc().open(data)
    if decoded is None:
        return None
    pixels, width, height = decoded[0], decoded[1], decoded[2]
    array = np.asarray(pixels, dtype=np.float32).reshape(height, width, 4)
    return np.clip(array * 255.0 + 0.5, 0, 255).astype(np.uint8)


def _array(values, width, count, default):
    if values:
        return np.array(values, dtype=np.float32).reshape(-1, width)[:, :width]
    return np.tile(np.array(default, dtype=np.float32), (count, 1))


def _read_mesh(data, index, bone_index_of):
    parsed = eleven.xmpr().open_xmpr(io.BytesIO(data))
    mesh = Mesh()
    mesh.index = index
    mesh.name = parsed["name"]
    mesh.material_name = parsed["material_name"]
    mesh.render_program_hash = parsed["render_program_hash"]
    mesh.draw_priority = parsed["draw_priority"]
    mesh.mesh_type = parsed["mesh_type"]

    # Byte 54 of the properties block is the base transparency of the mesh, studio_eleven's reader skips it
    properties = struct.unpack_from("<I", data, 12)[0]
    if properties + 54 < len(data):
        mesh.base_alpha = (255 - data[properties + 54]) / 255.0

    vertices = parsed["vertices"]
    mesh.positions = np.array(vertices["positions"], dtype=np.float32).reshape(-1, 3)
    count = len(mesh.positions)
    mesh.normals = _array(vertices["normals"], 3, count, (0.0, 1.0, 0.0))
    for unit, key in enumerate(("uv_data0", "uv_data1")):
        mesh.uv[unit] = np.array(vertices[key], dtype=np.float32).reshape(-1, 2) if vertices[key] else None
    mesh.tint = _array(vertices["tint_data"], 4, count, (1.0, 1.0, 1.0, 1.0))
    mesh.flags = np.array(vertices["color_data"], dtype=np.float32).reshape(-1, 4) if vertices["color_data"] else None

    if vertices["weights"] and vertices["bone_indices"]:
        mesh.weights = np.array(vertices["weights"], dtype=np.float32).reshape(-1, 4)
        mesh.bone_indices = np.array([[bone_index_of(h, 0) for h in row] for row in vertices["bone_indices"]], dtype=np.int32)

    triangles = parsed["triangles"]
    mesh.triangles = np.array(triangles, dtype=np.uint32).reshape(-1, 3) if len(triangles) else np.zeros((0, 3), np.uint32)

    if parsed["single_bind"] is not None:
        mesh.single_bind = bone_index_of(parsed["single_bind"])
    elif parsed["parent_node"]:
        mesh.parent_node = bone_index_of(parsed["parent_node"])

    return mesh


def _names(table, kind):
    return [name for name in table.get(kind, {}).values() if isinstance(name, str)]


def load(data, name):
    """The archive read once: from the archives preload read, from the cache on disk, or read now and kept there."""
    key = cache_key(data, name)
    model = _preloaded.pop(key, None)
    if model is None:
        model = _read_cache(key)
    if model is None:
        model = read(data, name)
        _write_cache(key, model)
    return model


# Cache of the read archives: pickles named by the content of the archive and the code that reads it

CACHE_LIMIT = 1024 * 1024 * 1024

_signature = None
_preloaded = {}
_pool = None
_pruned = False


def cache_directory():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "studio_render", "cache")


def _code_signature():
    """Changes with the code that reads the archives (this file and studio_eleven's readers): older pickles are not used."""
    global _signature
    if _signature is None:
        digest = hashlib.sha1(b"studio_render model cache 1")
        folders = [os.path.join(eleven.location(), "formats"), os.path.join(eleven.location(), "compression")]
        paths = [os.path.abspath(__file__)]
        for folder in folders:
            for root, _, names in os.walk(folder):
                paths += [os.path.join(root, n) for n in names if n.endswith(".py")]
        for path in sorted(paths):
            try:
                with open(path, "rb") as stream:
                    digest.update(stream.read())
            except OSError:
                pass
        _signature = digest.hexdigest()[:16]
    return _signature


def cache_key(data, name):
    digest = hashlib.sha1(data)
    digest.update(name.encode("utf-8", "replace"))
    return f"{_code_signature()}_{digest.hexdigest()}"


def _cache_path(key):
    return os.path.join(cache_directory(), key + ".pkl")


def is_cached(data, name):
    key = cache_key(data, name)
    return key in _preloaded or os.path.isfile(_cache_path(key))


def _read_cache(key):
    path = _cache_path(key)
    try:
        with open(path, "rb") as stream:
            return pickle.load(stream)
    except FileNotFoundError:
        return None
    except Exception as error:
        print(f"[studio_render] cache {key}: {error}")
        try:
            os.remove(path)
        except OSError:
            pass
        return None


def _write_cache(key, model):
    global _pruned
    path = _cache_path(key)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temporary = f"{path}.{os.getpid()}.tmp"
        with open(temporary, "wb") as stream:
            pickle.dump(model, stream, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(temporary, path)
    except Exception as error:
        print(f"[studio_render] cache {key} not written: {error}")
        return
    if not _pruned:
        _pruned = True
        prune_cache()


def prune_cache(limit=CACHE_LIMIT):
    """Removes the pickles of older code and, past the limit, the least recently used ones."""
    directory = cache_directory()
    try:
        entries = [os.path.join(directory, n) for n in os.listdir(directory) if n.endswith(".pkl")]
    except OSError:
        return
    signature = _code_signature()
    kept = []
    for path in entries:
        try:
            if not os.path.basename(path).startswith(signature + "_"):
                os.remove(path)
            else:
                stat = os.stat(path)
                kept.append((max(stat.st_atime, stat.st_mtime), stat.st_size, path))
        except OSError:
            pass
    total = sum(size for _, size, _ in kept)
    for _, size, path in sorted(kept):
        if total <= limit:
            break
        try:
            os.remove(path)
            total -= size
        except OSError:
            pass


def _read_in_worker(data, name, key):
    model = read(data, name)
    _write_cache(key, model)
    return model


def _worker_count():
    return max(1, min((os.cpu_count() or 2) - 1, 6))


def worker_pool():
    """Processes that read archives: the readers are pure Python, several archives are read at once and the window
    keeps running."""
    global _pool
    if _pool is None:
        _pool = concurrent.futures.ProcessPoolExecutor(max_workers=_worker_count())
    return _pool


def shutdown_pool():
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)
        _pool = None


def preload(archives, progress=None):
    """Reads (name, bytes) archives that are not cached yet in the worker processes, the next load of each one is
    immediate. progress(done, total) is called from the calling thread."""
    pending = {}
    for name, data in archives:
        key = cache_key(data, name)
        if key not in pending and key not in _preloaded and not os.path.isfile(_cache_path(key)):
            pending[key] = (name, data)
    if not pending:
        return
    total = len(pending)
    if progress:
        progress(0, total)
    try:
        pool = worker_pool()
        futures = {pool.submit(_read_in_worker, data, name, key): key for key, (name, data) in pending.items()}
    except Exception as error:
        print(f"[studio_render] archives read in the window ({error})")
        shutdown_pool()
        return
    for done, future in enumerate(concurrent.futures.as_completed(futures), 1):
        try:
            _preloaded[futures[future]] = future.result()
        except Exception as error:
            # load() reads it again and reports the error
            print(f"[studio_render] {pending[futures[future]][0]}: {error}")
        if progress:
            progress(done, total)


def read(data, name):
    xpck, res, atr, mtr, txp = eleven.xpck(), eleven.res(), eleven.atr(), eleven.mtr(), eleven.txp()
    files = xpck.open_file(data)
    model = Model(name)

    table = {}
    if "RES.bin" in files:
        table = res.open_res(data=files["RES.bin"])
        try:
            model.library = res.open_render_library(files["RES.bin"])
        except Exception:
            model.library = None
    RES = res.RESType

    # Nodes (.mbn), in file order
    bone_names = table.get(RES.BONE, {})
    mbn = eleven.mbn()
    for file_name in sorted(n for n in files if n.endswith(".mbn")):
        bone_data = mbn.open(files[file_name])
        if bone_data is None:
            continue
        index = len(model.bones)
        model.bones.append(Bone(index, bone_data, bone_names.get(bone_data["crc32"], f"node_{index}")))
    model.bone_by_hash = {bone.hash: bone.index for bone in model.bones}
    for bone in model.bones:
        bone.parent = model.bone_by_hash.get(bone.parent_hash) if bone.parent_hash else None

    def bone_index_of(value, default=None):
        return model.bone_by_hash.get(value, default)

    # Textures (.xi) follow the order of TEXTURE_DATA
    texture_table = table.get(RES.TEXTURE_DATA, {})
    texture_keys = list(texture_table)
    for index, file_name in enumerate(sorted(n for n in files if n.endswith(".xi"))):
        if index >= len(texture_keys):
            break
        entry = texture_table[texture_keys[index]]
        rgba = _decode_texture(files[file_name])
        if rgba is not None:
            model.textures[texture_keys[index]] = Texture(entry["name"], rgba, entry.get("sampler", dict(res.DEFAULT_SAMPLER)))

    for value in table.get(RES.MATERIAL_DATA, {}).values():
        material = Material(value["name"])
        modes = value.get("texture_modes", [])
        for slot, texture_hash in enumerate(value["textures"]):
            material.textures.append((model.textures.get(texture_hash), modes[slot] if slot < len(modes) else 1))
        model.materials[material.name] = material

    # The .atr are numbered like MATERIAL_2, the .mtr like MATERIAL_1
    names = _names(table, RES.MATERIAL_2) or [v["name"] for v in table.get(RES.MATERIAL_DATA, {}).values()]
    model.material_order = [crc(n) for n in names]
    for index, file_name in enumerate(sorted(n for n in files if n.endswith(".atr"))):
        if index < len(names) and names[index] in model.materials:
            try:
                model.materials[names[index]].atr = atr.read_atr(files[file_name])
            except Exception as error:
                print(f"[studio_render] {name}/{file_name}: {error}")

    lighting_names = _names(table, RES.MATERIAL_1) or names
    for index, file_name in enumerate(sorted(n for n in files if n.endswith(".mtr"))):
        if index < len(lighting_names) and lighting_names[index] in model.materials:
            try:
                model.materials[lighting_names[index]].lighting = mtr.read_mtr(files[file_name])
            except Exception as error:
                print(f"[studio_render] {name}/{file_name}: {error}")

    xcsl = eleven.xcsl()
    for file_name in sorted(n for n in files if n.endswith(".sil")):
        try:
            model.outlines.append(xcsl.read(files[file_name]))
        except Exception as error:
            print(f"[studio_render] {name}/{file_name}: outline skipped ({error})")

    material_by_hash = {m.hash: m.name for m in model.materials.values()}
    for file_name in sorted(n for n in files if n.endswith(".txp")):
        texproj_hash, material_hash, uv_index = txp.read_txp(io.BytesIO(files[file_name]))[:3]
        model.texprojs.append((texproj_hash, material_by_hash.get(material_hash), uv_index))

    for index, file_name in enumerate(sorted(n for n in files if n.endswith(".prm"))):
        try:
            model.meshes.append(_read_mesh(files[file_name], index, bone_index_of))
        except Exception as error:
            print(f"[studio_render] {name}/{file_name}: {error}")

    manager, minf = eleven.animation_manager(), eleven.minf()
    for file_name in sorted(files):
        content = files[file_name]
        if file_name.endswith((".mtn2", ".imm2", ".mtm2", ".mtn3", ".imm3", ".mtm3")):
            try:
                animation = manager.AnimationManager(reader=io.BytesIO(content))
                model.animations.setdefault(animation.AnimationName, []).append(animation)
            except Exception as error:
                print(f"[studio_render] {name}/{file_name}: {error}")
        elif file_name.endswith((".mtninf", ".imminf", ".mtminf")):
            try:
                _, split_name, animation_hash, start, end, speed = minf.open_minf1(content)
                splits = model.splits.setdefault(animation_hash, [])
                if all(split["name"] != split_name for split in splits):
                    splits.append({"name": split_name, "start": start, "end": end, "speed": speed})
            except Exception:
                pass
        elif file_name.endswith((".cmb", ".lut")) and file_name[:3].isdigit():
            model.library_files[file_name[-3:]][int(file_name[:3])] = content
        elif file_name.endswith(".shdc") and file_name[3:6].isdigit():
            model.library_files["shdc"][int(file_name[3:6])] = content

    if "CMR.bin" in files:
        xcma, xcmt = eleven.xcma(), eleven.xcmt()
        cameras = {}
        for file_name in sorted(n for n in files if n.endswith((".cmr2", ".cmr"))):
            try:
                camera = xcma.read(files[file_name])
                cameras[camera["hash"]] = camera
            except Exception as error:
                print(f"[studio_render] {name}/{file_name}: {error}")
        try:
            order = xcmt.open(data=files["CMR.bin"])
            model.cameras = [cameras[h] for h in order if h in cameras]
        except Exception:
            model.cameras = list(cameras.values())

    for file_name in sorted(files):
        if file_name.endswith((".xc", ".xv", ".xk")) and files[file_name][:4] in ARCHIVE_MAGICS:
            try:
                model.children.append(read(files[file_name], file_name))
            except Exception as error:
                print(f"[studio_render] {name}/{file_name}: {error}")

    return model


def load_file(path):
    with open(path, "rb") as stream:
        data = stream.read()
    return load(data, os.path.basename(path))
