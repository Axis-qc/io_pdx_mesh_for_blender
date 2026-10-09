"""
Build/refresh Blender preview objects for a simulated PDX particle system.

 渲染策略：
 - 每个子系统一个网格对象，容量 = max_amount（封顶 4000），quad 拓扑一次建好；
   每帧只 foreach 刷新顶点坐标 / 顶点色 / UV，未用到的 quad 收成零面积退化面。
 - 立牌（billboard=yes 或 pitch 非 90）面向活动相机；pitch≈90 的粒子（冲击波环）
   平铺在水平面。
 - 坐标映射：模拟空间 (x, y, z) → Blender (x, -z, y)（粒子 +Y 朝上 → Blender +Z；
   粒子 -Z 朝飞船 → Blender -Y）。
 - 材质：ParticleAdditive → 发光叠加观感；ParticleAlphaBlend → 普通透明混合。
 - 显示缩放（默认 0.02）只影响整体呈现尺寸，等比，不改内部比例；
   不复现父级/entity 挂载缩放。

 author : Axis-qc (fork of ross-g/io_pdx_mesh 0.91.2)
"""

import logging
import math

import bpy  # type: ignore

from .simulator import SystemSim

IO_LOG = logging.getLogger("io_pdx.particles")

MAX_QUADS = 4000


def _set_input(node, names, value):
    """按候选名列表给节点赋值（兼容 4.x/5.x Principled 插槽改名）。"""
    for n in names:
        if n in node.inputs:
            node.inputs[n].default_value = value
            return True
    return False


