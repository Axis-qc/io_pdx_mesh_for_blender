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
MAX_SUBSTEPS = 32         # 单帧最多子步数，防卡死
_EPS = 1e-9


def _frac(x):
    return x - math.floor(x)


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
        self.box_half_x = bx[0] if sub.emitter_type == "box" else 0.0
        self.box_half_y = by[0] if sub.emitter_type == "box" else 0.0
        sr = sub.sphere_radius
        self.sphere_r = sr[0] if sub.emitter_type == "sphere" else 0.0
        self.sphere_r_var = sr[1] if sub.emitter_type == "sphere" else 0.0
        self.sphere_yaw = sub.sphere_yaw
        self.sphere_pitch = sub.sphere_pitch

        # 运行状态
        self.reset()

    def reset(self):
        self.t = 0.0
        self.acc = 0.0
        self.spawn_count = 0
        # 粒子记录：[出生时间, 寿命, x,y,z, vx,vy,vz, 角速度, 初始角, 权重]
        self.particles = []

    # ---- 发射 ----

    def _w(self, i, salt=0):
        return _frac((i + 1) * GOLDEN + salt * 0.3172210261)

    def _spawn(self, t):
        sub = self.sub
        i = self.spawn_count
        self.spawn_count += 1
        w0 = self._w(i, self.index)

        life = self.life + self.life_var * (2.0 * w0 - 1.0)
        life = max(life, 0.05)

        # 发射点
        if sub.emitter_type == "box" and (self.box_half_x or self.box_half_y):
            wx = self._w(i, 1)
            wy = self._w(i, 2)
            px = (2.0 * wx - 1.0) * self.box_half_x
            py = (2.0 * wy - 1.0) * self.box_half_y
            pz = 0.0
        elif sub.emitter_type == "sphere" and self.sphere_r > 0.0:
            # 球面点：半径 ± 方差，yaw/pitch 在给定范围内取（与引擎行为近似）
            r = max(self.sphere_r + self.sphere_r_var * (2.0 * self._w(i, 1) - 1.0), 0.0)
            sy = self.sphere_yaw[0] + self.sphere_yaw[1] * (2.0 * self._w(i, 2) - 1.0)
            sp = self.sphere_pitch[0] + self.sphere_pitch[1] * (2.0 * self._w(i, 3) - 1.0)
            d = sph_to_dir(sp, sy)
            px = d[0] * r
            py = d[1] * r
            pz = d[2] * r
        else:
            px = py = pz = 0.0
        pos0 = sub.position
        px += pos0[0]
        py += pos0[1]
        pz += pos0[2]

        # 初速度方向
        p = self.pitch[0] + self.pitch[1] * (2.0 * self._w(i, 3) - 1.0)
        y = self.yaw[0] + self.yaw[1] * (2.0 * self._w(i, 4) - 1.0)
        d = sph_to_dir(p, y)
        v = self.vel + self.vel_var * (2.0 * self._w(i, 5) - 1.0)

        ang0 = sub.rotation[0] + sub.rotation[1] * (2.0 * self._w(i, 6) - 1.0)
        angvel = sub.rotation_speed[0] + sub.rotation_speed[1] * (2.0 * self._w(i, 7) - 1.0)

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
        """把子系统状态推进到绝对时间 t_target（秒）。"""
        if t_target < self.t:
            self.reset()
        t0 = self.t
        dt = t_target - t0
        if dt <= 0.0:
            return

        steps = min(int(dt / SUBSTEP) + 1, MAX_SUBSTEPS)
        h = dt / steps
        fric = self._friction_k()

        for _ in range(steps):
            t_now = self.t
            # 发射窗口
            if self.rate > 0.0 and t_now >= self.start and (self.duration < 0.0 or t_now < self.start + self.duration):
                self.acc += self.rate * h
                while self.acc >= 1.0 and len(self.particles) < self.sub.max_amount:
                    self.acc -= 1.0
                    self._spawn(t_now)
            else:
                self.acc = 0.0
            # 积分
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
            self.t = t_now + h

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
        """建议预览时长（秒）：所有窗口与寿命的最大和。"""
        d = 1.0
        for s in self.subs:
            end = s.start + (s.duration if s.duration > 0 else 0.0) + s.life + s.life_var
            d = max(d, end)
        return d
