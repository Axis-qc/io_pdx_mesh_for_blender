"""
粒子子系统特征自检：逐项断言「解析到了」且「真的生效了」。

跑法（脚本位置无关，放在 test/ 下即可）：
    & 'G:\\SteamLibrary\\steamapps\\common\\Blender\\blender.exe' -b --factory-startup \
      --python test/check_particle_features.py

游戏根目录默认 G:\\SteamLibrary\\steamapps\\common\\Stellaris，
可用环境变量 IO_PDX_TEST_GAME 覆盖（换机器/换安装位置时不必改脚本）。

检查这些特征，各自走不同代码路径，任一处没加载都会在这里失败：
  1. 立牌朝向   billboard=yes 的粒子是否面向活动视口镜头（无视口时退回摄像机）
  2. 平铺环     particle_pitch≈90 的粒子是否躺在水平面上
  3. 场发射     box 的三轴、sphere 的半径是否都被解析且真的散开
  4. 位置偏移   subsystem 的 position 是否加到出生点上
  5. 脉冲发射   emission_pulse_duration / _silence 的静默相是否真的停发
  6. 三轴自转   rotation_speed_pitch / _yaw / _roll 的朝向增量是否符合契约
  7. 回退      时间轴回退是否走关键帧、且与从 0 全量重放逐位一致
  8. 向量化    力场向量化与纯 Python 是否逐位一致

抽样口径：解析层（读 .asset、数键）全量扫，成本秒级；
模拟层（SystemSim + advance 跑物理）抽样，固定种子可复现——
单次 advance 是数百毫秒，全量 4700+ 子系统要几十分钟，不值得。

不修改任何文件；只在结尾打印报告。
"""

import os
import random
import sys

# 只读自检：不写 __pycache__，保证跑完仓库零副作用（否则会在插件目录下留
# __pycache__，虽已被 .gitignore 忽略，仍然脏）。必须在 import 插件之前设置。
sys.dont_write_bytecode = True

# 插件根目录 = 本脚本所在目录的上一级（不写死盘符，脚本可以随处放）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(ROOT))

import bpy  # noqa: E402
from io_pdx_mesh.pdx_particles import parser as P  # noqa: E402
from io_pdx_mesh.pdx_particles import simulator as S  # noqa: E402
from io_pdx_mesh.pdx_particles import blender_preview as BP  # noqa: E402

GAME = os.environ.get(
    "IO_PDX_TEST_GAME", r"G:\SteamLibrary\steamapps\common\Stellaris")

# 抽样规模。区分两类检查：
#   - 解析层（读 .asset、数键、查字段）：全量扫，成本是秒级，全量更可信。
#   - 模拟层（SystemSim + advance 跑物理）：**必须抽样**。单次 advance 是数百
#     毫秒量级，全量 4700 个子系统要跑几十分钟——为验证一条契约不值得。
# 固定种子保证结果可复现，不因每次随机到不同样本而时好时坏。
SAMPLE_N = 240
RNG = random.Random(20241010)


