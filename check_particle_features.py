"""
粒子子系统特征自检：逐项断言「解析到了」且「真的生效了」。

跑法（在插件根目录下）：
    & 'G:\\SteamLibrary\\steamapps\\common\\Blender\\blender.exe' -b --factory-startup \
      --python check_particle_features.py

检查四类特征，各自走不同代码路径，任一处没加载都会在这里失败：
  1. 立牌朝向   billboard=yes 的粒子是否跟随活动相机
  2. 平铺环     particle_pitch≈90 的粒子是否躺在水平面上
  3. 场发射     box 的三轴、sphere 的半径是否都被解析且真的散开
  4. 位置偏移   subsystem 的 position 是否加到出生点上

不修改任何文件；只在结尾打印报告并以退出码反映结果。
"""

import os
import sys

EXT = r"C:\Users\hp\AppData\Roaming\Blender Foundation\Blender\5.2\extensions\user_default"
sys.path.insert(0, EXT)

import bpy  # noqa: E402
from io_pdx_mesh.pdx_particles import parser as P  # noqa: E402
from io_pdx_mesh.pdx_particles import simulator as S  # noqa: E402
from io_pdx_mesh.pdx_particles import blender_preview as BP  # noqa: E402

GAME = r"G:\SteamLibrary\steamapps\common\Stellaris"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name,
                                  "  -- " + detail if detail else ""))
    return ok


print("=" * 78)
print("A. 解析层：字段是否被收下（不是丢进 unknown_keys）")
print("=" * 78)

lib = P.ParticleLibrary([GAME]).scan()
print("  索引系统数: {0}".format(len(lib.systems)))

subs = [sb for sy in lib.systems.values() for sb in sy.subsystems]
print("  子系统总数: {0}".format(len(subs)))

check("box 解析出 box_z 字段", all(hasattr(sb, "box_z") for sb in subs))

used_z = [sb for sb in subs if sb.emitter_type == "box" and sb.box_z[0] > 0]
check("存在用 box_emitter_z 的子系统", len(used_z) > 0,
      "{0} 个".format(len(used_z)))
still_unknown = [sb for sb in subs if "box_emitter_z" in sb.unknown_keys]
check("box_emitter_z 不再进 unknown_keys", len(still_unknown) == 0,
      "{0} 个残留".format(len(still_unknown)))

used_pos = [sb for sb in subs if any(abs(c) > 1e-9 for c in sb.position)]
check("存在带非零 position 的子系统", len(used_pos) > 0,
      "{0} / {1}".format(len(used_pos), len(subs)))

print()
print("=" * 78)
print("B. 模拟层：box 三轴真的散开（不是塌成一维或平面）")
print("=" * 78)


def spread_of(system, axis, frames=240):
    """跑一段时间，返回该子系统粒子的某轴跨度。"""
    sim = S.SystemSim(system)
    best = 0.0
    for i in range(1, frames + 1):
        sim.advance(i / float(S.PARTICLE_FPS))
        for s in sim.subs:
            if not s.particles:
                continue
            vals = [p[2 + axis] for p in s.particles]
            best = max(best, max(vals) - min(vals))
    return best


def spread_of_subsystem(sy, name, n=120):
    """复算出生点跨度（不依赖存活窗口）。"""
    st = next((s for s in S.SystemSim(sy).subs if s.sub.name == name), None)
    if st is None:
        return None
    for i in range(n):
        st.spawn_count = i
        st._spawn(0.0)
    pts = list(st.particles)
    if not pts:
        return None
    return tuple(max(p[2 + k] for p in pts) - min(p[2 + k] for p in pts)
                 for k in range(3))


