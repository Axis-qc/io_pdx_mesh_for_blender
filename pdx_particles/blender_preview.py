"""
Build/refresh Blender preview objects for a simulated PDX particle system.

 渲染策略：
 - 每个子系统一个网格对象，容量 = max_amount（封顶 4000），quad 拓扑一次建好；
   每帧只 foreach 刷新顶点坐标 / 顶点色 / UV，未用到的 quad 收成零面积退化面。
 - 立牌（billboard=yes 或 pitch 非 90）面向镜头：3D 视口镜头优先，没有视口才退回
   场景摄像机；billboard=no 的固定朝向粒子不跟随镜头，姿态由 particle_yaw（绕世界 +Y）
   与 particle_pitch（绕面片长轴自转）决定，两者为 0 时贴图 U=+X、V=+Y、法线 +Z。
   billboard=yes 恒正面朝镜头并完全忽略这两个字段（pdx_axis_probe 实机定案）。
 - 坐标映射：模拟空间 (x, y, z) → Blender (x, z, y)，与 mesh 导入的 SPACE_MATRIX
   同一套（交换 Y/Z）。粒子空间 -Z 是前方 → Blender -Y 是前方，与导入模型的
   船头方向（-Y）一致；粒子上方 +Y → Blender +Z。
 - 材质：ParticleAdditive → 发光叠加观感；ParticleAlphaBlend → 普通透明混合。
 - 不复现父级/entity 挂载缩放，只保证粒子系统内部尺寸比例。

 author : Axis-qc (fork of ross-g/io_pdx_mesh 0.91.2)
"""

import logging
import math

import bpy  # type: ignore

# numpy 是可选加速项：Blender 自带 numpy，但插件被裁剪过的环境可能没有。
# 缺失时退回纯 Python 实现（结果完全一致，只是慢），不影响可用性。
try:
    import numpy as _NP  # type: ignore
except Exception:  # noqa: BLE001
    _NP = None

from .simulator import PARTICLE_FPS, SystemSim

IO_LOG = logging.getLogger("io_pdx.particles")

MAX_QUADS = 4000

# 面片四个角的 (su, sv)，与纯 Python 版保持同序，避免顶点绕序变化
_CORNERS = ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0))
_UV_CORNERS = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))


def _region_3d_of(window):
    """一个 window 里面积最大的 3D 视口 → (像素面积, region_3d)；没有则 (0, None)。

    screen.areas 是普通集合，没有 active / active_index（5.2 实测两者都不存在），
    所以「这个窗口的活动视口」也只能按面积取。同一窗口内通常只有一个 VIEW_3D，
    分屏出多个时用户视线主要落在大的那个上。
    """
    screen = getattr(window, "screen", None)
    if screen is None:
        return 0, None
    best = None
    best_px = -1
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        spaces = getattr(area, "spaces", None)
        space = spaces.active if spaces is not None else None
        r3d = getattr(space, "region_3d", None) if space is not None else None
        if r3d is None:
            continue
        px = area.width * area.height
        if px > best_px:
            best_px = px
            best = r3d
    if best is None:
        return 0, None
    return best_px, best


def _active_region_3d():
    """取「当前活动窗口」的 3D 视口 region_3d，取不到再全局兜底。

    用户要的是主窗口里正在看的那个视口，所以先认 bpy.context.window（实测定时器
    里可用，但 bpy.context.area 为 None——「活动区域」没有 API）。context 取不到
    窗口时（后台、脚本环境）退回「所有窗口里面积最大的 VIEW_3D」，单窗口时两者
    等价。

    不直接用全局最大：多开窗口时，主窗口较小而副窗口较大就会跟错窗口，面片会朝
    一个用户没在看的视口转。
    """
    win = getattr(bpy.context, "window", None)
    if win is not None:
        _px, r3d = _region_3d_of(win)
        if r3d is not None:
            return r3d
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return None
    best = None
    best_px = -1
    for w in wm.windows:
        px, r3d = _region_3d_of(w)
        if r3d is not None and px > best_px:
            best_px = px
            best = r3d
    return best


