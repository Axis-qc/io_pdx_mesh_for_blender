"""
PDX asset index: one pass over each root's gfx tree, producing searchable
lists of models, particles and entities.

 纯 Python，不依赖 bpy，可用解释器单独跑。

 索引范围是整个 gfx/，不只 gfx/models 与 gfx/particles：mod 未必遵守原版
 目录规范，只扫约定目录会漏掉。分类维度直接用文件所在目录（相对 gfx/），
 这是原版与 mod 共有的天然组织方式，零维护。

 .asset 只解析一次：本模块自己走一遍，把系统塞进复用的 ParticleLibrary
 （贴图解析要用它），而不是让 ParticleLibrary 再扫一遍。

 author : Axis-qc (fork of ross-g/io_pdx_mesh 0.91.2)
"""

import logging
import os
import time

from .parser import ENTITY_KEYS, SYSTEM_KEYS, ParticleLibrary, PdxParticleSystem, parse_file

KIND_MODEL = "model"
KIND_PARTICLE = "particle"
KIND_ENTITY = "entity"

ALL_KINDS = (KIND_MODEL, KIND_PARTICLE, KIND_ENTITY)

_SKIP_DIRS = {"__pycache__", ".git", ".svn"}
_MESH_EXT = ".mesh"
_GFX_EXT = ".gfx"
_ASSET_EXT = ".asset"

INDEX_LOG = logging.getLogger("io_pdx.particles.index")


def _note_skip(skipped, root, reason):
    """记下一个被跳过的根与原因：日志一条，同时留给界面显示。"""
    INDEX_LOG.warning("asset root skipped: %s (%s)", root, reason)
    if skipped is not None:
        skipped.append({"root": root, "reason": reason})


def _norm(path):
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _rel_under(base, path):
    """path 相对 base 的路径（正斜杠）；不在 base 下则返回 ""。"""
    try:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(base))
    except ValueError:
        return ""
    if rel.startswith(".."):
        return ""
    return rel.replace("\\", "/")


def _category_of(root, file_path):
    """文件所在目录（相对 gfx/）作为分类名。"""
    gfx = os.path.join(root, "gfx")
    rel = _rel_under(gfx, os.path.dirname(os.path.abspath(file_path)))
    return rel if rel and rel != "." else "(gfx root)"


def _iter_gfx_files(root, skipped=None):
    """递归列出 root/gfx 下的 .mesh / .gfx / .asset。"""
    gfx = os.path.join(root, "gfx")
    if not os.path.isdir(gfx):
        _note_skip(skipped, root, "no gfx folder")
        return
    n = 0
    for dirpath, dirnames, filenames in os.walk(gfx):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for fn in sorted(filenames):
            ext = os.path.splitext(fn)[1].lower()
            if ext in (_MESH_EXT, _GFX_EXT, _ASSET_EXT):
                n += 1
                yield os.path.join(dirpath, fn), ext
    if not n:
        _note_skip(skipped, root, "no indexed files under gfx")


def _text_of(node, key):
    """取子节点标量文本；去引号。"""
    c = node.child(key) if node is not None else None
    if c is None or c.value is None:
        return ""
    return str(c.value).strip().strip('"')


class IndexEntry(object):
    """一条可搜索、可加载的条目。

    name     展示名（模型用文件名主干，粒子用注册名，实体用 entity 名）
    keys     全部可搜索关键字（小写）
    path     加载入口：模型是 .mesh，粒子是注册来源 .gfx，实体是 .asset
    category 分类（相对 gfx/ 的目录）
    """

    __slots__ = ("kind", "name", "keys", "path", "category", "root", "is_vanilla", "detail")

    def __init__(self, kind, name, keys, path, category, root, is_vanilla, detail):
        self.kind = kind
        self.name = name
        self.keys = tuple(keys)
        self.path = path
        self.category = category
        self.root = root
        self.is_vanilla = is_vanilla
        self.detail = detail or {}

    def matches(self, query):
        if not query:
            return True
        q = query.lower()
        return any(q in k for k in self.keys)

    def __repr__(self):
        return "IndexEntry({0}, {1!r}, {2!r})".format(self.kind, self.name, self.category)