# 原版 857 个 box 里，绝大多数三轴都是 0（box_emitter_* 写成 0.0），
# 真正 z 非零的只有 22 个。挑一个「只有 z 非零」的样本来验：修前 box_emitter_z
# 被整个丢掉，出生点三轴全 0 → 所有粒子挤在一个点；修后应散成一条 z 向线段。
box_sys = None
for nm, sy in lib.systems.items():
    for sb in sy.subsystems:
        if (sb.emitter_type == "box" and sb.box_z[0] > 0
                and sb.box_x[0] == 0 and sb.box_y[0] == 0):
            box_sys = (nm, sy, sb)
            break
    if box_sys:
        break

if box_sys is None:
    # 退而求其次：任意 z 非零的 box
    for nm, sy in lib.systems.items():
        for sb in sy.subsystems:
            if sb.emitter_type == "box" and sb.box_z[0] > 0:
                box_sys = (nm, sy, sb)
                break
        if box_sys:
            break

if box_sys:
    nm, sy, sb = box_sys
    st = next(s for s in S.SystemSim(sy).subs if s.sub.name == sb.name)
    print("  样本: {0} / {1}".format(nm, sb.name))
    print("    box 声明 x={0:g} y={1:g} z={2:g}  (取 value)".format(
        sb.box_x[0], sb.box_y[0], sb.box_z[0]))
    print("    模拟采用半长 x={0:g} y={1:g} z={2:g}".format(
        st.box_half_x, st.box_half_y, st.box_half_z))

    spans = spread_of_subsystem(sy, sb.name)
    if spans is None:
        check("box 样本生成了粒子", False)
    else:
        print("    发射点跨度 x={0:.3f} y={1:.3f} z={2:.3f}".format(*spans))
        check("box 的 z 轴确实散开（修前恒为 0）", spans[2] > 1e-6,
              "z 跨度 {0:.4f}".format(spans[2]))
        decl = (abs(st.box_half_x) * 2, abs(st.box_half_y) * 2, abs(st.box_half_z) * 2)
        bad = [n for n, sp_, d in zip("xyz", spans, decl) if d > 0 and sp_ <= 1e-6]
        check("声明非零的轴都有分布", not bad,
              "缺失轴: {0}".format(bad) if bad else "与声明一致")
else:
    check("找到 z 非零的 box 样本", False)

print()
print("=" * 78)
print("C. 位置偏移：position 真的加到出生点上")
print("=" * 78)

pos_sys = None
for nm, sy in lib.systems.items():
    for sb in sy.subsystems:
        if (sb.emitter_type == "point" and sb.emitter_type != "box"
                and any(abs(c) > 1.0 for c in sb.position)):
            pos_sys = (nm, sy, sb)
            break
    if pos_sys:
        break

if pos_sys:
    nm, sy, sb = pos_sys
    sim = S.SystemSim(sy)
    st = next(s for s in sim.subs if s.sub.name == sb.name)
    for i in range(30):
        st.spawn_count = i
        st._spawn(0.0)
    if st.particles:
        px = [p[2] for p in st.particles]
        py = [p[3] for p in st.particles]
        pz = [p[4] for p in st.particles]
        cx = (max(px) + min(px)) / 2.0
        cy = (max(py) + min(py)) / 2.0
        cz = (max(pz) + min(pz)) / 2.0
        want = sb.position
        # 点发射器 + 位置偏移：所有出生点应聚在 position 附近
        err = max(abs(cx - want[0]), abs(cy - want[1]), abs(cz - want[2]))
        print("  样本: {0} / {1}  position={2}".format(
            nm, sb.name, tuple(round(c, 2) for c in want)))
        print("    出生点中心=({0:.2f}, {1:.2f}, {2:.2f})".format(cx, cy, cz))
        check("position 已应用（出生点中心≈position）", err < 1e-6,
              "最大偏差 {0:.3f}".format(err))
    else:
        check("位置偏移样本生成了粒子", False)
else:
    check("找到可测的 position 样本", False)

print()
print("=" * 78)
print("D. 立牌朝向：billboard=yes 跟随相机，pitch≈90 平铺")
print("=" * 78)

