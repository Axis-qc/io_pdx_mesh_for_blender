"""
Paradox particle files (.gfx / .asset / entity .asset), text parser.

 Clausewitz 文本格式解析：`key = value` / `key = { ... }` / 裸值序列（曲线），
 提取成粒子模拟器用的类型化结构。本模块不依赖 bpy，可在无 Blender 环境下测试。

 支持的变体（均出自本 mod 与原版实文件）：
 - 紧凑单行写法（br_judgment_hit.asset）
 - `值,动画名` 复合值、`值 方差` 二元组、`值,动画名 方差` 三元组
 - `key =` 换行后 `{`（lev_stellarite_move_trail.asset）
 - 一行多个赋值、`#` 注释
 - 顶层块名：particle / pdxparticle_system / entity

 author : Axis-qc (fork of ross-g/io_pdx_mesh 0.91.2)
"""

import logging
import math
import os
import re
import sys

DATA_LOG = logging.getLogger("io_pdx.particles.parser")

# 顶层块名，出现这些 key 的块按其类型处理
SYSTEM_KEYS = ("particle", "pdxparticle_system", "pdxparticle")
ENTITY_KEYS = ("entity",)


# ----------------------------------------------------------------------------------------------------------------------
# Generic tree
# ----------------------------------------------------------------------------------------------------------------------

class Node:
    """通用节点：命名标量赋值（value）、命名子块（children）、裸值序列（positional，如曲线）。"""

    __slots__ = ("key", "value", "positional", "children")

    def __init__(self, key):
        self.key = key
        self.value = None        # 标量：float / str / None
        self.positional = []     # 裸值列表（block 内无名 token）
        self.children = []       # 子 Node 列表（block 内命名条目）

    def child(self, key):
        for c in self.children:
            if c.key == key:
                return c
        return None

    def child_values(self, key):
        """同名 key 出现多次时返回全部（如多个 subsystem）。"""
        return [c for c in self.children if c.key == key]

    def __repr__(self):
        return "Node({0}, value={1!r}, pos={2!r}, n_children={3})".format(
            self.key, self.value, self.positional, len(self.children))


def _convert_token(tok):
    if tok.startswith('"') and tok.endswith('"') and len(tok) >= 2:
        return tok[1:-1]
    try:
        return float(tok)
    except ValueError:
        return tok


def _tokenize(text):
    # 去注释（# 到行尾）；字符串里不会出现 #
    lines = []
    for line in text.splitlines():
        idx = line.find("#")
        lines.append(line if idx < 0 else line[:idx])
    body = "\n".join(lines)
    return re.findall(r'"[^"]*"|[{}=]|[^\s{}=]+', body)


def _parse_block(tokens, i):
    """tokens[i] 应为 '{' 的下一个 token。返回 (Node, next_i)，Node 已消费到匹配的 '}' 之后。"""
    node = Node("")
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok == "}":
            return node, i + 1
        if tok == "{":
            # 匿名子块（不 expected，容错处理）
            sub, i = _parse_block(tokens, i + 1)
            node.children.append(sub)
            continue
        if i + 1 < n and tokens[i + 1] == "=":
            key = tok
            i += 2
            if i < n and tokens[i] == "{":
                sub, i = _parse_block(tokens, i + 1)
                sub.key = key
                node.children.append(sub)
            else:
                child = Node(key)
                child.value = _convert_token(tokens[i])
                node.children.append(child)
                i += 1
            continue
        # 裸值（曲线数值、列表）
        node.positional.append(_convert_token(tok))
        i += 1
    # 未闭合容错
    DATA_LOG.warning("particle file: unterminated block, auto-closing")
    return node, i


def parse_text(text):
    """解析整份文件 → [顶层 Node]（每个都带 key）。"""
    tokens = _tokenize(text)
    out = []
    i, n = 0, len(tokens)
    while i < n:
        tok = tokens[i]
        if i + 1 < n and tokens[i + 1] == "=":
            key = tok
            i += 2
            if i < n and tokens[i] == "{":
                sub, i = _parse_block(tokens, i + 1)
                sub.key = key
                out.append(sub)
            else:
                child = Node(key)
                child.value = _convert_token(tokens[i])
                out.append(child)
                i += 1
        else:
            i += 1  # 游离 token，忽略
    return out


