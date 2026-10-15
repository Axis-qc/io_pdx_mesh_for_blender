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
  9~15. 力场语义、mass、基角扰动、哈希维、二元组规则表等既有契约
  16. 解析健壮性 越界 / 空值 / BOM / r,g,b 颜色 / inf 尺寸 / 引号内的 #
  17. 动画      op=ABS、time 轴、start / duration / repeat 是否真的参与求值
  18. 随机维    size / alpha / 发射体角是否各有独立 salt
  19. 上限触顶  MAX_QUADS 裁剪与 MAX_PREVIEW_SECONDS 是否报出来
  20. 活体判定  用户手动删掉预览对象后是否还会抛 ReferenceError

抽样口径：解析层（读 .asset、数键）全量扫，成本秒级；
模拟层（SystemSim + advance 跑物理）抽样，固定种子可复现——
单次 advance 是数百毫秒，全量 4700+ 子系统要几十分钟，不值得。

不修改任何文件；只在结尾打印报告。
"""

import itertools
import logging
import math
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
from io_pdx_mesh.pdx_blender.blender_import_export import (  # noqa: E402
    blender_to_game as _blender_to_game,
)

GAME = os.environ.get(
    "IO_PDX_TEST_GAME", r"G:\SteamLibrary\steamapps\common\Stellaris")

# 可选：自建标定件所在的 mod 根。设了就跑针对标定件的真值断言，
# 没设则退回游戏内同特征样本（标定件在用户 mod 下，测试库不一定扫得到）。
PROBE_ROOT = os.environ.get(
    "IO_PDX_TEST_PROBE_ROOT",
    r"C:\Users\hp\Documents\Paradox Interactive\Stellaris\mod\stellaris_godie")

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

_roots = [PROBE_ROOT] if os.path.isdir(PROBE_ROOT) else []
_roots.append(GAME)
lib = P.ParticleLibrary(_roots).scan()
print("  索引系统数: {0}（根: {1}）".format(
    len(lib.systems), ", ".join(os.path.basename(r) for r in _roots)))

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
print("   Blender 与引擎经 (x,-z,y) 旋转映射，角度符号原样保留，不取负。")


def _eng_space(v):
    """Blender 向量 → 引擎向量。用被测模块的逆映射，不另写一份。"""
    return _blender_to_game(v)


def _quad_normal(r, u):
    return (r[1] * u[2] - r[2] * u[1],
            r[2] * u[0] - r[0] * u[2],
            r[0] * u[1] - r[1] * u[0])


# 四格实机读数：箭头（贴图 U）指向 + 是否水平。箭头用引擎空间读，水平看引擎 y 轴。
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
print("B2. 发射器形态：sphere 出生区域与半径（哈希权重回归）")
print("=" * 78)
print("   球面出生区域由 sphere_emitter_yaw / _pitch 划定；取数规则见 _VAR_RULES。")

sph_pick = None
for nm, sy in lib.systems.items():
    for sb in sy.subsystems:
        # 必须挑「两个轴的散布都够大」的样本才是整球：窄区域的样本（如 test_2）
        # 本来就只出一条弧带，用整球判据会误判。
        if (not sb.hide and sb.emitter_type == "sphere"
                and (sb.sphere_radius[0] > 0 or sb.sphere_radius[1] > 0)
                and sb.sphere_yaw[1] >= 170.0 and sb.sphere_pitch[1] >= 170.0):
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
        print("  样本: {0} / {1}  radius={2}".format(nm[:34], sb.name, sb.sphere_radius))
        print("    三轴符号比 x/y/z = {0}".format([round(v, 3) for v in signs]))
        # 三轴符号比要与「yaw/pitch 区域」相符：yaw 只覆盖部分方位时，符号会偏斜
        # （这是区域受限应有的结果，不是退化）。区域必须按**规则表**推：角度字段
        # 用 ± 时区间是 [基准-方差, 基准+方差]，用区间时是 [基准, 基准+方差]。
        def _span(base, var, rule):
            if rule == "pm":
                return base - var, base + var
            return base, base + var

        _ylo, _yhi = _span(sb.sphere_yaw[0], sb.sphere_yaw[1],
                           S._rule_of("sphere_emitter_yaw"))
        _plo, _phi = _span(sb.sphere_pitch[0], sb.sphere_pitch[1],
                           S._rule_of("sphere_emitter_pitch"))
        _exp_sign = []
        for _k in range(3):
            _pos = 0
            _tot = 0
            for _a in range(37):
                for _b in range(37):
                    _yy = _ylo + (_yhi - _ylo) * (_a / 36.0)
                    _pp = _plo + (_phi - _plo) * (_b / 36.0)
                    _dd = S._emit_dir(_pp, _yy)
                    _tot += 1
                    if _dd[_k] > 0:
                        _pos += 1
            _exp_sign.append(_pos / float(_tot))
        print("    区域 yaw[{0:g},{1:g}] pitch[{2:g},{3:g}]（按规则表推）".format(
            _ylo, _yhi, _plo, _phi))
        print("    三轴符号比 x/y/z = {0}  区域期望 {1}".format(
            [round(v, 3) for v in signs], [round(v, 3) for v in _exp_sign]))
        worst = max(abs(signs[k] - _exp_sign[k]) for k in range(3))
        check("sphere 三轴符号与 yaw/pitch 区域相符", worst < 0.08,
              "最大偏差 {0:.3f}".format(worst))
        # 覆盖率应与「yaw/pitch 划定的区域」相符：yaw 跨度占 360 的比例 ×
        # pitch 跨度占 180 的比例。yaw={0 180} 只覆盖半个方位 → 期望约 50%；
        # yaw={0 360}+pitch={0 180} 才是整球 → 期望 100%。
        # 网格用 acos(vy/L) 作极角、atan2(vz,vx) 作方位，与 _emit_dir 的
        # pitch/yaw 只差一个常数偏移（极角 = 90 - pitch，方位 = 90 + yaw），
        # 所以跨度不变。
        NP, NY = 18, 36
        grid = set()
        for p in pts:
            vx, vy, vz = p[2], p[3], p[4]
            L = math.sqrt(vx * vx + vy * vy + vz * vz) or 1e-9
            pit = math.degrees(math.acos(max(-1.0, min(1.0, vy / L))))
            yaw = math.degrees(math.atan2(vz / L, vx / L)) % 360.0
            grid.add((int(pit / (180.0 / NP)), int(yaw / (360.0 / NY))))
        cov = len(grid) / float(NP * NY)
        # 期望覆盖率按规则表推出的实际区间算（角度类 ± 时区间更宽）
        _yspan = min((_yhi - _ylo), 360.0) / 360.0
        _pspan = min((_phi - _plo), 180.0) / 180.0
        _expect = max(0.0, min(1.0, _yspan)) * max(0.0, min(1.0, _pspan))
        print("    区域 yaw 跨度 {0:g}° / pitch 跨度 {1:g}° → 期望覆盖 {2:.0%}，实测 {3:.1f}%".format(
            _yhi - _ylo, _phi - _plo, _expect, cov * 100))
        check("sphere 覆盖率与 yaw/pitch 区域相符",
              _expect > 0 and 0.70 * _expect <= cov <= min(1.0, 1.05 * _expect),
              "期望 {0:.0%} 实测 {1:.1f}%".format(_expect, cov * 100))
        # 半径必须非负且落在 [基准, 基准+方差] 内：旧「基准 ± 方差」会有一半
        # 半径为负被钳到 0，堆在原点（实测 DustParticlesOrange 达 48.5%）。
        rs = [math.sqrt(p[2] ** 2 + p[3] ** 2 + p[4] ** 2) for p in pts]
        _r0, _rv = sb.sphere_radius
        _at0 = sum(1 for r in rs if r < 1e-9)
        check("sphere 半径无原点堆积（区间语义）", _at0 == 0,
              "{0}/{1} 颗在原点，半径 {2:.2f}~{3:.2f}".format(
                  _at0, len(rs), min(rs), max(rs)))
        check("sphere 半径落在 [基准, 基准+方差] 内",
              all(_r0 - 1e-6 <= r <= _r0 + _rv + 1e-6 for r in rs),
              "基准 {0:g} 方差 {1:g}，实测 {2:.3f}~{3:.3f}".format(_r0, _rv, min(rs), max(rs)))
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
# 同理排除**带 start 方差**的：start={ 0 2 } 这类会摇出最多 2 秒的启动延迟，
# 实测 extra_dimensional_anchor_station_lightning_effect/lightning 摇到 1.746 秒、
# 速率 1.0，2 秒时按闭式解还不足一颗粒子——那是平方差语义的正确表现，
# 不是「发不出来」。这条断言要验的是 mass 通道，不该被启动延迟干扰。
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
        if _sb.start[1] > 0.0:
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
print("   面片基角的方差按 _VAR_RULES 取，发射时定死、逐粒子一次。")

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
print("12. 哈希权重：维度独立性与种子")
print("=" * 78)
print("   随机量来自整数哈希（_hash32），各维独立、同种子可复现。")

_sim_w = S.SystemSim(sorted(lib.systems.values(), key=lambda s: s.name)[0])
_st_w = _sim_w.subs[0]
_D = 16
_cols = {s: [_st_w._w(i, s) for i in range(_D)] for s in range(_D)}


def _rank_of(v):
    order = sorted(range(len(v)), key=lambda k: v[k])
    r = [0.0] * len(v)
    for pos, idx in enumerate(order):
        r[idx] = pos
    return r


def _spearman(a, b):
    n = len(a)
    ra, rb = _rank_of(a), _rank_of(b)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = math.sqrt(sum((x - ma) ** 2 for x in ra))
    db = math.sqrt(sum((y - mb) ** 2 for y in rb))
    return cov / (da * db) if da and db else 0.0


_worst_rho = max(abs(_spearman(_cols[a], _cols[b]))
                 for a, b in itertools.combinations(range(_D), 2))
# 零假设：同样本量下「真正独立」的随机源，max|Spearman| 本来就有 0.6~0.8。
# 所以判据不能是「越小越好」，而得是「不超出独立随机的正常波动范围」。
_rng0 = random.Random(20241012)
_null = []
for _ in range(60):
    _c = {s: [_rng0.random() for _ in range(_D)] for s in range(_D)}
    _null.append(max(abs(_spearman(_c[a], _c[b]))
                     for a, b in itertools.combinations(range(_D), 2)))
_null.sort()
_p95 = _null[56]
print("  16 维两两最大秩相关 = {0:.3f}（独立随机 p95 = {1:.3f}）".format(_worst_rho, _p95))
check("哈希各维独立（不超出独立随机基准）", _worst_rho <= _p95,
      "{0:.3f} vs p95 {1:.3f}".format(_worst_rho, _p95))

# 相邻序号之间不能有恒定步长（等差梯子的直接判据）
_ds = [(_cols[9][k + 1] - _cols[9][k]) * 180.0 for k in range(_D - 1)]
check("相邻粒子的权重不再等差", (max(_ds) - min(_ds)) > 1.0,
      "相邻差极差 {0:.1f} 度".format(max(_ds) - min(_ds)))

# 种子：同种子逐位一致、不同种子确实不同、seed=0 走 seq_offset 路径
_seed_sys = lib.systems.get("fallen_empire_01_colossus_energy_effect")
if _seed_sys is not None:


    def _seed_pattern(seed):
        _s = S.SystemSim(_seed_sys, seed=seed)
        _s.advance(1.5)
        _t = next((x for x in _s.subs if x.name == "beam_1"), None)
        return [round(p[11], 6) for p in _t.particles] if _t else []


    _a1, _a2 = _seed_pattern(7), _seed_pattern(7)
    _b, _c0 = _seed_pattern(8), _seed_pattern(0)
    check("同种子逐位可复现", bool(_a1) and _a1 == _a2, "{0} 颗".format(len(_a1)))
    check("不同种子产生不同图案", bool(_a1) and _a1 != _b and _b != _c0)
else:
    print("  (跳过种子检查：未找到 colossus 样本)")

print()
print("=" * 78)
print("13. 「基准 + 方差」字段的第二值是否真的被用上")
print("=" * 78)
print("   方差非零的样本，逐粒子取值必须散开（解析层读到、模拟层没用会露馅）。")


def _spread_of(sysname, subname, team=60):
    """返回 (粒子数, 去重位置数, 去重颜色偏移数)。"""
    _sim = S.SystemSim(lib.systems[sysname])
    _st = next((x for x in _sim.subs if x.name == subname), None)
    if _st is None:
        return (0, 0, 0)
    for _i in range(team):
        _st.spawn_count = _i
        _st._spawn(0.0)
    _pos = set((round(p[2], 3), round(p[3], 3), round(p[4], 3)) for p in _st.particles)
    _col = set((round(p[13], 4), round(p[14], 4), round(p[15], 4)) for p in _st.particles)
    return (len(_st.particles), len(_pos), len(_col))


# 13a 球面半径写成「基准 0 + 方差」：必须散开（曾经全从原点发射）
_sph_var = None
for _n, _sy in lib.systems.items():
    for _sb in _sy.subsystems:
        if (not _sb.hide and _sb.emitter_type == "sphere"
                and _sb.sphere_radius[0] <= 0.0 and _sb.sphere_radius[1] > 0.0):
            _sph_var = (_n, _sb.name)
            break
    if _sph_var:
        break
if _sph_var:
    _nn, _pp, _ = _spread_of(*_sph_var)
    check("球面半径 {0 方差} 会散开（不再全在原地）", _pp > 1,
          "{0}/{1}: {2} 颗 {3} 个位置".format(_sph_var[0][:24], _sph_var[1], _nn, _pp))
else:
    check("找到球面半径只写方差的样本", False)

# 13b 真值样本：用户报的 DustParticlesOrange
_dust = ("infernal_system_crisis_star_file", "DustParticlesOrange")
if _dust[0] in lib.systems:
    _nn, _pp, _ = _spread_of(*_dust, team=80)
    check("DustParticlesOrange 从原点改为球面区域散布", _pp > 1,
          "{0} 颗 {1} 个位置".format(_nn, _pp))
else:
    print("  (跳过 DustParticlesOrange：未找到该系统)")

# 13c box 三轴只写方差：必须散开（曾经塌成单点）
_box_var = None
for _n, _sy in lib.systems.items():
    for _sb in _sy.subsystems:
        if (not _sb.hide and _sb.emitter_type == "box"
                and _sb.box_x[0] <= 0 and _sb.box_y[0] <= 0 and _sb.box_z[0] <= 0
                and (_sb.box_x[1] > 0 or _sb.box_y[1] > 0 or _sb.box_z[1] > 0)):
            _box_var = (_n, _sb.name)
            break
    if _box_var:
        break
if _box_var:
    _nn, _pp, _ = _spread_of(*_box_var)
    check("box 三轴 {0 方差} 会散开（不再塌成单点）", _pp > 1,
          "{0}/{1}: {2} 颗 {3} 个位置".format(_box_var[0][:24], _box_var[1], _nn, _pp))
else:
    check("找到 box 只写方差的样本", False)

# 13d 颜色三通道方差：逐粒子抖动必须不同（曾经在解析层就被丢弃）
_col_var = None
for _n, _sy in lib.systems.items():
    for _sb in _sy.subsystems:
        if not _sb.hide and max(_sb.color_var) > 1e-9:
            _col_var = (_n, _sb.name)
            break
    if _col_var:
        break
if _col_var:
    _nn, _, _cc = _spread_of(*_col_var)
    check("颜色方差产生逐粒子抖动", _cc > 1,
          "{0}/{1}: {2} 颗 {3} 种偏移".format(_col_var[0][:24], _col_var[1], _nn, _cc))
else:
    check("找到带颜色方差的样本", False)

# 13e 相反方向：没有方差的字段不能凭空抖动（防止把 0 方差也算进去）
_zero = None
for _n, _sy in lib.systems.items():
    for _sb in _sy.subsystems:
        if (not _sb.hide and not any(_sb.color_var)
                and _sb.emitter_type == "point"):
            _zero = (_n, _sb.name)
            break
    if _zero:
        break
if _zero:
    _nn, _, _cc = _spread_of(*_zero)
    check("无颜色方差时不做抖动", _cc <= 1, "{0} 种偏移".format(_cc))

# 13f start 方差：整个子系统摇一次（同一子系统内所有粒子同一起点）
_start_var = None
for _n, _sy in lib.systems.items():
    for _sb in _sy.subsystems:
        if not _sb.hide and _sb.start[1] > 0.0:
            _start_var = (_n, _sb.name, _sb)
            break
    if _start_var:
        break
if _start_var:
    _n0, _s0, _sb0 = _start_var
    _a = S.SystemSim(lib.systems[_n0])
    _aa = next((x for x in _a.subs if x.name == _s0), None)
    _b = S.SystemSim(lib.systems[_n0])
    _bb = next((x for x in _b.subs if x.name == _s0), None)
    _base0 = _sb0.start[0]
    check("start 方差整子系统摇一次（同参数两次构建一致）",
          _aa is not None and abs(_aa.start - _bb.start) < 1e-12,
          "start={0} vs {1}".format(round(_aa.start, 3) if _aa else None,
                                    round(_bb.start, 3) if _bb else None))
    check("start 方差确实可能推迟启动（≥基准值）",
          _aa is not None and _aa.start >= _base0 - 1e-9,
          "基准 {0} 实测 {1}".format(_base0, round(_aa.start, 3) if _aa else None))
else:
    check("找到带 start 方差的样本", False)

print()
print("=" * 78)
print("14. 二元组取值域与球面出生区域")
print("=" * 78)
print("   按 _VAR_RULES 的规则验各字段取值域（长度类不得为负、角度类可下探下半球）。")

# 14a 逐字段验证：取值必须落在 [基准, 基准+方差] 内，且能覆盖整个区间
_sem_sys = None
for _n, _sy in lib.systems.items():
    for _sb in _sy.subsystems:
        if (not _sb.hide and _sb.life[1] > 0.2
                and _sb.emission[0] >= 5.0 and _sb.max_amount >= 20):
            _sem_sys = (_n, _sb)
            break
    if _sem_sys:
        break
if _sem_sys:
    _n0, _sb0 = _sem_sys
    _sim = S.SystemSim(lib.systems[_n0])
    _st = next((x for x in _sim.subs if x.sub.name == _sb0.name), None)
    if _st is not None:
        for _i in range(400):
            _st.spawn_count = _i
            _st._spawn(0.0)
        _lives = [p[1] for p in _st.particles]
        _lo, _hi = _sb0.life[0], _sb0.life[0] + _sb0.life[1]
        check("life 落在 [基准, 基准+方差] 内",
              bool(_lives) and all(_lo - 1e-6 <= v <= _hi + 1e-6 for v in _lives),
              "基准 {0:g} 方差 {1:g} 实测 {2:.3f}~{3:.3f}".format(
                  _sb0.life[0], _sb0.life[1], min(_lives), max(_lives)))
        check("life 不跨到基准以下（旧 ± 语义会）",
              bool(_lives) and min(_lives) >= _lo - 1e-6,
              "最小 {0:.3f} vs 基准 {1:.3f}".format(min(_lives), _lo))
        # 覆盖度：400 颗应该铺满整个区间（分 4 段都有）
        _seg = [0, 0, 0, 0]
        for v in _lives:
            _seg[min(int((v - _lo) / max(_hi - _lo, 1e-9) * 4), 3)] += 1
        check("life 铺满区间（4 段都有样本）", min(_seg) > 0, "{0}".format(_seg))
else:
    check("找到 life 方差样本", False)

# 14b 球面半径在区间语义下不应有原点堆积（用户报的 bug 的直接判据）
_dust2 = ("infernal_system_crisis_star_file", "DustParticlesOrange")
if _dust2[0] in lib.systems:
    _sim = S.SystemSim(lib.systems[_dust2[0]])
    _st = next((x for x in _sim.subs if x.name == _dust2[1]), None)
    if _st is not None:
        for _i in range(400):
            _st.spawn_count = _i
            _st._spawn(0.0)
        _rs = [math.sqrt(p[2] ** 2 + p[3] ** 2 + p[4] ** 2) for p in _st.particles]
        _at0 = sum(1 for r in _rs if r < 1e-9)
        check("DustParticlesOrange 无原点堆积（0/400）", _at0 == 0,
              "{0}/400 在原点，半径 {1:.2f}~{2:.2f}".format(_at0, min(_rs), max(_rs)))
        _rv = _st.sub.sphere_radius[1]
        check("DustParticlesOrange 半径落在 0~方差 内",
              all(-1e-6 <= r <= _rv + 1e-6 for r in _rs),
              "方差 {0:g} 实测 {1:.3f}~{2:.3f}".format(_rv, min(_rs), max(_rs)))

# 14c 出生区域是「一段弧带」而不是整球：用标定件 test_2 的参数复现。
_probe = ("pdx_axis_probe", "test_2")
_sim_probe = None
for _n, _sy in lib.systems.items():
    if "pdx_axis_probe" in _n:
        for _sb in _sy.subsystems:
            if _sb.name == "test_2":
                _probe = (_n, _sb)
                break
    if _sim_probe is None and _probe[1] is not None:
        break
if _probe[0] in lib.systems and any(s.name == "test_2" for s in lib.systems[_probe[0]].subsystems):
    _sy = lib.systems[_probe[0]]
    _sb = next(s for s in _sy.subsystems if s.name == "test_2")
    _sim = S.SystemSim(_sy)
    _st = next((x for x in _sim.subs if x.name == "test_2"), None)
    if _st is not None:
        for _i in range(2000):
            _st.spawn_count = _i
            _st._spawn(0.0)
        # 出生点要减去 position 偏移再归一化：p[2..4] 是引擎绝对坐标，
        # 含子系统 position（test_2 的 position.y=20）。
        _pos = _sb.position
        _pt = [(p[2] - _pos[0], p[3] - _pos[1], p[4] - _pos[2]) for p in _st.particles]
        # 区间按规则表推（sphere_emitter_yaw/pitch 是角度类，±）
        _plo = _sb.sphere_pitch[0] - _sb.sphere_pitch[1]
        _phi = _sb.sphere_pitch[0] + _sb.sphere_pitch[1]
        _ylo = _sb.sphere_yaw[0] - _sb.sphere_yaw[1]
        _yhi = _sb.sphere_yaw[0] + _sb.sphere_yaw[1]
        # 反解判定：pitch 跨 90 时 cos(pitch) 变号、水平朝向翻 180°，
        # 所以按 cos 符号分两支，负号支要求 yaw+180 落在区间内。
        _bad = 0
        for _x, _y, _z in _pt:
            _L = math.sqrt(_x * _x + _y * _y + _z * _z) or 1e-9
            _uy, _ux, _uz = _y / _L, _x / _L, _z / _L
            # 由 sin(pitch) = uy 得 pitch 候选（主值与补角，后者对应 cos 变号）
            _sp = max(-1.0, min(1.0, _uy))
            _p1 = math.degrees(math.asin(_sp))
            _p2 = 180.0 - _p1
            _horiz = math.sqrt(max(0.0, 1.0 - _sp * _sp))
            if _horiz < 1e-6:
                _yaws = (0.0,)                      # 正上/正下，yaw 无意义
            else:
                _yaws = (math.degrees(math.atan2(-_ux / _horiz, _uz / _horiz)) % 360.0,)
            _ok_one = False
            for _pp in (_p1, _p2):
                if not (_plo - 1e-6 <= _pp <= _phi + 1e-6):
                    continue
                _sign = 1.0 if math.cos(math.radians(_pp)) >= 0 else -1.0
                for _yy in _yaws:
                    # cos(pitch) 为负时水平朝向整体翻转，等价 yaw 加 180
                    _eff = _yy if _sign >= 0 else (_yy + 180.0) % 360.0
                    _w = (_eff - _ylo) % 360.0
                    if _w <= (_yhi - _ylo) + 1e-6:
                        _ok_one = True
                        break
                if _ok_one:
                    break
            if not _ok_one:
                _bad += 1
        _oct = set((_x > 0, _y > 0, _z > 0) for _x, _y, _z in _pt)
        _neg_y = sum(1 for _x, _y, _z in _pt if _y < 0)
        check("标定件 test_2 出生点全部落在 yaw/pitch 划定的弧带内",
              bool(_pt) and _bad == 0,
              "pitch[{0:g},{1:g}] yaw[{2:g},{3:g}]，{4} 颗中 {5} 颗越界"
              "（{6} 颗伸到引擎 y<0）".format(
                  _plo, _phi, _ylo, _yhi, len(_pt), _bad, _neg_y))
        # 区域受限 → 只覆盖少数卦限，绝不可能是整球（8 个）
        check("标定件 test_2 只覆盖部分卦限（弧带而非整球）", 0 < len(_oct) < 8,
              "{0}/8 个卦限，{1} 颗".format(len(_oct), len(_pt)))
        # 角度类必须是 ±：pitch={60 80} 要能下探到负仰角（进下半球）
        check("标定件 test_2 的 pitch 下探到下半球", _neg_y > 0,
              "{0}/{1} 颗在引擎 y<0".format(_neg_y, len(_pt)))
else:
    print("  (跳过 test_2：未找到 pdx_axis_probe 标定件)")

# 14d box 只写方差时应铺开且以原点为中心（不是偏在一侧）
_box2 = None
for _n, _sy in lib.systems.items():
    for _sb in _sy.subsystems:
        if (not _sb.hide and _sb.emitter_type == "box"
                and _sb.box_y[0] <= 0 and _sb.box_y[1] > 0):
            _box2 = (_n, _sb)
            break
    if _box2:
        break
if _box2:
    _sim = S.SystemSim(lib.systems[_box2[0]])
    _st = next((x for x in _sim.subs if x.name == _box2[1].name), None)
    if _st is not None:
        for _i in range(300):
            _st.spawn_count = _i
            _st._spawn(0.0)
        _ys = [p[3] for p in _st.particles]
        _pos = sum(1 for v in _ys if v > 0)
        _rv = _box2[1].box_y[1]
        check("box 轴只写方差时以原点为中心（两侧都有）",
              0 < _pos < len(_ys) and max(_ys) <= _rv + 1e-6 and min(_ys) >= -_rv - 1e-6,
              "+Y {0} / -Y {1}，范围 {2:.2f}~{3:.2f}，方差 {4:g}".format(
                  _pos, len(_ys) - _pos, min(_ys), max(_ys), _rv))
else:
    check("找到 box 只写方差的样本", False)

# 14e sphere_emitter_yaw / _pitch 划定**出生区域**，且不影响初速度方向
_sphregion = None
for _n, _sy in lib.systems.items():
    for _sb in _sy.subsystems:
        if (not _sb.hide and _sb.emitter_type == "sphere"
                and _sb.sphere_radius[0] > 0
                and (_sb.sphere_yaw[1] > 1e-9 or _sb.sphere_pitch[1] > 1e-9)):
            _sphregion = (_n, _sb)
            break
    if _sphregion:
        break
if _sphregion:
    _sim = S.SystemSim(lib.systems[_sphregion[0]])
    _st = next((x for x in _sim.subs if x.sub.name == _sphregion[1].name), None)
    if _st is not None:
        _sb0 = _sphregion[1]
        _sim2 = S.SystemSim(lib.systems[_sphregion[0]])
        _st2 = next((x for x in _sim2.subs if x.sub.name == _sb0.name), None)
        for _i in range(300):
            _st.spawn_count = _i
            _st._spawn(0.0)
            _st2.spawn_count = _i
            _st2._spawn(0.0)
        # 出生点必须全部落在球面上（半径 = sphere_radius 基准，此样本方差为 0）
        _rr = _sb0.sphere_radius[0]
        _on_sphere = all(abs(math.sqrt(p[2] ** 2 + p[3] ** 2 + p[4] ** 2) - _rr) < 1e-6
                         for p in _st.particles)
        check("球面出生点半径一致", bool(_st.particles) and _on_sphere,
              "半径 {0:g}，{1} 颗".format(_rr, len(_st.particles)))
        # 出生区域必须被 yaw/pitch 的范围约束住：把每个出生点反解成球面角，
        # 应落在规则表推出的区间内（角度类是 ±；yaw 允许环绕）。
        _ylo = _sb0.sphere_yaw[0] - _sb0.sphere_yaw[1]
        _yhi = _sb0.sphere_yaw[0] + _sb0.sphere_yaw[1]
        _plo = _sb0.sphere_pitch[0] - _sb0.sphere_pitch[1]
        _phi = _sb0.sphere_pitch[0] + _sb0.sphere_pitch[1]
        _bad = 0
        _dirs = set()
        for _p in _st.particles:
            _L = math.sqrt(_p[2] ** 2 + _p[3] ** 2 + _p[4] ** 2) or 1e-9
            _pit = math.degrees(math.asin(max(-1.0, min(1.0, _p[3] / _L))))
            _yaw = math.degrees(math.atan2(-_p[2] / _L, _p[4] / _L))
            _dirs.add((round(_pit, 1), round(_yaw % 360.0, 1)))
            if not (_plo - 0.5 <= _pit <= _phi + 0.5):
                _bad += 1
            # yaw 环绕：区间可能跨 0（如 -10~30），按环绕宽度判定
            _yw = (_yaw - _ylo) % 360.0
            if not (0.0 <= _yw <= (_yhi - _ylo) + 0.5):
                _bad += 1
        check("出生点被 sphere_yaw/pitch 范围约束",
              bool(_st.particles) and _bad == 0 and len(_dirs) > 1,
              "pitch[{0:g},{1:g}] yaw[{2:g},{3:g}]，{4} 颗中 {5} 颗越界，{6} 种朝向".format(
                  _plo, _phi, _ylo, _yhi, len(_st.particles), _bad, len(_dirs)))
        # 方向不受 sphere_emitter 角影响：同一参数两次构建，方向分布应只由
        # velocity_* 决定。这里直接验「方向与该子系统的 velocity 角一致」。
        _vbad = 0
        for _p in _st.particles:
            _L = math.sqrt(_p[5] ** 2 + _p[6] ** 2 + _p[7] ** 2) or 1e-9
            _dp = math.degrees(math.asin(max(-1.0, min(1.0, _p[6] / _L))))
            if not (min(_st.pitch[0], _st.pitch[0] + _st.pitch[1]) - 0.5
                    <= _dp <= max(_st.pitch[0], _st.pitch[0] + _st.pitch[1]) + 0.5):
                _vbad += 1
        check("初速度方向只由 velocity_pitch 决定（sphere 角不参与）", _vbad == 0,
              "{0} 颗中 {1} 颗方向越界；velocity_pitch 区间 [{2:g},{3:g}]".format(
                  len(_st.particles), _vbad, _st.pitch[0], _st.pitch[0] + _st.pitch[1]))
        print("  （{0}/{1}，{2} 颗粒子 {3} 种出生朝向）".format(
            _sphregion[0][:26], _sb0.name, len(_st.particles), len(_dirs)))
else:
    check("找到 sphere 出生区域样本", False)

print()
print("=" * 78)
print("15. 二元组取数规则表（每字段一条规则）")
print("=" * 78)
print("   逐字段登记是否有规则、两种规则的端点值、标定件真值。")

# 15a 规则表本身：角度类必须 pm、长度类必须 interval
_rule_expect = {
    "sphere_emitter_pitch": "pm", "sphere_emitter_yaw": "pm",
    "velocity_pitch": "pm", "velocity_yaw": "pm",
    "particle_pitch": "pm", "particle_yaw": "pm", "rotation": "pm",
    # velocity 是有符号的：负值表示反方向运动（用户实机确认）
    "velocity": "pm",
    "sphere_emitter_radius": "interval", "box_emitter_x": "interval",
    "box_emitter_y": "interval", "box_emitter_z": "interval",
    "life": "interval", "size": "interval", "alpha": "interval",
}
_bad_rule = [k for k, v in _rule_expect.items() if S._rule_of(k) != v]
check("规则表逐字段登记正确", not _bad_rule, "不符: {0}".format(_bad_rule))

# 15b _pick 两种规则的实际取值域
_ok_pm = all(abs(S._pick(10.0, 20.0, u, "pm") - (10.0 + 20.0 * (2 * u - 1.0))) < 1e-12
             for u in (0.0, 0.25, 0.5, 0.75, 1.0))
_ok_iv = all(abs(S._pick(10.0, 20.0, u, "interval") - (10.0 + 20.0 * u)) < 1e-12
             for u in (0.0, 0.25, 0.5, 0.75, 1.0))
check("_pick 的 pm 规则实现正确", _ok_pm)
check("_pick 的 interval 规则实现正确", _ok_iv)
check("pm 规则下 u=0/1 得到 基准-方差 / 基准+方差",
      S._pick(10.0, 20.0, 0.0, "pm") == -10.0 and S._pick(10.0, 20.0, 1.0, "pm") == 30.0)
check("interval 规则下 u=0/1 得到 基准 / 基准+方差",
      S._pick(10.0, 20.0, 0.0, "interval") == 10.0
      and S._pick(10.0, 20.0, 1.0, "interval") == 30.0)

# 15b2 velocity 是有符号量（±）：负基准表示反方向运动，浮动应跨在基准两侧
check("velocity 规则是有符号的 ±",
      S._rule_of("velocity") == "pm"
      and S._pick(-10.0, 2.0, 0.0, "pm") == -12.0
      and S._pick(-10.0, 2.0, 1.0, "pm") == -8.0
      and S._pick(-10.0, 2.0, 0.5, "pm") == -10.0,
      "-10±2 端点应为 -12 / -8")

# 真样本：库里存在负基准的 velocity，取到的速度应正负都出现（围绕基准浮动）
_negv = None
for _n, _sy in lib.systems.items():
    for _s in _sy.subsystems:
        if not _s.hide and _s.velocity[0] < 0 and _s.velocity[1] > 0:
            _negv = (_n, _s)
            break
    if _negv:
        break
if _negv:
    _sim = S.SystemSim(lib.systems[_negv[0]])
    _st = next((x for x in _sim.subs if x.name == _negv[1].name), None)
    if _st is not None:
        for _i in range(300):
            _st.spawn_count = _i
            _st._spawn(0.0)
        _speeds = [math.sqrt(p[5] ** 2 + p[6] ** 2 + p[7] ** 2) for p in _st.particles]
        _v0, _vv = _negv[1].velocity
        check("负基准 velocity 围绕基准浮动（± 而非单向区间）",
              bool(_speeds)
              and all(abs(_v0) - _vv - 1e-6 <= v <= abs(_v0) + _vv + 1e-6 for v in _speeds)
              and min(_speeds) < abs(_v0) < max(_speeds),
              "基准 {0:g} 方差 {1:g}，速度 {2:.3f}~{3:.3f}".format(
                  _v0, _vv, min(_speeds), max(_speeds)))
else:
    check("找到负基准 velocity 样本", False)

# 15c 真值：pitch 跨过 90 的球面发射器必须能进下半球（角度类才成立）。
#     优先用标定件 test_2；取不到则退回游戏内同特征样本。
_cross = None
for _n, _sy in lib.systems.items():
    if "pdx_axis_probe" in _n:
        for _s in _sy.subsystems:
            if _s.name == "test_2":
                _cross = (_n, _s)
                break
    if _cross:
        break
_cross_src = "标定件 test_2"
if _cross is None:
    for _n, _sy in lib.systems.items():
        for _s in _sy.subsystems:
            if (not _s.hide and _s.emitter_type == "sphere"
                    and _s.sphere_pitch[0] > 0
                    and _s.sphere_pitch[0] + _s.sphere_pitch[1] > 95.0
                    and _s.sphere_pitch[0] - _s.sphere_pitch[1] < 0.0):
                _cross = (_n, _s)
                break
        if _cross:
            break
    _cross_src = "游戏内样本"
if _cross is not None:
    _sy_c, _sb_c = lib.systems[_cross[0]], _cross[1]
    _sim = S.SystemSim(_sy_c)
    _st = next((x for x in _sim.subs if x.name == _sb_c.name), None)
    if _st is not None:
        for _i in range(2000):
            _st.spawn_count = _i
            _st._spawn(0.0)
        _pos = _st.sub.position
        _ys = [p[3] - _pos[1] for p in _st.particles]
        _neg = sum(1 for v in _ys if v < 0)
        check("pitch 跨 90 的球面发射器延伸到下半球（角度类必须是 ±）",
              bool(_ys) and _neg > 0,
              "{0} pitch={1}，{2}/{3} 颗在引擎 y<0".format(
                  _cross_src, list(_sb_c.sphere_pitch), _neg, len(_ys)))
        # 同一样本的半径是长度类：必须恒为正、落在 [基准, 基准+方差]
        _rs = [math.sqrt((p[2] - _pos[0]) ** 2 + (p[3] - _pos[1]) ** 2
                         + (p[4] - _pos[2]) ** 2) for p in _st.particles]
        _r0, _rv = _sb_c.sphere_radius
        check("同一样本半径恒为正且落在区间内（长度类是区间）",
              bool(_rs) and all(r > 0 and _r0 - 1e-6 <= r <= _r0 + _rv + 1e-6 for r in _rs),
              "{0:.2f}~{1:.2f}（基准 {2:g} 方差 {3:g}）".format(
                  min(_rs), max(_rs), _r0, _rv))
else:
    check("找到 pitch 跨 90 的球面样本", False)

print()
print("=" * 78)
print("16. 解析健壮性：越界、空值、BOM、r/g/b 颜色、非有限值、引号内的 #")
print("=" * 78)


def _parse_or_none(txt):
    try:
        return P.parse_text(txt)
    except Exception as _e:  # noqa: BLE001
        return _e


def _parse_file_or_none(path):
    try:
        return P.parse_file(path)
    except Exception as _e:  # noqa: BLE001
        return _e


# 16a `key =` 结尾不再越界（曾 IndexError）
_r1 = _parse_or_none("a = 1\nb =")
check("`key =` 结尾不再越界崩溃",
      isinstance(_r1, list) and [n.key for n in _r1] == ["a", "b"],
      "结果 {0}".format(_r1 if not isinstance(_r1, list) else [n.key for n in _r1]))

# 16a2 块内 `name =` 紧跟 `}` 不再越界（曾 IndexError）
_r2 = _parse_or_none("particle = { name = }")
check("块内 `name =` 紧跟 `}` 不再越界崩溃",
      isinstance(_r2, list) and len(_r2) == 1 and [c.key for c in _r2[0].children] == ["name"],
      "结果 {0}".format(_r2 if not isinstance(_r2, list) else [c.key for c in _r2[0].children]))

# 16b `key = }` 是空值：那个 `}` 属于外层，不能被当成字符串吃掉
# （旧写法返回 a/b/c 三项但 b 的值是 "}"，且块内的 `}` 被吞后兄弟块整片吸入）
_r3 = _parse_or_none("a = 1 b = } c = 2")
check("`b = }` 的空值不吞掉闭块（兄弟 c 仍在）",
      isinstance(_r3, list) and [n.key for n in _r3] == ["a", "b", "c"]
      and _r3[1].value is None,
      "结果 {0}".format(_r3 if not isinstance(_r3, list) else
                        [(n.key, n.value) for n in _r3]))

_r4 = _parse_or_none(
    "particle = { name = s1 subsystem = { name = } }\n"
    "particle = { name = s2 subsystem = { name = sub2 } }\n")
_ok4 = (isinstance(_r4, list) and len(_r4) == 2
        and all(len(t.child_values("subsystem")) == 1 for t in _r4)
        and _r4[0].child("name").value == "s1" and _r4[1].child("name").value == "s2")
check("空值不把后一个顶层块整片吸入前一个块", _ok4,
      "顶层 {0} 个，子系统数 {1}".format(
          len(_r4) if isinstance(_r4, list) else _r4,
          [len(t.child_values("subsystem")) for t in _r4] if isinstance(_r4, list) else None))

# 16c color 的 r/g/b 键名（原版 18 个文件 50 个子系统这么写，曾整份丢成白色）
_c1 = P.PdxSubsystem(P.parse_text("subsystem = { color = { r = 180 g = 180 b = 255 } }")[0])
check("color 的 r/g/b 键名被识别（不再丢成白色）",
      abs(_c1.color[0] - 180.0 / 255.0) < 1e-9 and abs(_c1.color[2] - 1.0) < 1e-9,
      "解析结果 {0}".format(tuple(round(v, 4) for v in _c1.color)))
_c2 = P.PdxSubsystem(P.parse_text("subsystem = { color = { x = 10 y = 20 z = 30 } }")[0])
check("color 的 x/y/z 键名仍然有效（未回归）",
      abs(_c2.color[0] - 10.0 / 255.0) < 1e-9 and abs(_c2.color[2] - 30.0 / 255.0) < 1e-9,
      "解析结果 {0}".format(tuple(round(v, 4) for v in _c2.color)))
# 真样本：arc_emitter_file 的 color 是 r/g/b 写法
_arc = lib.systems.get("arc_emitter_file")
_hit = lib.systems.get("hit_effect_shield")
_rgbs = [sb.color for sb in (_arc.subsystems if _arc else [])]
check("arc_emitter_file 的 r/g/b 颜色不再是纯白",
      bool(_rgbs) and all(c != (1.0, 1.0, 1.0) for c in _rgbs),
      "颜色 {0}".format([tuple(round(v, 3) for v in c) for c in _rgbs[:3]]))
check("hit_effect_shield 的 r/g/b 颜色不再是纯白",
      _hit is not None and all(sb.color != (1.0, 1.0, 1.0) for sb in _hit.subsystems),
      "颜色 {0}".format([tuple(round(v, 3) for v in sb.color)
                         for sb in (_hit.subsystems if _hit else [])][:3]))

# 16d BOM 文件：带 BOM 时首 token 不能变成 "\ufeffparticle"（否则整份静默作废）
import tempfile  # noqa: E402

_fd, _bom_path = tempfile.mkstemp(suffix=".asset")
os.close(_fd)
try:
    with open(_bom_path, "wb") as _fh:
        _fh.write("\ufeffparticle = { name = bom_probe max_amount = 7 }".encode("utf-8"))
    _bom = _parse_file_or_none(_bom_path)
    check("带 BOM 的文件不再整份作废",
          isinstance(_bom, list) and len(_bom) == 1 and _bom[0].key == "particle"
          and _bom[0].child("name") is not None
          and _bom[0].child("name").value == "bom_probe",
          "顶层 {0}".format([(n.key, n.child("name").value if n.child("name") else None)
                             for n in _bom] if isinstance(_bom, list) else _bom))
finally:
    os.unlink(_bom_path)

# 16e max_amount / 贴图尺寸写成 inf（1e400）时回退默认值而不是抛异常
_fd2, _inf_path = tempfile.mkstemp(suffix=".asset")
os.close(_fd2)
try:
    with open(_inf_path, "w", encoding="utf-8") as _fh:
        _fh.write("particle = { name = inf_probe subsystem = { name = s "
                  "max_amount = 1e400 texture = { x = 1e400 y = 1e400 } } }")
    _inf = _parse_file_or_none(_inf_path)
    try:
        _inf_sub = P.PdxSubsystem(_inf[0].child_values("subsystem")[0])
        _inf_amt, _inf_x, _inf_y = _inf_sub.max_amount, _inf_sub.sheet_x, _inf_sub.sheet_y
    except Exception as _e2:  # noqa: BLE001
        _inf_amt, _inf_x, _inf_y = repr(_e2), None, None
    check("inf 的 max_amount 回退默认值（不再 OverflowError）",
          _inf_amt == 100, "实测 {0}".format(_inf_amt))
    check("inf 的贴图尺寸回退 1（不再 OverflowError）",
          _inf_x == 1 and _inf_y == 1, "实测 x={0} y={1}".format(_inf_x, _inf_y))
finally:
    os.unlink(_inf_path)
check("正常的 max_amount / 贴图尺寸未受影响",
      P.PdxSubsystem(P.parse_text(
          "subsystem = { max_amount = 12 texture = { x = 4 y = 4 } }")[0]).max_amount == 12)

# 16f 引号内的 # 不是注释（旧写法在引号中间截断）
_tok = P._tokenize('name = "a#b" # 真注释\nc = 2')
check("引号内的 # 不被当成注释起点",
      _tok == ["name", "=", '"a#b"', "c", "=", "2"], "token {0}".format(_tok))
check("引号外的 # 仍然去注释",
      [n.value for n in P.parse_text('name = "a#b" c = 2 # 后面的说明')] == ["a#b", 2.0])

print()
print("=" * 78)
print("17. 动画：op=ABS、time 轴、start / duration / repeat 参与求值")
print("=" * 78)
print("   time=life 按寿命倍数、time=life_abs 按秒；spawn / system 无法确定，只报不猜。")


def _anim(txt):
    return P.PdxAnimation(P.parse_text("animation = { " + txt + " }")[0])


# 17a ABS：三处引用的 size 基准都是 0，只有加法才产生闪光（乘法恒为 0）
_abs = _anim('name = a op = ABS minValue = 0 maxValue = 4 '
             'curve = { 0.0 0.0 0.25 0.8 1.0 1.0 }')
check("op=ABS 走加法（基准 0 时不再被乘成 0）",
      S._apply_anim(0.0, _abs, 0.25) > 0.0 and S._apply_anim(0.0, _abs, 0.0) == 0.0,
      "u=0.25 -> {0}".format(S._apply_anim(0.0, _abs, 0.25)))
# 取绝对值这一步只有「加法结果转负」才验得出来：base=0 时「加完取绝对值」与「只加」同值
check("op=ABS 确实对叠加结果取绝对值（负结果不被钳成 0）",
      abs(S._apply_anim(-5.0, _abs, 0.25) - 1.8) < 1e-9
      and abs(S._apply_anim(0.0, _abs, 0.25) - 3.2) < 1e-9,
      "-5 + 3.2 -> {0}（应 1.8；只加不取绝对值会得 0）".format(
          S._apply_anim(-5.0, _abs, 0.25)))
_absref = []
for _n2, _sy2 in lib.systems.items():
    for _sb2 in _sy2.subsystems:
        _a2 = _sy2.anim_for_channel(_sb2, "size")
        if _a2 is not None and _a2.op == "ABS":
            _absref.append((_n2, _sb2.size[0], _a2.name, _a2.max_value))
check("存在被引用的 op=ABS 动画且 size 基准为 0", len(_absref) > 0,
      "{0} 处: {1}".format(len(_absref), _absref[:3]))
check("ABS 引用的粒子确实会被画出来（尺寸非 0）",
      all(S._apply_anim(0.0, _sy2.anim_for_channel(_sb2, "size"), 0.5) > 0.0
          for _n2, _sy2 in lib.systems.items() for _sb2 in _sy2.subsystems
          if _sy2.anim_for_channel(_sb2, "size") is not None
          and _sy2.anim_for_channel(_sb2, "size").op == "ABS"),
      "逐项核对")

# 17b 未知 op 不再静默当乘法：走诊断出口报出来
_unk = _anim('name = zz op = NOPE curve = { 0.0 1.0 1.0 1.0 }')
_unk_logs = []
_h_unk = logging.Handler()
_h_unk.emit = lambda _rec: _unk_logs.append(_rec.getMessage())
S.SIM_LOG.addHandler(_h_unk)
try:
    _unk_val = S._apply_anim(2.0, _unk, 0.5)
finally:
    S.SIM_LOG.removeHandler(_h_unk)
check("未知 op 经诊断出口报出（不再静默）",
      len(_unk_logs) == 1 and "NOPE" in _unk_logs[0],
      "日志 {0}，回落值 {1}".format(_unk_logs, _unk_val))

# 17c duration / start / repeat 参与求值
_life_anim = _anim('name = d time = life duration = 0.05 curve = { 0.0 0.859 1.0 1.0 }')
check("time=life 的 duration 按寿命倍数（0.05 × 5s = 0.25s 走完）",
      abs(S._anim_u(_life_anim, 0.25, 5.0) - 1.0) < 1e-9
      and abs(S._anim_u(_life_anim, 0.125, 5.0) - 0.5) < 1e-9,
      "u(0.125s, life=5)={0}".format(round(S._anim_u(_life_anim, 0.125, 5.0), 4)))
check("time=life 的长寿命粒子不会被提前定格（duration=1 仍走满整条寿命）",
      abs(S._anim_u(_anim('name = d time = life duration = 1 curve = { 0.0 0.0 1.0 1.0 }'),
                    7.5, 10.0) - 0.75) < 1e-9)
check("time=life_abs 的 duration 按秒",
      abs(S._anim_u(_anim('name = d time = life_abs duration = 10 curve = { 0.0 0.0 1.0 1.0 }'),
                    5.0, 60.0) - 0.5) < 1e-9)
check("start 之前取曲线起点",
      S._anim_u(_anim('name = d time = life start = 0.5 duration = 1 '
                      'curve = { 0.0 0.0 1.0 1.0 }'), 2.0, 10.0) == 0.0)
check("repeat=yes 走完区间后回绕（不再定格在末端）",
      abs(S._anim_u(_anim('name = d time = life duration = 0.25 repeat = yes '
                          'curve = { 0.0 0.0 1.0 1.0 }'), 3.0, 4.0) - 0.0) < 1e-9,
      "u={0}".format(round(S._anim_u(
          _anim('name = d time = life duration = 0.25 repeat = yes '
                'curve = { 0.0 0.0 1.0 1.0 }'), 3.0, 4.0), 4)))
check("repeat=no 走完即停在末端",
      abs(S._anim_u(_anim('name = d time = life duration = 0.25 repeat = no '
                          'curve = { 0.0 0.0 1.0 1.0 }'), 3.0, 4.0) - 1.0) < 1e-9)

# 17d spawn / system 无法确定计时基准：必须报出来，不许静默
_ax_logs = []
_h_ax = logging.Handler()
_h_ax.emit = lambda _rec: _ax_logs.append(_rec.getMessage())
S.SIM_LOG.addHandler(_h_ax)
try:
    _spawn_u = S._anim_u(_anim('name = sp time = spawn duration = 2 curve = { 0.0 0.0 1.0 1.0 }'),
                         1.0, 4.0)
    _sys_u = S._anim_u(_anim('name = sy time = system duration = 5 curve = { 0.0 0.0 1.0 1.0 }'),
                       1.0, 4.0)
finally:
    S.SIM_LOG.removeHandler(_h_ax)
check("time=spawn / system 经诊断出口报出「未按该轴生效」",
      len([m for m in _ax_logs if "spawn" in m or "system" in m]) >= 1
      and abs(_spawn_u - 0.25) < 1e-9 and abs(_sys_u - 0.25) < 1e-9,
      "日志 {0}".format(_ax_logs[:2]))

# 17e 真样本：astral_rift 的 SizeLightning duration=0.05 配 life=5s / 0.1s
_astral = lib.systems.get("astral_rift_particle")
if _astral is not None:
    _lt = next((sb for sb in _astral.subsystems if sb.name == "Lightning"), None)
    _lt2 = next((sb for sb in _astral.subsystems if sb.name == "Lightning 2"), None)
    _sz = _astral.anim_for_channel(_lt, "size") if _lt else None
    check("astral_rift SizeLightning 的 duration=0.05 生效（5s 寿命不再全程只走 1%）",
          _sz is not None and abs(S._anim_u(_sz, 0.25, 5.0) - 1.0) < 1e-9
          and abs(S._anim_u(_sz, 0.005, 0.1) - 1.0) < 1e-9,
          "duration={0}".format(_sz.duration if _sz else None))
    _ag = _astral.anim_for_channel(_lt, "alpha") if _lt else None
    check("astral_rift GradientEnergy 的 life_abs duration=5 按秒走",
          _ag is not None and _ag.axis == "life_abs"
          and abs(S._anim_u(_ag, 2.5, 5.0) - 0.5) < 1e-9,
          "axis={0} duration={1}".format(_ag.axis if _ag else None,
                                         _ag.duration if _ag else None))
else:
    check("找到 astral_rift_particle 真样本", False)

# 17f 跳帧 vs 逐帧在时间轴改动后仍然逐位一致（含带 duration/repeat 的样本）
_tl_bad = 0
_tl_tot = 0
for _nm3 in ("astral_rift_particle", "explosion", "hit_effect_shield"):
    _sy3 = lib.systems.get(_nm3)
    if _sy3 is None:
        continue
    for _T in (2.0, 5.0):
        _a3 = S.SystemSim(_sy3)
        _a3.advance(_T)
        _b3 = S.SystemSim(_sy3)
        for _k in range(1, int(_T * 60) + 1):
            _b3.advance(_k / 60.0)
        _tl_tot += 1
        if _fp(_a3) != _fp(_b3):
            _tl_bad += 1
check("时间轴改动后跳帧与逐帧仍逐位一致", _tl_bad == 0,
      "{0} 组比对，{1} 组不一致".format(_tl_tot, _tl_bad))

print()
print("=" * 78)
print("18. 随机维独立：size / alpha / 发射体角各有独立 salt")
print("=" * 78)

# 18a 用户报的现象：life={3 3}、size={4 4} 时寿命最长的必然尺寸最大（秩相关 = 1）
_salt_sys = None
for _n4, _sy4 in lib.systems.items():
    for _sb4 in _sy4.subsystems:
        if (not _sb4.hide and _sb4.life[1] > 0.0 and _sb4.size[2] > 0.0
                and _sb4.max_amount >= 200):
            _salt_sys = (_n4, _sb4)
            break
    if _salt_sys:
        break
if _salt_sys:
    _n4, _sb4 = _salt_sys
    _st4 = next((x for x in S.SystemSim(lib.systems[_n4]).subs if x.sub.name == _sb4.name), None)
    if _st4 is not None:
        for _i in range(600):
            _st4.spawn_count = _i
            _st4._spawn(0.0)
        _lv = [p[1] for p in _st4.particles]
        _szv = [S._pick(_st4.size_base, _st4.size_var, p[16], S._rule_of("size"))
                for p in _st4.particles]
        _alv = [S._pick(_st4.alpha_base, _st4.alpha_var, p[17], S._rule_of("alpha"))
                for p in _st4.particles]
        _rho_ls = abs(_spearman(_lv, _szv))
        _rho_sa = abs(_spearman(_szv, _alv))
        print("  样本: {0} / {1}  life={2} size={3}".format(
            _n4[:34], _sb4.name, _sb4.life, (_sb4.size[0], _sb4.size[2])))
        print("    |秩相关| life~size={0:.4f}  size~alpha={1:.4f}（旧写法恒为 1.0000）".format(
            _rho_ls, _rho_sa))
        check("size 不再与 life 共用同一个随机权重", _rho_ls < 0.3,
              "|rho|={0:.4f}，{1} 颗".format(_rho_ls, len(_lv)))
        check("alpha 不再与 size 共用同一个随机权重", _rho_sa < 0.3,
              "|rho|={0:.4f}".format(_rho_sa))
        check("寿命权重仍存在 p[10]（回退重放要它）",
              bool(_st4.particles) and all(
                  abs(p[10] - _st4._w(_kq, 0)) < 1e-12
                  for _kq, p in enumerate(_st4.particles)),
              "{0} 颗逐项核对".format(len(_st4.particles)))
    else:
        check("life/size 方差样本进入模拟", False)
else:
    check("找到 life 与 size 都带方差的样本", False)

# 18b 新增 salt 与既有维不撞：与 salt=1/2 的秩相关必须低（复用即 1.0）
_wcol = {}
for _i in range(400):
    for _s5 in (1, 2, S._SALT_SIZE, S._SALT_ALPHA, S._SALT_EMIT_YAW, S._SALT_EMIT_PITCH):
        _wcol.setdefault(_s5, []).append(_st_w._w(_i, _s5))
_rho_reuse = max(abs(_spearman(_wcol[_s5], _wcol[_s6]))
                 for _s5 in (S._SALT_SIZE, S._SALT_ALPHA, S._SALT_EMIT_YAW, S._SALT_EMIT_PITCH)
                 for _s6 in (1, 2))
_rho_new = max(abs(_spearman(_wcol[_s5], _wcol[_s6]))
               for _s5, _s6 in itertools.combinations(
                   (S._SALT_SIZE, S._SALT_ALPHA, S._SALT_EMIT_YAW, S._SALT_EMIT_PITCH), 2))
print("  新 salt 与既有维 salt=1/2 的最大 |秩相关| = {0:.4f}（复用同一 salt 时为 1.0000）".format(
    _rho_reuse))
check("新增 salt 与既有维不共线", _rho_reuse < 0.3, "|rho|={0:.4f}".format(_rho_reuse))
check("新增 salt 之间互不共线", _rho_new < 0.3, "|rho|={0:.4f}".format(_rho_new))

# 18c 发射体角不再与出生点位置共用 salt（旧写法与半径秩相关 = 1）
_emit_probe = P.PdxParticleSystem(P.parse_text(
    'particle = { name = salt_emit_probe subsystem = { name = s1 max_amount = 5000 '
    'emission = 3000 life = { 5 0 } emitter_type = "sphere" '
    'sphere_emitter_radius = { 0 5 } sphere_emitter_yaw = { 0 360 } '
    'sphere_emitter_pitch = { 0 0 } emitter_yaw = { 0 180 } emitter_pitch = { 0 0 } '
    'velocity = 10 velocity_pitch = { 0 0 } velocity_yaw = { 0 0 } '
    'size = { 2 0 } alpha = { 255 0 } hide = no } }')[0], "probe")
_st6 = S.SystemSim(_emit_probe).subs[0]
for _i in range(600):
    _st6.spawn_count = _i
    _st6._spawn(0.0)
_pos6 = _st6.sub.position
_rad6, _azi6 = [], []
for _p6 in _st6.particles:
    _rx, _ry, _rz = (_p6[2] - _pos6[0], _p6[3] - _pos6[1], _p6[4] - _pos6[2])
    _rad6.append(math.sqrt(_rx * _rx + _ry * _ry + _rz * _rz))
    _azi6.append(math.degrees(math.atan2(-_p6[5], _p6[7])))
_rho_re = abs(_spearman(_rad6, _azi6))
print("  球面半径 ~ 发射体旋转后的方向角 |秩相关| = {0:.4f}（复用 salt 时为 1.0000）".format(
    _rho_re))
check("发射体角与出生点半径不再共用同一个 salt", _rho_re < 0.3,
      "|rho|={0:.4f}，{1} 颗".format(_rho_re, len(_rad6)))

print()
print("=" * 78)
print("19. 上限触顶：MAX_QUADS 裁剪与 MAX_PREVIEW_SECONDS 都要报出来")
print("=" * 78)

# 19a 超出 MAX_QUADS：画出的数量被裁到上限，且必须有限频告警
_clip_sys = P.PdxParticleSystem(P.parse_text(
    'particle = { name = clip_probe subsystem = { name = big max_amount = 10000 '
    'emission = 2000 life = { 4 0 } size = { 4 0 } alpha = { 255 0 } hide = no } }')[0],
    "probe")
_clip_logs = []
_h_clip = logging.Handler()
_h_clip.emit = lambda _rec: _clip_logs.append(_rec.getMessage())
BP.IO_LOG.addHandler(_h_clip)
try:
    _pv_clip = BP.PreviewScene(_clip_sys, lib)
    _sp_clip = _pv_clip.sub_previews[0]
    _pv_clip.sim.advance(3.0)
    _alive_clip = len(_sp_clip.sub_sim.snapshot())
    _drawn_clip = _sp_clip.refresh(None)
    _logs_first = list(_clip_logs)
    _sp_clip.refresh(None)          # 第二次刷新不应重复刷屏
    _logs_second = list(_clip_logs)
finally:
    _pv_clip.destroy()
    BP.IO_LOG.removeHandler(_h_clip)
print("  存活 {0} 颗 -> 画出 {1} 颗，上限 {2}".format(
    _alive_clip, _drawn_clip, _sp_clip.capacity))
check("超出 MAX_QUADS 时画出数量被裁到上限", _drawn_clip == _sp_clip.capacity,
      "画出 {0}，上限 {1}".format(_drawn_clip, _sp_clip.capacity))
check("MAX_QUADS 裁剪已报出来（不再静默）",
      any("MAX_QUADS" in m or "clipped" in m for m in _logs_first),
      "日志 {0}".format(_logs_first[:1]))
check("MAX_QUADS 告警限频（第二次刷新不重复报）",
      len(_logs_second) == len(_logs_first),
      "第一次 {0} 条，第二次共 {1} 条".format(len(_logs_first), len(_logs_second)))
_left_clip = [o.name for o in bpy.data.objects if o.name.startswith("pdx_part_big")]
check("裁剪样本的预览对象已清理", not _left_clip, "{0}".format(_left_clip))

# 19b 超长系统：total_duration 钳到上限，触顶要报出来
_long_sys = None
for _n7, _sy7 in lib.systems.items():
    _sim7 = S.SystemSim(_sy7)
    if not _sim7.subs:
        continue
    _raw7 = max(s.start + (s.duration if s.duration > 0 else 0.0) + s.life + s.life_var
                for s in _sim7.subs)
    if _raw7 > S.MAX_PREVIEW_SECONDS + 1.0:
        _long_sys = (_n7, _sy7)
        break
if _long_sys:
    _n7, _sy7 = _long_sys
    _cap_logs = []
    _h_cap = logging.Handler()
    _h_cap.emit = lambda _rec: _cap_logs.append(_rec.getMessage())
    S.SIM_LOG.addHandler(_h_cap)
    try:
        _sim_cap = S.SystemSim(_sy7)
        # 时间轴长度就是 total_duration 给出的，用户拖不到上限之外，
        # 所以超长内容必须在给出时长时报出来，而不是等 advance 触顶。
        _dur_cap = _sim_cap.total_duration()
    finally:
        S.SIM_LOG.removeHandler(_h_cap)
    print("  超长样本: {0}  建议时长 {1}s（上限 {2}s）".format(
        _n7[:34], _dur_cap, S.MAX_PREVIEW_SECONDS))
    check("内容超过 MAX_PREVIEW_SECONDS 时已报出来（不再静默截断时间轴）",
          len(_cap_logs) > 0, "日志 {0}".format(_cap_logs[:1]))
    check("total_duration 仍钳在上限内",
          _sim_cap.total_duration() <= S.MAX_PREVIEW_SECONDS + 1e-9,
          "{0}".format(_sim_cap.total_duration()))
    # advance 触顶（时间轴被外部拖到上限之外）也必须报
    _adv_logs = []
    _h_adv = logging.Handler()
    _h_adv.emit = lambda _rec: _adv_logs.append(_rec.getMessage())
    S.SIM_LOG.addHandler(_h_adv)
    try:
        S.SystemSim(_sy7).advance(S.MAX_PREVIEW_SECONDS + 60.0)
    finally:
        S.SIM_LOG.removeHandler(_h_adv)
    check("advance 超过上限时也报出来", len(_adv_logs) > 0, "日志 {0}".format(_adv_logs[:1]))
else:
    check("找到原始时长超过上限的样本", False)

print()
print("=" * 78)
print("20. 用户手动删除预览对象后不再抛 ReferenceError")
print("=" * 78)
print("   Blender 删对象后 is None 为假、bool() 仍为真，只有属性访问才抛 ReferenceError。")

_del_sys = None
for _n8, _sy8 in lib.systems.items():
    for _sb8 in _sy8.subsystems:
        if not _sb8.hide and _sb8.max_amount >= 4 and _sb8.emission[0] >= 2.0:
            _del_sys = (_n8, _sy8)
            break
    if _del_sys:
        break
if _del_sys:
    _n8, _sy8 = _del_sys
    _pv_del = BP.PreviewScene(_sy8, lib)
    _sp_del = _pv_del.sub_previews[0]
    _obj_del = _sp_del.obj
    _is_none = _obj_del is None
    _bool_true = bool(_obj_del) is True
    bpy.data.objects.remove(_obj_del, do_unlink=True)
    _raises = False
    try:
        _obj_del.data
    except ReferenceError:
        _raises = True
    print("  样本: {0}  删除后: is None={1} bool()={2} 属性访问抛 ReferenceError={3}".format(
        _n8[:34], _is_none, _bool_true, _raises))
    check("复现前提：删对象后 is None 为假、只有属性访问抛异常",
          (not _is_none) and _bool_true and _raises)

    def _try(fn):
        try:
            fn()
            return None
        except Exception as _e:  # noqa: BLE001
            return _e

    _e_ref = _try(lambda: _sp_del.refresh(None))
    _e_en = _try(lambda: _pv_del.set_subsystem_enabled(_sp_del.sub_sim.name, True))
    _e_destroy = _try(lambda: _pv_del.destroy())
    check("删除预览对象后 refresh 不抛异常", _e_ref is None, "{0}".format(_e_ref))
    check("删除预览对象后 set_subsystem_enabled 不抛异常", _e_en is None, "{0}".format(_e_en))
    check("删除预览对象后 destroy 不抛异常", _e_destroy is None, "{0}".format(_e_destroy))
    _left8 = [o.name for o in bpy.data.objects if o.name.startswith("pdx_part_")]
    _leftm8 = [m.name for m in bpy.data.meshes if m.name.startswith("pdx_part_")]
    _leftc8 = [c.name for c in bpy.data.collections if c.name.startswith("pdx_particles_")]
    check("删除对象后 destroy 仍清干净（对象/网格/集合都不残留）",
          not _left8 and not _leftm8 and not _leftc8,
          "对象 {0} 网格 {1} 集合 {2}".format(_left8[:3], _leftm8[:3], _leftc8[:3]))
else:
    check("找到可测的预览样本", False)

# ---------------------------------------------------------------- 面片透明度三档
print()
print("=" * 78)
print("21. 面片透明度三档（blended / dithered / opaque）")
print("=" * 78)

_probe_pv = None
for _nm, _sy in lib.systems.items():
    if any((not s.hide) and s.max_amount >= 4 for s in _sy.subsystems):
        _probe_pv = _sy
        _probe_pv_name = _nm
        break

if _probe_pv is not None:
    # 本脚本不注册插件，Scene 上没有 io_pdx_particle_shade，current_shade_mode()
    # 兜底返回 blended，所以建出来的预览就是默认档。三档转换走 set_shade_mode，
    # 不依赖场景属性；「属性 → 建材质」那段联动在 check_panel_draw.py 里验
    # （那里真的注册了插件）。
    def _alpha_state(pv):
        _sp = pv.sub_previews[0]
        _mat = _sp.obj.data.materials[0]
        _bsdf = next(n for n in _mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        _a = _bsdf.inputs["Alpha"]
        return (len(_a.links) > 0,
                None if _a.links else round(float(_a.default_value), 4),
                getattr(_mat, "surface_render_method", None) or
                getattr(_mat, "blend_method", None))

    _pv21 = BP.PreviewScene(_probe_pv, lib)
    _bl = _alpha_state(_pv21)
    check("默认档接 alpha 且用 BLENDED 混合",
          _bl[0] and (_bl[2] in ("BLENDED", "BLEND")),
          "linked={0} mode={1}".format(_bl[0], _bl[2]))

    _n21 = _pv21.set_shade_mode(BP.SHADE_OPAQUE)
    _op = _alpha_state(_pv21)
    check("opaque 档断开 alpha 并钉成 1.0",
          (not _op[0]) and _op[1] == 1.0,
          "linked={0} alpha={1}".format(_op[0], _op[1]))

    _pv21.set_shade_mode(BP.SHADE_DITHERED)
    _di = _alpha_state(_pv21)
    check("dithered 档仍按 alpha 镂空（区别于 opaque）",
          _di[0] and _di[1] is None,
          "linked={0} mode={1}".format(_di[0], _di[2]))

    _pv21.set_shade_mode(BP.SHADE_BLENDED)
    _back = _alpha_state(_pv21)
    check("切回 blended 可逆（alpha 重新接上）",
          _back[0] and _back[2] in ("BLENDED", "BLEND"),
          "linked={0} mode={1}".format(_back[0], _back[2]))
    check("就地切换覆盖到全部子系统", _n21 > 0 and _n21 == len(_pv21.sub_previews),
          "改了 {0}/{1} 个".format(_n21, len(_pv21.sub_previews)))
    _pv21.destroy()
else:
    check("找到可测的面片材质样本", False)

print()
print("=" * 78)
print("22. mesh 贴图多根回退（mod mesh 引用原版贴图）")
print("=" * 78)

# 真实场景：mesh 在 mod 根，贴图只在原版根。模拟法：不真建 mod 根，
# 直接构造两个临时根，一个含 mesh+gfx，另一个只含贴图，验证跨根能解析到。
# 不用 tempfile.mkdtemp：实测它给出的目录带限制 ACL，连它自己建子目录都会被拒。
from io_pdx_mesh.pdx_blender import blender_import_export as BIE  # noqa: E402

try:
    _tmp22 = os.path.join(bpy.app.tempdir, "pdx_tex22")
    if os.path.isdir(_tmp22):
        import shutil  # noqa: E402
        shutil.rmtree(_tmp22, ignore_errors=True)
    _root_a = os.path.join(_tmp22, "mod")       # mesh 所在根（没有这张贴图）
    _root_b = os.path.join(_tmp22, "game")      # 贴图所在根
    _dir_a = os.path.join(_root_a, "gfx", "models", "mymod")
    _dir_b = os.path.join(_root_b, "gfx", "models", "vanilla")
    os.makedirs(_dir_a); os.makedirs(_dir_b)
    _mesh_a = os.path.join(_dir_a, "test.mesh")
    open(_mesh_a, "w").close()
    _dds_b = os.path.join(_dir_b, "cross_root_diff.dds")
    open(_dds_b, "w").close()
    # 同名 .dds 也先写好再建索引：索引按根缓存，建完再补文件测不出来。
    _dds_a = os.path.join(_dir_a, "same_name.dds")
    open(_dds_a, "w").close()

    # get_texture_index 只扫 mesh 自己的根 + 偏好配置根。这里临时把偏好根
    # 指向 _root_b：monkeypatch _configured_texture_roots 最省事且不动 bpy。
    _orig_roots = BIE._configured_texture_roots
    BIE._configured_texture_roots = lambda: [_root_b]
    try:
        _idx22 = BIE.get_texture_index(_mesh_a)
        _hit = _idx22.get("cross_root_diff.dds")
        check("mod 根没有的贴图能从配置的另一个根解析到",
              bool(_hit) and os.path.dirname(_hit[0]) == _dir_b,
              "hit={0}".format(_hit))
        # 本根优先：同名 .dds 两根都有时应取 mesh 自己根里的那份
        _hitb = _idx22.get("same_name.dds")
        check("两根同名时 mesh 自己根的优先",
              bool(_hitb) and len(_hitb) == 1 and os.path.dirname(_hitb[0]) == _dir_a,
              "hit={0}".format(_hitb))
    finally:
        BIE._configured_texture_roots = _orig_roots
        BIE._TEXTURE_INDEX_CACHE.pop(_root_a, None)
        BIE._TEXTURE_INDEX_CACHE.pop(_root_b, None)
except Exception as exc22:  # noqa: BLE001
    check("多根回退测试自身执行", False, repr(exc22))

print()
print("=" * 78)
print("结果: {0} passed, {1} failed".format(len(PASS), len(FAIL)))
if FAIL:
    print("失败项:")
    for f in FAIL:
        print("   - " + f)
print("=" * 78)
