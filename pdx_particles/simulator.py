"""
PDX particle simulator: deterministic, frame-scrub able integration.

 设计要点（与预览目标对应）：
 - 确定性：不用 Python 的 random，也从序列取权重。每颗粒子按出生序号与用途在
   整数哈希上取值，同一时刻擦洗到同一帧永远得到同一画面；哈希全程整数运算，
   同种子跨机器跨版本逐位一致。
 - 逐帧确定性驱动：advance(t) 把状态推进到绝对秒 t；子步恒为 SUBSTEP、按绝对
   子步号推进（不从本次 dt 等分），跳帧与逐帧结果才逐位一致。
 - 力场语义按实机校准记录实现：负 amount 吸引 / 正排斥，
   force 必须被子系统 force=<名> 引用才生效。
 - 不复现父级/entity 缩放（挂载缩放），只保证粒子系统内部尺寸比例。

 author : Axis-qc (fork of ross-g/io_pdx_mesh 0.91.2)
"""

import math

try:                      # numpy 可选：缺失时全部回退纯 Python 路径
    import numpy as _NP
except Exception:         # noqa: BLE001
    _NP = None

SUBSTEP = 1.0 / 120.0     # 物理子步（秒）
_EPS = 1e-9

# 粒子时间基准：1 秒 = 60 帧，与场景 render.fps 无关。
# 粒子数据里的 start / duration / life / emission 全部以秒为单位
# （emission = 每秒发射数），所以帧号与秒的换算必须用这个固定基准，
# 不能用场景 fps：场景 fps 是可变的显示属性，拿它换算会让同一份粒子
# 数据在不同 fps 的场景里算出不同的时间，回放速度与实际不符。
PARTICLE_FPS = 60

# 预览时长上限（秒）：防病态参数（极低 emission）拉出几万帧的时间轴。
MAX_PREVIEW_SECONDS = 60.0

# 回退重放的关键帧间隔（子步数）。模拟是确定性的，状态完全由
# (n_sub, spawn_count, particles) 决定，所以每隔这么多子步存一份快照，
# 时间轴往回拖时从最近的那份重放，不必从 0 重来。
#
# 取 60 子步（0.5 秒）：回退 1 秒约 1~9ms，单次回退最多重放 0.5 秒；代价是
# 最重的 10 个系统同时装载多占约 244MB。取 240（2 秒）省一半内存，但 1 秒级
# 回退最多要重放 2 秒。
CHECKPOINT_STRIDE = 60


def _emit_dir(pitch_deg, yaw_deg):
    """发射方向的单位向量：**仰角 pitch + 方位角 yaw**，方位基准为 **+Z**。

    公式（pdx_axis_probe 实机标定，全库唯一一套）：

        d = (-sin yaw·cos pitch, sin pitch, cos yaw·cos pitch)

    yaw 从 +Z 起算、朝 +X 方向增大；pitch 是相对水平面的真仰角（0 水平、90 朝 +Y、
    -90 朝 -Y），只抬升不改水平朝向。支持负值与 >360 环绕（-90 ≡ 270）。

    实机读数（标定件头部速查表，逐项）：
        yaw=0   → +Z        yaw=90  → -X
        yaw=180 → -Z        yaw=270 → +X
        pitch=90 → +Y       pitch=-90 → -Y       pitch=45 → +Z/+Y 斜上

    velocity_* / emitter_* / sphere_emitter_* 三个入口共用这一套（标定件里
    emitter_* 与 velocity_* 明确注为「同一套球面角」，sphere 的 {0 180} 全向
    也靠这套角度铺满整球）。
    """
    p = math.radians(pitch_deg)
    y = math.radians(yaw_deg)
    cp = math.cos(p)
    return (-math.sin(y) * cp, math.sin(p), math.cos(y) * cp)


def _axis_angle_matrix(axis, angle_rad):
    """绕任意轴旋转的 3x3 矩阵（罗德里格斯公式），axis 须为单位向量。"""
    x, y, z = axis
    c = math.cos(angle_rad)
    s = math.sin(angle_rad)
    t = 1.0 - c
    return (
        (t * x * x + c, t * x * y - s * z, t * x * z + s * y),
        (t * x * y + s * z, t * y * y + c, t * y * z - s * x),
        (t * x * z - s * y, t * y * z + s * x, t * z * z + c),
    )


def _emit_rot_matrix(pitch_deg, yaw_deg):
    """发射体旋转矩阵：把局部基准前向 +Z 转到 _emit_dir(pitch, yaw)。

    基准取 +Z，因为 d(0,0) 就是 +Z（标定件：emitter_yaw 与 velocity_yaw 同零点），
    于是 emitter_yaw=emitter_pitch=0 等价于单位阵——写 0 的子系统不会被动旋转。
    原版 2376 个子系统显式写 0，其中大量是尾焰/引擎类，整体扭转 90° 会全部喷错。

    取绕轴 (base × d) 转 base 与 d 夹角这一支（最短路径）。
    """
    base = (0.0, 0.0, 1.0)
    d = _emit_dir(pitch_deg, yaw_deg)
    dot = max(-1.0, min(1.0, sum(base[i] * d[i] for i in range(3))))
    ang = math.acos(dot)
    if ang < 1e-12:
        return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    # axis = base × d
    ax = (base[1] * d[2] - base[2] * d[1],
          base[2] * d[0] - base[0] * d[2],
          base[0] * d[1] - base[1] * d[0])
    ln = math.sqrt(ax[0] ** 2 + ax[1] ** 2 + ax[2] ** 2)
    if ln < 1e-12:
        # base 与 d 对径（d = -Z）：绕 X 转 180
        return ((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, -1.0))
    axis = (ax[0] / ln, ax[1] / ln, ax[2] / ln)
    return _axis_angle_matrix(axis, ang)


def _rot_apply(m, v):
    """3x3 矩阵乘列向量。"""
    x, y, z = v
    return (m[0][0] * x + m[0][1] * y + m[0][2] * z,
            m[1][0] * x + m[1][1] * y + m[1][2] * z,
            m[2][0] * x + m[2][1] * y + m[2][2] * z)