def active_view_basis():
    """取 3D 视口的镜头基向量 (right, up)，没有视口时返回 None。

    用户看到的是「界面里的实际镜头」，即视口视角（region_3d.view_rotation），
    不是场景里摆的摄像机（scene.camera）。两者可能同时存在，且视口总是存在，
    所以视口优先、摄像机只作回退。

    四元数取矩阵列 0/列 1，与 view_matrix.inverted() 的列逐位一致，且两向量
    正交、单位长（均实测）。
    """
    r3d = _active_region_3d()
    if r3d is None:
        return None
    try:
        m = r3d.view_rotation.to_matrix()
    except Exception:  # noqa: BLE001
        return None
    return ((m[0][0], m[1][0], m[2][0]), (m[0][1], m[1][1], m[2][1]))


def view_rotation_key():
    """视口镜头的可比较指纹，用于「镜头不动了才刷新」的脏检查。"""
    r3d = _active_region_3d()
    if r3d is None:
        return None
    q = r3d.view_rotation
    return (round(q[0], 6), round(q[1], 6), round(q[2], 6), round(q[3], 6))


def camera_basis():
    """场景摄像机的镜头基向量（无摄像机返回 None）。视口不可用时的回退。"""
    scene = getattr(bpy.context, "scene", None)
    cam = getattr(scene, "camera", None) if scene is not None else None
    if cam is None:
        return None
    m = cam.matrix_world
    return ((m[0][0], m[1][0], m[2][0]), (m[0][1], m[1][1], m[2][1]))


def _orient_basis(right, up, p_ang, y_ang):
    """把面片基向量按 pitch/yaw 两轴自转转过 p_ang / y_ang 弧度。

    这是「三轴自转」的累积增量（快照末两列），作用在已选好的初始基向量上；
    与 _fixed_quad_basis 的基角（particle_yaw/pitch）是两回事，不会重复转。

    两者都为 0 时原样返回（绝大多数子系统走这条快路径，不做任何三角运算）。
    转轴沿世界 +X / +Y，与 rotation_speed_pitch / _yaw 的语义对应。
    """
    if p_ang == 0.0 and y_ang == 0.0:
        return right, up
    if p_ang != 0.0:
        cp, sp = math.cos(p_ang), math.sin(p_ang)
        right = (right[0], right[1] * cp - right[2] * sp,
                 right[1] * sp + right[2] * cp)
        up = (up[0], up[1] * cp - up[2] * sp, up[1] * sp + up[2] * cp)
    if y_ang != 0.0:
        cy, sy = math.cos(y_ang), math.sin(y_ang)
        right = (right[0] * cy + right[2] * sy, right[1],
                 -right[0] * sy + right[2] * cy)
        up = (up[0] * cy + up[2] * sy, up[1], -up[0] * sy + up[2] * cy)
    return right, up


def _rot_about(axis, v, ang):
    """罗德里格斯公式：把向量 v 绕单位轴 axis 转 ang 弧度。"""
    ax, ay, az = axis
    cross = (ay * v[2] - az * v[1], az * v[0] - ax * v[2], ax * v[1] - ay * v[0])
    dot = ax * v[0] + ay * v[1] + az * v[2]
    c, s = math.cos(ang), math.sin(ang)
    k = dot * (1.0 - c)
    return (v[0] * c + cross[0] * s + ax * k,
            v[1] * c + cross[1] * s + ay * k,
            v[2] * c + cross[2] * s + az * k)