# 造一个相机，比较不同相机姿态下网格顶点的朝向
sc = bpy.context.scene
cam_data = bpy.data.cameras.new("_chk_cam")
cam = bpy.data.objects.new("_chk_cam", cam_data)
sc.collection.objects.link(cam)
sc.camera = cam

# 挑 billboard 与小 pitch 的点发射器系统
pick = None
for nm, sy in lib.systems.items():
    for sb in sy.subsystems:
        if (not sb.hide and sb.billboard and sb.emitter_type == "point"
                and abs(sb.particle_pitch[0] - 90.0) > 5.0
                and sb.max_amount >= 10 and sb.emission[0] >= 5.0):
            pick = (nm, sy, sb)
            break
    if pick:
        break

if pick:
    nm, sy, sb = pick
    pv = BP.PreviewScene(sy, lib)
    sp = next((x for x in pv.sub_previews if x.sub_sim.name == sb.name), None)
    if sp is None:
        check("billboard 样本进入预览", False, "子系统未出现在预览里")
        pv.destroy()
    else:
        sc.frame_start = 1
        sc.frame_set(1)
        sim = pv.sim
        sim.reset()
        sim.advance(1.0)

    def quad_basis():
        sp.refresh(cam.matrix_world)
        co = [0.0] * (len(sp.mesh.vertices) * 3)
        sp.mesh.vertices.foreach_get("co", co)
        # 取第一个 quad 的两条边
        v = [(co[i * 3], co[i * 3 + 1], co[i * 3 + 2]) for i in range(4)]
        e1 = tuple(v[1][k] - v[0][k] for k in range(3))
        e2 = tuple(v[3][k] - v[0][k] for k in range(3))
        return e1, e2

    import mathutils  # noqa: E402
    cam.location = (0.0, -100.0, 0.0)
    cam.rotation_euler = (1.5708, 0, 0)
    bpy.context.view_layer.update()
    e1a, e2a = quad_basis()

    cam.location = (100.0, 0.0, 0.0)
    cam.rotation_euler = (1.5708, 0, 1.5708)
    bpy.context.view_layer.update()
    e1b, e2b = quad_basis()

    import math
    def ang(a, b):
        na = math.sqrt(sum(x * x for x in a)) or 1e-9
        nb = math.sqrt(sum(x * x for x in b)) or 1e-9
        d = sum(a[i] * b[i] for i in range(3)) / (na * nb)
        return math.degrees(math.acos(max(-1.0, min(1.0, d))))

    delta = max(ang(e1a, e1b), ang(e2a, e2b))
    print("  样本: {0} / {1}".format(nm, sb.name))
    print("    相机 -Y:  e1={0}".format(tuple(round(x, 2) for x in e1a)))
    print("    相机 +X:  e1={0}".format(tuple(round(x, 2) for x in e1b)))
    print("    两次朝向夹角: {0:.1f} 度".format(delta))
    check("billboard 粒子随相机改变朝向", delta > 30.0,
          "夹角 {0:.1f} 度".format(delta))
    pv.destroy()
else:
    check("找到可测的 billboard 样本", False)

# 平铺环：pitch≈90 应躺在水平面（法线朝 +Z）
flat_pick = None
for nm, sy in lib.systems.items():
    for sb in sy.subsystems:
        # hide=yes 的子系统不进预览（SystemSim 直接跳过），选了会 StopIteration
        if (not sb.hide
                and abs(sb.particle_pitch[0] - 90.0) < 1.0 and sb.particle_pitch[1] < 1.0
                and sb.max_amount >= 4 and sb.emission[0] >= 2.0):
            flat_pick = (nm, sy, sb)
            break
    if flat_pick:
        break

