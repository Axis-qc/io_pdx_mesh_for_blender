"""
PDX particle simulator: deterministic, frame-scrub able integration.

 设计要点（与预览目标对应）：
 - 确定性：不用随机数。每颗粒子按『出生序号 × 黄金比例分数』取权重槽 W，
   同一时刻擦洗到同一帧永远得到同一画面。
 - 逐帧确定性驱动：simulate(t) 把状态推进到绝对秒 t；时间轴倒退时从头重算，
   前进小步长则增量推进，两条路径结果一致。
 - 力场语义按实机校准记录实现：负 amount 吸引 / 正排斥，division=16，
   force 必须被子系统 force=<名> 引用才生效。
 - 不复现父级/entity 缩放（挂载缩放），只保证粒子系统内部尺寸比例。

 author : Axis-qc (fork of ross-g/io_pdx_mesh 0.91.2)
"""

import math

from .parser import sph_to_dir

GOLDEN = 0.6180339887498949
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


def _frac(x):
    return x - math.floor(x)


def _emit_dir(pitch_deg, yaw_deg):
    """发射体朝向对应的单位方向（emitter 系）。

    有两套互不相同的球坐标约定，不能混用：
    - velocity_* / sphere_emitter_*：pitch 自 **+Y 轴**量（sph_to_dir），pitch=0
      落在极点、此时 yaw 完全退化；
    - emitter_*：pitch 是水平面仰角、yaw 是 XZ 平面方位角，基准方向为 **+X**。

    依据是 fallen_empire_01_colossus_energy_effect：三个球按等边三角形摆在 XZ
    平面，`emitter_yaw` 恰好等于「位置角 - 180」（ball_1 位置角 59.04→yaw -120；
    ball_2 位置角 180→yaw 0；ball_3 位置角 -59.04→yaw 120），而 velocity 恒为
    -100。按下式取 d 再乘 -100，三球速度全部沿径向朝外（夹角 0.96°/0°/0.96°，
    3/3 通过）。若改用 sph_to_dir，ball_2 会得到竖直向下的 (0,-100,0)，与另两球
    的径向不一致——差异恰好只出现在 yaw=0 那个球上，正是基准方向不同所致。

        d = (cos p·cos y, sin p, cos p·sin y)

    p=0 水平、p=90 朝 +Y、p=270 与 -90 都朝 -Y：原版取值里确实同时出现 270 与
    -90，且存在负值（-20），说明它是可绕圈的仰角而非 0..180 的极角。
    """
    p = math.radians(pitch_deg)
    y = math.radians(yaw_deg)
    cp = math.cos(p)
    return (cp * math.cos(y), math.sin(p), cp * math.sin(y))


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
    """发射体旋转矩阵：把代码里的基准方向 **+Y** 转到 _emit_dir(pitch, yaw)。

    基准必须是 +Y 而不是 +X：被旋转的对象是初速度方向，而本文件算初速度方向用的
    是 sph_to_dir(velocity_pitch, velocity_yaw)，velocity 角为 0 时它给出 +Y。
    若把矩阵写成「+X → d」，作用到 +Y 上会得到第三个方向，三个球全错（实测
    0/3，速度全塌成 (0,-100,0)）；写成「+Y → d」时两个显式写了 yaw 的球精确吻合
    （径向夹角 0.96°/0.96°）。

    取绕轴 (Y × d) 转 Y 与 d 夹角这一支（最短路径）。
    注意 R(pitch=0, yaw=0) **不是**单位阵：_emit_dir(0,0)=+X，而基准是 +Y，所以它把
    +Y 转到 +X。单位阵出现在 pitch=90 那类「d 与基准 +Y 同向」的角度。
    （这条曾被写成「yaw=pitch=0 时退化为单位阵」，与实现不符，已更正。）
    """
    d = _emit_dir(pitch_deg, yaw_deg)
    dot = max(-1.0, min(1.0, d[1]))          # d · Y
    ang = math.acos(dot)
    if ang < 1e-12:
        return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    # axis = Y × d
    ax = (1.0 * d[2] - 0.0 * d[1], 0.0 * d[0] - 0.0 * d[2], 0.0 * d[1] - 1.0 * d[0])
    ln = math.sqrt(ax[0] ** 2 + ax[1] ** 2 + ax[2] ** 2)
    if ln < 1e-12:
        # d 与 Y 对径（pitch=±90 且 cos p=0 之外的退化），绕 X 转 180
        return ((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, -1.0))
    axis = (ax[0] / ln, ax[1] / ln, ax[2] / ln)
    return _axis_angle_matrix(axis, ang)


def _rot_apply(m, v):
    """3x3 矩阵乘列向量。"""
    x, y, z = v
    return (m[0][0] * x + m[0][1] * y + m[0][2] * z,
            m[1][0] * x + m[1][1] * y + m[1][2] * z,
            m[2][0] * x + m[2][1] * y + m[2][2] * z)


# 各维用的互质基数（Halton 序列）。维度顺序固定，改动会改变既有回放结果。
# 至少要覆盖 _spawn 用到的全部维度（0..15）且互不相同：一旦两维取到同一个
# 基数，它们就变成同一个数，分布会重新退化。
_HALTON_BASES = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53)