def _fixed_quad_basis(yaw_deg, pitch_deg):
    """billboard=no 固定朝向面片的 (right, up)，Blender 空间。

    依据 pdx_axis_probe 标定件实机读数（billboard=no，逐格单变量）：
      默认姿态：贴图 U（箭头）→ 世界 +X、V → 世界 +Y，面片立 X-Y 平面、法线 +Z。
      particle_yaw  ：绕世界 +Y 转，+90° 把 +X 转到 -Z。
      particle_pitch：绕**面片自身长轴（贴图 U 轴）**自转，不改变指向，只翻平面。
                      （名字叫 pitch，实际是 roll —— 曾按「绕 right 抬起」实现，已证伪）

    本函数返回**Blender 空间**的基向量。引擎与 Blender 经 (x, z, y) 交换（SPACE_MATRIX），
    该映射行列式为 -1（镜像），所以**引擎里的正向旋转在 Blender 里符号相反**：
      引擎 U=+X → Blender (1, 0, 0)   ；
      引擎 V=+Y → Blender (0, 0, 1)   ；
      引擎绕 +Y 转 +θ → Blender 绕 +Z 转 **-θ**；
      绕长轴自转同理，取 **-pitch**。

    如实机读数逐条自洽（z1/z2/z3/z7 四格）：
      yaw=0,  pitch=0   → 箭头 +X                       （z1）
      yaw=90, pitch=0   → 箭头 -Y（引擎 -Z）             （z2）
      yaw=0,  pitch=90  → 箭头 +X、面片水平（法线 +Z）    （z3）
      yaw=90, pitch=90  → 箭头 -Y、面片水平               （z7）
    注意 yaw 与 pitch 的旋转都作用于**两个基向量**，且 pitch 的转轴是 yaw 之后的当前
    长轴，才能同时满足 z2 与 z7。

    默认姿态下 right × up = -Y（Blender 正面视角方向），材质关了背面剔除，正反都可见。
    """
    right = (1.0, 0.0, 0.0)
    up = (0.0, 0.0, 1.0)
    if yaw_deg:
        axis = (0.0, 0.0, 1.0)
        ang = -math.radians(yaw_deg)
        right = _rot_about(axis, right, ang)
        up = _rot_about(axis, up, ang)
    if pitch_deg:
        # 绕当前长轴（= right）自转；right 与转轴平行，保持不变
        up = _rot_about(right, up, -math.radians(pitch_deg))
    return right, up


def _build_arrays_py(snap, n, cap, right, up, sheet_x, sheet_y, do_sheet):
    """纯 Python 组数组（numpy 不可用时的回退，逻辑与向量化版逐项对应）。"""
    coords = [0.0] * (cap * 12)
    colors = [0.0] * (cap * 16)
    uvs = [0.0] * (cap * 8)
    for i in range(n):
        x, y, z, size, r, g, b, a, ang, cell, p_ang, y_ang = snap[i]
        c, s = math.cos(ang), math.sin(ang)
        right_i, up_i = _orient_basis(right, up, p_ang, y_ang)
        r2 = (right_i[0] * c - up_i[0] * s, right_i[1] * c - up_i[1] * s,
              right_i[2] * c - up_i[2] * s)
        u2 = (right_i[0] * s + up_i[0] * c, right_i[1] * s + up_i[1] * c,
              right_i[2] * s + up_i[2] * c)
        # 1 局部单位 = 1 Blender 米：size 直读，size=1 即 1x1 标准面片
        half = size * 0.5
        # 坐标映射：与 mesh 导入同一套 (x, z, y)（见 SPACE_MATRIX）。
        # 粒子空间 -Z 是前方，映射后 Blender -Y 是前方，与导入的模型船头一致。
        bx, by, bz = x, z, y
        vbase = i * 12
        for (su, sv), k in zip(_CORNERS, range(4)):
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
        if do_sheet:
            # cell<0 = 贴图集没配动画（spritesheet_animation=no）：实机读数是
            # 默认只取第 1 帧，所以退回 cell 0，而不是铺满整张贴图。
            cell_i = cell if cell >= 0 else 0
            cx = cell_i % sheet_x
            cy = cell_i // sheet_x
            u0 = cx / sheet_x
            v0 = 1.0 - (cy + 1) / sheet_y
            du = 1.0 / sheet_x
            dv = 1.0 / sheet_y
        else:
            u0, v0, du, dv = 0.0, 0.0, 1.0, 1.0
        for (su, sv), k in zip(_UV_CORNERS, range(4)):
            o = ubase + k * 2
            uvs[o] = u0 + su * du
            uvs[o + 1] = v0 + sv * dv
    return coords, colors, uvs


