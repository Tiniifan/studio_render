"""OpenGL (moderngl) drawing of a Scene with the shaders of StudioRender.

The GLSL comes from studio_eleven's StudioRender core, unchanged: the combiner program (combiner.build_program), the
vertex and fragment sources (shaders.vertex_source / fragment_source / outline_fragment_source), the fragment lighting
(lighting.environment_of / lut_texture_rows) and the resolved ATR state (state.resolve). This module only does what
the Blender gpu module did for StudioRender: buffers, textures, uniforms and the GL state.

Differences with StudioRender inside Blender, because OpenGL can do what gpu.state of Blender 3.4 could not:
- the blend factors and equations of the .atr are applied exactly (separate RGB / alpha), so the shader never applies
  a source factor itself;
- the frame holds the stored 8 bit values and the textures are uploaded as they are: the sampler filters, and the
  combiner and the blend work on, the stored values like the PICA200, without any colour space conversion
  (gamma_correct off). Blender's sRGB textures made the filtering run on linear values, which brightened the texels
  mixed by the bilinear filter.
"""
import numpy as np
import moderngl

import animation, eleven

MAX_TEXTURES = 4
BUCKETS = 64
MIN_ALPHA = 0.001

DEPTH_FUNCS = {0x0200: "0", 0x0201: "<", 0x0202: "==", 0x0203: "<=", 0x0204: ">", 0x0205: "!=", 0x0206: ">=", 0x0207: "1"}

# Constants of StudioRender's draw_outline (rendering/studio_render/engine.py)
OUTLINE_WIDTH_SCALE = 4.219409
OUTLINE_REFERENCE_PROJECTION = 1.0
OUTLINE_SIZE_SHARE = 0.025

PALETTE_SCALE = 0.25


def column_major(matrix):
    return np.asarray(matrix, dtype=np.float32).T.copy().tobytes()


def is_power_of_two(value):
    return value > 1 and value & (value - 1) == 0


