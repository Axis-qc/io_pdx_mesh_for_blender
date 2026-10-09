"""
Paradox particle files (.gfx / .asset / entity .asset), text parser.

 Clausewitz 文本格式解析：`key = value` / `key = { ... }` / 裸值序列（曲线），
 提取成粒子模拟器用的类型化结构。本模块不依赖 bpy，可在无 Blender 环境下测试。

 支持的变体（均出自本 mod 与原版实文件）：
 - 紧凑单行写法（br_judgment_hit.asset）
 - `值,动画名` 复合值、`值 方差` 二元组、`值,动画名 方差` 三元组
 - `key =` 换行后 `{`（lev_stellarite_move_trail.asset）
 - 一行多个赋值、`#` 注释
 - 顶层块名：particle / pdxparticle_system / entity / objectTypes

 author : Axis-qc (fork of ross-g/io_pdx_mesh 0.91.2)
"""

import logging
import math
import os
import re

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
    __slots__ = ("type", "name", "position", "direction", "local_force", "yaw", "division", "amount", "unknown")

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
        known = {"type", "name", "position", "direction", "local_force", "yaw", "division", "amount"}
        self.unknown = {c.key for c in node.children if c.key not in known}


class PdxSubsystem:
    __slots__ = (
        "name", "max_amount", "emitter_type", "box_x", "box_y",
        "sphere_radius", "sphere_yaw", "sphere_pitch",
        "texture_file", "texture_shader", "sheet_x", "sheet_y", "sheet_anim", "sheet_loop",
        "color", "alpha", "position",
        "start", "duration", "velocity_pitch", "velocity_yaw", "velocity",
        "life", "emission", "size", "particle_yaw", "particle_pitch",
        "rotation", "rotation_speed", "mass",
        "force_refs", "billboard", "local_space", "trail", "invert", "hide", "sort",
        "unknown_keys",
    )

    def __init__(self, node):
        nm = node.child("name")
        self.name = str(nm.value) if nm is not None and nm.value is not None else "<unnamed>"
        self.max_amount = int(_pair(node.child("max_amount"), 100.0)[0])
        et = node.child("emitter_type")
        self.emitter_type = str(et.value).strip('"') if et is not None and et.value is not None else "point"
        self.box_x = _pair(node.child("box_emitter_x"))
        self.box_y = _pair(node.child("box_emitter_y"))
        self.sphere_radius = _pair(node.child("sphere_emitter_radius"))
        self.sphere_yaw = _pair(node.child("sphere_emitter_yaw"))
        self.sphere_pitch = _pair(node.child("sphere_emitter_pitch"))
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
            self.color = (
                _pair(color.child("x"), 255.0)[0] / 255.0,
                _pair(color.child("y"), 255.0)[0] / 255.0,
                _pair(color.child("z"), 255.0)[0] / 255.0,
            )
            a = color.child("alpha")
        else:
            self.color = (1.0, 1.0, 1.0)
            a = None
        self.alpha = _triplet(a, 255.0)
        self.position = _vec3(node.child("position"))

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
            "sphere_emitter_radius", "sphere_emitter_yaw", "sphere_emitter_pitch",
            "invert", "trail", "spritesheet_animation", "spritesheet_animation_loop", "local_space",
            "billboard", "hide", "texture", "color", "position", "start", "duration",
            "emitter_yaw", "emitter_pitch", "velocity_pitch", "velocity_yaw", "velocity",
            "life", "emission", "size", "particle_yaw", "particle_pitch", "rotation",
            "rotation_speed", "mass", "force", "sort",
        }
        self.unknown_keys = {c.key for c in node.children if c.key not in known}


class PdxParticleSystem:
    __slots__ = ("name", "subsystems", "animations", "forces", "source_file", "unknown_top")

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
        known = {"name", "subsystem", "animation", "force"}
        self.unknown_top = {c.key for c in node.children if c.key not in known}

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
    """entity 包装文件 → [{entity, events:[{particle, time, keep_particle}]}]"""
    out = []
    for top in parse_file(path):
        if top.key not in ENTITY_KEYS:
            continue
        name = top.child("name")
        entry = {"entity": str(name.value) if name is not None and name.value is not None else "", "events": []}
        for state in top.child_values("state"):
            for ev in state.child_values("event"):
                p = ev.child("particle")
                t = ev.child("time")
                entry["events"].append({
                    "particle": str(p.value).strip('"') if p is not None and p.value is not None else "",
                    "time": _as_float(t.value, 0.0) if t is not None and t.value is not None else 0.0,
                    "keep_particle": _bool(ev.child("keep_particle")),
                })
        out.append(entry)
    return out


# ----------------------------------------------------------------------------------------------------------------------
# Library：多文件系统索引与贴图路径解析
# ----------------------------------------------------------------------------------------------------------------------

class ParticleLibrary:
    """扫多个根目录（mod / 原版）的 gfx/particles/*.asset 建立系统名索引，并解析贴图绝对路径。"""

    def __init__(self, roots):
        self.roots = [r for r in roots if r and os.path.isdir(r)]
        self.systems = {}
        self.files_scanned = 0

    def scan(self, extra_files=()):
        for root in self.roots:
            pdir = os.path.join(root, "gfx", "particles")
            if not os.path.isdir(pdir):
                continue
            for fn in sorted(os.listdir(pdir)):
                if fn.lower().endswith(".asset"):
                    self._scan_file(os.path.join(pdir, fn))
        for f in extra_files:
            if os.path.isfile(f):
                self._scan_file(f)
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


def sph_to_dir(pitch_deg, yaw_deg):
    """粒子空间球坐标（pitch 自 +Y 轴，yaw 绕 Y）→ 单位方向向量。pitch=90 → 水平盘。"""
    p = math.radians(pitch_deg)
    y = math.radians(yaw_deg)
    sp = math.sin(p)
    return (sp * math.cos(y), math.cos(p), sp * math.sin(y))