def sample(items, n=SAMPLE_N):
    """固定种子抽样：items 少于 n 时全取，否则随机取 n 个。"""
    items = list(items)
    if len(items) <= n:
        return items
    return RNG.sample(items, n)


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
# 真正 z 非零的只有 22 个。挑一个「只有 z 非零」的样本来验：box_emitter_z 若没被
# 读进来，出生点三轴全 0、所有粒子挤在一个点；读对了应散成一条 z 向线段。
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
        check("box 的 z 轴确实散开（未读入时恒为 0）", spans[2] > 1e-6,
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

    def quad_basis(basis):
        # refresh 现在收的是镜头基向量 (right, up)，不再收相机矩阵：
        # 立牌朝向的来源改为「视口镜头优先，摄像机兜底」，见 active_view_basis()。
        sp.refresh(basis)
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
    # 用相机世界矩阵的列 0/列 1 作为基向量（与 camera_basis 同构）
    def cam_cols():
        m = cam.matrix_world
        return ((m[0][0], m[1][0], m[2][0]), (m[0][1], m[1][1], m[2][1]))
    e1a, e2a = quad_basis(cam_cols())

    cam.location = (100.0, 0.0, 0.0)
    cam.rotation_euler = (1.5708, 0, 1.5708)
    bpy.context.view_layer.update()
    e1b, e2b = quad_basis(cam_cols())

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

# 视口镜头：面片应面向「界面里的实际镜头」（region_3d），不是场景摄像机。
# 这是 billboard 的默认来源，也是「视角一转面片就露馅」那个 bug 的修复点。
_view_basis = BP.active_view_basis()
if _view_basis is None:
    check("活动视口可取到镜头基向量", False, "无视口（后台模式？）")
else:
    import mathutils  # noqa: E402
    r3d = BP._active_region_3d()

    # 用单位四元数当自证式真值：view_rotation 是「相机空间 → 世界」的旋转，
    # 单位四元数即相机与世界同向，此时 right 必须是 (1,0,0)、up 必须是 (0,1,0)。
    # 不用 view_matrix 校验：视口没绘制过时它不更新（后台实测停在旧值），
    # 拿它当真值会把正确的实现判成错的。
    _saved_rot = r3d.view_rotation.copy()
    r3d.view_rotation = mathutils.Quaternion((1.0, 0.0, 0.0, 0.0))
    _ident = BP.active_view_basis()
    ok_ident = (_ident is not None
                and all(abs(_ident[0][i] - (1.0, 0.0, 0.0)[i]) < 1e-6 for i in range(3))
                and all(abs(_ident[1][i] - (0.0, 1.0, 0.0)[i]) < 1e-6 for i in range(3)))
    check("单位四元数视角下基向量 = (right +X, up +Y)", ok_ident,
          "" if _ident is None else "right={0} up={1}".format(
              tuple(round(x, 4) for x in _ident[0]),
              tuple(round(x, 4) for x in _ident[1])))
    # 再转一个角度验证「基向量跟着 view_rotation 走」。用 180°（w=cos90°=0，
    # z=sin90°=1）而不是 90°，因为这里设的是四元数本身不是欧拉角：
    # (0,0,0.7071,0.7071) 的 w=cos(θ/2)=0.7071 → θ=180°。转 180° 后
    # right 从 +X 翻成 -X，与 R@(1,0,0) 一致。
    r3d.view_rotation = mathutils.Quaternion((0.0, 0.0, 1.0, 0.0))
    _rot180 = BP.active_view_basis()
    r3d.view_rotation = _saved_rot
    ok_rot = (_rot180 is not None
              and all(abs(_rot180[0][i] - (-1.0, 0.0, 0.0)[i]) < 1e-5
                      for i in range(3)))
    check("转 180° 后 right 随之翻转", ok_rot,
          "" if _rot180 is None else "right={0}".format(
              tuple(round(x, 4) for x in _rot180[0])))

    # 正交且单位长
    n0 = math.sqrt(sum(x * x for x in _view_basis[0]))
    n1 = math.sqrt(sum(x * x for x in _view_basis[1]))
    dp = sum(a * b for a, b in zip(_view_basis[0], _view_basis[1]))
    check("视口基向量正交且单位长",
          abs(n0 - 1.0) < 1e-6 and abs(n1 - 1.0) < 1e-6 and abs(dp) < 1e-6,
          "|r|={0:.6f} |u|={1:.6f} r·u={2:.2e}".format(n0, n1, dp))
    # 指纹可比较：同一镜头两次取值应相同
    k1 = BP.view_rotation_key()
    k2 = BP.view_rotation_key()
    check("视口镜头指纹稳定（可做脏检查）", k1 is not None and k1 == k2)

    # 单窗口时「当前活动窗口」与「全局面积最大」必须指向同一个 region_3d，
    # 否则 _active_region_3d 的活动窗口优先逻辑会选错。后台只有一个 window，
    # 这里验的是这条等价性；多窗口行为无法在后台构造（window_new 会报
    # context 错误），需真实 GUI 验证。
    #
    # 比较必须用 as_pointer()：Blender 的 w is bpy.context.window 永远为 False
    # （每次访问都新建 Python 包装对象），用 is 比会把所有东西都判成不同。
    def _px_of(r3d_target):
        best = -1
        for _w in bpy.context.window_manager.windows:
            _s = getattr(_w, "screen", None)
            for _a in getattr(_s, "areas", ()):
                _sp = _a.spaces.active if _a.spaces is not None else None
                if _a.type == "VIEW_3D" and _sp is not None \
                        and _sp.region_3d is not None \
                        and _sp.region_3d.as_pointer() == r3d_target.as_pointer():
                    best = max(best, _a.width * _a.height)
        return best

    _global = None
    _global_px = -1
    for _w in bpy.context.window_manager.windows:
        _p, _r = BP._region_3d_of(_w)
        if _r is not None and _p > _global_px:
            _global_px, _global = _p, _r
    _chosen = BP._active_region_3d()
    _chosen_px = _px_of(_chosen) if _chosen is not None else -1
    check("单窗口下活动视口 = 全局最大视口",
          _global is not None and _chosen is not None
          and _chosen.as_pointer() == _global.as_pointer()
          and _chosen_px == _global_px and _chosen_px > 0,
          "chosen_px={0} global_px={1}".format(_chosen_px, _global_px))

    # 面片法线应指向视线反方向（dot ≈ -1 表示正对镜头）
    bill_pick = None
    for _nm, _sy in lib.systems.items():
        for _sb in _sy.subsystems:
            if (not _sb.hide and _sb.billboard
                    and abs(_sb.particle_pitch[0] - 90.0) > 5.0
                    and _sb.max_amount >= 4 and _sb.emission[0] >= 2.0):
                bill_pick = (_nm, _sy, _sb)
                break
        if bill_pick:
            break
    if bill_pick:
        _nm, _sy, _sb = bill_pick
        _pv = BP.PreviewScene(_sy, lib)
        _sp = next(x for x in _pv.sub_previews if x.sub_sim.name == _sb.name)
        sc.frame_set(1)
        _pv.sim.reset()
        _pv.sim.advance(1.0)

        def _normal_now():
            _sp.refresh(BP.active_view_basis())
            _co = [0.0] * (len(_sp.mesh.vertices) * 3)
            _sp.mesh.vertices.foreach_get("co", _co)
            _v = [(_co[i * 3], _co[i * 3 + 1], _co[i * 3 + 2]) for i in range(4)]
            _e1 = tuple(_v[1][k] - _v[0][k] for k in range(3))
            _e2 = tuple(_v[3][k] - _v[0][k] for k in range(3))
            return (_e1[1] * _e2[2] - _e1[2] * _e2[1],
                    _e1[2] * _e2[0] - _e1[0] * _e2[2],
                    _e1[0] * _e2[1] - _e1[1] * _e2[0])

        _R = r3d.view_rotation.to_matrix()
        _vd = (_R @ mathutils.Vector((0.0, 0.0, -1.0))).normalized()
        _n = _normal_now()
        _ln = math.sqrt(sum(x * x for x in _n)) or 1e-9
        _dot = sum(_n[i] * _vd[i] for i in range(3)) / _ln
        print("  样本: {0} / {1}".format(_nm, _sb.name))
        print("    面法线与视线点积: {0:.4f} (-1.0 = 正对镜头)".format(_dot))
        check("billboard 面片正对活动视口镜头", _dot < -0.9,
              "点积 {0:.4f}".format(_dot))
        _pv.destroy()
    else:
        check("找到可测的视口朝向样本", False)

# 平铺环：billboard=no 且 pitch≈90 应躺在水平面（法线朝 +Z）
# 必须限定 billboard=no：billboard=yes 的面片恒朝镜头，pitch 对它无效
# （pdx_axis_probe 实机定案），拿它验平铺会把正确的实现判成错——判断顺序若先看
# pitch≈90，129 个同时写了 billboard=yes 的子系统会被摁平。
flat_pick = None
for nm, sy in lib.systems.items():
    for sb in sy.subsystems:
        # hide=yes 的子系统不进预览（SystemSim 直接跳过），选了会 StopIteration
        if (not sb.hide and not sb.billboard
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
    sp.refresh(None)          # None = 既无视口也无摄像机的兜底分支
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
print("D2. billboard=no 固定朝向姿态（pdx_axis_probe 四格实机读数）")
print("=" * 78)
print("   默认 U=+X、V=+Y；yaw 绕世界 +Y；pitch 绕面片自身长轴自转（实为 roll）。")
print("   Blender 与引擎经 (x,z,y) 交换（镜像），所以引擎正向旋转在 Blender 里取负号。")


def _eng_space(v):
    """Blender 向量 → 引擎向量：(bx,by,bz) -> (bx,bz,by)。"""
    return (v[0], v[2], v[1])


def _quad_normal(r, u):
    return (r[1] * u[2] - r[2] * u[1],
            r[2] * u[0] - r[0] * u[2],
            r[0] * u[1] - r[1] * u[0])


# 四格实机读数：箭头（贴图 U）指向 + 是否水平
_fix_cases = (
    (0.0, 0.0, (1.0, 0.0, 0.0), False),
    (90.0, 0.0, (0.0, 0.0, -1.0), False),
    (0.0, 90.0, (1.0, 0.0, 0.0), True),
    (90.0, 90.0, (0.0, 0.0, -1.0), True),
)
_fix_bad = 0
for _y, _p, _arrow, _horiz in _fix_cases:
    _r, _u = BP._fixed_quad_basis(_y, _p)
    _a = _eng_space(_r)
    _n = _eng_space(_quad_normal(_r, _u))
    if max(abs(_a[i] - _arrow[i]) for i in range(3)) > 1e-9:
        _fix_bad += 1
    if (abs(_n[1]) > 0.9) != _horiz:
        _fix_bad += 1
check("billboard=no 四格姿态与实机读数一致（箭头指向 + 水平判定）", _fix_bad == 0,
      "{0} 处不符".format(_fix_bad))

_r0, _u0 = BP._fixed_quad_basis(0.0, 0.0)
check("默认姿态 right=+X、up=+Z（误成 -Y / +X 即为错）",
      all(abs(_r0[i] - (1.0, 0.0, 0.0)[i]) < 1e-9 for i in range(3))
      and all(abs(_u0[i] - (0.0, 0.0, 1.0)[i]) < 1e-9 for i in range(3)),
      "right={0} up={1}".format(_r0, _u0))

# billboard=yes 必须忽略 yaw/pitch：给一个 pitch≈90 的 billboard=yes 子系统，
# 它的面片应仍正面朝镜头（法线 ≈ -Y 兜底基），而不是被摁平在水平面。
_bb_flat = None
for _nm, _sy in lib.systems.items():
    for _sb in _sy.subsystems:
        if (not _sb.hide and _sb.billboard
                and abs(_sb.particle_pitch[0] - 90.0) < 1.0
                and _sb.max_amount >= 4 and _sb.emission[0] >= 2.0):
            _bb_flat = (_nm, _sy, _sb)
            break
    if _bb_flat:
        break
if _bb_flat:
    _nm, _sy, _sb = _bb_flat
    _pv = BP.PreviewScene(_sy, lib)
    _sp = next(x for x in _pv.sub_previews if x.sub_sim.name == _sb.name)
    sc.frame_set(1)
    _pv.sim.reset()
    _pv.sim.advance(1.0)
    _sp.refresh(None)        # None = 走兜底基（+X / +Z），法线应为 -Y
    _co = [0.0] * (len(_sp.mesh.vertices) * 3)
    _sp.mesh.vertices.foreach_get("co", _co)
    _v = [(_co[i * 3], _co[i * 3 + 1], _co[i * 3 + 2]) for i in range(4)]
    _e1 = tuple(_v[1][k] - _v[0][k] for k in range(3))
    _e2 = tuple(_v[3][k] - _v[0][k] for k in range(3))
    _nn = (_e1[1] * _e2[2] - _e1[2] * _e2[1],
           _e1[2] * _e2[0] - _e1[0] * _e2[2],
           _e1[0] * _e2[1] - _e1[1] * _e2[0])
    _ln = math.sqrt(sum(x * x for x in _nn)) or 1e-9
    _yc = abs(_nn[1] / _ln)
    print("  样本: {0} / {1}  billboard=yes pitch={2} 面法线≈±Y 对齐度 {3:.3f}".format(
        _nm[:34], _sb.name, _sb.particle_pitch, _yc))
    check("billboard=yes 时 pitch≈90 仍朝镜头（不被平铺分支抢走）", _yc > 0.9,
          "对齐度 {0:.3f}".format(_yc))
    _pv.destroy()
else:
    check("找到 billboard=yes 且 pitch≈90 的样本", False)

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

# 真值样本：colossus 三球的 position 是等边三角形、emitter_yaw 是 -120/0/120，
# 粒子应向中心汇聚。这条同时钉住 position 轴序与 emitter_yaw 零点：位置少转 90°
# 或给 emitter 方向多加 90°，三球与「朝心」都会差 90°。
colossus = [n for n in sorted(lib.systems) if "colossus_energy_effect" in n]
check("找到 colossus 真值样本", bool(colossus), "{0} 个".format(len(colossus)))

if colossus:
    nmm = colossus[0]
    sim = S.SystemSim(lib.systems[nmm])
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
        inward = (-pos[0] / Lc, 0.0, -pos[2] / Lc)
        Lv = math.sqrt(sum(c * c for c in vel)) or 1e-9
        ang = math.degrees(math.acos(max(-1.0, min(1.0,
            sum(vel[i] * inward[i] for i in range(3)) / Lv))))
        detail.append("{0}={1:.2f}度".format(want, ang))
    print("  样本: {0}  三球与朝心夹角: {1}".format(nmm, ", ".join(detail)))
    # 核心断言：三球都沿径向朝中心飞
    dev = [float(x.split("=")[1].rstrip("度")) for x in detail]
    check("colossus 三球沿径向朝中心汇聚", len(dev) == 3 and max(dev) < 5.0,
          "与朝心夹角 {0}".format(detail))

    # position 轴序：出生点方位 120 度均布、半径 22~23.3（整体偏 90 度即为错）
    azs = []
    rads = []
    for want in ("ball_1", "ball_2", "ball_3"):
        st = next((s for s in sim.subs if s.name == want), None)
        if st is None or not st.particles:
            continue
        p = st.particles[0]
        azs.append(math.degrees(math.atan2(p[2], p[4])) % 360.0)
        rads.append(math.hypot(p[2], p[4]))
    azs.sort()
    gaps = [(azs[(i + 1) % 3] - azs[i]) % 360.0 for i in range(3)]
    check("colossus 三球出生点 120 度均布且半径≈22~23.3", 
          len(azs) == 3 and max(abs(g - 120.0) for g in gaps) < 3.0
          and all(21.0 < r < 24.0 for r in rads),
          "方位 {0} 半径 {1}".format([round(a, 1) for a in azs],
                                     [round(r, 1) for r in rads]))

    # 轨迹真的落到原点附近（只掠过不靠近即为错）
    rec = []
    sim2 = S.SystemSim(lib.systems[nmm], 0.0)
    for fr in range(0, 200):
        sim2.advance(fr / 60.0)
        for nmb, arr in sim2.snapshot():
            if str(nmb) == "ball_2":
                rec.extend(arr)
    if rec:
        rr = [math.hypot(q[0], q[2]) for q in rec]
        check("colossus ball_2 轨迹落到原点附近", min(rr) < 2.0,
              "半径 {0:.2f} -> {1:.2f}".format(max(rr), min(rr)))
    else:
        check("colossus ball_2 产生轨迹", False, "无粒子")

    # 显式写 0 必须等价于「转到 +Z」，不能当作不旋转；没写键的才是真不旋转
    yaw0 = [sb for sy in lib.systems.values() for sb in sy.subsystems
            if sb.has_emitter_rot and not (sb.emitter_yaw[0] or sb.emitter_pitch[0])]
    check("存在显式写 0 的子系统（与「没写」区分开）", len(yaw0) > 0,
          "{0} 个".format(len(yaw0)))
    nokey = [sb for sy in lib.systems.values() for sb in sy.subsystems
             if not sb.has_emitter_rot]
    check("存在完全没写 emitter 键的子系统", len(nokey) > 0,
          "{0} 个".format(len(nokey)))
    # emitter_yaw 零点：0 → +Z、90 → -X（标定件实机读数）
    _d0 = S._emit_dir(0.0, 0.0)
    _d90 = S._emit_dir(0.0, 90.0)
    check("emitter_yaw 零点为 +Z、90 度为 -X",
          abs(_d0[0]) < 1e-9 and abs(_d0[2] - 1.0) < 1e-9
          and abs(_d90[0] + 1.0) < 1e-9 and abs(_d90[2]) < 1e-9,
          "0度={0} 90度={1}".format(tuple(round(c, 3) for c in _d0),
                                    tuple(round(c, 3) for c in _d90)))

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

    # 没写键的子系统不建旋转矩阵，出射方向保持 velocity 的原值
    kept = 0
    tot2 = 0
    for sy in lib.systems.values():
        for s in S.SystemSim(sy).subs:
            if not s.sub.has_emitter_rot:
                tot2 += 1
                if s.emit_rot is None:
                    kept += 1
    check("没写 emitter 键的子系统不建旋转矩阵", kept == tot2,
          "{0}/{1}".format(kept, tot2))

# ---------------------------------------------------------------- 脉冲发射
print()
print("=" * 78)
print("5. 脉冲发射 emission_pulse_duration / emission_pulse_silence")
print("=" * 78)
print("   这两个键必须解析进来（107 个子系统用到）；丢了就表现成一直喷而不是脉冲。")
print("   这里验「工作相发射、静默相停发」。")

pulsed = []
for sy in lib.systems.values():
    for sb in sy.subsystems:
        if sb.emission_pulse_duration[0] > 0 and sb.emission_pulse_silence[0] > 0:
            pulsed.append((sy, sb))
print("  原版脉冲子系统: {0} 个".format(len(pulsed)))
check("存在脉冲发射子系统", len(pulsed) > 0, "{0} 个".format(len(pulsed)))

# 逐子系统验排程：工作相内应单调增长，静默相内必须持平。
# 注意不能用 spawn_count 验——它受 max_amount 限流，脉冲会被掩盖。
violations = 0
checked_pulse = 0
for sy, sb in sample(pulsed, 40):
    sim = S.SystemSim(sy)
    st = next((s for s in sim.subs if s.sub.name == sb.name), None)
    if st is None or st.pulse_period <= 0.0:
        continue
    checked_pulse += 1
    step = st.pulse_period / 40.0
    prev = None
    prev_phase = None
    for i in range(41):
        t = st.start + i * step
        n = st._expected_spawns(t)
        phase = (t - st.start) % st.pulse_period
        # 静默相内不允许增长（两端都要在静默相，避免跨相位边界误判）
        if (prev is not None and n > prev
                and phase >= st.pulse_on + 1e-9 and prev_phase >= st.pulse_on + 1e-9):
            violations += 1
        prev, prev_phase = n, phase
check("脉冲子系统在静默相不发射", violations == 0,
      "抽样 {0} 个，{1} 次违规".format(checked_pulse, violations))

# 非脉冲子系统不能被误伤：排程应等于闭式解 int((t-start)*rate)。
# 注意不能写成「10s 应是 5s 的两倍」——start≠0 时 (10-start)/(5-start) 不是 2
# （例：start=1、rate=30 时是 9/4=2.25）。
plain_bad = 0
plain_checked = 0
plain_examples = []
for sy in sample(list(lib.systems.values()), 300):
    for s in S.SystemSim(sy).subs:
        sb = s.sub
        if (sb.emission_pulse_duration[0] == 0 and sb.emission_pulse_silence[0] == 0
                and sb.emission[0] > 0 and sb.duration[0] < 0):
            for T in (5.0, 10.0):
                if T <= s.start:
                    continue
                plain_checked += 1
                want = int((T - s.start) * s.rate)
                got = s._expected_spawns(T)
                if got != want:
                    plain_bad += 1
                    if len(plain_examples) < 3:
                        plain_examples.append("{0}/{1} T={2} got={3} want={4}".format(
                            sy.name[:24], sb.name[:12], T, got, want))
check("非脉冲子系统的发射排程等于闭式解", plain_bad == 0,
      "抽查 {0} 个时刻，偏离 {1} 个 {2}".format(
          plain_checked, plain_bad, ("; ".join(plain_examples)) if plain_examples else ""))

# 脉冲不能破坏「跳帧 vs 逐帧」一致性。
# 这里必须真跑物理（正是要验两条推进路径一致），所以样本小、时间短。
path_bad = 0
path_tot = 0
for sy, sb in sample(pulsed, 3):
    for T in (3.0, 6.0):
        a = S.SystemSim(sy)
        a.advance(T)
        b = S.SystemSim(sy)
        for k in range(1, int(T * 60) + 1):
            b.advance(k / 60.0)
        path_tot += 1
        if {n: len(v) for n, v in a.snapshot()} != {n: len(v) for n, v in b.snapshot()}:
            path_bad += 1
check("脉冲下跳帧与逐帧结果一致", path_bad == 0,
      "{0} 组比对".format(path_tot))

# ---------------------------------------------------------------- 三轴自转
print()
print("=" * 78)
print("6. 三轴自转 rotation_speed_pitch / _yaw / _roll")
print("=" * 78)
print("   三个轴按世界轴平铺（pitch→X、yaw→Y、roll→Z），182 个子系统用到。")

axis_subs = [(sy, sb) for sy in lib.systems.values() for sb in sy.subsystems
             if (sb.rotation_speed_pitch[0] or sb.rotation_speed_yaw[0]
                 or sb.rotation_speed_roll[0])]
check("存在带轴自转的子系统", len(axis_subs) > 0, "{0} 个".format(len(axis_subs)))

# snapshot 元组宽度必须是 14（0.96.8 起末四列：三轴自转增量 + 面片基角）
sim0 = S.SystemSim(sorted(lib.systems.values(), key=lambda s: s.name)[0])
sim0.advance(2.0)
width = 0
for _n, rows in sim0.snapshot():
    if rows:
        width = len(rows[0])
        break
check("snapshot 元组宽度为 14", width == 14, "实测 {0}".format(width))

# 轴自转必须真的改变朝向：验契约「增量 == radians(速率 × 年龄)」。
# 这里**手工把 size/alpha 动画关掉**，让 snapshot 的唯一过滤条件变成「年龄」，
# 从而能与 st.particles 按序一一对齐。直接按位置匹配是不行的：
# point 发射器会让同一时刻出生的多颗粒子位置完全相同，字典匹配会互相覆盖。
turned = 0
probe = 0
axis_bad = 0
axis_examples = []
for sy, sb in axis_subs:
    st = next((s for s in S.SystemSim(sy).subs if s.sub.name == sb.name), None)
    if st is None or not (st.pitch_speed or st.yaw_speed):
        continue
    probe += 1
    # 关掉 size/alpha 过滤，保证 snapshot 与「年龄过滤后的 particles」逐行对应
    st.size_anim = None
    st.alpha_anim = None
    st.size_base, st.size_var = 100.0, 0.0
    st.alpha_base, st.alpha_var = 1.0, 0.0
    st.advance(2.0)
    rows = st.snapshot()
    if not rows:
        continue
    kept = [p for p in st.particles if 0.0 <= st.t - p[0] < p[1]]
    if len(kept) != len(rows):
        axis_bad += 1
        continue
    nonzero = False
    for p, r in zip(kept, rows):
        age = st.t - p[0]
        if st.pitch_speed:
            if abs(math.radians(st.pitch_speed * age) - r[10]) > 1e-9:
                axis_bad += 1
            if r[10] != 0.0:
                nonzero = True
        if st.yaw_speed:
            if abs(math.radians(st.yaw_speed * age) - r[11]) > 1e-9:
                axis_bad += 1
            if r[11] != 0.0:
                nonzero = True
    if nonzero:
        turned += 1
    elif len(axis_examples) < 3:
        axis_examples.append("{0}/{1} speed=({2},{3}) n={4}".format(
            sy.name[:24], sb.name[:12], st.pitch_speed, st.yaw_speed, len(rows)))
    if probe >= 10:
        break
check("轴自转的朝向增量随时间累积", turned > 0,
      "{0}/{1} 个在转 {2}".format(turned, probe,
                                  ("; ".join(axis_examples)) if axis_examples else ""))
check("朝向增量 == radians(速率 × 年龄)", axis_bad == 0,
      "逐粒子核对，{0} 处不符".format(axis_bad))

# 无轴自转的必须不受影响。注意速率是 [基值] + [方差]：只看 [0] 会漏掉
# rotation_speed_pitch=(0.0, 2.0) 这类「只在方差里非零」的。
still_tot = 0
still_ok = 0
still_examples = []
# 抽样即可：这条只验「速率为 0 时增量恒为 0」，与具体系统无关，几十个样本足够。
# 时间取 0.5 秒（不是 1 秒）——够产生粒子，又少一半子步。
for sy in sample(list(lib.systems.values()), 80):
    for s in S.SystemSim(sy).subs:
        sb = s.sub
        if s.pitch_speed or s.yaw_speed:
            continue                      # 用模拟里的真实速率判定，而不是解析字段的 [0]
        still_tot += 1
        s.advance(0.5)
        rows = s.snapshot()
        if all(r[10] == 0.0 and r[11] == 0.0 for r in rows):
            still_ok += 1
        elif len(still_examples) < 3:
            still_examples.append("{0}/{1}".format(sy.name[:24], sb.name[:12]))
check("无轴自转的子系统朝向增量恒为 0", still_ok == still_tot,
      "抽样 {0} 个 {1}".format(still_tot,
                               ("; ".join(still_examples)) if still_examples else ""))

# ---------------------------------------------------------------- 回归关键帧
print()
print("=" * 78)
print("7. 时间轴回退（关键帧回退）")
print("=" * 78)


def _fp(sim):
    import hashlib
    import struct as _st
    h = hashlib.sha256()
    for s in sim.subs:
        h.update(s.name.encode("utf-8"))
        h.update(_st.pack("<q", s.n_sub))
        h.update(_st.pack("<q", s.spawn_count))
        for p in s.particles:
            for v in p:
                h.update(_st.pack("<d", float(v)))
    return h.hexdigest()


# 回退后重放 vs 直接从 0 全量重放，必须逐位一致
equiv_bad = 0
equiv_tot = 0
sample_names = [n for n in ("magnetic_storm_2_file", "par_01_beholder_portrait_ship_part_effect_file",
                            "particle_storm_1_file") if n in lib.systems]
if not sample_names:
    sample_names = sorted(lib.systems)[:2]
for nm in sample_names:
    sy = lib.systems[nm]
    for T in (4.0, 8.0):
        a = S.SystemSim(sy)
        a.advance(T + 4.0)
        a.advance(T)                 # 回退
        b = S.SystemSim(sy)
        b.advance(T)                 # 参考
        equiv_tot += 1
        if _fp(a) != _fp(b):
            equiv_bad += 1
check("回退后状态与从 0 全量重放逐位一致", equiv_bad == 0,
      "{0} 组比对，{1} 组不一致".format(equiv_tot, equiv_bad))

# 回退必须走关键帧而不是全量 reset
calls = {"rewind": 0, "reset": 0}
_orig_rewind = S.SubsystemSim.rewind
_orig_reset = S.SubsystemSim.reset


def _spy_rewind(self, t):
    calls["rewind"] += 1
    return _orig_rewind(self, t)


def _spy_reset(self):
    calls["reset"] += 1
    return _orig_reset(self)


S.SubsystemSim.rewind = _spy_rewind
S.SubsystemSim.reset = _spy_reset
try:
    sy = lib.systems[sample_names[0]]
    sim = S.SystemSim(sy)
    sim.advance(20.0)
    calls["rewind"] = calls["reset"] = 0
    sim.advance(19.9833333)          # 回退 1 帧
finally:
    S.SubsystemSim.rewind = _orig_rewind
    S.SubsystemSim.reset = _orig_reset
check("回退走关键帧（rewind 被调用且未全量 reset）",
      calls["rewind"] > 0 and calls["reset"] == 0,
      "rewind={0} reset={1}".format(calls["rewind"], calls["reset"]))

# ---------------------------------------------------------------- 力场向量化
print()
print("=" * 78)
print("8. 力场向量化（numpy，带 point/turbulence/vortex/spin 的子系统）")
print("=" * 78)

if S._NP is None:
    print("  numpy 不可用，跳过向量化检查")
else:
    # 抽样统计覆盖面：给全部 4700+ 个子系统各建一个 SystemSim 太慢，
    # 而这条只关心「有没有走向量化的」，抽样足够。
    vec_subs = 0
    tot_subs = 0
    for sy in sample(list(lib.systems.values()), 120):
        for s in S.SystemSim(sy).subs:
            tot_subs += 1
            if s.use_vec:
                vec_subs += 1
    check("存在走向量化积分的子系统", vec_subs > 0,
          "抽样 {0} 个子系统中 {1} 个（{2:.1f}%）".format(
              tot_subs, vec_subs, 100.0 * vec_subs / max(tot_subs, 1)))

    # 关键：强制关闭向量化后结果必须逐位相同。
    # 只在抽样集合里找走向量化的样本——不要对全库每个系统都建 SystemSim
    # 只为了挑 4 个（白跑几千次构造）。
    vsamples = []
    for sy in sample(list(lib.systems.values()), 200):
        if any(s.use_vec for s in S.SystemSim(sy).subs):
            vsamples.append(sy.name)
        if len(vsamples) >= 3:
            break
    vbad = 0
    vtot = 0
    for nm in vsamples:
        sy = lib.systems[nm]
        for T in (4.0, 8.0):
            a = S.SystemSim(sy)
            for s in a.subs:
                s.use_vec = False
            a.advance(T)
            b = S.SystemSim(sy)
            b.advance(T)
            vtot += 1
            if _fp(a) != _fp(b):
                vbad += 1
    check("向量化与纯 Python 逐位一致", vbad == 0,
          "{0} 组比对，{1} 组不一致".format(vtot, vbad))

    # numpy 与 math 的三角函数逐位一致性（这是向量化安全的前提）
    import math as _m
    import struct as _st2
    import random as _rnd
    _rnd.seed(2024)
    diffs = 0
    for _ in range(20000):
        x = _rnd.uniform(-200.0, 200.0)
        if _st2.pack("<d", _m.sin(x)) != _st2.pack("<d", float(S._NP.sin(x))):
            diffs += 1
        if _st2.pack("<d", _m.cos(x)) != _st2.pack("<d", float(S._NP.cos(x))):
            diffs += 1
    check("numpy 的 sin/cos 与 math 逐位一致", diffs == 0,
          "2 万点 {0} 处差异".format(diffs))

bpy.data.objects.remove(cam, do_unlink=True)

print()
print("=" * 78)
print("9. vortex 力场语义（径向吸引，不是绕轴公转）")
print("=" * 78)
print("   pdx_axis_probe 四轮实机订正：vortex 只有指向 direction 轴线的径向吸引、")
print("   无切向分量；到轴距离 0 时完全无力（与 spin 的绕轴公转不同）。")

# 构造一个最小系统：vortex 轴 +Y、力心在原点，粒子放 +X 侧。
_vo = P.PdxForce.__new__(P.PdxForce)
_vo.type = "vortex"; _vo.name = "vo"; _vo.position = (0.0, 0.0, 0.0)
_vo.direction = (0.0, 1.0, 0.0); _vo.local_force = True
_vo.yaw = 0.0; _vo.division = 16.0; _vo.amount = 1.0
_sp_f = P.PdxForce.__new__(P.PdxForce)
_sp_f.type = "spin"; _sp_f.name = "sp"; _sp_f.position = (0.0, 0.0, 0.0)
_sp_f.direction = (0.0, 1.0, 0.0); _sp_f.local_force = True
_sp_f.yaw = 0.0; _sp_f.division = 16.0; _sp_f.amount = 1.0

_ss = S.SubsystemSim.__new__(S.SubsystemSim)
_ss.forces = [_vo]
_a = _ss._accel((3.0, 5.0, 0.0), (0.0, 0.0, 0.0), 0.0)
# 垂直分量 = (3,0,0)，单位化 (1,0,0)，正 amount 吸向轴 → 加速度应为 -X 方向
print("  粒子 (3, 5, 0)、轴 +Y、amount=+1 -> 加速度 {0}".format(
    tuple(round(x, 4) for x in _a)))
check("vortex 正 amount 吸向轴线（-X 方向）",
      _a[0] < -0.9 and abs(_a[1]) < 1e-9 and abs(_a[2]) < 1e-9,
      "加速度 {0}".format(tuple(round(x, 4) for x in _a)))

# 落在轴上（垂直分量为 0）时完全无力
_a_on = _ss._accel((0.0, 7.0, 0.0), (0.0, 0.0, 0.0), 0.0)
check("vortex 粒子在轴线上时不受力",
      max(abs(x) for x in _a_on) < 1e-12,
      "加速度 {0}".format(tuple(round(x, 6) for x in _a_on)))

# 无切向分量：切向方向 = axis × perp = Y × X = -Z，该分量必须为 0
check("vortex 无切向分量（不是公转）",
      abs(_a[2]) < 1e-12, "z 分量 {0:.3e}".format(_a[2]))

# spin 仍必须是公转（有切向分量），与 vortex 区分开
_ss.forces = [_sp_f]
_a_sp = _ss._accel((3.0, 5.0, 0.0), (0.0, 0.0, 0.0), 0.0)
check("spin 有切向分量（与 vortex 不同）", abs(_a_sp[2]) > 0.9,
      "z 分量 {0:.4f}".format(_a_sp[2]))

# 向量化版必须与标量版逐位一致（vortex 新分支）
if S._NP is not None:
    _bad = 0
    # 注意：比对前必须把 forces 切回 vortex——上面测 spin 时改成了 [_sp_f]，
    # 若不切回来就是「spin 的标量值 vs vortex 的向量值」，必然不符。
    _ss.forces = [_vo]
    for _pt in ((3.0, 5.0, 0.0), (0.0, 7.0, 0.0), (-2.0, 1.0, 4.0), (0.5, -3.0, 2.5)):
        _sf = _ss._accel(_pt, (0.0, 0.0, 0.0), 0.0)
        _va = S._accel_vec(S._NP.asarray([_pt[0]]), S._NP.asarray([_pt[1]]),
                           S._NP.asarray([_pt[2]]), [_vo], 0.0)
        _vv = (float(_va[0][0]), float(_va[1][0]), float(_va[2][0]))
        if max(abs(_sf[i] - _vv[i]) for i in range(3)) > 1e-12:
            _bad += 1
    check("vortex 向量化与标量一致", _bad == 0, "{0} 处不符".format(_bad))

print()
print("=" * 78)
print("10. mass=0 的粒子直接消失")
print("=" * 78)
_mz = [(sy, sb) for sy in lib.systems.values() for sb in sy.subsystems
       if sb.mass[0] <= 0.0]
check("存在 mass=0 的样本", len(_mz) > 0, "{0} 个".format(len(_mz)))
if _mz:
    _sy, _sb = _mz[0]
    _st = next((s for s in S.SystemSim(_sy).subs if s.name == _sb.name), None)
    if _st is None:
        check("mass=0 子系统进入模拟", False)
    else:
        _st.advance(3.0)
        print("  样本: {0} / {1}  mass={2}".format(_sy.name[:34], _sb.name, _sb.mass))
        check("mass=0 不产生任何粒子", len(_st.particles) == 0,
              "{0} 颗".format(len(_st.particles)))

# 非零 mass 不受影响（不能把整个 mass 通道关掉）。
# 只挑「立刻持续发射」的样本：start=0、无限窗口、emission≥1、max_amount≥1，
# 且**不是脉冲发射**——脉冲子系统在 t=2.0 可能正落在静默相，本来就没有粒子
# （实测 glow_glitch 的 pulse_period=2.5、lightning_burst=6.6，两者在 2 秒时
# 都处于停发段），不排除它们会把正确实现误判成失败。
_ok_mass = True
_checked_mass = 0
for _sy in sample(list(lib.systems.values()), 80):
    for _s in S.SystemSim(_sy).subs:
        _sb = _s.sub
        if _sb.mass[0] <= 0.0 or _sb.hide:
            continue
        if _sb.emission[0] < 1.0 or _sb.duration[0] >= 0.0 or _sb.start[0] > 0.0:
            continue
        if _sb.max_amount < 1 or _s.pulse_period > 0.0:
            continue
        _checked_mass += 1
        _s.advance(2.0)
        if not _s.particles:
            _ok_mass = False
check("非零 mass 的子系统仍正常发射", _ok_mass and _checked_mass > 0,
      "核对 {0} 个".format(_checked_mass))

print()
print("=" * 78)
print("11. 面片基角随机扰动 particle_pitch_var / particle_yaw_var")
print("=" * 78)
print("   原版结构由 _decomp/editor_registry.cpp 实锤：_var 用 CreateComponentVariation，")
print("   且把同名基值当父节点传入 = 基值 + 变化量。修复前只取 [0]，全系统共享一组基向量。")
print("   扰动在**发射时定死**，逐粒子一次（与 rotation 的基角同样式），不是每帧变。")

_jit = [(sy.name, sb.name) for sy in lib.systems.values() for sb in sy.subsystems
        if not sb.billboard and (abs(sb.particle_pitch[1]) > 1e-6
                                 or abs(sb.particle_yaw[1]) > 1e-6)]
check("存在 billboard=no 且带基角扰动的子系统", len(_jit) > 0,
      "{0} 个".format(len(_jit)))


def _basis_set(subname_sy, subname, sim_t=2.0):
    """跑一个子系统，返回 (去重后的基向量集合, 粒子数) 与最小 |法线·+Z|。"""
    sim = S.SystemSim(lib.systems[subname_sy])
    sim.advance(sim_t)
    st = next((s for s in sim.subs if s.name == subname), None)
    if st is None or not st.particles:
        return set(), 0, None
    snap = st.snapshot()
    per = bool(st.sub.particle_pitch[1] or st.sub.particle_yaw[1])
    bases = set()
    aligns = []
    for rec in snap:
        if per:
            r, u = BP._fixed_quad_basis(rec[13], rec[12])
        else:
            r, u = BP._fixed_quad_basis(st.sub.particle_yaw[0], st.sub.particle_pitch[0])
        bases.add(tuple(round(v, 5) for v in r + u))
        cr = (r[1] * u[2] - r[2] * u[1], r[2] * u[0] - r[0] * u[2], r[0] * u[1] - r[1] * u[0])
        ln = math.sqrt(sum(c * c for c in cr)) or 1e-9
        aligns.append(abs(cr[2] / ln))
    return bases, len(snap), (min(aligns) if aligns else None)


# 有扰动：多颗粒子的基向量应互不相同（修复前恒为 1 组）。
_var_ok = 0
_var_probed = 0
for _sy_n, _sb_n in sample(_jit, 40):
    _b, _n, _ = _basis_set(_sy_n, _sb_n)
    if _n < 2:
        continue
    _var_probed += 1
    if len(_b) > 1:
        _var_ok += 1
check("有 pitch/yaw 扰动的子系统逐粒子朝向不同", _var_probed > 0 and _var_ok == _var_probed,
      "{0}/{1} 个".format(_var_ok, _var_probed))

# 无扰动：必须仍然全系统一致（快路径没被破坏）。
_njit = [(sy.name, sb.name) for sy in lib.systems.values() for sb in sy.subsystems
         if not sb.billboard and abs(sb.particle_pitch[1]) < 1e-6
         and abs(sb.particle_yaw[1]) < 1e-6]
_nv_ok = 0
_nv_probed = 0
for _sy_n, _sb_n in sample(_njit, 40):
    _b, _n, _ = _basis_set(_sy_n, _sb_n)
    if _n < 2:
        continue
    _nv_probed += 1
    if len(_b) == 1:
        _nv_ok += 1
check("无扰动的子系统仍全系统朝向一致", _nv_probed > 0 and _nv_ok == _nv_probed,
      "{0}/{1} 个".format(_nv_ok, _nv_probed))

# 平铺回归：pitch≈90 且无扰动的，法线仍须与 +Z 完全对齐（308 个子系统的老契约）。
_flat_ok = 0
_flat_probed = 0
_flat = [(sy.name, sb.name) for sy in lib.systems.values() for sb in sy.subsystems
         if not sb.billboard and abs(sb.particle_pitch[0] - 90.0) < 1.0
         and abs(sb.particle_pitch[1]) < 1e-6 and abs(sb.particle_yaw[1]) < 1e-6]
for _sy_n, _sb_n in sample(_flat, 40):
    _b, _n, _al = _basis_set(_sy_n, _sb_n)
    if _n < 1 or _al is None:
        continue
    _flat_probed += 1
    if _al > 0.999:
        _flat_ok += 1
check("pitch≈90 无扰动的仍平铺（法线与 +Z 对齐）", _flat_probed > 0 and _flat_ok == _flat_probed,
      "{0}/{1} 个".format(_flat_ok, _flat_probed))

# 闭式解与逐行 _fixed_quad_basis 必须逐位一致（向量化分支的正确性前提）。
_rng = random.Random(20241012)
_ys = [_rng.uniform(-180.0, 180.0) for _ in range(300)]
_ps = [_rng.uniform(-180.0, 180.0) for _ in range(300)]
if BP._NP is not None:
    _R, _U = BP._quad_basis_arrays(
        BP._NP.radians(BP._NP.array(_ys)), BP._NP.radians(BP._NP.array(_ps)))
    _worst = 0.0
    for _i in range(300):
        _r0, _u0 = BP._fixed_quad_basis(_ys[_i], _ps[_i])
        _worst = max(_worst,
                     max(abs(_a - _b) for _a, _b in zip(_r0, _R[_i])),
                     max(abs(_a - _b) for _a, _b in zip(_u0, _U[_i])))
    check("逐粒子闭式解 == 逐行 _fixed_quad_basis", _worst < 1e-9,
          "最大差 {0:.2e}".format(_worst))

# 真值样本：colossus beam 三条都是 pitch={90 90}，ring_pulse / ball 是 {90 0}。
_col = lib.systems.get("fallen_empire_01_colossus_energy_effect")
if _col is not None:
    _beams = [s for s in _col.subsystems
              if s.name.startswith("beam") and abs(s.particle_pitch[1]) > 1e-6]
    check("colossus beam 带 pitch 扰动（真值样本）", len(_beams) == 3,
          "{0} 条，pitch={1}".format(len(_beams), [list(b.particle_pitch) for b in _beams]))
    _bv = 0
    _bn = 0
    for _b_sub in _beams:
        _bs, _cnt, _ = _basis_set("fallen_empire_01_colossus_energy_effect", _b_sub.name)
        if _cnt < 2:
            continue
        _bn += 1
        if len(_bs) > 1:
            _bv += 1
    check("colossus beam 逐粒子朝向发散", _bn > 0 and _bv == _bn, "{0}/{1} 条".format(_bv, _bn))
else:
    print("  (跳过 colossus 真值样本：未找到该系统)")

# 组数组必须真能跑：逐粒子基向量分支的形状广播只有打 _build_arrays 才会暴露
# （单元断言只调 _fixed_quad_basis，绕过组数组就测不到）。这里只打少量样本：
# 每个都要跑一次 SystemSim.advance，实测是整脚本里最贵的一段，取 8 个足够。
_arr_ok = True
_arr_detail = []
_arr_n = 0
for _sy_n, _sb_n in sample(_jit, 5) + sample(_flat, 3):
    _sim = S.SystemSim(lib.systems[_sy_n])
    _sim.advance(2.0)
    _st = next((s for s in _sim.subs if s.name == _sb_n), None)
    if _st is None:
        continue
    _snap = _st.snapshot()
    if not _snap:
        continue
    _sb = _st.sub
    _per = bool(_sb.particle_pitch[1] or _sb.particle_yaw[1])
    _r0, _u0 = BP._fixed_quad_basis(_sb.particle_yaw[0], _sb.particle_pitch[0])
    try:
        if BP._NP is not None:
            BP._build_arrays_np(_snap, len(_snap), max(len(_snap), 1), _r0, _u0,
                                max(_sb.sheet_x, 1), max(_sb.sheet_y, 1),
                                (max(_sb.sheet_x, 1) * max(_sb.sheet_y, 1)) > 1, _per)
        BP._build_arrays_py(_snap, len(_snap), max(len(_snap), 1), _r0, _u0,
                            max(_sb.sheet_x, 1), max(_sb.sheet_y, 1),
                            (max(_sb.sheet_x, 1) * max(_sb.sheet_y, 1)) > 1, _per)
    except Exception as _e:  # noqa: BLE001
        _arr_ok = False
        _arr_detail.append("{0}/{1}: {2}".format(_sy_n, _sb_n, _e))
    _arr_n += 1
check("组数组两种实现都能跑通（含逐粒子基角）",
      _arr_n > 0 and _arr_ok, "跑 {0} 个，{1}".format(_arr_n, "; ".join(_arr_detail[:3])))

print()
print("=" * 78)
print("结果: {0} passed, {1} failed".format(len(PASS), len(FAIL)))
if FAIL:
    print("失败项:")
    for f in FAIL:
        print("   - " + f)
print("=" * 78)
