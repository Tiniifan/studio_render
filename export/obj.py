"""Wavefront .obj (+ .mtl): the static geometry as studio_render draws it at rest, one object and one material per
mesh, positions, normals and uv0; no skeleton."""
import os

from .common import number


def write(scene, path):
    """Writes the scene as .obj + .mtl (static geometry as drawn at rest, no skeleton). Returns the written paths."""
    stem = os.path.splitext(os.path.basename(path))[0]
    mtl_path = os.path.join(os.path.dirname(path), stem + ".mtl")
    lines = ["# studio_render export", f"mtllib {os.path.basename(mtl_path)}"]
    offset = 1
    for part in scene.parts:
        lines.append(f"o {part.name}")
        lines.extend(f"v {number(x)} {number(y)} {number(z)}" for x, y, z in part.rest_positions)
        if part.uv is not None:
            lines.extend(f"vt {number(u)} {number(v)}" for u, v in part.uv)
        lines.extend(f"vn {number(x)} {number(y)} {number(z)}" for x, y, z in part.rest_normals)
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