def _radical_inverse(i, base):
    """i 在 base 进制下的小数反转，值落在 [0,1)。"""
    inv = 0.0
    f = 1.0 / base
    while i > 0:
        inv += f * (i % base)
        i //= base
        f /= base
    return inv


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


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


class SubsystemSim:
    """单子系统的发射/物理/显示状态。"""

    def __init__(self, system, sub, index, time_offset=0.0):
        self.system = system
        self.sub = sub
        self.index = index
        self.name = sub.name

        # 发射速率与窗口（entity 事件偏移加在窗口起点上）
        self.rate = max(sub.emission[0], 0.0)
        self.start = sub.start[0] + time_offset
        # 同一系统里多个子系统错开 Halton 序列起点，避免它们发出完全相同的
        # 落点图案（同参数的两个子系统本来会长得一模一样）。
        self.seq_offset = max(index, 0) * 97
        self.duration = sub.duration[0]   # -1 = 无限窗口
        self.life = max(sub.life[0], 0.001)
        self.life_var = sub.life[1]

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

        # box / sphere 发射尺寸
        bx = sub.box_x
        by = sub.box_y
        bz = sub.box_z
        self.box_half_x = bx[0] if sub.emitter_type == "box" else 0.0
        self.box_half_y = by[0] if sub.emitter_type == "box" else 0.0
        self.box_half_z = bz[0] if sub.emitter_type == "box" else 0.0
        sr = sub.sphere_radius
        self.sphere_r = sr[0] if sub.emitter_type == "sphere" else 0.0
        self.sphere_r_var = sr[1] if sub.emitter_type == "sphere" else 0.0
        self.sphere_yaw = sub.sphere_yaw
        self.sphere_pitch = sub.sphere_pitch

        # 发射体朝向：整个局部坐标系绕原点旋转，形状偏移与初速度方向一起转。
        # 原先 emitter_yaw/emitter_pitch 在 parser 的 known 集合里但从未被读取，
        # 既不生效也无诊断（静默丢弃）。原版 414 个子系统写非零值、2376 个显式
        # 写 0、2332 个完全没写。
        #
        # 关键：必须区分「显式写 0」与「没写」。实测证据
        # （arkship_01_exhaust_line_moving / fire_flare，position=(-15,0,0)、
        # velocity=-20）：把它放在 -X 侧再给负速度，物理上就是朝 -X 向后喷；
        # 若把「写 0」当作不旋转，尾焰会笔直朝下（(0,-20,0)），不合理。
        # 同一批 175 个样本里，读法「写 0 → +X」全部得到水平方向，另一读法有
        # 54 个笔直朝上。colossus 的 ball_2（position=(-22,0,0)）同理：只有把
        # 写 0 解释为 +X，三球才全部径向（0.96°/0.00°/0.96°），否则 ball_2 差 90°。
        # 故：写了键就旋转（0 → +X 方向），没写则保持代码原有的 +Y 基准不动。
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
        return int((upto - self.start) * self.rate)

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
        emitted = (upto - self.start) * self.rate
        # 出生早于 (t - 平均寿命) 的那批，按平均寿命算已到寿
        avg_life = max(self.life, 0.001)
        cutoff = t - avg_life
        expired = 0.0 if cutoff <= self.start else (
            (min(cutoff, upto) - self.start) * self.rate)
        alive = emitted - expired
        return max(0.0, min(alive, self.sub.max_amount))

    def reset(self):
        self.t = 0.0
        self.n_sub = 0          # 已执行的绝对子步数（唯一的时间状态，保证可重放）
        self.spawn_count = 0
        # 粒子记录：[出生时间, 寿命, x,y,z, vx,vy,vz, 角速度, 初始角, 权重]
        self.particles = []

    # ---- 发射 ----

    def _w(self, i, salt=0):
        """第 i 颗粒子在第 salt 维的确定性权重，落在 [0,1)。

        用 Halton 低差异序列：每一维取一个互质基数，维度之间互相独立；子系统之间
        靠序列起点错开（seq_offset），不占用额外维度。

        不能写成 frac((i+1)*GOLDEN + salt*C) 那种「同一序列加偏移」的形式——
        那样各维之间只差一个常数，等于同一个随机数，点集退化成一条直线。
        实测 16x16 网格只覆盖 12.5%（本该接近 100%），球面发射器的落点被压成
        一条测地线，三轴符号比 x=86.6%/y=50%/z=36.6%（应各约 50%），
        视觉上就是「球形发射场只出来一半」。换成 Halton 后球面覆盖
        10.8% → 86.7%，三轴符号回到 49.5/49.9/50.0%。
        """
        base = _HALTON_BASES[salt % len(_HALTON_BASES)]
        return _radical_inverse(self.seq_offset + i + 1, base)

    def _spawn(self, t):
        sub = self.sub
        i = self.spawn_count
        self.spawn_count += 1
        # 各维权重：每个用途占一个独立维度（Halton 互质基），互不相关。
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

        w0 = w_life
        life = self.life + self.life_var * (2.0 * w0 - 1.0)
        life = max(life, 0.05)

        # 发射点
        if sub.emitter_type == "box" and (self.box_half_x or self.box_half_y or self.box_half_z):
            px = (2.0 * w_pos1 - 1.0) * self.box_half_x
            py = (2.0 * w_pos2 - 1.0) * self.box_half_y
            pz = (2.0 * w_pos3 - 1.0) * self.box_half_z
        elif sub.emitter_type == "sphere" and self.sphere_r > 0.0:
            # 球面点：半径 ± 方差，yaw/pitch 在给定范围内取（与引擎行为近似）
            r = max(self.sphere_r + self.sphere_r_var * (2.0 * w_pos1 - 1.0), 0.0)
            sy = self.sphere_yaw[0] + self.sphere_yaw[1] * (2.0 * w_pos2 - 1.0)
            sp = self.sphere_pitch[0] + self.sphere_pitch[1] * (2.0 * w_pos3 - 1.0)
            d = sph_to_dir(sp, sy)
            px = d[0] * r
            py = d[1] * r
            pz = d[2] * r
        else:
            px = py = pz = 0.0

        # 初速度方向
        p = self.pitch[0] + self.pitch[1] * (2.0 * w_dir1 - 1.0)
        y = self.yaw[0] + self.yaw[1] * (2.0 * w_dir2 - 1.0)
        d = sph_to_dir(p, y)
        v = self.vel + self.vel_var * (2.0 * w_speed - 1.0)

        # 发射体朝向：旋转「形状偏移」与「出射方向」两样，position 不动。
        # position 是设计者摆放发射体的位置，转它会让发射体离开原位——例如
        # position=(0,6,12) 配 emitter_yaw=180，若连 position 一起转就变成
        # (0,6,-12)，等于把发射体挪走，而设计者本可以直接写那个坐标。
        # 反过来，只转形状与方向才解释得通：point 发射器没有体积，它写非零
        # emitter_yaw 仍有效果（cybernetic_01_ship_exhaust 的 180 让尾焰反向），
        # 这点只能由「方向被旋转」满足。
        # 有方差时逐粒子取角，否则用构造期算好的固定矩阵。
        # 注意判断顺序：不能写成 `m is None and self.emit_var`——__init__ 里的条件是
        # `has_emitter_rot or emit_var`，只要 emit_var 为真就已经建好矩阵，于是
        # `m is None` 蕴含 `not emit_var`，那个分支永远进不去（实测 9 个带方差的
        # 子系统在 _spawn 期调用 _emit_rot_matrix 共 0 次，方差被静默忽略）。
        # 现在直接按 emit_var 分支，方差才真正生效。
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
        angvel = sub.rotation_speed[0] + sub.rotation_speed[1] * (2.0 * w_rotv - 1.0)

        self.particles.append([t, life, px, py, pz, d[0] * v, d[1] * v, d[2] * v, angvel, ang0, w0])

    # ---- 物理 ----

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
            elif ftype in ("vortex", "spin"):
                axis = _norm3(f.direction) or (0.0, 1.0, 0.0)
                rx = p[0] - f.position[0]
                ry = p[1] - f.position[1]
                rz = p[2] - f.position[2]
                c = _cross(axis, (rx, ry, rz))
                ax += amt * c[0]
                ay += amt * c[1]
                az += amt * c[2]
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
            self.reset()
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
                    damp = math.exp(-fric * h)
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

        # 窗口后清理：无新粒子且全部到寿
        if self.duration >= 0.0 and t_target > self.start + self.duration + self.life:
            alive = self.particles
            alive[:] = [p for p in alive if t_target - p[0] < p[1]]

    # ---- 显示快照 ----

    def snapshot(self):
        out = []
        t = self.t
        sub = self.sub
        sheet_total = sub.sheet_x * sub.sheet_y
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
            r = _apply_anim(sub.color[0], self.anim_r, u)
            g = _apply_anim(sub.color[1], self.anim_g, u)
            b = _apply_anim(sub.color[2], self.anim_b, u)
            a = _clamp(alpha, 0.0, 1.0)
            if a <= 0.0:
                continue
            ang = math.radians(p[9] + p[8] * age)
            cell = -1
            if sub.sheet_anim and sheet_total > 1:
                cell = int(age * sub.sheet_loop * sheet_total) % sheet_total
            out.append((p[2], p[3], p[4], size, r, g, b, a, ang, cell))
        return out


class SystemSim:
    """整个粒子系统：全部子系统 + 总时长估算。"""

    def __init__(self, system, time_offset=0.0):
        self.system = system
        self.name = system.name
        self.time_offset = time_offset
        self.subs = []
        for i, sub in enumerate(system.subsystems):
            if sub.hide:
                continue
            self.subs.append(SubsystemSim(system, sub, i, time_offset))
        self.enabled = {s.name: True for s in self.subs}
        self.t = 0.0

    def reset(self):
        for s in self.subs:
            s.reset()
        self.t = 0.0

    def advance(self, t):
        if t < self.t - 1e-6:
            self.reset()
        for s in self.subs:
            if self.enabled.get(s.name, True):
                s.advance(t)
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