if flat_pick:
    nm, sy, sb = flat_pick
    pv = BP.PreviewScene(sy, lib)
    sp = next(x for x in pv.sub_previews if x.sub_sim.name == sb.name)
    sc.frame_set(1)
    pv.sim.reset()
    pv.sim.advance(1.0)
    sp.refresh(None)
    co = [0.0] * (len(sp.mesh.vertices) * 3)
    sp.mesh.vertices.foreach_get("co", co)
    v = [(co[i * 3], co[i * 3 + 1], co[i * 3 + 2]) for i in range(4)]
    e1 = tuple(v[1][k] - v[0][k] for k in range(3))
    e2 = tuple(v[3][k] - v[0][k] for k in range(3))
    n = (e1[1] * e2[2] - e1[2] * e2[1],
         e1[2] * e2[0] - e1[0] * e2[2],
         e1[0] * e2[1] - e1[1] * e2[0])
    ln = math.sqrt(sum(x * x for x in n)) or 1e-9
    zc = abs(n[2] / ln)
    print("  样本: {0} / {1}".format(nm, sb.name))
    print("    面法线与 +Z 的对齐度: {0:.3f} (1.0 = 完全水平)".format(zc))
    check("pitch≈90 的粒子平铺在水平面", zc > 0.9, "对齐度 {0:.3f}".format(zc))
    pv.destroy()
else:
    check("找到可测的平铺环样本", False)

print()
print("=" * 78)
print("B2. 发射器形态：sphere 各向同性（Halton 权重回归）")
print("=" * 78)

sph_pick = None
for nm, sy in lib.systems.items():
    for sb in sy.subsystems:
        if (not sb.hide and sb.emitter_type == "sphere"
                and sb.sphere_radius[0] > 0 and sb.sphere_pitch[1] > 90.0):
            sph_pick = (nm, sy, sb)
            break
    if sph_pick:
        break

if sph_pick:
    nm, sy, sb = sph_pick
    st = next((s for s in S.SystemSim(sy).subs if s.sub.name == sb.name), None)
    if st is None:
        check("sphere 样本进入模拟", False)
    else:
        N = 1500
        for i in range(N):
            st.spawn_count = i
            st._spawn(0.0)
        pts = list(st.particles)
        signs = [sum(1 for p in pts if p[2 + k] > 0) / float(len(pts)) for k in range(3)]
        print("  样本: {0} / {1}  r={2:g} pitch范围={3}".format(
            nm[:34], sb.name, sb.sphere_radius[0], sb.sphere_pitch))
        print("    三轴符号比 x/y/z = {0}".format([round(v, 3) for v in signs]))
        # 各向同性：三轴符号都应接近 0.5。旧的「同序列加偏移」写法会给出
        # 0.866/0.5/0.366 这类明显偏斜（点集退化成一条测地线）。
        worst = max(abs(v - 0.5) for v in signs)
        check("sphere 发射各向同性（三轴符号均≈0.5）", worst < 0.08,
              "最大偏差 {0:.3f}".format(worst))
        # 球面网格覆盖率：退化的写法只有约 11%
        NP, NY = 18, 36
        grid = set()
        for p in pts:
            vx, vy, vz = p[2], p[3], p[4]
            L = math.sqrt(vx * vx + vy * vy + vz * vz) or 1e-9
            pit = math.degrees(math.acos(max(-1.0, min(1.0, vy / L))))
            yaw = math.degrees(math.atan2(vz / L, vx / L)) % 360.0
            grid.add((int(pit / (180.0 / NP)), int(yaw / (360.0 / NY))))
        cov = len(grid) / float(NP * NY)
        print("    球面网格覆盖 {0:.1f}%  (退化写法约 11%)".format(cov * 100))
        check("sphere 球面覆盖 > 60%", cov > 0.60, "{0:.1f}%".format(cov * 100))
else:
    check("找到 sphere 样本", False)

print()
print("=" * 78)
print("B3. 朝向三态：billboard=no 固定 / billboard=yes 跟随 / pitch≈90 平铺")
print("=" * 78)

fixed_pick = None
for nm, sy in lib.systems.items():
    for sb in sy.subsystems:
        if (not sb.hide and not sb.billboard and sb.emitter_type == "point"
                and abs(sb.particle_pitch[0] - 90.0) > 5.0
                and sb.max_amount >= 4 and sb.emission[0] >= 3.0):
            fixed_pick = (nm, sy, sb)
            break
    if fixed_pick:
        break