def mip_atlas(rgba):
    """Level 0 on the left, the other levels stacked on its right: the layout StudioRender's sampler reads."""
    pixels = rgba.astype(np.float32)
    height, width = pixels.shape[:2]
    levels = [pixels]
    while max(levels[-1].shape[:2]) > 1:
        level = levels[-1]
        if level.shape[0] > 1:
            level = level.reshape(level.shape[0] // 2, 2, level.shape[1], 4).mean(axis=1)
        if level.shape[1] > 1:
            level = level.reshape(level.shape[0], level.shape[1] // 2, 2, 4).mean(axis=2)
        levels.append(level)
    rows = max(height, sum(level.shape[0] for level in levels[1:]))
    atlas = np.zeros((rows, width + levels[1].shape[1], 4), dtype=np.float32)
    atlas[:height, :width] = levels[0]
    row = 0
    for level in levels[1:]:
        atlas[row:row + level.shape[0], width:width + level.shape[1]] = level
        row += level.shape[0]
    return np.clip(atlas + 0.5, 0, 255).astype(np.uint8)


class _InheritedState(dict):
    def __missing__(self, key):
        return None


class MaterialBinding:
    """What one mesh needs to be drawn: combiner program, render state, textures, samplers, lighting material."""

    def __init__(self, engine_id, model, mesh):
        combiner, resources, render_defaults = eleven.combiner(), eleven.resources(), eleven.render_defaults()
        state, atr, res = eleven.state(), eleven.atr(), eleven.res()

        self.engine_id = engine_id
        self.render_default = None
        self.vertex = None
        stages, key = None, None
        program = _library_program(model, mesh.render_program_hash)
        if program is not None:
            stages = _library_combiner(model, program["combiner"])
            if stages is None:
                # A render program of the archive can use a combiner of the game
                stages = resources.load_combiner(engine_id, program["combiner"])
            if stages is not None:
                key = ("library", model.name, program["name"])
                self.vertex = _library_vertex(model, program["vertex_program"])

        if stages is None:
            render_default = render_defaults.find_render_default_by_hash(engine_id, mesh.render_program_hash)
            if render_default is None:
                render_default = render_defaults.get_default_render_default(engine_id)
            self.render_default = render_default
            stages = resources.render_default_stages(engine_id, render_default)
            if stages is None:
                fallback = render_defaults.get_default_render_default(engine_id)
                stages = resources.load_combiner(engine_id, fallback.data["combiner"])
            key = (engine_id, render_default.key)

        self.program_key = key
        self.program = combiner.build_program(stages) if stages else None

        material = model.materials.get(mesh.material_name)
        self.material = material
        file_version = eleven.engines().get_engine(engine_id).file_version
        atr_state = _InheritedState(material.atr if material is not None and material.atr else {})
        atr_state["version"] = file_version
        self.state = state.ResolvedState(atr.resolve_state(atr_state))

        self.lighting = material.lighting if material is not None and material.lighting is not None else \
            eleven.game_material().engine_material(engine_id)

        self.textures, self.samplers = [], []
        for texture, mode in (material.textures if material is not None else [])[:MAX_TEXTURES]:
            self.textures.append(texture if mode != 0 else None)
            self.samplers.append(texture.sampler if texture is not None else dict(res.DEFAULT_SAMPLER))

        self.palette = self._palette()

    def _palette(self):
        program = self.program
        if program is None:
            return None
        masked = program.palette_channels or (program.base is not None and program.base.palette_channels)
        texture = self.textures[0] if self.textures else None
        if not masked or texture is None or texture.rgba.shape[0] < 2 or texture.rgba.shape[1] < 2:
            return None
        pixels = texture.rgba
        # rgba is bottom-up, the colors are the pixels (0, 0), (1, 0) and (0, 1) from the top left of the image
        return {
            "r": tuple(float(c) / 255.0 * PALETTE_SCALE for c in pixels[-1, 0, :3]) + (1.0,),
            "g": tuple(float(c) / 255.0 * PALETTE_SCALE for c in pixels[-1, 1, :3]) + (1.0,),
            "b": tuple(float(c) / 255.0 * PALETTE_SCALE for c in pixels[-2, 0, :3]) + (1.0,),
        }

    @property
    def texture_units(self):
        if self.program is None:
            return ()
        return tuple(unit for unit in sorted(self.program.texture_units)
                     if unit < len(self.textures) and self.textures[unit] is not None)

    @property
    def fragment_lighting(self):
        return bool(self.program is not None and self.program.uses_fragment_lighting)

    def sampler(self, unit):
        sampler = self.samplers[unit]
        return sampler["wrap_s"], sampler["wrap_t"], sampler["mag_filter"] | (sampler["min_filter"] << 1), sampler["mip_filter"]

    def uses_mip(self, unit):
        width, height = self.textures[unit].size
        return bool(self.sampler(unit)[3]) and is_power_of_two(width) and is_power_of_two(height)

    def sampler_uniform(self, unit):
        wrap_s, wrap_t, filters, _ = self.sampler(unit)
        width, height = self.textures[unit].size
        return wrap_s, wrap_t, filters, (width | (height << 16)) if self.uses_mip(unit) else 0


def _library_program(model, render_program_hash):
    """Render program the archive holds itself (render library of RES.bin)."""
    library = model.library
    if not library:
        return None
    res = eleven.res()
    for program in library.get("render_programs", []):
        if res.get_name_hash(program["name"]) == render_program_hash:
            return program
    return None


def _library_combiner(model, name):
    """Combiner of the render library of the archive (numbered .cmb), None when the archive does not hold it."""
    names = [entry["name"] for entry in model.library.get("combiners", [])]
    if name not in names:
        return None
    data = model.library_files["cmb"].get(names.index(name))
    if data is None:
        return None
    try:
        return list(eleven.cmb().read_cmb(data)[1])
    except Exception as error:
        print(f"[studio_render] {model.name}: combiner {name}: {error}")
        return None


_vertex_stages = {}


def _library_vertex(model, name):
    """Vertex stage written from the vertex binary (SHDnnn.shdc) of the archive, None for a vertex program of the game."""
    vertex_binary = eleven.vertex_binary()
    for entry in model.library.get("vertex_programs", []):
        if entry["name"] != name:
            continue
        data = model.library_files["shdc"].get(vertex_binary.get_binary_index(entry["data"]))
        if data is None:
            return None
        key = (bytes(entry["data"]), data)
        if key not in _vertex_stages:
            try:
                _vertex_stages[key] = vertex_binary.make_vertex_glsl(data, vertex_binary.get_program_index(entry["data"]))
            except Exception as error:
                print(f"[studio_render] {model.name}: vertex program {name} drawn with the default vertex stage ({error})")
                _vertex_stages[key] = None
        return _vertex_stages[key]
    return None


class GpuMesh:
    """Vertex buffers of one mesh, the positions and normals are rewritten when the mesh is skinned."""

    def __init__(self, ctx, mesh):
        count = len(mesh.positions)
        zeros = np.zeros((count, 2), np.float32)
        self.ctx = ctx
        self.count = count
        self.buffers = {
            "atr_pos": (ctx.buffer(mesh.positions.astype("f4").tobytes(), dynamic=True), "3f"),
            "atr_nrm": (ctx.buffer(mesh.normals.astype("f4").tobytes(), dynamic=True), "3f"),
            "atr_clr": (ctx.buffer(mesh.tint.astype("f4").tobytes()), "4f"),
            "atr_tx0": (ctx.buffer((mesh.uv[0] if mesh.uv[0] is not None else zeros).astype("f4").tobytes()), "2f"),
            "atr_tx1": (ctx.buffer((mesh.uv[1] if mesh.uv[1] is not None else zeros).astype("f4").tobytes()), "2f"),
            "atr_tx2": (ctx.buffer((mesh.uv[2] if mesh.uv[2] is not None else zeros).astype("f4").tobytes()), "2f"),
            "atr_pr2": (ctx.buffer((mesh.flags if mesh.flags is not None else np.zeros((count, 4), np.float32)).astype("f4").tobytes()), "4f"),
        }
        self.index = ctx.buffer(mesh.triangles.astype("u4").tobytes()) if len(mesh.triangles) else None
        self.vaos = {}

    def update(self, positions, normals):
        self.buffers["atr_pos"][0].write(positions.astype("f4").tobytes())
        self.buffers["atr_nrm"][0].write(normals.astype("f4").tobytes())

    def vao(self, program):
        key = id(program)
        if key not in self.vaos:
            content = [(buffer, layout, name) for name, (buffer, layout) in self.buffers.items() if name in program]
            self.vaos[key] = self.ctx.vertex_array(program, content, self.index)
        return self.vaos[key]

    def release(self):
        for vao in self.vaos.values():
            vao.release()
        for buffer, _ in self.buffers.values():
            buffer.release()
        if self.index is not None:
            self.index.release()


class Renderer:
    def __init__(self, ctx):
        self.ctx = ctx
        self.programs = {}
        self.textures = {}
        self.luts = {}
        self.meshes = {}
        self.bindings = {}
        self._members = {}
        self.white = ctx.texture((1, 1), 4, bytes((255, 255, 255, 255)))
        self.background = (0.32, 0.34, 0.38)
        self.draw_outlines = True
        self.fragment_lighting = True
        self.errors = set()

    # Resources

    def release_scene(self):
        for gpu_mesh in self.meshes.values():
            gpu_mesh.release()
        for texture, _ in self.textures.values():
            texture.release()
        for _, texture in self.luts.values():
            texture.release()
        self.meshes.clear()
        self.textures.clear()
        self.luts.clear()
        self.bindings.clear()

    def gpu_mesh(self, mesh):
        key = id(mesh)
        if key not in self.meshes:
            self.meshes[key] = GpuMesh(self.ctx, mesh)
        return self.meshes[key]

    def binding(self, engine_id, model, mesh):
        key = (engine_id, id(mesh))
        if key not in self.bindings:
            self.bindings[key] = MaterialBinding(engine_id, model, mesh)
        return self.bindings[key]

    def texture(self, texture, mip):
        key = (id(texture), mip)
        if key not in self.textures:
            rgba = mip_atlas(texture.rgba) if mip else texture.rgba
            height, width = rgba.shape[:2]
            gl_texture = self.ctx.texture((width, height), 4, np.ascontiguousarray(rgba).tobytes())
            gl_texture.filter = (moderngl.NEAREST, moderngl.NEAREST)
            gl_texture.repeat_x = gl_texture.repeat_y = False
            self.textures[key] = (gl_texture, texture)
        return self.textures[key][0]

    def lut_texture(self, material_lighting):
        key = id(material_lighting)
        if key not in self.luts:
            lighting = eleven.lighting()
            rows = np.array(lighting.lut_texture_rows(material_lighting), dtype=np.float32)
            texture = self.ctx.texture((lighting.LUT_TEXTURE_WIDTH, lighting.LUT_TEXTURE_HEIGHT), 1, rows.tobytes(), dtype="f4")
            texture.filter = (moderngl.NEAREST, moderngl.NEAREST)
            self.luts[key] = (material_lighting, texture)
        return self.luts[key][1]

    def program(self, combiner_program, options, program_key, vertex=None):
        shaders = eleven.shaders()
        key = (program_key, options.key())
        if key not in self.programs:
            if options.outline:
                fragment = shaders.outline_fragment_source(options, combiner_program)
            else:
                fragment = shaders.fragment_source(combiner_program, options)
            # The outline keeps the extrusion of StudioRender, the vertex stage of the archive draws the mesh
            stages = [shaders.vertex_source(options)]
            if vertex is not None and not options.outline:
                stages.insert(0, vertex)
            self.programs[key] = None
            for stage in stages:
                try:
                    self.programs[key] = self.ctx.program(vertex_shader="#version 330 core\n" + stage,
                                                          fragment_shader="#version 330 core\n" + fragment)
                    break
                except Exception as error:
                    if (key, stage is vertex) not in self.errors:
                        print(f"[studio_render] shader {program_key}: {error}")
                        self.errors.add((key, stage is vertex))
        return self.programs[key]

    # Uniforms

    def _set(self, program, name, value):
        # A uniform keeps its value in the program: only what changed since the last draw with it is sent
        members = self._members.get(id(program))
        if members is None:
            members = self._members[id(program)] = {}
        found = members.get(name)
        if found is None:
            member = program.get(name, None)
            if member is None:
                members[name] = (None, None)
                return
            found = members[name] = [member, None]
        member = found[0]
        if member is None or found[1] == value:
            return
        if isinstance(value, (bytes, bytearray)):
            member.write(value)
        else:
            member.value = value
        found[1] = value

    def _bind_common(self, program, view, model_matrix, frame_state, tint, uv_rows):
        self._set(program, "unf_vtx_lcl_glb", column_major(model_matrix))
        self._set(program, "unf_vtx_glb_cmr", column_major(view.view))
        self._set(program, "unf_vtx_cmr_prj", column_major(view.projection))
        for index, row in enumerate(uv_rows):
            self._set(program, f"unf_vtx_txt_{index}", tuple(float(v) for v in row))
        self._set(program, "unf_vtx_clr", tuple(float(v) for v in tint))

    def _bind_textures(self, program, binding, units, start=0):
        for slot, unit in enumerate(units):
            texture = self.texture(binding.textures[unit], binding.uses_mip(unit))
            texture.use(start + slot)
            self._set(program, f"unf_frg_txt_2d_{unit}", start + slot)
            self._set(program, f"unf_frg_smp_{unit}", tuple(int(v) for v in binding.sampler_uniform(unit)))
        return start + len(units)

    def _bind_palette(self, program, binding):
        if binding.palette:
            for channel, color in binding.palette.items():
                self._set(program, f"unf_frg_palette_{channel}", color)

    def _bind_lighting(self, program, binding, lights, scene_ambient, unit):
        lighting = eleven.lighting()
        environment = lighting.environment_of(binding.lighting, scene_ambient)
        self._set(program, "unf_lgt_scene_ambient", tuple(environment["scene_ambient"]))
        for index, light in enumerate(lights):
            for field in ("position", "ambient", "diffuse", "specular0", "specular1"):
                self._set(program, f"unf_lgt_{field}_{index}", tuple(light[field]))
        self._set(program, "unf_lgt_config", int(environment["config"]))
        for field in ("emission", "ambient", "diffuse", "specular0", "specular1"):
            self._set(program, f"unf_mat_{field}", tuple(environment[field]))
        self._set(program, "unf_lgt_fresnel_selector", int(environment["fresnel_selector"]))
        self._set(program, "unf_lgt_clamp_highlights", bool(environment["clamp_highlights"]))
        self._set(program, "unf_lgt_two_side_diffuse", bool(environment["two_side_diffuse"]))
        self._set(program, "unf_lgt_enabled_d0", bool(environment["lut_enabled_d0"]))
        self._set(program, "unf_lgt_enabled_d1", bool(environment["lut_enabled_d1"]))
        self._set(program, "unf_lgt_enabled_refl", bool(environment["lut_enabled_refl"]))
        for table in lighting.LUT_TABLES:
            self._set(program, f"unf_lut_input_{table}", int(environment["lut_input"][table]))
            self._set(program, f"unf_lut_abs_{table}", bool(environment["lut_abs"][table]))
            self._set(program, f"unf_lut_scale_{table}", float(environment["lut_scale"][table]))
        self.lut_texture(binding.lighting).use(unit)
        self._set(program, "unf_frg_txt_lut", unit)

    # GL state

    def _apply_state(self, fbo, resolved):
        ctx = self.ctx
        if resolved.blend:
            ctx.enable(moderngl.BLEND)
            ctx.blend_func = (resolved.blend_rgb_source, resolved.blend_rgb_destination,
                              resolved.blend_alpha_source, resolved.blend_alpha_destination)
            ctx.blend_equation = (resolved.blend_rgb_equation, resolved.blend_alpha_equation)
        else:
            ctx.disable(moderngl.BLEND)

        if resolved.depth_test:
            ctx.enable(moderngl.DEPTH_TEST)
            ctx.depth_func = DEPTH_FUNCS.get(resolved.depth_func, "<")
        else:
            ctx.disable(moderngl.DEPTH_TEST)

        if resolved.cull:
            ctx.enable(moderngl.CULL_FACE)
            ctx.cull_face = "back"
        else:
            ctx.disable(moderngl.CULL_FACE)

        fbo.depth_mask = resolved.depth_write
        fbo.color_mask = tuple(bool(v) for v in resolved.color_mask)
        # moderngl only sends the masks to GL when the framebuffer is bound
        fbo.use()

    def _reset_state(self, fbo):
        ctx = self.ctx
        ctx.disable(moderngl.BLEND)
        ctx.disable(moderngl.CULL_FACE)
        ctx.enable(moderngl.DEPTH_TEST)
        ctx.depth_func = "<"
        fbo.depth_mask = True
        fbo.color_mask = (True, True, True, True)
        fbo.use()

    # Frame

    def render(self, fbo, scene, view, frame):
        ctx = self.ctx
        fbo.use()
        self._reset_state(fbo)
        ctx.front_face = "ccw"
        fbo.clear(*self.background, 1.0, depth=1.0)

        lighting = eleven.lighting()
        lights = [lighting.default_light()]
        camera_rotation = view.camera_rotation()

        draws = scene.draw_list(frame, camera_rotation)
        # The outlines of a model are drawn before its first mesh in the queue
        outlines = {}
        if self.draw_outlines:
            for item in draws:
                if item.outline is not None:
                    outlines.setdefault(item.entry, []).append(item)
        ambients = {}
        for item in draws:
            entry = item.entry
            outline_items = outlines.pop(entry, None)
            if outline_items:
                size_ndc = self._size_ndc(outline_items, view)
                for outline_item in outline_items:
                    self._draw_outline(fbo, entry, outline_item, view, size_ndc)
            if entry not in ambients:
                ambients[entry] = (eleven.engines().get_scene_ambient(entry.engine_id),) * 3
            self._draw_mesh(fbo, entry, item, view, lights, ambients[entry])

        self._reset_state(fbo)

    def _uv_rows(self, item):
        rows = []
        for unit in range(3):
            rows.extend(item.uv_rows[unit] if unit < len(item.uv_rows) else animation.IDENTITY_UV)
        return rows

    def _mesh_options(self, binding, light_count):
        resolved = binding.state
        lit = self.fragment_lighting and binding.fragment_lighting
        return eleven.shaders().ShaderOptions(
            texture_units=binding.texture_units,
            fragment_lighting=lit,
            light_count=light_count if lit else 0,
            alpha_test=resolved.alpha_test,
            alpha_func=resolved.alpha_func,
            gamma_correct=False,
        )

    @staticmethod
    def _outline_options(binding):
        """(options, program of the mesh it merges) of the outline pass of a mesh."""
        base = binding.program.base if binding.program is not None else None
        if base is not None:
            units = tuple(u for u in sorted(base.texture_units) if u < len(binding.textures) and binding.textures[u] is not None)
        elif 0 in binding.texture_units:
            units = (0,)
        else:
            units = ()
        textured = len(units) > 0
        # The samples are the stored values (gamma_correct off). StudioRender's flat outline (no texture, no merged
        # program) converts its colour to scene linear unless display_output is on, which needs gamma_correct: it
        # samples nothing, so both on write its colour unchanged
        flat = base is None and not textured
        options = eleven.shaders().ShaderOptions(
            texture_units=units,
            alpha_test=textured and binding.state.alpha_test,
            alpha_func=binding.state.alpha_func if textured else None,
            gamma_correct=flat,
            outline=True,
            display_output=flat,
        )
        return options, base

    def prepare(self, entry, mesh, outline=False, light_count=1):
        """Builds what drawing a mesh needs (program, buffers, textures, lighting table) before it is first drawn: the
        first playback no longer stops to compile the shaders and upload the textures."""
        binding = self.binding(entry.engine_id, entry.model, mesh)
        if binding.program is None:
            return
        options = self._mesh_options(binding, light_count)
        if self.program(binding.program, options, binding.program_key, binding.vertex) is None:
            return
        self.gpu_mesh(mesh)
        for unit in options.texture_units:
            self.texture(binding.textures[unit], binding.uses_mip(unit))
        if options.fragment_lighting:
            self.lut_texture(binding.lighting)
        if outline and self.draw_outlines:
            outline_options, base = self._outline_options(binding)
            self.program(base, outline_options, ("outline", binding.program_key))

    def _draw_mesh(self, fbo, entry, item, view, lights, scene_ambient):
        binding = self.binding(entry.engine_id, item.model, item.mesh)
        if binding.program is None:
            return
        resolved = binding.state
        options = self._mesh_options(binding, len(lights))
        lit = options.fragment_lighting
        program = self.program(binding.program, options, binding.program_key, binding.vertex)
        if program is None:
            return
        gpu_mesh = self.gpu_mesh(item.mesh)
        if gpu_mesh.index is None:
            return
        if item.positions is not None:
            gpu_mesh.update(item.positions, item.normals)

        self._apply_state(fbo, resolved)
        self._bind_common(program, view, item.matrix, None, item.tint, self._uv_rows(item))
        self._set(program, "unf_frg_alpha_ref", float(resolved.alpha_ref))
        unit = self._bind_textures(program, binding, options.texture_units)
        self._bind_palette(program, binding)
        if lit:
            self._bind_lighting(program, binding, lights, scene_ambient, unit)
        gpu_mesh.vao(program).render(moderngl.TRIANGLES)

    def _size_ndc(self, items, view):
        corners = []
        for item in items:
            positions = item.positions if item.positions is not None else item.mesh.positions
            if len(positions):
                low, high = positions.min(axis=0), positions.max(axis=0)
                for point in (low, high):
                    corners.append((item.matrix @ np.append(point, 1.0))[:3])
        if not corners:
            return None
        corners = np.array(corners)
        extent = float(np.max(corners.max(axis=0) - corners.min(axis=0)))
        center = np.append(corners.mean(axis=0), 1.0)
        distance = max(abs(float((view.view @ center)[2])), 1e-4)
        return extent * abs(view.projection[1, 1]) / distance

    def _draw_outline(self, fbo, entry, item, view, size_ndc):
        ctx = self.ctx
        outline = item.outline
        binding = self.binding(entry.engine_id, item.model, item.mesh)

        if outline["open_width"]:
            width = outline["thickness"] * OUTLINE_WIDTH_SCALE
            clamp_depth = 0.0
        else:
            width = outline["thickness"]
            clamp_depth = 1.0

        view_scale = 1.0
        if outline["open_width"] and width > 0.0:
            ring = width * OUTLINE_REFERENCE_PROJECTION
            if size_ndc is not None:
                ring = max(2.0 / max(view.height, 1), min(ring, OUTLINE_SIZE_SHARE * size_ndc))
            view_scale = ring / (width * max(abs(view.projection[1, 1]), 0.0001))

        options, base = self._outline_options(binding)
        units = options.texture_units
        program = self.program(base, options, ("outline", binding.program_key))
        if program is None:
            return
        gpu_mesh = self.gpu_mesh(item.mesh)
        if gpu_mesh.index is None:
            return
        if item.positions is not None:
            gpu_mesh.update(item.positions, item.normals)

        ctx.enable(moderngl.BLEND)
        ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
        ctx.blend_equation = moderngl.FUNC_ADD
        ctx.enable(moderngl.DEPTH_TEST)
        ctx.depth_func = "<="
        ctx.enable(moderngl.CULL_FACE)
        ctx.cull_face = "back" if np.linalg.det(item.matrix[:3, :3]) < 0 else "front"
        fbo.depth_mask = False
        fbo.color_mask = (True, True, True, True)
        fbo.use()

        color = tuple(float(c) / 255.0 if max(outline["color"]) > 1.0 else float(c) for c in outline["color"])
        self._bind_common(program, view, item.matrix, None, item.tint, self._uv_rows(item))
        self._set(program, "unf_frg_outline_color", color)
        self._set(program, "unf_frg_alpha_ref", float(binding.state.alpha_ref))
        self._set(program, "unf_vtx_silhouette_0", (1.0, float(outline["depth_min"]), float(outline["depth_max"]),
                                                    1.0 if item.mesh.flags is not None else 0.0))
        self._set(program, "unf_vtx_silhouette_1", (float(width), 1.0 - float(outline["visibility"]),
                                                    1.0 - float(outline["scale"]), clamp_depth))
        self._set(program, "unf_vtx_outline_view", float(view_scale))
        self._bind_textures(program, binding, units)
        if base is not None:
            self._bind_palette(program, binding)
        gpu_mesh.vao(program).render(moderngl.TRIANGLES)