def parse_file(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return parse_text(fh.read())


# ----------------------------------------------------------------------------------------------------------------------
# 值提取辅助
# ----------------------------------------------------------------------------------------------------------------------

def _split_compound(tok):
    """'255,fire_fade' → (255.0, 'fire_fade')；普通值 → (值, None)。非数值开头（如 force 引用 'upforce,vortex'）返回 None。"""
    if not isinstance(tok, str) or "," not in tok:
        return None
    parts = tok.split(",")
    try:
        return float(parts[0]), (parts[1] if len(parts) > 1 else None)
    except ValueError:
        return None  # 力场引用等多名字符串


def _as_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _pair(node, default=0.0):
    """『值 方差』二元组 → (value, variance)。支持标量 / 复合值 / block 裸值。"""
    if node is None:
        return (default, 0.0)
    if node.value is not None:
        comp = _split_compound(node.value) if isinstance(node.value, str) else None
        if comp:
            return (comp[0], 0.0)
        return (_as_float(node.value, default), 0.0)
    vals = list(node.positional)
    if not vals:
        return (default, 0.0)
    comp = _split_compound(vals[0]) if isinstance(vals[0], str) else None
    v = comp[0] if comp else _as_float(vals[0], default)
    var = _as_float(vals[1], 0.0) if len(vals) > 1 else 0.0
    return (v, var)


def _triplet(node, default=0.0):
    """『值,动画 方差』三元组 → (value, anim_name|None, variance)。"""
    if node is None:
        return (default, None, 0.0)
    if node.value is not None:
        comp = _split_compound(node.value) if isinstance(node.value, str) else None
        if comp:
            return (comp[0], comp[1], 0.0)
        return (_as_float(node.value, default), None, 0.0)
    vals = list(node.positional)
    if not vals:
        return (default, None, 0.0)
    comp = _split_compound(vals[0]) if isinstance(vals[0], str) else None
    v = comp[0] if comp else _as_float(vals[0], default)
    anim = comp[1] if comp else None
    var = _as_float(vals[1], 0.0) if len(vals) > 1 else 0.0
    return (v, anim, var)


def _bool(node, default=False):
    if node is None or node.value is None:
        return default
    return str(node.value).strip().lower() == "yes"


def _vec3(node, default=(0.0, 0.0, 0.0)):
    """position = {x=0 y=0 z=0} 或 force position = {0 0 0} 两种写法都支持。"""
    if node is None:
        return tuple(default)
    if node.value is not None:
        v = _as_float(node.value, default[0])
        return (v, v, v)
    if node.positional:
        p = node.positional
        return tuple(_as_float(p[i], default[i]) if i < len(p) else default[i] for i in range(3))
    x = node.child("x")
    y = node.child("y")
    z = node.child("z")
    return (
        _as_float(x.value, default[0]) if x is not None else default[0],
        _as_float(y.value, default[1]) if y is not None else default[1],
        _as_float(z.value, default[2]) if z is not None else default[2],
    )


# ----------------------------------------------------------------------------------------------------------------------
# Typed structures
# ----------------------------------------------------------------------------------------------------------------------

class PdxAnimation:
    __slots__ = ("name", "start", "duration", "repeat", "min_value", "max_value", "curve", "op", "axis")

    def __init__(self, node):
        nm = node.child("name")
        self.name = str(nm.value) if nm is not None and nm.value is not None else ""
        self.start = _pair(node.child("start"))[0]
        self.duration = _pair(node.child("duration"), 1.0)[0]
        self.repeat = _bool(node.child("repeat"))
        self.min_value = _pair(node.child("minValue"))[0]
        self.max_value = _pair(node.child("maxValue"), 1.0)[0]
        curve_node = node.child("curve")
        raw = list(curve_node.positional) if curve_node is not None else []
        self.curve = []
        for i in range(0, len(raw) - 1, 2):
            self.curve.append((_as_float(raw[i], 0.0), _as_float(raw[i + 1], 0.0)))
        op_node = node.child("op")
        self.op = str(op_node.value).strip('"') if op_node is not None and op_node.value else "MUL"
        time_node = node.child("time")
        self.axis = str(time_node.value).strip('"') if time_node is not None and time_node.value else "life"

    def evaluate(self, u):
        """u 为归一化时间 0..1（超出按 clamp），返回映射到 minValue..maxValue 的值。"""
        curve = self.curve
        if not curve:
            norm = 0.0
        elif u <= curve[0][0]:
            norm = curve[0][1]
        elif u >= curve[-1][0]:
            norm = curve[-1][1]
        else:
            norm = curve[-1][1]
            for i in range(len(curve) - 1):
                t0, v0 = curve[i]
                t1, v1 = curve[i + 1]
                if t0 <= u <= t1:
                    span = (t1 - t0) or 1e-9
                    norm = v0 + (v1 - v0) * (u - t0) / span
                    break
        return self.min_value + norm * (self.max_value - self.min_value)


class PdxForce:
    __slots__ = ("type", "name", "position", "direction", "local_force", "yaw", "division", "amount")

    def __init__(self, node):
        t = node.child("type")
        self.type = str(t.value).strip('"') if t is not None and t.value is not None else ""
        name_node = node.child("name")
        self.name = str(name_node.value).strip('"') if name_node is not None and name_node.value is not None else ""
        self.position = _vec3(node.child("position"))
        self.direction = _vec3(node.child("direction"))
        self.local_force = _bool(node.child("local_force"))
        self.yaw = _pair(node.child("yaw"))[0]
        self.division = _pair(node.child("division"), 16.0)[0]
        self.amount = _pair(node.child("amount"))[0]


class PdxSubsystem:
    __slots__ = (
        "name", "max_amount", "emitter_type", "box_x", "box_y", "box_z",
        "sphere_radius", "sphere_yaw", "sphere_pitch",
        "emitter_yaw", "emitter_pitch", "has_emitter_rot",
        "texture_file", "texture_shader", "sheet_x", "sheet_y", "sheet_anim", "sheet_loop",
        "color", "color_var", "alpha", "position",
        "start", "duration", "velocity_pitch", "velocity_yaw", "velocity",
        "life", "emission", "size", "particle_yaw", "particle_pitch",
        "rotation", "rotation_speed", "rotation_speed_pitch", "rotation_speed_yaw",
        "rotation_speed_roll", "emission_pulse_duration", "emission_pulse_silence", "mass",
        "force_refs", "billboard", "local_space", "trail", "invert", "hide", "sort",
        "children", "unknown_keys",
    )

    def __init__(self, node):
        nm = node.child("name")
        self.name = str(nm.value) if nm is not None and nm.value is not None else "<unnamed>"
        # 嵌套的子子系统定义（childsystem = { ... }）。实测全库 15 处 / 9 个文件，
        # 块内顶层键与 subsystem 完全同构（32 个键，unknown 残留 0），是一套完整的
        # 子系统而非参数。当前**只解析并记录，不参与模拟与渲染**——原因：
        #   · 真实视觉收益只有 1 处明显（infernal_red_giant_explosion 的 sparks_child，
        #     峰值 +67 颗）；9 处边际；6 个星体文件因 hide=yes 增量恰为 0。
        #   · 15 处的 slave_particles 全为 0，说明「挂到父粒子上」缺少数据支撑，
        #     真做父子跟随是高风险低收益。
        # 解析出来至少让它在面板上可见（消除静默丢弃），也为将来要做时留好入口。
        self.children = [PdxSubsystem(c) for c in node.child_values("childsystem")]
        self.max_amount = int(_pair(node.child("max_amount"), 100.0)[0])
        et = node.child("emitter_type")
        self.emitter_type = str(et.value).strip('"') if et is not None and et.value is not None else "point"
        self.box_x = _pair(node.child("box_emitter_x"))
        self.box_y = _pair(node.child("box_emitter_y"))
        # 三个轴都要读；只读 x/y 会让 box 发射器塌成一个平面矩形。原版 748 个写了
        # 这个键，但值非零的只有 36 个（其余写成 { 0 方差 }，方差当前不参与散开）。
        self.box_z = _pair(node.child("box_emitter_z"))
        self.sphere_radius = _pair(node.child("sphere_emitter_radius"))
        self.sphere_yaw = _pair(node.child("sphere_emitter_yaw"))
        self.sphere_pitch = _pair(node.child("sphere_emitter_pitch"))
        # emitter_yaw / emitter_pitch：发射体相对子系统原点的旋转。
        # 用量（仅原版）：写了键 2790（显式写 0 有 2376、非零 414），没写 2332。
        #
        # has_emitter_rot：记下「键写没写」，供统计与面板展示。写 0 时旋转矩阵是
        # 单位阵，与完全没写的效果相同（见 simulator._emit_rot_matrix）。
        ey_node = node.child("emitter_yaw")
        ep_node = node.child("emitter_pitch")
        self.emitter_yaw = _pair(ey_node)
        self.emitter_pitch = _pair(ep_node)
        self.has_emitter_rot = ey_node is not None or ep_node is not None
        tex = node.child("texture")
        if tex is not None and tex.value is None:
            f = tex.child("file")
            s = tex.child("shader")
            self.texture_file = str(f.value).strip('"') if f is not None and f.value is not None else ""
            self.texture_shader = str(s.value).strip('"') if s is not None and s.value is not None else "ParticleAdditive"
            self.sheet_x = int(_pair(tex.child("x"), 1.0)[0])
            self.sheet_y = int(_pair(tex.child("y"), 1.0)[0])
        else:
            self.texture_file, self.texture_shader, self.sheet_x, self.sheet_y = "", "ParticleAdditive", 1, 1
        self.sheet_anim = _bool(node.child("spritesheet_animation"))
        self.sheet_loop = _pair(node.child("spritesheet_animation_loop"), 1.0)[0]

        color = node.child("color")
        if color is not None:
            # 三个通道各自也是「值 方差」：原版 572/663/562 个子系统写了非零方差
            # （如 y={ 150 20 }、z={ 80 20 }），只取 [0] 会把每颗粒子的颜色抖动丢掉。
            cr = _pair(color.child("x"), 255.0)
            cg = _pair(color.child("y"), 255.0)
            cb = _pair(color.child("z"), 255.0)
            self.color = (cr[0] / 255.0, cg[0] / 255.0, cb[0] / 255.0)
            self.color_var = (cr[1] / 255.0, cg[1] / 255.0, cb[1] / 255.0)
            a = color.child("alpha")
        else:
            self.color = (1.0, 1.0, 1.0)
            self.color_var = (0.0, 0.0, 0.0)
            a = None
        self.alpha = _triplet(a, 255.0)
        # position 轴序与世界不同：放 +X 写 pz，放 +Z 写 -px。
        _p = _vec3(node.child("position"))
        self.position = (_p[2], _p[1], -_p[0])

        self.start = _pair(node.child("start"))
        self.duration = _pair(node.child("duration"), -1.0)
        self.velocity_pitch = _pair(node.child("velocity_pitch"))
        self.velocity_yaw = _pair(node.child("velocity_yaw"))
        self.velocity = _pair(node.child("velocity"))
        self.life = _pair(node.child("life"), 1.0)
        self.emission = _pair(node.child("emission"), 10.0)
        self.size = _triplet(node.child("size"), 1.0)
        self.particle_yaw = _pair(node.child("particle_yaw"))
        self.particle_pitch = _pair(node.child("particle_pitch"))
        self.rotation = _pair(node.child("rotation"))
        self.rotation_speed = _pair(node.child("rotation_speed"))
        # 三轴自转角速度。原版 182 个子系统用到（pitch 75 / yaw 70 / roll 37）。
        self.rotation_speed_pitch = _pair(node.child("rotation_speed_pitch"))
        self.rotation_speed_yaw = _pair(node.child("rotation_speed_yaw"))
        self.rotation_speed_roll = _pair(node.child("rotation_speed_roll"))
        # 脉冲发射：[工作时长, 静默时长]，成对出现（实测 105 对 / 各 1 个落单）。
        # 两者都缺省为 0 表示持续发射；非零时发射按 工作->静默 循环。
        self.emission_pulse_duration = _pair(node.child("emission_pulse_duration"))
        self.emission_pulse_silence = _pair(node.child("emission_pulse_silence"))
        self.mass = _pair(node.child("mass"), 1.0)

        force_node = node.child("force")
        refs = []
        if force_node is not None and isinstance(force_node.value, str):
            refs = [p.strip().strip('"') for p in force_node.value.split(",") if p.strip()]
        self.force_refs = refs

        self.billboard = _bool(node.child("billboard"), True)
        self.local_space = _bool(node.child("local_space"))
        self.trail = _bool(node.child("trail"))
        self.invert = _bool(node.child("invert"))
        self.hide = _bool(node.child("hide"))
        sort_node = node.child("sort")
        self.sort = str(sort_node.value).strip('"') if sort_node is not None and sort_node.value is not None else ""

        known = {
            "name", "max_amount", "slave_particles", "emitter_type", "box_emitter_x", "box_emitter_y",
            "box_emitter_z",
            "sphere_emitter_radius", "sphere_emitter_yaw", "sphere_emitter_pitch",
            "invert", "trail", "spritesheet_animation", "spritesheet_animation_loop", "local_space",
            "billboard", "hide", "texture", "color", "position", "start", "duration",
            "emitter_yaw", "emitter_pitch", "velocity_pitch", "velocity_yaw", "velocity",
            "life", "emission", "size", "particle_yaw", "particle_pitch", "rotation",
            "rotation_speed", "rotation_speed_pitch", "rotation_speed_yaw",
            "rotation_speed_roll", "emission_pulse_duration", "emission_pulse_silence",
            "mass", "force", "sort",
            # childsystem 已解析成 children（见 __init__ 注释），不再算未知。
            # particle_roll 全库仅 1 处（planet_lightning.asset，值 { 0 0 } 即无效果），
            # 收进 known 是为了不再静默丢弃——真实现它是另一件事。
            "childsystem", "particle_roll",
        }
        self.unknown_keys = {c.key for c in node.children if c.key not in known}


class PdxParticleSystem:
    __slots__ = ("name", "subsystems", "animations", "forces", "source_file")

    def __init__(self, node, source_file):
        nm = node.child("name")
        self.name = str(nm.value) if nm is not None and nm.value is not None else "<unnamed>"
        self.source_file = source_file
        self.subsystems = [PdxSubsystem(c) for c in node.child_values("subsystem")]
        self.animations = {}
        for c in node.child_values("animation"):
            anim = PdxAnimation(c)
            self.animations[anim.name] = anim
        self.forces = {}
        for c in node.child_values("force"):
            force = PdxForce(c)
            self.forces[force.name] = force

    def nested_subsystems(self):
        """所有被解析但**不参与模拟/渲染**的嵌套子子系统（childsystem）。

        供面板/日志做诊断用：至少让用户知道「这里还有 N 个嵌套子系统没画出来」，
        而不是像以前那样完全静默。返回 [(宿主子系统名, 子子系统)]。
        """
        out = []
        for sub in self.subsystems:
            for child in getattr(sub, "children", ()):  # noqa: B009
                out.append((sub.name, child))
        return out

    # ---- 动画引用解析（R/G/B 命名约定：全系统通用或 '<通道>_<子系统名>'）----

    def anim_for_channel(self, sub, channel):
        """channel: 'R'|'G'|'B'|'alpha'|'size' → 动画或 None。alpha/size 走复合值引用。"""
        c = channel.upper()
        if c in ("R", "G", "B"):
            return self.animations.get(c) or self.animations.get("{0}_{1}".format(c, sub.name))
        if channel == "alpha":
            return self.animations.get(sub.alpha[1]) if sub.alpha[1] else None
        if channel == "size":
            return self.animations.get(sub.size[1]) if sub.size[1] else None
        return None


# ----------------------------------------------------------------------------------------------------------------------
# .gfx 注册 / entity 事件
# ----------------------------------------------------------------------------------------------------------------------

def parse_gfx_registrations(path):
    """gfx 注册文件 → [{name, type, scale}]"""
    out = []
    for top in parse_file(path):
        for reg in top.child_values("pdxparticle"):
            name = reg.child("name")
            typ = reg.child("type")
            scale = reg.child("scale")
            out.append({
                "name": str(name.value).strip('"') if name is not None and name.value is not None else "",
                "type": str(typ.value).strip('"') if typ is not None and typ.value is not None else "",
                "scale": _as_float(scale.value, 1.0) if scale is not None and scale.value is not None else 1.0,
            })
    return out


def parse_entity_events(path):
    """entity 包装文件 → [{entity, events:[{particle, time, node, ...}]}]

    事件参数按实机出现频率取用（原版 8292 个 entity 块统计）：
    node 32111 处、time 32107、particle 31137、trigger_once 30886。
    node 是粒子挂在哪根挂点上，缺了它粒子只能堆在原点。

    两个 key 都要读：event 是常规事件，start_event 是状态启动时触发
    （全库 4642 处，astral_rift_entity 的粒子就写在里面），只读 event 会漏掉。
    """
    out = []
    for top in parse_file(path):
        if top.key not in ENTITY_KEYS:
            continue
        name = top.child("name")
        # 与其他字段一致地去引号：parse 层已去一层，这里兜底（原写法会留下引号，
        # 导致 entity 名带 " 与事件里的 particle 名对不上）。
        ent_name = ""
        if name is not None and name.value is not None:
            ent_name = str(name.value).strip().strip('"')
        entry = {"entity": ent_name, "events": []}
        for state in top.child_values("state"):
            for ev in state.child_values("event") + state.child_values("start_event"):
                p = ev.child("particle")
                t = ev.child("time")
                n = ev.child("node")
                entry["events"].append({
                    "particle": str(p.value).strip().strip('"') if p is not None and p.value is not None else "",
                    "time": _as_float(t.value, 0.0) if t is not None and t.value is not None else 0.0,
                    "node": str(n.value).strip().strip('"') if n is not None and n.value is not None else "",
                    "keep_particle": _bool(ev.child("keep_particle")),
                    "trigger_once": _bool(ev.child("trigger_once")),
                })
        out.append(entry)
    return out


# ----------------------------------------------------------------------------------------------------------------------
# Library：多文件系统索引与贴图路径解析
# ----------------------------------------------------------------------------------------------------------------------

_SKIP_DIRS = {"__pycache__", ".git", ".svn"}

# Paradox 引擎各游戏在 Steam 上的 appid；用于在库里按 appmanifest 认游戏目录。
STEAM_APPIDS = {
    "281990": "Stellaris",
    "236850": "Europa Universalis IV",
    "394360": "Hearts of Iron IV",
    "1158310": "Crusader Kings III",
    "529340": "Victoria 3",
    "949230": "Cities: Skylines II",
}


def _steam_root_candidates():
    """各平台的 Steam 安装根（只给候选，不保证存在）。"""
    home = os.path.expanduser("~")
    if os.name == "nt":
        try:
            import winreg  # noqa: PLC0415
        except ImportError:
            winreg = None
        if winreg is not None:
            for hive, key in (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
            ):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        val, _ = winreg.QueryValueEx(k, "SteamPath")
                        yield val.replace("/", os.sep)
                except OSError:
                    pass
        yield os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Steam")
        yield os.path.join(home, "AppData", "Local", "Steam")
    elif sys.platform == "darwin":
        yield os.path.join(home, "Library", "Application Support", "Steam")
    else:
        yield os.path.join(home, ".steam", "steam")
        yield os.path.join(home, ".local", "share", "Steam")
        yield os.path.join(home, ".var", "app", "com.valvesoftware.Steam", "data", "Steam")


def _parse_vdf_paths(text):
    """从 libraryfolders.vdf 粗取所有库路径（"path" "X:\\\\...")。"""
    out = []
    for m in re.finditer(r'"path"\s*"([^"]+)"', text):
        p = m.group(1).replace("\\\\", "\\")
        if p:
            out.append(p)
    return out


def find_steam_libraries():
    """返回所有存在的 Steam 库目录（含 Steam 安装根本身），按路径大小写不敏感去重。"""
    libs = []
    seen = set()

    def add(p):
        if not p or not os.path.isdir(p):
            return
        key = os.path.normcase(os.path.normpath(p))
        if key in seen:
            return
        seen.add(key)
        libs.append(p)

    for root in _steam_root_candidates():
        if not os.path.isdir(root):
            continue
        add(root)
        steamapps = os.path.join(root, "steamapps")
        for name in ("libraryfolders.vdf", "libraryfolders.vdf.bak"):
            vdf = os.path.join(steamapps, name)
            if not os.path.isfile(vdf):
                continue
            try:
                with open(vdf, "r", encoding="utf-8", errors="replace") as fh:
                    for p in _parse_vdf_paths(fh.read()):
                        add(p)
            except OSError:
                pass
    return libs


def find_game_roots(appids=None):
    """在各 Steam 库里按 appmanifest 找游戏安装目录 → {appid: [目录]}。

    不写死任何本机路径：库位置来自 Steam 自己的配置，游戏目录来自 acf 里的
    installdir。非 Steam 版（GOG / 微软商店）查不到，此时返回空，由用户手填。
    """
    want = set(appids) if appids is not None else set(STEAM_APPIDS)
    found = {}
    for lib in find_steam_libraries():
        steamapps = os.path.join(lib, "steamapps")
        if not os.path.isdir(steamapps):
            continue
        try:
            names = os.listdir(steamapps)
        except OSError:
            continue
        for fn in names:
            if not (fn.startswith("appmanifest_") and fn.endswith(".acf")):
                continue
            appid = fn[len("appmanifest_"):-len(".acf")]
            if appid not in want:
                continue
            try:
                with open(os.path.join(steamapps, fn), "r", encoding="utf-8",
                          errors="replace") as fh:
                    txt = fh.read()
            except OSError:
                continue
            m = re.search(r'"installdir"\s*"([^"]+)"', txt)
            if not m:
                continue
            game = os.path.join(steamapps, "common", m.group(1))
            if os.path.isdir(game):
                found.setdefault(appid, []).append(game)
    return found


def detect_game_roots():
    """自动检测到的游戏根目录，优先返回确实能用的（有 gfx/particles 或 gfx/models）。

    Paradox 各游戏目录布局不同：Stellaris 是 <root>/gfx，Victoria 3 是
    <root>/game/gfx，所以只认 Steam 的 installdir 会拿到一批对粒子无用的路径。
    排序把「能用的」放前面，但仍全部返回，由调用方与用户决定。
    """
    found = find_game_roots()
    usable, rest = [], []
    for appid in STEAM_APPIDS:
        for p in found.get(appid, []):
            if p in usable or p in rest:
                continue
            (usable if looks_like_game_root(p) else rest).append(p)
    return usable + rest


def looks_like_game_root(path):
    """目录是否像个 Paradox 游戏根：有 gfx 且 gfx 下有 particles 或 models。"""
    if not path or not os.path.isdir(path):
        return False
    gfx = os.path.join(path, "gfx")
    if not os.path.isdir(gfx):
        return False
    return any(os.path.isdir(os.path.join(gfx, sub))
               for sub in ("particles", "models"))


def iter_files(root, subdir, exts):
    """递归列出 root/subdir 下后缀命中的文件，排序输出，跳过缓存目录。

    原版的粒子本体大量放在 gfx/particles 的子目录里（combat / ships /
    stars_and_planets / misc / cosmic_storms_assets），只扫顶层会漏掉约三分之二。
    """
    base = os.path.join(root, *subdir.split("/")) if subdir else root
    if not os.path.isdir(base):
        return
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for fn in sorted(filenames):
            if os.path.splitext(fn)[1].lower() in exts:
                yield os.path.join(dirpath, fn)


class ParticleCatalogEntry:
    """一条可选中的粒子：.gfx 注册项（name/type/scale）+ 它指向的本体。

    列表按注册名展示——那才是实体事件里写的名字。同一个注册名在多个 .gfx 里
    重复出现时不去重，各留一条并带来源文件，便于区分。
    """

    __slots__ = ("name", "type", "scale", "source_file", "system")

    def __init__(self, name, type_, scale, source_file, system):
        self.name = name
        self.type = type_
        self.scale = scale
        self.source_file = source_file
        self.system = system          # 解析到的本体（PdxParticleSystem），没有则 None

    @property
    def resolved(self):
        return self.system is not None


def build_catalog(roots, roots_for_index=None):
    """收集 roots 下所有 pdxparticle 注册项 → [ParticleCatalogEntry]。

    只扫 gfx/**/*.gfx（注册所在处），不碰 gfx/particles 下的 .asset 本体——
    本体由 ParticleLibrary 索引。返回列表按注册名排序，同名保持来源文件次序。

    roots_for_index 用于索引本体与贴图（可含原版根）：列表仍只列 roots 里的注册
    项，但 mod 的粒子常引用原版贴图、继承原版本体，索引范围要更宽。
    """
    index_roots = roots_for_index if roots_for_index is not None else roots
    lib = ParticleLibrary(index_roots).scan()
    out = []
    for root in lib.roots:
        if root not in roots:
            continue          # 原版根只用来解析，不往列表里添条目
        for path in iter_files(root, "gfx", {".gfx"}):
            try:
                regs = parse_gfx_registrations(path)
            except Exception as err:  # noqa: BLE001
                DATA_LOG.error("gfx registration parse failed: %s (%s)", path, err)
                continue
            for reg in regs:
                if not reg["name"]:
                    continue
                system = lib.systems.get(reg["type"])
                out.append(ParticleCatalogEntry(
                    reg["name"], reg["type"], reg["scale"], path, system))
    out.sort(key=lambda e: (e.name.lower(), e.source_file))
    return out, lib


class ParticleLibrary:
    """扫多个根目录的 gfx/**/*.asset 建立系统名索引，并解析贴图绝对路径。

    扫整个 gfx/ 而不只是 gfx/particles/：粒子本体不一定放在 gfx/particles 下，
    只扫那里会让注册表列出、预览却报 "No body found"。非粒子 .asset 的顶层键
    不是 SYSTEM_KEYS，不会收进来。

    递归扫描：本体在子目录里同样收进来。同名本体后扫到的覆盖先扫到的，
    因此把优先级高的根（mod）放在 roots 前面。
    """

    def __init__(self, roots):
        self.roots = [r for r in roots if r and os.path.isdir(r)]
        self.systems = {}
        self.files_scanned = 0

    def scan(self):
        for root in self.roots:
            for path in iter_files(root, "gfx", {".asset"}):
                self._scan_file(path)
        return self

    def _scan_file(self, path):
        self.files_scanned += 1
        try:
            for top in parse_file(path):
                if top.key in SYSTEM_KEYS:
                    system = PdxParticleSystem(top, path)
                    if system.name:
                        self.systems[system.name] = system
        except Exception as err:  # noqa: BLE001
            DATA_LOG.error("parsing failed: %s (%s)", path, err)

    def resolve_texture(self, rel_path, source_file):
        """相对路径 → 绝对路径；找不到返回 None。优先贴图来源文件所在 mod 根。"""
        rel = rel_path.replace("\\", "/").lstrip("/")
        candidates = []
        if source_file:
            root = _infer_root(source_file)
            if root:
                candidates.append(os.path.join(root, rel.replace("/", os.sep)))
        for root in self.roots:
            candidates.append(os.path.join(root, rel.replace("/", os.sep)))
        for c in candidates:
            if os.path.isfile(c):
                return c
        return None


def _infer_root(file_path):
    """向上找包含 gfx 的目录（复用 mesh 侧 infer_asset_root 思路）。"""
    p = os.path.dirname(os.path.abspath(file_path))
    for _ in range(4):
        if os.path.isdir(os.path.join(p, "gfx")):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return None