if fixed_pick:
    nm, sy, sb = fixed_pick
    pv = BP.PreviewScene(sy, lib)
    sp = next((x for x in pv.sub_previews if x.sub_sim.name == sb.name), None)
    if sp is None:
        check("billboard=no 样本进入预览", False)
    else:
        pv.sim.reset()
        pv.sim.advance(1.0)

        def edges_with(mat):
            sp.refresh(mat)
            co = [0.0] * (len(sp.mesh.vertices) * 3)
            sp.mesh.vertices.foreach_get("co", co)
            v = [(co[i * 3], co[i * 3 + 1], co[i * 3 + 2]) for i in range(4)]
            return (tuple(v[1][k] - v[0][k] for k in range(3)),
                    tuple(v[3][k] - v[0][k] for k in range(3)))

        ident = ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1))
        cc, ss = math.cos(math.radians(70)), math.sin(math.radians(70))
        rot = ((cc, -ss, 0, 0), (ss, cc, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1))
        e1a, e2a = edges_with(ident)
        e1b, e2b = edges_with(rot)

        def ang(a, b):
            na = math.sqrt(sum(x * x for x in a)) or 1e-9
            nb = math.sqrt(sum(x * x for x in b)) or 1e-9
            dd = sum(a[i] * b[i] for i in range(3)) / (na * nb)
            return math.degrees(math.acos(max(-1.0, min(1.0, dd))))

        delta = max(ang(e1a, e1b), ang(e2a, e2b))
        print("  样本: {0} / {1}  billboard={2} pitch={3}".format(
            nm[:34], sb.name, sb.billboard, sb.particle_pitch))
        print("    换相机后朝向夹角 {0:.2f} 度  (固定朝向应≈0)".format(delta))
        check("billboard=no 的粒子不跟随相机（固定朝向）", delta < 1.0,
              "夹角 {0:.2f} 度".format(delta))
        pv.destroy()
else:
    check("找到 billboard=no 样本", False)

print()
print("=" * 78)
print("B4. 发射体旋转 emitter_yaw / emitter_pitch")
print("=" * 78)

# 真值样本：fallen_empire_*_colossus_energy_effect 把三个球按等边三角形摆在 XZ
# 平面，emitter_yaw 恰好等于各球「朝心方向」的方位角（ball_1 -120.96→-120、
# ball_2 0.00→0、ball_3 120.96→120），velocity 恒为 -100。这是唯一能把
# 「emitter 系基准是 +X」证死的样本，故用它当断言。
colossus = [n for n in sorted(lib.systems) if "colossus_energy_effect" in n]
check("找到 colossus 真值样本", bool(colossus), "{0} 个".format(len(colossus)))

