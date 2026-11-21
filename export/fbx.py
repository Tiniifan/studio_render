"""Binary FBX 7.4 (.fbx), written in pure Python (Blender does not read ASCII FBX): meshes, materials, textures, the
skeleton as LimbNode models under a Null (the armature), skin deformers and clusters with the bind pose.
UpAxis = +Y, FrontAxis = +Z, CoordAxis = +X, UnitScaleFactor = 100 (FBX counts in centimeters).

The bind pose is the one of scene.py (cluster TransformLink, BindPose); the joints keep their rest scale (Blender puts
it on the pose bones, as studio_eleven does).
"""
import os
import struct
import zlib

import numpy as np

import animation

from .common import object_name


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
    return object_name(name) + "\x00\x01" + kind


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


def write(scene, path):
    """Writes the scene as binary FBX 7.4. Returns the written paths."""
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