def _build_arrays_np(snap, n, cap, right, up, sheet_x, sheet_y, do_sheet):
    """numpy 向量化组数组，与纯 Python 版逐位等价。"""
    np = _NP
    # snap 是 list[tuple]，转成 (n,12) 一次成型；多余容量留 0（与纯 Python 版
    # 初始化为 0 的语义一致——mesh 顶点数固定为 cap，未用到的槽位保持原值/0）。
    # 宽度 12：末两列是面片朝向的 pitch/yaw（弧度），供三轴自转用。
    arr = np.array(snap[:n], dtype=np.float64).reshape(n, 12)
    px, py, pz = arr[:, 0], arr[:, 1], arr[:, 2]
    size = arr[:, 3]
    r, g, b, a = arr[:, 4], arr[:, 5], arr[:, 6], arr[:, 7]
    ang, cell = arr[:, 8], arr[:, 9]
    p_ang, y_ang = arr[:, 10], arr[:, 11]

    coords = np.zeros((cap, 4, 3), dtype=np.float32)
    colors = np.zeros((cap, 4, 4), dtype=np.float32)
    uvs = np.zeros((cap, 4, 2), dtype=np.float32)

    c = np.cos(ang)
    s = np.sin(ang)
    R = np.asarray(right, dtype=np.float64)
    U = np.asarray(up, dtype=np.float64)
    # 三轴自转：逐粒子转基向量。全部为 0 时（绝大多数子系统）跳过，省掉两次
    # (n,3) 的三角运算。
    if np.any(p_ang) or np.any(y_ang):
        cp, sp = np.cos(p_ang)[:, None], np.sin(p_ang)[:, None]
        cy, sy = np.cos(y_ang)[:, None], np.sin(y_ang)[:, None]
        # 把常量基向量广播成 (n,3)，再逐粒子旋转（绕 X 转 pitch，再绕 Y 转 yaw）
        R = np.broadcast_to(R, (n, 3)).copy()
        U = np.broadcast_to(U, (n, 3)).copy()
        Rx0 = R[:, 0].copy()
        Rx1 = R[:, 1] * cp[:, 0] - R[:, 2] * sp[:, 0]
        Rx2 = R[:, 1] * sp[:, 0] + R[:, 2] * cp[:, 0]
        Ux1 = U[:, 1] * cp[:, 0] - U[:, 2] * sp[:, 0]
        Ux2 = U[:, 1] * sp[:, 0] + U[:, 2] * cp[:, 0]
        Ux0 = U[:, 0].copy()
        R = np.stack([Rx0 * cy[:, 0] + Rx2 * sy[:, 0], Rx1,
                      -Rx0 * sy[:, 0] + Rx2 * cy[:, 0]], axis=1)
        U = np.stack([Ux0 * cy[:, 0] + Ux2 * sy[:, 0], Ux1,
                      -Ux0 * sy[:, 0] + Ux2 * cy[:, 0]], axis=1)
        r2 = R * c[:, None] - U * s[:, None]                    # (n,3)
        u2 = R * s[:, None] + U * c[:, None]
    else:
        r2 = R[None, :] * c[:, None] - U[None, :] * s[:, None]  # (n,3)
        u2 = R[None, :] * s[:, None] + U[None, :] * c[:, None]

    half = size * 0.5
    # 坐标映射：与纯 Python 版一致，同 mesh 导入的 (x, z, y)
    base = np.stack([px, pz, py], axis=1)                      # (n,3)
    su = np.asarray([c0 for c0, _ in _CORNERS], dtype=np.float64)[None, :, None]
    sv = np.asarray([c1 for _, c1 in _CORNERS], dtype=np.float64)[None, :, None]
    off = (r2[:, None, :] * su + u2[:, None, :] * sv) * half[:, None, None]
    coords[:n] = base[:, None, :] + off

    colors[:n, :, 0] = r[:, None]
    colors[:n, :, 1] = g[:, None]
    colors[:n, :, 2] = b[:, None]
    colors[:n, :, 3] = a[:, None]

    if do_sheet:
        # cell<0 = 没配 spritesheet_animation：实机读数是默认只取第 1 帧，
        # 所以退回 cell 0，而不是铺满整张贴图。
        cell_i = cell.astype(np.int64)
        safe = np.where(cell_i >= 0, cell_i, 0)
        cx = safe % sheet_x
        cy = safe // sheet_x
        u0 = cx / float(sheet_x)
        v0 = 1.0 - (cy + 1) / float(sheet_y)
        du = np.full(n, 1.0 / float(sheet_x), dtype=np.float64)
        dv = np.full(n, 1.0 / float(sheet_y), dtype=np.float64)
    else:
        u0 = np.zeros(n, dtype=np.float64)
        v0 = np.zeros(n, dtype=np.float64)
        du = np.ones(n, dtype=np.float64)
        dv = np.ones(n, dtype=np.float64)

    usu = np.asarray([u for u, _ in _UV_CORNERS], dtype=np.float64)[None, :]
    vsv = np.asarray([v for _, v in _UV_CORNERS], dtype=np.float64)[None, :]
    uvs[:n, :, 0] = u0[:, None] + usu * du[:, None]
    uvs[:n, :, 1] = v0[:, None] + vsv * dv[:, None]

    return coords.reshape(-1), colors.reshape(-1), uvs.reshape(-1)