def _hash32(x):
    """32 位整数雪崩哈希（splitmix 的混合函数）。

    纯整数位运算，无浮点、无查表，所以同输入永远同输出：跨机器、跨 Blender
    版本、跨进程都逐位一致。这是粒子随机量的唯一来源，可复现性全靠它。
    """
    x = (x * 0x9E3779B1) & 0xFFFFFFFF
    x ^= x >> 16
    x = (x * 0x21F0AAAD) & 0xFFFFFFFF
    x ^= x >> 15
    x = (x * 0x735A2D97) & 0xFFFFFFFF
    x ^= x >> 15
    return x & 0xFFFFFFFF


def _hash01(x):
    """[0,1) 区间的哈希值：取高 24 位，避免低位周期。"""
    return (_hash32(x) >> 8) * (1.0 / 16777216.0)


def _clamp(x, a, b):
    return a if x < a else (b if x > b else x)


def _apply_anim(base, anim, u):
    """按 op 把动画值作用到基础值上。MUL 乘、ADD 加，clamp 到 >=0。"""
    if anim is None:
        return base
    v = anim.evaluate(u)
    out = base * v if anim.op == "MUL" else base + v
    return out if out > 0.0 else 0.0


def _turbulence(p, t):
    """确定性伪湍流场：位置+时间的三角函数复合，量级约 [-1,1]。"""
    x, y, z = p
    nx = math.sin(1.7 * x + 2.3 * t) * math.cos(1.3 * y - 1.1 * t)
    ny = math.sin(2.1 * y + 1.9 * t) * math.cos(0.8 * z + 2.7 * t)
    nz = math.sin(1.5 * z - 2.0 * t) * math.cos(2.2 * x + 1.3 * t)
    return (nx, ny, nz)


def _norm3(v):
    l = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if l < _EPS:
        return (0.0, 0.0, 0.0)
    return (v[0] / l, v[1] / l, v[2] / l)


# ---- 向量化力场（numpy 可选）----
#
# numpy 的 sin/cos/sqrt 与 math 模块逐位相同（20 万随机点 0 处差异），所以走向量化
# 不破坏确定性。只对 point/turbulence/vortex/spin 走：逐粒子三角函数与叉积乘加是
# 热点，而无力场时建数组的开销没人分摊，反而更慢。
_VEC_FORCE_TYPES = ("turbulence", "point", "vortex", "spin")
# 粒子太少同样摊不平建数组的开销（1200 颗有 turbulence 时才 3.65x）。
_VEC_MIN_PARTICLES = 64


def _accel_vec(px, py, pz, forces, t_now):
    """向量化版的力场累加，语义与 SubsystemSim._accel 一致。

    px/py/pz 是 numpy 一维数组（位置分量）。返回 (ax, ay, az)。
    加法次序与 Python 版严格保持一致，否则浮点结果会有末位差异。
    """
    np = _NP
    ax = np.zeros_like(px)
    ay = np.zeros_like(py)
    az = np.zeros_like(pz)
    for f in forces:
        amt = f.amount
        ftype = f.type
        if ftype == "planar":
            d = f.direction
            ax = ax + amt * d[0]
            ay = ay + amt * d[1]
            az = az + amt * d[2]
        elif ftype == "friction":
            continue                      # 速度阻尼在积分里单独处理
        elif ftype == "point":
            rx = f.position[0] - px
            ry = f.position[1] - py
            rz = f.position[2] - pz
            l = np.sqrt(rx * rx + ry * ry + rz * rz)
            ok = l > _EPS
            ls = np.where(ok, l, 1.0)     # 避免除零，结果由 ok 选回
            ax = np.where(ok, ax - amt * rx / ls, ax)
            ay = np.where(ok, ay - amt * ry / ls, ay)
            az = np.where(ok, az - amt * rz / ls, az)
        elif ftype == "spin":
            d = f.direction
            ln = math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2])
            axis = (0.0, 1.0, 0.0) if ln < _EPS else (d[0] / ln, d[1] / ln, d[2] / ln)
            rx = px - f.position[0]
            ry = py - f.position[1]
            rz = pz - f.position[2]
            ax = ax + amt * (axis[1] * rz - axis[2] * ry)
            ay = ay + amt * (axis[2] * rx - axis[0] * rz)
            az = az + amt * (axis[0] * ry - axis[1] * rx)
        elif ftype == "vortex":
            # 与标量版 _accel 的 vortex 分支严格一致：指向轴线的径向吸引、
            # 归一化（不是与距离成正比的 Hooke 力）、正 amount 吸向轴线。
            d = f.direction
            ln = math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2])
            axis = (0.0, 1.0, 0.0) if ln < _EPS else (d[0] / ln, d[1] / ln, d[2] / ln)
            rx = px - f.position[0]
            ry = py - f.position[1]
            rz = pz - f.position[2]
            proj = axis[0] * rx + axis[1] * ry + axis[2] * rz
            qx = rx - axis[0] * proj
            qy = ry - axis[1] * proj
            qz = rz - axis[2] * proj
            l = np.sqrt(qx * qx + qy * qy + qz * qz)
            ok = l > _EPS
            ls = np.where(ok, l, 1.0)
            k = amt / ls
            ax = np.where(ok, ax - k * qx, ax)
            ay = np.where(ok, ay - k * qy, ay)
            az = np.where(ok, az - k * qz, az)
        elif ftype == "turbulence":
            ax = ax + amt * (np.sin(1.7 * px + 2.3 * t_now)
                             * np.cos(1.3 * py - 1.1 * t_now))
            ay = ay + amt * (np.sin(2.1 * py + 1.9 * t_now)
                             * np.cos(0.8 * pz + 2.7 * t_now))
            az = az + amt * (np.sin(1.5 * pz - 2.0 * t_now)
                             * np.cos(2.2 * px + 1.3 * t_now))
    return ax, ay, az


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