class SubsystemPreview:
    def __init__(self, sub_sim, collection, display_scale, library):
        self.sub_sim = sub_sim
        self.display_scale = display_scale
        self.library = library
        self.obj = None
        self.mesh = None
        self.capacity = 0
        self._build(collection)

    # ---- 一次性构建 ----

    def _build(self, collection):
        sub = self.sub_sim.sub
        self.capacity = max(1, min(sub.max_amount, MAX_QUADS))
        cap = self.capacity

        mesh = bpy.data.meshes.new("pdx_part_{0}".format(self.sub_sim.name))
        verts = []
        faces = []
        for _ in range(cap):
            verts.extend([(0.0, 0.0, 0.0)] * 4)
            b = len(faces) * 4
            faces.append((b, b + 1, b + 2, b + 3))
        mesh.from_pydata(verts, [], faces)
        mesh.update()
        # 顶点色与 UV
        mesh.color_attributes.new(name="Col", type="FLOAT_COLOR", domain="POINT")
        mesh.uv_layers.new(name="UVMap")

        obj = bpy.data.objects.new("pdx_part_{0}".format(self.sub_sim.name), mesh)
        collection.objects.link(obj)
        obj.hide_render = True  # 预览对象不参与 F12 渲染

        mat = self._material(sub)
        if mat is not None:
            obj.data.materials.append(mat)

        self.obj = obj
        self.mesh = mesh

    def _material(self, sub):
        tex_path = None
        if sub.texture_file and self.library is not None:
            tex_path = self.library.resolve_texture(
                sub.texture_file, self.sub_sim.system.source_file or "")
        additive = sub.texture_shader != "ParticleAlphaBlend"

        mat = bpy.data.materials.new("pdx_mat_{0}".format(self.sub_sim.name))
        mat.use_nodes = True
        if hasattr(mat, "surface_render_method"):
            mat.surface_render_method = "BLENDED"
        else:
            try:
                mat.blend_method = "BLEND"
            except Exception:  # noqa: BLE001
                pass
        mat.use_backface_culling = False

        nt = mat.node_tree
        nodes = nt.nodes
        links = nt.links
        out = nodes.get("Material Output")
        bsdf = nodes.get("Principled BSDF")
        if bsdf is None:
            bsdf = nodes.new("ShaderNodeBsdfPrincipled")

        vcol = nodes.new("ShaderNodeVertexColor")
        vcol.layer_name = "Col"

        rgb_out = vcol.outputs["Color"]
        alpha_out = vcol.outputs["Alpha"]

        if tex_path:
            try:
                img = bpy.data.images.load(tex_path, check_existing=True)
                try:
                    img.colorspace_settings.name = "sRGB"
                except Exception:  # noqa: BLE001
                    pass
                tex = nodes.new("ShaderNodeTexImage")
                tex.image = img
                tex.extension = "CLIP" if (sub.sheet_x * sub.sheet_y) > 1 else "REPEAT"
                # 纹理颜色 × 顶点色（按 socket 类型+名称动态查找，兼容 4.x/5.x identifier 差异）
                mix = nodes.new("ShaderNodeMix")
                mix.data_type = "RGBA"
                mix.blend_type = "MULTIPLY"
                mix.inputs["Factor"].default_value = 1.0
                a_sock = next(s for s in mix.inputs if s.type == "RGBA" and s.name == "A")
                b_sock = next(s for s in mix.inputs if s.type == "RGBA" and s.name == "B")
                res_sock = next(s for s in mix.outputs if s.type == "RGBA" and s.name == "Result")
                links.new(tex.outputs["Color"], a_sock)
                links.new(vcol.outputs["Color"], b_sock)
                rgb_out = res_sock
                # alpha = 纹理 alpha × 顶点色 alpha
                amul = nodes.new("ShaderNodeMath")
                amul.operation = "MULTIPLY"
                links.new(tex.outputs["Alpha"], amul.inputs[0])
                links.new(vcol.outputs["Alpha"], amul.inputs[1])
                alpha_out = amul.outputs[0]
            except Exception as err:  # noqa: BLE001
                IO_LOG.warning("texture load failed %s: %s", tex_path, err)

        _set_input(bsdf, ["Base Color"], (0.0, 0.0, 0.0, 1.0))
        _set_input(bsdf, ["Roughness"], 1.0)
        _set_input(bsdf, ["Specular IOR Level", "Specular"], 0.0)
        _set_input(bsdf, ["Emission Color", "Emission"], (1.0, 1.0, 1.0, 1.0))
        _set_input(bsdf, ["Emission Strength"], 2.0 if additive else 0.4)
        links.new(rgb_out, bsdf.inputs["Base Color"])
        links.new(rgb_out, bsdf.inputs["Emission Color"] if "Emission Color" in bsdf.inputs else bsdf.inputs["Emission"])
        links.new(alpha_out, bsdf.inputs["Alpha"])
        if out is not None:
            links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
        return mat

    # ---- 每帧刷新 ----

    def refresh(self, camera_inv=None):
        sim = self.sub_sim
        sub = sim.sub
        ds = self.display_scale
        snap = sim.snapshot()
        cap = self.capacity
        if cap <= 0:
            return 0

        # 相机立牌基向量：世界矩阵的列 0/列 1 = 相机局部 X/Y 轴在世界中的方向
        if camera_inv is not None:
            m = camera_inv
            right = (m[0][0], m[1][0], m[2][0])
            up = (m[0][1], m[1][1], m[2][1])
        else:
            right, up = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)

        # pitch≈90 的粒子是水平环（冲击波），平铺在水平面上
        flat = abs(sub.particle_pitch[0] - 90.0) < 1.0 and sub.particle_pitch[1] < 1.0
        if flat:
            right, up = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)

        sheet_x = max(sub.sheet_x, 1)
        sheet_y = max(sub.sheet_y, 1)
        do_sheet = sub.sheet_anim and (sheet_x * sheet_y) > 1

        coords = [0.0] * (cap * 12)
        colors = [0.0] * (cap * 16)
        uvs = [0.0] * (cap * 8)
        n = min(len(snap), cap)

        for i in range(n):
            x, y, z, size, r, g, b, a, ang, cell = snap[i]
            c, s = math.cos(ang), math.sin(ang)
            r2 = (right[0] * c - up[0] * s, right[1] * c - up[1] * s, right[2] * c - up[2] * s)
            u2 = (right[0] * s + up[0] * c, right[1] * s + up[1] * c, right[2] * s + up[2] * c)
            half = size * 0.5 * ds
            bx = x * ds
            by = -z * ds
            bz = y * ds
            vbase = i * 12
            for (su, sv), k in (((-1.0, -1.0), 0), ((1.0, -1.0), 1), ((1.0, 1.0), 2), ((-1.0, 1.0), 3)):
                o = vbase + k * 3
                coords[o] = bx + (r2[0] * su + u2[0] * sv) * half
                coords[o + 1] = by + (r2[1] * su + u2[1] * sv) * half
                coords[o + 2] = bz + (r2[2] * su + u2[2] * sv) * half
            cbase = i * 16
            for k in range(4):
                o = cbase + k * 4
                colors[o] = r
                colors[o + 1] = g
                colors[o + 2] = b
                colors[o + 3] = a
            ubase = i * 8
            if do_sheet and cell >= 0:
                cx = cell % sheet_x
                cy = cell // sheet_x
                u0 = cx / sheet_x
                v0 = 1.0 - (cy + 1) / sheet_y
                du = 1.0 / sheet_x
                dv = 1.0 / sheet_y
            else:
                u0, v0, du, dv = 0.0, 0.0, 1.0, 1.0
            for (su, sv), k in (((0.0, 0.0), 0), ((1.0, 0.0), 1), ((1.0, 1.0), 2), ((0.0, 1.0), 3)):
                o = ubase + k * 2
                uvs[o] = u0 + su * du
                uvs[o + 1] = v0 + sv * dv

        mesh = self.mesh
        mesh.vertices.foreach_set("co", coords)
        mesh.color_attributes["Col"].data.foreach_set("color", colors)
        mesh.uv_layers[0].data.foreach_set("uv", uvs)
        mesh.update()
        return n