def _set_input(node, names, value):
    """按候选名列表给节点赋值（兼容 4.x/5.x Principled 插槽改名）。"""
    for n in names:
        if n in node.inputs:
            node.inputs[n].default_value = value
            return True
    return False


class SubsystemPreview:
    def __init__(self, sub_sim, collection, library):
        self.sub_sim = sub_sim
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
            # 顶点顺序取反（b+3..b）而不是正向：坐标映射 (x,z,y) 的行列式为 -1
            # （镜像），同样的绕序在 Blender 里算出的面法线符号与引擎相反。引擎
            # 默认姿态的 U×V = +X×+Y = +Z，正向绕序搬到 Blender 会得到 -Z，反向后
            # 才是 +Z，与 pdx_axis_probe 标定的「法线 +Z」对齐。
            # 画面不变（材质关了背面剔除、纯自发光，正反同图），改的是「面朝哪边」
            # 这个属性本身，以及依赖它的法线/光照类判断。
            faces.append((b + 3, b + 2, b + 1, b))
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
            if tex_path is None:
                # 找不到贴图多半是原版根没配（mod 粒子常引用原版贴图）。不报错
                # 而是留下没有纹理的材质，用户只会看到一片光斑，很难定位。
                IO_LOG.warning(
                    "particle texture not found: %s (subsystem %s); "
                    "add the Stellaris install folder as Vanilla root",
                    sub.texture_file, self.sub_sim.name)
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
        # 节点按 type 查，不按名字：Blender 界面语言非英文时节点名会被翻译成
        # 本地语言（中文界面下出力节点叫「材质输出」、BSDF 叫「原理化 BSDF」），
        # 而 nodes.get("Material Output") 这种英文名查找会全部落空。落空的后果
        # 不是报错而是静默错接：会新建第二个 BSDF 接顶点色，但它从不连到输出，
        # 真正出图的仍是默认空 BSDF，看起来就是「着色没绑上」。
        # 插槽名（Base Color / Alpha / Emission Color 等）不随界面语言变化，可照用。
        out = next((n for n in nodes if n.type == "OUTPUT_MATERIAL"), None)
        bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
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

    def refresh(self, cam_basis=None):
        sim = self.sub_sim
        sub = sim.sub
        snap = sim.snapshot()
        cap = self.capacity

        # 立牌基向量二选一：
        # - billboard=no：固定朝向，不跟随镜头，朝向由 particle_yaw / particle_pitch
        #   决定（pitch=90 的平铺姿态由 _fixed_quad_basis 算出水平面）；
        # - billboard=yes：恒正面朝镜头，忽略 particle_yaw / particle_pitch
        #   （标定件：billboard=yes 的 base / yaw=90 / pitch=90 三组显示完全一致）。
        # 判断顺序必须先看 billboard：先判 pitch≈90 会把 129 个两者都写的子系统
        # 摁平在水平面上，而它们本该正常朝镜头。
        if not sub.billboard:
            right, up = _fixed_quad_basis(sub.particle_yaw[0], sub.particle_pitch[0])
        elif cam_basis is not None:
            right, up = cam_basis
        else:
            # 既无视口也无摄像机：面向 -Y 立起（Blender 默认视向），up 取世界 +Z
            right, up = (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)

        sheet_x = max(sub.sheet_x, 1)
        sheet_y = max(sub.sheet_y, 1)
        # 贴图集只要 >1 帧就按格取 UV，与 spritesheet_animation 无关：anim=no 时
        # snapshot 给的 cell 为 -1，组数组里退回第 1 帧（实机读数即默认只取第 1 帧）。
        do_sheet = (sheet_x * sheet_y) > 1

        n = min(len(snap), cap)

        # 组数组：numpy 向量化，无 numpy 时回退纯 Python。拼数组是 refresh 的大头
        # （实测占 77%，比 foreach_set 本身贵）。
        if _NP is not None and n > 0:
            coords, colors, uvs = _build_arrays_np(
                snap, n, cap, right, up, sheet_x, sheet_y, do_sheet)
        else:
            coords, colors, uvs = _build_arrays_py(
                snap, n, cap, right, up, sheet_x, sheet_y, do_sheet)

        mesh = self.mesh
        mesh.vertices.foreach_set("co", coords)
        mesh.color_attributes["Col"].data.foreach_set("color", colors)
        mesh.uv_layers[0].data.foreach_set("uv", uvs)
        mesh.update()
        return n