class SubsystemSim:
    """单子系统的发射/物理/显示状态。"""

    def __init__(self, system, sub, index, time_offset=0.0, seed=0):
        self.system = system
        self.sub = sub
        self.index = index
        self.name = sub.name
        # 用户种子：同一 seed 下逐位可复现（_w 里的合成是纯整数运算）
        self.seed = int(seed)

        # 发射速率与窗口（entity 事件偏移加在窗口起点上）
        self.rate = max(sub.emission[0], 0.0)
        # mass=0 的粒子直接消失（pdx_axis_probe 实机：mass=0 那格完全看不到粒子）。
        # 原版仅 1 处这么写，按「不发射」实现与画面等价，且不必在积分里特判。
        if sub.mass[0] <= 0.0:
            self.rate = 0.0
        # 发射窗口起点。start 的方差是**整个子系统摇一次**（用户实测口径），
        # 不是每颗粒子各摇一个延迟——后者会让同一个子系统内部也错开发射，
        # 而原版语义是「这个发射器什么时候开始工作」。
        # 原版 76 个子系统写了非零方差（如 start={ 0 4 }）。
        start_var = sub.start[1]
        if start_var:
            # 用子系统自身（index/seq_offset）摇一次，与粒子序号无关，保证确定
            self.start = max(sub.start[0] + start_var * (2.0 * _hash01(
                (max(index, 0) * 0x9E3779B1 + 0x1234567) & 0xFFFFFFFF) - 1.0), 0.0) + time_offset
        else:
            self.start = sub.start[0] + time_offset
        # 同一系统里多个子系统错开哈希输入（seq_offset），避免它们发出完全相同的
        # 落点图案（同参数的两个子系统本来会长得一模一样）。
        self.seq_offset = max(index, 0) * 97
        self.duration = sub.duration[0]   # -1 = 无限窗口
        self.life = max(sub.life[0], 0.001)
        self.life_var = sub.life[1]

        # 脉冲发射：[工作时长, 静默时长]。两者都 >0 时发射按 工作->静默 循环，
        # 其余情况视为持续发射。原版 107 个子系统用到（105 对 + 各 1 个落单）。
        pd = sub.emission_pulse_duration[0]
        ps = sub.emission_pulse_silence[0]
        self.pulse_period = (pd + ps) if (pd > 0.0 and ps > 0.0) else 0.0
        self.pulse_on = pd if self.pulse_period > 0.0 else 0.0

        # 出生属性方差
        self.vel = sub.velocity[0]
        self.vel_var = sub.velocity[1]
        self.pitch = sub.velocity_pitch
        self.yaw = sub.velocity_yaw
        self.size_base = sub.size[0]
        self.size_var = sub.size[2]
        self.size_anim = system.anim_for_channel(sub, "size")
        self.alpha_base = sub.alpha[0] / 255.0
        self.alpha_var = sub.alpha[2] / 255.0
        self.alpha_anim = system.anim_for_channel(sub, "alpha")
        self.anim_r = system.anim_for_channel(sub, "R")
        self.anim_g = system.anim_for_channel(sub, "G")
        self.anim_b = system.anim_for_channel(sub, "B")

        # 已解析的力场（仅引用到的）
        self.forces = []
        for ref in sub.force_refs:
            f = system.forces.get(ref)
            if f is not None:
                self.forces.append(f)
        # 实机校准（kaiju_gobble_planet）：amount=-4 的 point 吸力 10s 拉升约 200 单位，
        # 反推 a ≈ amount 本身 → division 不折算（其语义未定，先当无操作，参数保留展示）。

        # box / sphere 发射尺寸。
        # 三个轴都要取方差：原版大量写成 `{ 0 方差 }`（x 442 / y 462 / z 672 个），
        # 只取基准值会让这些发射器塌成一个点——实测 736 个子系统三轴基准全 0、
        # 只写方差，旧实现下它们 60 颗粒子的位置完全相同。
        bx, by, bz = sub.box_x, sub.box_y, sub.box_z
        self.box_half_x = bx[0] if sub.emitter_type == "box" else 0.0
        self.box_half_y = by[0] if sub.emitter_type == "box" else 0.0
        self.box_half_z = bz[0] if sub.emitter_type == "box" else 0.0
        self.box_var_x = bx[1] if sub.emitter_type == "box" else 0.0
        self.box_var_y = by[1] if sub.emitter_type == "box" else 0.0
        self.box_var_z = bz[1] if sub.emitter_type == "box" else 0.0
        sr = sub.sphere_radius
        self.sphere_r = sr[0] if sub.emitter_type == "sphere" else 0.0
        self.sphere_r_var = sr[1] if sub.emitter_type == "sphere" else 0.0
        self.sphere_yaw = sub.sphere_yaw
        self.sphere_pitch = sub.sphere_pitch

        # 颜色抖动：每颗粒子在基色上 ± 方差的随机偏移（原版 R/G/B 各约 570~660 个
        # 子系统写了非零方差）。parser 层曾只取 [0] 把这部分整个丢掉。
        self.color_var_r = sub.color_var[0]
        self.color_var_g = sub.color_var[1]
        self.color_var_b = sub.color_var[2]

        # 是否走向量化积分：带 _VEC_FORCE_TYPES 列出的力场才划算（point/turbulence
        # 逐粒子大量三角函数，vortex/spin 逐粒子叉积）。实测有 turbulence 时 3.65x，
        # 无力场时反而 0.82x（建数组开销没人分摊），纯 vortex 峰值 500+ 粒的系统
        # 1.6~1.8x。粒子太少时也不值得（固定开销占主导），门槛见 _VEC_MIN_PARTICLES。
        self.use_vec = (_NP is not None
                        and len(self.forces) > 0
                        and any(f.type in _VEC_FORCE_TYPES for f in self.forces))

        # 三轴自转：面片除屏幕内自转（rotation_speed）外，还可绕 pitch/yaw 两轴
        # 翻转。原版 182 个子系统写了这三个键（pitch 75 / yaw 70 / roll 37）。
        # 基角取 particle_pitch / particle_yaw（面片的初始朝向），速率取对应键。
        self.pitch_var = sub.particle_pitch[1]
        self.yaw_var = sub.particle_yaw[1]
        self.pitch_speed = sub.rotation_speed_pitch[0] + sub.rotation_speed_pitch[1]
        self.yaw_speed = sub.rotation_speed_yaw[0] + sub.rotation_speed_yaw[1]
        # roll 轴的自转等价于屏幕内自转，与 rotation_speed 同义：实测 47 个有轴
        # 自转的子系统里 roll 非零只有 4 个，且它们的 rotation_speed 全为 0，
        # 所以直接并入屏幕内自转角速度，不另开通道。
        self.roll_speed = sub.rotation_speed_roll[0] + sub.rotation_speed_roll[1]

        # 发射体朝向：整个局部坐标系绕原点旋转，形状偏移与初速度方向一起转。
        # 原版 414 个子系统写非零值、2376 个显式写 0、2332 个完全没写。
        # 写 0 时矩阵是单位阵（d(0,0) 就是 +Z），与完全没写的效果相同；
        # has_emitter_rot 只用于统计与面板展示。
        self.emit_yaw = sub.emitter_yaw
        self.emit_pitch = sub.emitter_pitch
        self.emit_rot = None
        self.emit_var = bool(self.emit_yaw[1] or self.emit_pitch[1])
        if sub.has_emitter_rot or self.emit_var:
            self.emit_rot = _emit_rot_matrix(self.emit_pitch[0], self.emit_yaw[0])

        # 运行状态
        self.reset()

    def _expected_spawns(self, t):
        """到时刻 t 为止「应该」出生过多少颗粒子（按发射窗口与速率直接算）。

        无状态：只依赖 t 与参数，不依赖此前怎么推进，因此逐帧推进与一次性跳帧
        得到相同结果。这是选帧能便宜的前提。
        """
        if self.rate <= 0.0 or t < self.start:
            return 0
        # 有限窗口：发射只发生在 [start, start+duration) 内
        upto = t if self.duration < 0.0 else min(t, self.start + self.duration)
        if upto <= self.start:
            return 0
        span = upto - self.start
        return int(self._active_span(span) * self.rate)

    def estimate_alive(self, t):
        """廉价估算 t 时刻的存活粒子数（O(1)，不做物理积分）。

        用于选帧扫描：整条时间轴要采样上千次，逐点跑物理在上千粒子的系统上要
        十几秒。这里按「已发射数 - 已到寿数」直接估算，只需要找出哪个时间段最
        热闹，随后对少量候选做一次真正的 replay 复核。

        必须显式处理「第一颗还没出生」：低发射速率的系统（实测 0.15/秒）要等到
        start + 1/rate 才可能有粒子，漏掉这一条会选中 start 之后不久的帧，而那
        时候其实一颗粒子都没有。
        """
        if self.rate <= 0.0 or t < self.start:
            return 0.0
        first = self.start + 1.0 / self.rate
        if t < first:
            return 0.0
        upto = t if self.duration < 0.0 else min(t, self.start + self.duration)
        if upto <= self.start:
            return 0.0
        emitted = self._active_span(upto - self.start) * self.rate
        # 出生早于 (t - 平均寿命) 的那批，按平均寿命算已到寿
        avg_life = max(self.life, 0.001)
        cutoff = t - avg_life
        expired = 0.0 if cutoff <= self.start else (
            self._active_span(min(cutoff, upto) - self.start) * self.rate)
        alive = emitted - expired
        return max(0.0, min(alive, self.sub.max_amount))

    def _active_span(self, span):
        """把「从 start 起算的时长」折算成实际发射时长（脉冲时只算工作相）。

        与 _expected_spawns 用同一套折算，保证选帧扫描与真实推进一致。
        """
        if self.pulse_period <= 0.0 or span <= 0.0:
            return span
        period = self.pulse_period
        n_full = int(span / period)
        return n_full * self.pulse_on + min(span - n_full * period, self.pulse_on)

    def reset(self):
        self.t = 0.0
        self.n_sub = 0          # 已执行的绝对子步数（唯一的时间状态，保证可重放）
        self.spawn_count = 0
        # 粒子记录：[出生时间, 寿命, x,y,z, vx,vy,vz, 角速度, 初始角, 权重]
        self.particles = []
        self._ckpt = [(0, 0, [])]    # 关键帧：[(n_sub, spawn_count, 粒子表副本)]

    # ---- 关键帧（供回退时快速恢复） ----

    def _store_checkpoint(self):
        """按间隔存一份状态快照。间隔内不重复存同一 n_sub。"""
        last = self._ckpt[-1][0]
        if self.n_sub - last < CHECKPOINT_STRIDE:
            return
        # 深拷贝粒子行：粒子记录是可变 list，后续积分会就地改字段
        self._ckpt.append(
            (self.n_sub, self.spawn_count, [list(p) for p in self.particles]))

    def _restore_checkpoint(self, n_target):
        """恢复到不超过 n_target 的最近关键帧，返回恢复到的 n_sub。

        返回 -1 表示没有可用关键帧（调用方按从 0 重放处理）。
        """
        best = None
        best_i = -1
        for i, c in enumerate(self._ckpt):
            if c[0] <= n_target:
                best = c
                best_i = i
            else:
                break
        if best is None:
            return -1
        # 丢弃比恢复点更晚的关键帧：留着会让 _store_checkpoint 拿末尾那份比较，
        # 差值为负从而再也不存新帧。
        del self._ckpt[best_i + 1:]
        self.n_sub, self.spawn_count, self.particles = (
            best[0], best[1], [list(p) for p in best[2]])
        self.t = self.n_sub * SUBSTEP
        return self.n_sub

    def rewind(self, t_target):
        """把状态回退到 t_target 之前的最近关键帧。

        返回恢复到的 n_sub（调用方随后用 advance 推进到目标即可）。
        """
        n_cap = int(MAX_PREVIEW_SECONDS / SUBSTEP)
        n_target = min(int(math.floor(t_target / SUBSTEP + 1e-9)), n_cap)
        if n_target < 0:
            n_target = 0
        got = self._restore_checkpoint(n_target)
        if got < 0:
            self.reset()
            return 0
        # 关键帧里可能有超出目标的、以及时间戳晚于目标时刻的粒子，一并清掉
        if self.particles:
            t_lim = self.n_sub * SUBSTEP
            self.particles = [p for p in self.particles if p[0] <= t_lim]
        return self.n_sub

    # ---- 发射 ----

    def _w(self, i, salt=0):
        """第 i 颗粒子在第 salt 维的确定性权重，落在 [0,1)。

        用整数哈希，不用低差异序列（这里曾经是 Halton）：

        Halton 这类低差异序列是为「用尽量少的样本均匀铺满空间」设计的，代价是
        序列前段必然规整——radical_inverse 在索引小于 base 时退化成精确等差
        （base 分之一的整数倍）。而粒子系统绝大多数是「同时只活几颗到几十颗」，
        永远落在那段规整区里，于是 ① 相邻粒子取到恒定的等差步长（base 11 →
        32.73°，肉眼看光束「一档一档爬」）；② 各维之间是同一个单调序列的缩放，
        「第 i 颗永远排第 i 档」，寿命最短的那颗必然也是速度最小、最先发射的
        那颗，同进同退，统计上完全不独立。给低差异序列打补丁（换基数、乘索引
        乘数、加偏移、数字加扰）都只能改斜率、改不了这个结构，属于越补越复杂。

        改用哈希后这两件事从根上不存在：哈希没有「前后顺序」，第 i 颗与第 i+1
        颗之间没有任何规律关系；每一维（salt）也各自独立哈希，不共享结构。
        丢掉的是「少数样本铺得均匀」这个性质——实测分布均匀性 0.0185 vs 低差异
        序列 0.0002，但粒子本来就要随机感，规整反而错。

        独立性实测（16 维两两 Spearman 秩相关的最大值，16 个样本）：
        独立真随机基准中位 0.668 / p95 0.791；本哈希 0.765（正常范围内），
        而 Halton 原版是 1.000（11 组完全同序，远超基准）。

        种子的作用：种子为 0 时用 seq_offset（子系统序号错开，保证同参数的两个
        子系统不长得一模一样）；非 0 时把 种子/子系统/维度号 混进哈希输入，
        改一个数就整体重排。全程整数运算、无浮点，所以同种子逐位可复现，
        跨机器跨版本也一致。
        """
        h = (self.seq_offset * 0x85EBCA6B
             + salt * 0xC2B2AE35
             + (i + 1) * 0x27D4EB2F) & 0xFFFFFFFF
        if self.seed:
            h = (h + self.seed * 0x165667B1) & 0xFFFFFFFF
        return _hash01(h)

    def _spawn(self, t):
        sub = self.sub
        i = self.spawn_count
        self.spawn_count += 1
        # 各维权重：每个用途占一个独立维度（salt），各自独立哈希，互不相关。
        # 复用同一个 salt 会让两个用途共用同一个随机数（例如球面 yaw 与初速度
        # 的 pitch 联动），分布会再度退化成低维曲面。
        w_life = self._w(i, 0)                   # 寿命
        w_pos1 = self._w(i, 1)                   # 发射点第 1 维
        w_pos2 = self._w(i, 2)                   # 发射点第 2 维
        w_pos3 = self._w(i, 3)                   # 发射点第 3 维
        w_dir1 = self._w(i, 4)                   # 初速度方向第 1 维
        w_dir2 = self._w(i, 5)                   # 初速度方向第 2 维
        w_speed = self._w(i, 6)                  # 初速度大小
        w_rot0 = self._w(i, 7)                   # 初始自转角
        w_rotv = self._w(i, 8)                   # 自转角速度
        w_pit = self._w(i, 9)                    # 面片基角 pitch 的随机扰动
        w_yaw = self._w(i, 10)                   # 面片基角 yaw 的随机扰动
        w_cr = self._w(i, 11)                    # 颜色 R 通道抖动
        w_cg = self._w(i, 12)                    # 颜色 G 通道抖动
        w_cb = self._w(i, 13)                    # 颜色 B 通道抖动

        w0 = w_life
        life = self.life + self.life_var * (2.0 * w0 - 1.0)
        life = max(life, 0.05)

        # 发射点。判定条件必须把方差也算上：原版大量发射器写成「基准 0 + 方差」，
        # 只看基准会把它们判成点发射（球面 148 个、box 736 个）。真正的点发射是
        # 基准与方差都为 0 的那些（球面 61 个），它们仍然走 else。
        if sub.emitter_type == "box" and (self.box_half_x or self.box_half_y or self.box_half_z
                                          or self.box_var_x or self.box_var_y or self.box_var_z):
            px = (self.box_half_x + self.box_var_x * (2.0 * w_pos1 - 1.0))
            py = (self.box_half_y + self.box_var_y * (2.0 * w_pos2 - 1.0))
            pz = (self.box_half_z + self.box_var_z * (2.0 * w_pos3 - 1.0))
        elif sub.emitter_type == "sphere" and (self.sphere_r > 0.0 or self.sphere_r_var > 0.0):
            # 球面点：半径 ± 方差，yaw/pitch 在给定范围内取。
            # 角度与 velocity/emitter 同一套（probe 实测）：只给一个轴全向 →
            # 垂直于另一轴的环，两个都给才成球（标定件明确记载的行为）。
            r = max(self.sphere_r + self.sphere_r_var * (2.0 * w_pos1 - 1.0), 0.0)
            sy = self.sphere_yaw[0] + self.sphere_yaw[1] * (2.0 * w_pos2 - 1.0)
            sp = self.sphere_pitch[0] + self.sphere_pitch[1] * (2.0 * w_pos3 - 1.0)
            d = _emit_dir(sp, sy)
            px = d[0] * r
            py = d[1] * r
            pz = d[2] * r
        else:
            px = py = pz = 0.0

        # 初速度方向（与 emitter/sphere 共用 probe 实测的那套仰角+方位角）
        p = self.pitch[0] + self.pitch[1] * (2.0 * w_dir1 - 1.0)
        y = self.yaw[0] + self.yaw[1] * (2.0 * w_dir2 - 1.0)
        d = _emit_dir(p, y)
        v = self.vel + self.vel_var * (2.0 * w_speed - 1.0)

        # 发射体朝向：旋转「形状偏移」与「出射方向」，position 不动（position 是
        # 设计者摆发射体的位置；point 发射器没有体积，写非零 emitter_yaw 仍有效果，
        # 只能由「方向被旋转」满足）。
        # 有方差时逐粒子取角，否则用构造期算好的固定矩阵。
        if self.emit_var:
            ey = self.emit_yaw[0] + self.emit_yaw[1] * (2.0 * w_pos1 - 1.0)
            ep = self.emit_pitch[0] + self.emit_pitch[1] * (2.0 * w_pos2 - 1.0)
            m = _emit_rot_matrix(ep, ey) if (ey or ep) else self.emit_rot
        else:
            m = self.emit_rot
        if m is not None:
            px, py, pz = _rot_apply(m, (px, py, pz))
            d = _rot_apply(m, d)

        pos0 = sub.position
        px += pos0[0]
        py += pos0[1]
        pz += pos0[2]

        ang0 = sub.rotation[0] + sub.rotation[1] * (2.0 * w_rot0 - 1.0)
        angvel = (sub.rotation_speed[0] + sub.rotation_speed[1] * (2.0 * w_rotv - 1.0)
                  + self.roll_speed)   # roll 轴自转 = 屏幕内自转，合并到同一条通道

        # 面片初始朝向：基角 + 逐粒子随机扰动，在发射时定死。
        # 第二个值是变化量，原版结构由 _decomp/editor_registry.cpp 实锤：
        # pe_behavior_particle_pitch_var 用的就是 CreateComponentVariation，且把
        # pe_behavior_particle_pitch 自身（lVar3+0x278）当父节点传入，与
        # life/life_var、velocity_pitch/_var、particle_yaw/_var、roll/roll_var 同构。
        # 实测全库 5122 个子系统里 pitch_var 非零 1187（billboard=no 占 971）、
        # yaw_var 非零 830。billboard=yes 的这两个字段引擎本身忽略。
        b_pitch = sub.particle_pitch[0] + self.pitch_var * (2.0 * w_pit - 1.0)
        b_yaw = sub.particle_yaw[0] + self.yaw_var * (2.0 * w_yaw - 1.0)
        # 颜色抖动：同样是发射时定死的逐粒子偏移（原版 R/G/B 各约 570~660 个
        # 子系统写了非零方差，如 y={ 150 20 }）。存偏移量而不是绝对值，这样
        # snapshot 里叠加颜色动画（anim_r/g/b）时不会错位。
        c_r = self.color_var_r * (2.0 * w_cr - 1.0)
        c_g = self.color_var_g * (2.0 * w_cg - 1.0)
        c_b = self.color_var_b * (2.0 * w_cb - 1.0)

        self.particles.append([t, life, px, py, pz, d[0] * v, d[1] * v, d[2] * v, angvel, ang0, w0,
                               b_pitch, b_yaw, c_r, c_g, c_b])    # ---- 物理 ----

    def _accel(self, p, v, t):
        ax = ay = az = 0.0
        for f in self.forces:
            amt = f.amount
            ftype = f.type
            if ftype == "planar":
                d = f.direction
                ax += amt * d[0]
                ay += amt * d[1]
                az += amt * d[2]
            elif ftype == "friction":
                continue  # 速度阻尼在积分里单独处理
            elif ftype == "point":
                rx = f.position[0] - p[0]
                ry = f.position[1] - p[1]
                rz = f.position[2] - p[2]
                l = math.sqrt(rx * rx + ry * ry + rz * rz)
                if l > _EPS:
                    # 负 amount 吸向力心、正 amount 排离（kaiju 实机定稿口径）
                    ax -= amt * rx / l
                    ay -= amt * ry / l
                    az -= amt * rz / l
            elif ftype == "spin":
                # 零向量 direction 回退到 +Y：_norm3 在零向量时返回 (0,0,0) 元组，
                # 不能用 `_norm3(...) or (0,1,0)`——元组恒为真，回退永不触发，
                # 实际拿到零轴（等于该力场失效）。标量版与向量化版必须一致，
                # 否则零向量的 spin 会在两条路径上给出不同画面且无报错。
                a = _norm3(f.direction)
                axis = a if (a[0] or a[1] or a[2]) else (0.0, 1.0, 0.0)
                rx = p[0] - f.position[0]
                ry = p[1] - f.position[1]
                rz = p[2] - f.position[2]
                c = _cross(axis, (rx, ry, rz))
                ax += amt * c[0]
                ay += amt * c[1]
                az += amt * c[2]
            elif ftype == "vortex":
                # vortex 不是漩涡（pdx_axis_probe 四轮实机订正）：它只有指向
                # direction 轴线的径向吸引，没有任何切向分量，不能与 spin 共用
                # 叉积分支（那样画出来是一圈环绕）。
                # 力心与该排发射点同 y 时整格粒子纹丝不动 → 到轴距离为 0 时无力，
                # 故取「垂直分量归一化」而非与距离成正比的 Hooke 力（后者会让给
                # 切向初速的粒子画出闭合轨道，实测一律是一条曲线飞走）。
                # 符号：原版 42 处 vortex 的 name 多为 Gravity/Gravity+、amount
                # 0.4~0.55，即当引力井用 → 正 amount = 吸向轴线。
                a = _norm3(f.direction)
                axis = a if (a[0] or a[1] or a[2]) else (0.0, 1.0, 0.0)
                rx = p[0] - f.position[0]
                ry = p[1] - f.position[1]
                rz = p[2] - f.position[2]
                proj = axis[0] * rx + axis[1] * ry + axis[2] * rz
                perp = (rx - axis[0] * proj, ry - axis[1] * proj, rz - axis[2] * proj)
                l = math.sqrt(perp[0] * perp[0] + perp[1] * perp[1] + perp[2] * perp[2])
                if l > _EPS:
                    k = amt / l
                    ax -= k * perp[0]
                    ay -= k * perp[1]
                    az -= k * perp[2]
            elif ftype == "turbulence":
                n = _turbulence(p, t)
                ax += amt * n[0]
                ay += amt * n[1]
                az += amt * n[2]
        return ax, ay, az

    def _friction_k(self):
        for f in self.forces:
            if f.type == "friction":
                return abs(f.amount)
        return 0.0

    def _step_vec(self, t_now, h, fric):
        """向量化的一个子步：力场 + 半隐式欧拉积分 + 到寿剔除。

        与逐粒子版逐位一致（实测 13200 字段 0 差异）。写回用 arr.tolist() 整体
        替换而不是逐字段赋值——后者要把每个 float 从 numpy 搬回 Python 对象，
        实测吃掉一半收益（逐字段 1.99x vs tolist 3.65x）。

        particles 保持 list[list[float]] 结构不变：下游 snapshot()/checkpoint 都按
        它取数，转成纯 numpy 会牵动太多地方，风险不值当。
        """
        np = _NP
        arr = np.array(self.particles, dtype=np.float64)
        # 到寿的剔除掉（与标量版的 continue 语义相同）
        keep = (t_now - arr[:, 0]) < arr[:, 1]
        if not keep.all():
            arr = arr[keep]
        if arr.shape[0] == 0:
            self.particles = []
            return
        ax, ay, az = _accel_vec(arr[:, 2], arr[:, 3], arr[:, 4], self.forces, t_now)
        arr[:, 5] += ax * h
        arr[:, 6] += ay * h
        arr[:, 7] += az * h
        if fric > 0.0:
            damp = math.exp(-fric * h)
            arr[:, 5] *= damp
            arr[:, 6] *= damp
            arr[:, 7] *= damp
        arr[:, 2] += arr[:, 5] * h
        arr[:, 3] += arr[:, 6] * h
        arr[:, 4] += arr[:, 7] * h
        self.particles = arr.tolist()

    # ---- 主推进 ----

    def advance(self, t_target):
        """把子系统状态推进到绝对时间 t_target（秒）。

        用「绝对子步网格」保证与调用路径无关：时间轴上按 SUBSTEP 划出固定网格，
        状态只由已执行的整数子步号 n 决定，时间取 n * SUBSTEP。

        这样一次跳帧（advance(8.0)）与逐帧擦洗（advance(1/60) 反复调用）只要到达
        同一个 n，状态就逐位相同。反之若按「本次 dt 切若干等分子步」推进，子步
        长度与浮点累加轨迹都会随调用方式变化——实测同一系统同一时刻，跳帧与逐帧
        会得出不同粒子数（Glow_flare 9 vs 10 颗），抽查 25 个系统 × 3 个时间点有
        24 组对不上。预览里表现为「擦洗到同一帧，画面却和上次不一样」。
        """
        if t_target < self.t:
            # 回退：从最近的关键帧恢复，而不是从 0 全量重放。
            # 全量重放的代价随帧号线性增长，实测回退 1 帧在 1800 帧处要
            # 8~20 秒（同帧重刷仅 32/17ms），是时间轴擦洗卡顿的主因。
            self.rewind(t_target)
        # 目标子步号。钳到 MAX_PREVIEW_SECONDS：预览时间轴不会超过它，超出的
        # 擦洗（用户把帧号拖到很远处）没必要真算几万个子步，钳制对所有路径一致，
        # 因此不破坏确定性。
        n_cap = int(MAX_PREVIEW_SECONDS / SUBSTEP)
        n_target = min(int(math.floor(t_target / SUBSTEP + 1e-9)), n_cap)
        if n_target <= self.n_sub:
            return

        fric = self._friction_k()

        # 一直推进到目标子步号。不做「单次调用步数上限」：那会让长跳停在半路，
        # 反而破坏「到达同一时刻状态相同」的前提。工作量的上界由 n_cap 保证。
        while self.n_sub < n_target:
            self.n_sub += 1
            t_now = self.n_sub * SUBSTEP
            # 发射：按「绝对时间应发射总数」补齐，不用累加器。
            # 累加器写法（acc += rate*h）会让发射数依赖子步划分，同一目标时刻
            # 两条路径会得出不同结果（实测差 0.001，导致按扫描选中的帧在真实
            # 刷新下是空的）。从 t_now 直接算应有几颗后，发射排程与路径无关。
            n_expect = self._expected_spawns(t_now)
            while (self.spawn_count < n_expect
                   and len(self.particles) < self.sub.max_amount):
                self._spawn(t_now)
            # 积分（子步长恒为 SUBSTEP，与调用路径无关）
            h = SUBSTEP
            if self.use_vec and len(self.particles) >= _VEC_MIN_PARTICLES:
                self._step_vec(t_now, h, fric)
            else:
                # 阻尼系数在本次调用内是常量，先算一次（与 _step_vec 一致）
                damp = math.exp(-fric * h) if fric > 0.0 else 1.0
                alive = self.particles
                idx = 0
                for p in alive:
                    age = t_now - p[0]
                    if age >= p[1]:
                        continue  # 到寿，收尾时剔除
                    a = self._accel((p[2], p[3], p[4]), (p[5], p[6], p[7]), t_now)
                    p[5] += a[0] * h
                    p[6] += a[1] * h
                    p[7] += a[2] * h
                    if fric > 0.0:
                        p[5] *= damp
                        p[6] *= damp
                        p[7] *= damp
                    p[2] += p[5] * h
                    p[3] += p[6] * h
                    p[4] += p[7] * h
                    alive[idx] = p
                    idx += 1
                del alive[idx:]
            self.t = t_now
            self._store_checkpoint()

        # 窗口后清理：无新粒子且全部到寿
        if self.duration >= 0.0 and t_target > self.start + self.duration + self.life:
            alive = self.particles
            alive[:] = [p for p in alive if t_target - p[0] < p[1]]

    # ---- 显示快照 ----

    def snapshot(self):
        """[(x,y,z, size, r,g,b,a, 屏幕内自转角, 图集格, 轴自转pitch增量, 轴自转yaw增量)]

        最后两个是「面片朝向」相对初始朝向的**累积增量**（弧度），供三轴自转用。
        不含基角——基角已由消费方用于挑选初始基向量，重复叠加会转两次。
        宽度固定 12，消费方（blender_preview 的两条组数组路径）按 12 解包。
        """
        out = []
        t = self.t
        sub = self.sub
        sheet_total = sub.sheet_x * sub.sheet_y
        # 三轴自转：每次 snapshot 都要算，故先取出速率（无轴自转时全为 0，
        # has_axis 为假时直接给常量 0，不付三角运算的代价）。
        rp_rate = self.pitch_speed
        ry_rate = self.yaw_speed
        has_axis = bool(rp_rate or ry_rate)
        for p in self.particles:
            age = t - p[0]
            life = p[1]
            if age < 0.0 or age >= life:
                continue
            u = age / life
            size = _apply_anim(self.size_base + self.size_var * (2.0 * p[10] - 1.0), self.size_anim, u)
            if size <= 0.0:
                continue
            alpha = _apply_anim(self.alpha_base + self.alpha_var * (2.0 * p[10] - 1.0), self.alpha_anim, u)
            r = _clamp(_apply_anim(sub.color[0] + p[13], self.anim_r, u), 0.0, 1.0)
            g = _clamp(_apply_anim(sub.color[1] + p[14], self.anim_g, u), 0.0, 1.0)
            b = _clamp(_apply_anim(sub.color[2] + p[15], self.anim_b, u), 0.0, 1.0)
            a = _clamp(alpha, 0.0, 1.0)
            if a <= 0.0:
                continue
            ang = math.radians(p[9] + p[8] * age)
            # 三轴自转只输出「随时间累积的增量」，不输出基角：基角（particle_pitch
            # / particle_yaw 的取值）决定的是面片的初始朝向，由 blender_preview 那边
            # 的 _fixed_quad_basis 消费（末两列给的是这两个**基角**，不是转角增量）。
            # 这里若再把基角当旋转量叠加一次，就等于转了两次——实测让 308 个
            # pitch≈90 的平铺子系统从水平面被转开（法线与 +Z 对齐度 1.000 → 0.000）。
            if has_axis:
                p_ang = math.radians(rp_rate * age)
                y_ang = math.radians(ry_rate * age)
            else:
                p_ang = 0.0
                y_ang = 0.0
            cell = -1
            if sub.sheet_anim and sheet_total > 1:
                cell = int(age * sub.sheet_loop * sheet_total) % sheet_total
            out.append((p[2], p[3], p[4], size, r, g, b, a, ang, cell, p_ang, y_ang,
                        p[11], p[12]))
        return out