class PreviewScene:
    """一个粒子系统的完整预览（collection + 各子系统对象 + 参照球）。"""

    def __init__(self, system, library, display_scale=0.02, time_offset=0.0):
        self.system = system
        self.library = library
        self.sim = SystemSim(system, time_offset)
        self.display_scale = display_scale
        self.collection = None
        self.sub_previews = []
        self.ref_sphere = None
        self._build()

    def _build(self):
        name = "pdx_particles_{0}".format(self.system.name)
        coll = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(coll)
        self.collection = coll
        for sub_sim in self.sim.subs:
            self.sub_previews.append(SubsystemPreview(sub_sim, coll, self.display_scale, self.library))
        # 参照球（线框，半径 30 局部单位 = 星球量级中值）
        mesh = bpy.data.meshes.new("pdx_ref_sphere")
        verts, faces = _uv_sphere(30.0, 24, 12)
        mesh.from_pydata(verts, [], faces)
        mesh.update()
        sph = bpy.data.objects.new("pdx_ref_sphere", mesh)
        coll.objects.link(sph)
        sph.display_type = "WIRE"
        sph.hide_render = True
        sph.hide_viewport = True  # 默认关
        self.ref_sphere = sph

    def show_ref_sphere(self, show):
        self.ref_sphere.hide_viewport = not show
        self.ref_sphere.hide_render = not show

    def refresh(self):
        """按当前场景帧驱动模拟并刷新所有网格。"""
        scene = bpy.context.scene
        fps = (scene.render.fps or 24) * max(scene.render.fps_base, 1e-6)
        t = (scene.frame_current - scene.frame_start) / fps
        self.sim.advance(max(t, 0.0))
        camera_inv = None
        cam = scene.camera
        if cam is not None:
            camera_inv = cam.matrix_world
        total = 0
        for sp in self.sub_previews:
            total += sp.refresh(camera_inv)
        return total

    def set_subsystem_enabled(self, name, enabled):
        self.sim.enabled[name] = enabled
        for sp in self.sub_previews:
            if sp.sub_sim.name == name:
                sp.obj.hide_viewport = not enabled

    def destroy(self):
        """删对象/网格/材质/贴图/集合（只删本预览创建的）。"""
        images = set()
        mats = set()
        meshes = set()
        for sp in self.sub_previews:
            if sp.obj is None:
                continue
            for slot in sp.obj.data.materials:
                if slot:
                    mats.add(slot.name)
                    if slot.use_nodes:
                        for node in slot.node_tree.nodes:
                            if node.type == "TEX_IMAGE" and node.image:
                                images.add(node.image.name)
            meshes.add(sp.obj.data.name)
            bpy.data.objects.remove(sp.obj, do_unlink=True)
        if self.ref_sphere is not None:
            meshes.add(self.ref_sphere.data.name)
            bpy.data.objects.remove(self.ref_sphere, do_unlink=True)
        for n in meshes:
            m = bpy.data.meshes.get(n)
            if m and m.users == 0:
                bpy.data.meshes.remove(m)
        for n in mats:
            m = bpy.data.materials.get(n)
            if m and m.users == 0:
                bpy.data.materials.remove(m)
        for n in images:
            img = bpy.data.images.get(n)
            if img and img.users == 0:
                bpy.data.images.remove(img)
        if self.collection is not None:
            try:
                bpy.data.collections.remove(self.collection)
            except ReferenceError:
                pass
            self.collection = None
        self.sub_previews = []


def _uv_sphere(radius, segments, rings):
    """不依赖 ops 的 UV 球拓扑（避免后台/错误上下文 poll 失败）。"""
    verts = []
    # 顶行
    verts.append((0.0, 0.0, radius))
    for j in range(1, rings):
        phi = math.pi * j / rings
        for i in range(segments):
            theta = 2.0 * math.pi * i / segments
            verts.append((
                radius * math.sin(phi) * math.cos(theta),
                radius * math.sin(phi) * math.sin(theta),
                radius * math.cos(phi),
            ))
    verts.append((0.0, 0.0, -radius))
    faces = []
    last = len(verts) - 1
    for i in range(segments):
        faces.append((0, 1 + i, 1 + (i + 1) % segments))
    for j in range(rings - 2):
        base0 = 1 + j * segments
        base1 = base0 + segments
        for i in range(segments):
            i2 = (i + 1) % segments
            faces.append((base0 + i, base1 + i, base1 + i2, base0 + i2))
    base0 = 1 + (rings - 2) * segments
    for i in range(segments):
        faces.append((last, base0 + (i + 1) % segments, base0 + i))
    return verts, faces