class PreviewScene:
    """一个粒子系统的完整预览（collection + 各子系统对象 + 参照球）。"""

    def __init__(self, system, library, time_offset=0.0):
        self.system = system
        self.library = library
        self.sim = SystemSim(system, time_offset)
        self.collection = None
        self.sub_previews = []
        self.ref_sphere = None
        self.last_count = 0
        self._last_view_key = None
        self._build()

    def _build(self):
        name = "pdx_particles_{0}".format(self.system.name)
        coll = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(coll)
        self.collection = coll
        for sub_sim in self.sim.subs:
            self.sub_previews.append(SubsystemPreview(sub_sim, coll, self.library))
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
        """按当前场景帧驱动模拟并刷新所有网格。

        帧 → 秒 用固定的 PARTICLE_FPS（60），不用场景 render.fps：粒子数据里
        的时间量全部以秒为单位，拿可变的场景 fps 换算会让同一份数据在不同
        fps 的场景里跑出不同速度。

        面片朝向由 _view_basis 现取（视口 → 摄像机 → 兜底），所以帧驱动的刷新
        天然会用上最新视角。
        """
        scene = bpy.context.scene
        t = (scene.frame_current - scene.frame_start) / PARTICLE_FPS
        self.sim.advance(max(t, 0.0))
        total = 0
        basis = self._view_basis()
        for sp in self.sub_previews:
            total += sp.refresh(basis)
        self.last_count = total
        return total

    def _view_basis(self):
        """取当前应使用的镜头基向量：(视口 → 摄像机 → None)。"""
        basis = active_view_basis()
        if basis is None:
            basis = camera_basis()
        return basis

    def refresh_for_view(self, view_key):
        """视口镜头变化时调用：镜头停了才重写网格。

        返回重写的粒子数；镜头与上次相同则返回 None（什么都不做）。
        view_key 为 None（取不到视口）时不做脏检查，直接走普通 refresh。
        """
        if view_key is not None and view_key == self._last_view_key:
            return None
        self._last_view_key = view_key
        return self.refresh()

    def set_subsystem_enabled(self, name, enabled):
        self.sim.enabled[name] = enabled
        # 重新启用时把该子系统同步到当前时刻：它在禁用期间没有推进物理，
        # 只被对齐过 t；而 SubsystemSim.advance 在目标时间已经到达时会短路
        # （n_target <= n_sub 直接 return），于是启用后若帧号未变，它会停在
        # 空状态，用户看到的是「这个子系统整段消失」。这里显式推进一次。
        if enabled:
            scene = bpy.context.scene
            t = (scene.frame_current - scene.frame_start) / PARTICLE_FPS
            for sp in self.sub_previews:
                if sp.sub_sim.name == name:
                    sp.sub_sim.advance(max(t, 0.0))
                    sp.refresh(self._view_basis())
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
