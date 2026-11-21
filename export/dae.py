"""Collada 1.4.1 (.dae): meshes with normals and uv0, materials and images, the skeleton as a joint node hierarchy, skin
controllers. <up_axis>Y_UP</up_axis>, <unit meter="1"/>.

Blender's Collada importer ignores the rest transform of the joints when it differs from the bind pose, so the rest
scale is baked: the bind pose is the world rest matrix itself (scale included, the scale-free one of scene.py for a node
scaled to 0) and the vertices are written as drawn at rest. Each joint also gets Blender's <extra> tip and roll, without
which Blender's importer loses the rest rotations (and the bones of nodes scaled to 0).
"""
from xml.sax.saxutils import escape, quoteattr

import numpy as np

from .common import Names, identifier, number


def _floats(values):
    return " ".join(number(v) for v in np.asarray(values, dtype=np.float64).ravel())


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


def write(scene, path):
    """Writes the scene as Collada 1.4.1. Returns the written paths."""
    out = ['<?xml version="1.0" encoding="utf-8"?>',
           '<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">',
           "  <asset>", "    <contributor><authoring_tool>studio_render</authoring_tool></contributor>",
           "    <created>1970-01-01T00:00:00</created>", "    <modified>1970-01-01T00:00:00</modified>",
           '    <unit name="meter" meter="1"/>', "    <up_axis>Y_UP</up_axis>", "  </asset>"]

    sids = Names()
    joint_ids = [sids.get(identifier(joint.name)) for joint in scene.joints]

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
        armature = sids.get(identifier(scene.name or "Armature"))
        out.append(f'      <node id="{armature}" name={quoteattr(scene.name or "Armature")} type="NODE">')
        for root in scene.roots:
            out += joint_node(root, 4)
        out.append("      </node>")
    for index, part in enumerate(scene.parts):
        node = sids.get(identifier(part.name))
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