class PDXIndex(object):
    """一次扫描建三类索引。roots 前面的优先（mod 放前面）。"""

    def __init__(self, roots, vanilla_root="", hidden_roots=()):
        self.roots = []
        self.skipped = []
        seen = set()
        for r in roots:
            if not r:
                continue
            a = os.path.abspath(r)
            key = _norm(a)
            if not os.path.isdir(a):
                _note_skip(self.skipped, a, "not a folder")
                continue
            if key in seen:
                _note_skip(self.skipped, a, "duplicate of an earlier root")
                continue
            seen.add(key)
            self.roots.append(a)
        self.vanilla_root = _norm(vanilla_root) if vanilla_root else ""
        # 隐藏根：参与索引与贴图解析，但其条目默认不进列表（query 滤掉）。
        self.hidden_roots = {_norm(os.path.abspath(r))
                             for r in hidden_roots if r and os.path.isdir(r)}
        self.entries = []
        self.stats = {}
        self.built_at = 0.0
        # 贴图解析与粒子本体都由它承载
        self.library = ParticleLibrary(self.roots)
        # 注册名 → 本体名。entity 事件里写的是 .gfx 的注册名，两者常不同
        # （plasma_cannon_medium_hit_effect → plasma_cannon_hit_effect），
        # 按本体名直接查会漏掉。
        self.reg_to_body = {}
        # ---- 骨骼动画链（三层，缺一层都到不了文件）----
        # pdxmesh 名 → {动画 id: 动画注册名}
        # .gfx 里写 animation = { id = "survey" type = "survey_base_effect_animation" }，
        # entity 的 state.animation 用的正是这个 id，不是动画名。
        self.pdxmesh_anims = {}
        # 动画注册名 → .anim 绝对路径（.asset 里 animation = { name, file }，
        # file 相对该 .asset 所在目录）
        self.anim_defs = {}
        # ---- 贴图覆盖层 ----
        # pdxmesh 名 → {shape 名: {"diffuse":…, "normal":…, "specular":…}}
        # .mesh 内嵌材质的贴图名不可信（technosphere.mesh 写的
        # guardians_02_technosphere_* 全库不存在，官方靠 .gfx 的 meshsettings
        # 覆盖成正确名），引擎以 gfx 为准，这里照抄该语义。
        self.mesh_tex_overrides = {}

    # ---------------------------------------------------------------- build

    def build(self):
        t0 = time.time()
        mesh_files = []          # (path, root, is_vanilla)
        pdxmesh_defs = {}        # name -> (abs mesh path, root, is_vanilla)
        regs = []                # (name, type, scale, source_path, root, is_vanilla)
        entities = []            # (name, pdxmesh, particles, source_path, root, is_vanilla)
        n_files = 0

        for root in self.roots:
            is_vanilla = self._is_vanilla(root)
            for path, ext in _iter_gfx_files(root, self.skipped):
                n_files += 1
                if ext == _MESH_EXT:
                    mesh_files.append((path, root, is_vanilla))
                elif ext == _GFX_EXT:
                    self._scan_gfx(path, root, is_vanilla, pdxmesh_defs, regs)
                else:
                    self._scan_asset(path, root, is_vanilla, entities)

        self._build_models(mesh_files, pdxmesh_defs)
        self._build_particles(regs)
        self._build_entities(entities, pdxmesh_defs)

        self.built_at = time.time()
        self.stats = {
            "roots": len(self.roots),
            "files": n_files,
            "model": sum(1 for e in self.entries if e.kind == KIND_MODEL),
            "particle": sum(1 for e in self.entries if e.kind == KIND_PARTICLE),
            "entity": sum(1 for e in self.entries if e.kind == KIND_ENTITY),
            "seconds": round(self.built_at - t0, 2),
        }
        return self

    def _is_vanilla(self, root):
        if not self.vanilla_root:
            return False
        return _norm(root) == self.vanilla_root

    def _is_hidden(self, root):
        return _norm(root) in self.hidden_roots

    def _fallback_roots(self, root):
        """pdxmesh file 跨根回退顺序：vanilla 根优先，其余根殿后，跳过本根。"""
        out = []
        for r in ([self.vanilla_root] if self.vanilla_root else []):
            if r and _norm(r) != _norm(root) and os.path.isdir(r):
                out.append(r)
        for r in self.roots:
            if _norm(r) != _norm(root) and _norm(r) not in {_norm(x) for x in out}:
                out.append(r)
        return out

    def _scan_gfx(self, path, root, is_vanilla, pdxmesh_defs, regs):
        try:
            tops = parse_file(path)
        except Exception:  # noqa: BLE001
            return
        for top in tops:
            for blk in top.child_values("pdxmesh"):
                name = _text_of(blk, "name")
                rel = _text_of(blk, "file")
                if name and rel:
                    # file 相对「定义所在的根」拼路径，但 mod 常只定义引用、
                    # mesh 文件本体在原版（如 lev_drake_mesh 指向原版巨龙 mesh）。
                    # 本根没有就按 vanilla_root → 其余根的顺序回退找同一相对路径，
                    # 找不到才放弃；否则模型与挂点全部加载失败。
                    rel_local = rel.replace("\\", os.sep)
                    abs_mesh = os.path.normpath(os.path.join(root, rel_local))
                    if not os.path.isfile(abs_mesh):
                        for alt in self._fallback_roots(root):
                            cand = os.path.normpath(os.path.join(alt, rel_local))
                            if os.path.isfile(cand):
                                abs_mesh = cand
                                break
                    pdxmesh_defs.setdefault(name, (abs_mesh, root, is_vanilla))
                # meshsettings = { name = "<shape 名>" texture_diffuse/normal/specular }：
                # 对 mesh 内嵌材质的贴图名覆盖层，同一条 pdxmesh 可有多条（按 shape 名）。
                if name:
                    for ms in blk.child_values("meshsettings"):
                        shape = _text_of(ms, "name")
                        if not shape:
                            continue
                        entry = self.mesh_tex_overrides.setdefault(name, {}).setdefault(shape, {})
                        for key, dst in (("texture_diffuse", "diffuse"),
                                         ("texture_normal", "normal"),
                                         ("texture_specular", "specular")):
                            v = _text_of(ms, key)
                            if v:
                                entry[dst] = v
                # animation = { id = "...", type = "..." }：id 是 entity state 里
                # 用的状态别名，type 才指向 .asset 的动画注册名。同一个 pdxmesh 可
                # 有多条（实测 3087 条分布在 1150 个 pdxmesh 上）。
                for anim in blk.child_values("animation"):
                    aid = _text_of(anim, "id")
                    atype = _text_of(anim, "type")
                    if name and aid and atype:
                        self.pdxmesh_anims.setdefault(name, {}).setdefault(aid, atype)
            for reg in top.child_values("pdxparticle"):
                name = _text_of(reg, "name")
                if not name:
                    continue
                typ = _text_of(reg, "type")
                if typ:
                    self.reg_to_body.setdefault(name, typ)
                scale_node = reg.child("scale")
                try:
                    scale = (float(scale_node.value)
                             if scale_node is not None and scale_node.value is not None else 1.0)
                except (TypeError, ValueError):
                    scale = 1.0
                regs.append((name, typ, scale, path, root, is_vanilla))

    def _scan_asset(self, path, root, is_vanilla, entities):
        try:
            tops = parse_file(path)
        except Exception:  # noqa: BLE001
            return
        for top in tops:
            # animation 这个键含义重载，只能按字段判：
            #   .asset 的 animation = { name, file }    → 骨骼动画注册（2469 处）
            #   .gfx   的 animation = { id, type }      → pdxmesh 上的 id 映射（3087 处）
            #   state = { animation = "survey" }        → 叶子标量（6346 处）
            #   particle .asset 的 animation = { name, start, duration, curve, ... }
            #                                           → 粒子通道动画，与骨骼无关
            # 前三者互斥（重叠 0）；第四类带 name 但**没有 file**，所以要求
            # (name, file) 同时存在即可把它排除，不会误收成骨骼动画注册。
            if top.key == "animation":
                nm = _text_of(top, "name")
                rel = _text_of(top, "file")
                if nm and rel:
                    self.anim_defs.setdefault(nm, os.path.normpath(
                        os.path.join(os.path.dirname(path), rel.replace("\\", os.sep))))
            if top.key in SYSTEM_KEYS:
                try:
                    system = PdxParticleSystem(top, path)
                except Exception:  # noqa: BLE001
                    continue
                if system.name:
                    # 同名后扫到的覆盖先扫到的；roots 已把 mod 排在前面，
                    # 这里再按「已存在就不覆盖」保证 mod 优先。
                    self.library.systems.setdefault(system.name, system)
            if top.key in ENTITY_KEYS:
                name = _text_of(top, "name")
                if not name:
                    continue
                # 记 (粒子名, 挂点名, 起播延迟)：挂点是粒子该出现的位置，
                # 只留粒子名会让加载时无从定位。
                particles = []
                for state in top.child_values("state"):
                    for ev in state.child_values("event") + state.child_values("start_event"):
                        p = _text_of(ev, "particle")
                        if p:
                            t = ev.child("time")
                            try:
                                when = float(t.value) if t is not None and t.value is not None else 0.0
                            except (TypeError, ValueError):
                                when = 0.0
                            particles.append({"particle": p, "node": _text_of(ev, "node"),
                                              "time": when})
                # 各 state 的骨骼动画。值是 pdxmesh 上的动画 id（不是动画名），
                # 要经 pdxmesh_anims 换注册名再换文件；实体的动画与粒子可以同时
                # 存在（实测 1300 个 entity 两者都有），也可能只有动画（1526 个）。
                anims = []
                for state in top.child_values("state"):
                    aid = _text_of(state, "animation")
                    if not aid:
                        continue
                    spd = state.child("animation_speed")
                    try:
                        speed = (float(spd.value)
                                 if spd is not None and spd.value is not None else 1.0)
                    except (TypeError, ValueError):
                        speed = 1.0
                    anims.append({"state": _text_of(state, "name"), "id": aid,
                                  "speed": speed})
                # 实体自带的挂点（实测 8429 个 entity 里 2160 个有，占 25.6%）：
                # 这些点不在 .mesh 里，只导入模型不会建出它们，必须自己解析。
                locators = []
                for loc in top.child_values("locator"):
                    lname = _text_of(loc, "name")
                    if not lname:
                        continue
                    pos = loc.child("position")
                    vals = list(getattr(pos, "positional", []) or []) if pos is not None else []
                    try:
                        xyz = [float(v) for v in vals[:3]] if len(vals) >= 3 else [0.0, 0.0, 0.0]
                    except (TypeError, ValueError):
                        xyz = [0.0, 0.0, 0.0]
                    locators.append({"name": lname, "position": xyz})
                simple = [p["particle"] for p in particles]
                entities.append((name, _text_of(top, "pdxmesh"), particles, simple,
                                 locators, anims, path, root, is_vanilla))

    # ------------------------------------------------------------- entries

    def _build_models(self, mesh_files, pdxmesh_defs):
        # 反查：mesh 绝对路径 → 指向它的 pdxmesh 名
        by_mesh = {}
        for name, (abs_mesh, _root, _v) in pdxmesh_defs.items():
            by_mesh.setdefault(_norm(abs_mesh), []).append(name)

        seen = set()
        for path, root, is_vanilla in mesh_files:
            key = _norm(path)
            if key in seen:
                continue
            seen.add(key)
            stem = os.path.splitext(os.path.basename(path))[0]
            pdxnames = sorted(by_mesh.get(key, []))
            keys = [stem.lower()] + [n.lower() for n in pdxnames]
            # 贴图覆盖层跟随 pdxmesh 名；一个 mesh 被多条 pdxmesh 引用时合并
            # （后面的覆盖先面的，与引擎后扫描覆盖同序）。
            merged_ov = {}
            for name in pdxnames:
                merged_ov.update(self.mesh_tex_overrides.get(name) or {})
            # 这个模型能被哪些动画驱动：把每个 pdxmesh 名上的 id 映射摊平。
            self.entries.append(IndexEntry(
                KIND_MODEL, stem, keys, path, _category_of(root, path),
                root, is_vanilla, {"pdxmesh": pdxnames,
                                   "tex_overrides": merged_ov,
                                   "animations": self._anims_of(pdxnames)}))

    def _anims_of(self, pdxmesh_names):
        """pdxmesh 名列表 → [{id, type, file}]（同一动画去重）。

        实测 2968 个 .mesh 里 974 个带动画映射；id 是 state 用的别名，
        type 才指向 .asset 注册名，file 是最终 .anim（解析不出就留空）。
        """
        out = []
        seen = set()
        for name in pdxmesh_names:
            for aid, atype in (self.pdxmesh_anims.get(name) or {}).items():
                key = (aid, atype)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"id": aid, "type": atype,
                            "file": self.anim_file(atype)})
        return out

    def anim_file(self, anim_type):
        """动画注册名 → .anim 绝对路径；未注册或文件不存在返回 ""。

        文件不存在也当解析失败：注册指向的 .anim 实测 2457/2457 都在，
        真缺了说明是坏数据，让界面显示空比给个点不开的路径好。
        """
        p = self.anim_defs.get(anim_type) or ""
        return p if p and os.path.isfile(p) else ""

    def _build_particles(self, regs):
        # 注册项的 type 指向本体；本体分类优先（更贴近用户找的位置）
        body_cat = {}
        for name, system in self.library.systems.items():
            src_root = _root_of(system.source_file, self.roots)
            if not src_root:
                _note_skip(self.skipped, system.source_file, "outside every indexed root")
            body_cat[name] = _category_of(src_root, system.source_file)
        for name, typ, scale, src, root, is_vanilla in regs:
            has_body = typ in self.library.systems
            category = body_cat.get(typ) or _category_of(root, src)
            keys = [name.lower()]
            if typ:
                keys.append(typ.lower())
            self.entries.append(IndexEntry(
                KIND_PARTICLE, name, keys, src, category, root, is_vanilla,
                {"type": typ, "scale": scale, "resolved": has_body}))

    def _build_entities(self, entities, pdxmesh_defs):
        for (name, pdxmesh, particles, simple, locators, state_anims,
             src, root, is_vanilla) in entities:
            mesh = ""
            if pdxmesh:
                hit = pdxmesh_defs.get(pdxmesh)
                if hit:
                    mesh = hit[0]
            # state.animation 存的是 pdxmesh 上的动画 id，经该 pdxmesh 的映射换
            # 成动画注册名，再经注册表换成文件。entity 与它的 pdxmesh 一一对应，
            # 所以只用自己那个 pdxmesh 的映射（实测 6346 条 state 动画全部命中，
            # 不会误取到别的模型的同名 id）。
            table = self.pdxmesh_anims.get(pdxmesh) or {}
            anims = []
            for sa in state_anims:
                atype = table.get(sa["id"], "")
                anims.append({"state": sa["state"], "id": sa["id"],
                              "speed": sa["speed"], "type": atype,
                              "file": self.anim_file(atype) if atype else ""})
            keys = [name.lower()]
            if pdxmesh:
                keys.append(pdxmesh.lower())
            keys.extend(p.lower() for p in simple)
            keys.extend(a["id"].lower() for a in anims)
            self.entries.append(IndexEntry(
                KIND_ENTITY, name, keys, src, _category_of(root, src),
                root, is_vanilla,
                {"pdxmesh": pdxmesh, "mesh": mesh,
                 "tex_overrides": dict(self.mesh_tex_overrides.get(pdxmesh) or {}),
                 "particles": simple,
                 "events": particles, "locators": locators,
                 "animations": anims,
                 "has_particle": bool(simple), "has_anim": bool(anims)}))

    # -------------------------------------------------------------- query

    def categories(self, kind):
        """该类的分类名 → 条目数，按名称排序。"""
        counts = {}
        for e in self.entries:
            if e.kind == kind:
                counts[e.category] = counts.get(e.category, 0) + 1
        return dict(sorted(counts.items()))

    def system_for_registration(self, name):
        """注册名或本体名 → 粒子本体（PdxParticleSystem），取不到返回 None。

        entity 事件的 particle 字段写的是注册名，必须经 .gfx 注册表换算成本体名；
        有的 mod 直接把本体名当注册名，所以两种都认。
        """
        system = self.library.systems.get(name)
        if system is not None:
            return system
        body = self.reg_to_body.get(name)
        if body:
            return self.library.systems.get(body)
        return None

    def query(self, kind, text="", disabled=(), mod_only=False, require_particle=False,
              require_content=False):
        """按分类禁用、来源与关键字过滤。disabled 为分类名集合。

        require_content：只要「有粒子或有动画」的条目（Entity 页用）。与
        require_particle 分开是因为 entity 里 1526 个只有骨骼动画、没有粒子，
        只按粒子筛会把它们整片挡在列表外。
        """
        out = []
        for e in self.entries:
            if e.kind != kind:
                continue
            if disabled and e.category in disabled:
                continue
            if mod_only and e.is_vanilla:
                continue
            # 隐藏根：条目仍在索引里（贴图解析、动画表共用），只是不列出
            if self._is_hidden(e.root):
                continue
            if require_particle and not e.detail.get("has_particle", False):
                continue
            if require_content:
                d = e.detail
                if not (d.get("has_particle") or d.get("has_anim")):
                    continue
            if not e.matches(text):
                continue
            out.append(e)
        return out


def _root_of(file_path, roots):
    """文件属于哪个已索引根（取最长匹配，兼容嵌套）；不属于任何根时返回 ""。"""
    best = ""
    target = _norm(file_path)
    for r in roots:
        ra = os.path.abspath(r)
        if target.startswith(_norm(ra) + os.sep) and len(ra) > len(best):
            best = ra
    if not best and roots:
        INDEX_LOG.warning("file outside every indexed root: %s", file_path)
    return best