if colossus:
    nmm = colossus[0]
    sim = S.SystemSim(lib.systems[nmm])
    ok = tot = 0
    detail = []
    for want in ("ball_1", "ball_2", "ball_3"):
        st = next((s for s in sim.subs if s.name == want), None)
        if st is None:
            continue
        for i in range(8):
            st.spawn_count = i
            st._spawn(0.0)
        if not st.particles:
            continue
        p = st.particles[0]
        pos = (p[2], p[3], p[4])
        vel = (p[5], p[6], p[7])
        Lc = math.hypot(pos[0], pos[2]) or 1e-9
        outward = (pos[0] / Lc, 0.0, pos[2] / Lc)
        Lv = math.sqrt(sum(c * c for c in vel)) or 1e-9
        ang = math.degrees(math.acos(max(-1.0, min(1.0,
            sum(vel[i] * outward[i] for i in range(3)) / Lv))))
        tot += 1
        if ang < 1.5:
            ok += 1
        detail.append("{0}={1:.2f}度".format(want, ang))
    print("  样本: {0}".format(nmm))
    print("    三球速度与径向朝外夹角: {0}".format(", ".join(detail)))
    check("colossus 三球全部径向朝外（emitter 系基准为 +X）", ok == tot and tot == 3,
          "{0}/{1}".format(ok, tot))

    # 显式写 0 必须等价于「转到 +X」，不能当作不旋转
    yaw0 = [sb for sy in lib.systems.values() for sb in sy.subsystems
            if sb.has_emitter_rot and not (sb.emitter_yaw[0] or sb.emitter_pitch[0])]
    check("存在显式写 0 的子系统（与「没写」区分开）", len(yaw0) > 0,
          "{0} 个".format(len(yaw0)))
    nokey = [sb for sy in lib.systems.values() for sb in sy.subsystems
             if not sb.has_emitter_rot]
    check("存在完全没写 emitter 键的子系统", len(nokey) > 0,
          "{0} 个".format(len(nokey)))

    # 带方差的发射体必须逐粒子取角。
    # 曾有个不可达分支 `m is None and self.emit_var`：__init__ 的条件是
    # `has_emitter_rot or emit_var`，emit_var 为真时矩阵已建好，于是 m is None
    # 蕴含 not emit_var，该分支永不进入，9 个带方差的子系统方差被静默忽略。
    var_subs = [(sy, sb) for sy in lib.systems.values() for sb in sy.subsystems
                if (sb.emitter_yaw[1] or sb.emitter_pitch[1])]
    check("存在带方差的发射体样本", len(var_subs) > 0, "{0} 个".format(len(var_subs)))
    if var_subs:
        # 方差作用于「形状偏移」与「初速度方向」的旋转，两者取其中非退化的那个来验：
        # 烟雾类粒子 velocity=0，只能看位置散开；有的发射体没有体积，就看方向。
        checked_any = False
        for sy, sb in var_subs:
            st = next((s for s in S.SystemSim(sy).subs if s.sub.name == sb.name), None)
            if st is None:
                continue
            N = 40
            for i in range(N):
                st.spawn_count = i
                st._spawn(0.0)
            if len(st.particles) < 2:
                continue
            dirs = set()
            poss = set()
            for p in st.particles:
                v = (p[5], p[6], p[7])
                L = math.sqrt(sum(c * c for c in v))
                if L > 1e-9:
                    dirs.add((round(v[0] / L, 3), round(v[1] / L, 3), round(v[2] / L, 3)))
                poss.add((round(p[2], 3), round(p[3], 3), round(p[4], 3)))
            spread = max(len(dirs), len(poss))
            print("  样本: {0} / {1}".format(sy.name[:34], sb.name))
            print("    方差 yaw={0} pitch={1}".format(sb.emitter_yaw, sb.emitter_pitch))
            print("    {0} 颗粒子 -> {1} 个不同方向, {2} 个不同位置".format(
                N, len(dirs), len(poss)))
            if spread > 1:
                check("带方差的发射体逐粒子散开（非单一方向/位置）", True,
                      "方向 {0} / 位置 {1}".format(len(dirs), len(poss)))
                checked_any = True
                break
        if not checked_any:
            check("带方差的发射体逐粒子散开（非单一方向/位置）", False,
                  "所有带方差样本都退化")

    # 没写键的必须保持原 +Y 基准（不受影响）
    kept = 0
    tot2 = 0
    for sy in lib.systems.values():
        for s in S.SystemSim(sy).subs:
            if not s.sub.has_emitter_rot:
                tot2 += 1
                if s.emit_rot is None:
                    kept += 1
    check("没写键的子系统不被旋转（保持 +Y）", kept == tot2,
          "{0}/{1}".format(kept, tot2))

bpy.data.objects.remove(cam, do_unlink=True)

print()
print("=" * 78)
print("结果: {0} passed, {1} failed".format(len(PASS), len(FAIL)))
if FAIL:
    print("失败项:")
    for f in FAIL:
        print("   - " + f)
print("=" * 78)