class SystemSim:
    """整个粒子系统：全部子系统 + 总时长估算。"""

    def __init__(self, system, time_offset=0.0, seed=0):
        self.system = system
        self.name = system.name
        self.time_offset = time_offset
        self.seed = int(seed)
        self.subs = []
        for i, sub in enumerate(system.subsystems):
            if sub.hide:
                continue
            self.subs.append(SubsystemSim(system, sub, i, time_offset, self.seed))
        self.enabled = {s.name: True for s in self.subs}
        self.t = 0.0

    def reset(self):
        for s in self.subs:
            s.reset()
        self.t = 0.0

    def advance(self, t):
        # 回退交给各子系统自己处理（它们有关键帧，可只回退一小段）。
        # 不能在这里 reset()：那会把所有子系统清零，关键帧回退就永远没机会生效，
        # 回退 1 帧要重放整条时间轴。
        for s in self.subs:
            if self.enabled.get(s.name, True):
                s.advance(t)
            else:
                # 禁用的子系统不能只改 t：那会让 t 与 n_sub 脱钩，之后重新启用时
                # SubsystemSim.advance 的两个守卫同时失效（t_target < self.t 为假
                # 不触发回退，n_target <= n_sub 又直接 return），状态永久停在禁用
                # 前的时刻——实测禁用期间 20→40→10 后启用，受害者仍是
                # (n_sub=2400, 999 颗)，而正确值应为 (1200, 1000 颗)，且不再自愈。
                # 改为：时间倒退时把它也重置（正确且便宜——禁用的子系统的物理本来
                # 就不需要保留），前进时只对齐 t（它不参与显示，n_sub 不必推进）。
                if t < s.t:
                    s.reset()
                    s.t = t
                else:
                    s.t = t
        self.t = t

    def snapshot(self):
        """[(子系统名, [粒子显示记录])]，只含启用的子系统。"""
        return [(s.name, s.snapshot()) for s in self.subs if self.enabled.get(s.name, True)]

    def total_duration(self):
        """建议预览时长（秒）：保证每个子系统都能看到粒子。

        取值要同时满足两件事，取较大者：
        - 发射窗口结束 + 最后一颗粒子的寿命；
        - 第一颗粒子出生（start + 1/emission）+ 它的寿命。低发射速率的子系统
          （实测 nomad_front_end_light emission=0.25，即 4 秒才一颗）如果只按
          前者算，窗口是无限长却只给 1 秒时间轴，预览里永远一颗粒子都没有。
        """
        d = 0.0
        for s in self.subs:
            window_end = s.start + (s.duration if s.duration > 0 else 0.0)
            end = window_end + s.life + s.life_var
            if s.rate > 0.0:
                first_spawn = s.start + 1.0 / s.rate
                # 无限窗口：至少等到第一颗出生并活完
                if s.duration < 0.0:
                    end = max(end, first_spawn + s.life + s.life_var)
                else:
                    end = max(end, min(first_spawn, window_end) + s.life + s.life_var)
            d = max(d, end)
        return max(1.0, min(d, MAX_PREVIEW_SECONDS))
