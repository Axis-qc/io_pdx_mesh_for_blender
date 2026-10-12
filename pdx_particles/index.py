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
    return rel if rel else "(gfx root)"


def _iter_gfx_files(root):
    """递归列出 root/gfx 下的 .mesh / .gfx / .asset。"""
    gfx = os.path.join(root, "gfx")
    if not os.path.isdir(gfx):
        return
    for dirpath, dirnames, filenames in os.walk(gfx):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for fn in sorted(filenames):
            ext = os.path.splitext(fn)[1].lower()
            if ext in (_MESH_EXT, _GFX_EXT, _ASSET_EXT):
                yield os.path.join(dirpath, fn), ext


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

    def __init__(self, roots, vanilla_root=""):
        self.roots = []
        for r in roots:
            if not r:
                continue
            a = os.path.abspath(r)
            if os.path.isdir(a) and a not in self.roots:
                self.roots.append(a)
        self.vanilla_root = _norm(vanilla_root) if vanilla_root else ""
        self.entries = []
        self.stats = {}
        self.built_at = 0.0
        # 贴图解析与粒子本体都由它承载
        self.library = ParticleLibrary(self.roots)
        # 注册名 → 本体名。entity 事件里写的是 .gfx 的注册名，两者常不同
        # （plasma_cannon_medium_hit_effect → plasma_cannon_hit_effect），
        # 按本体名直接查会漏掉。
        self.reg_to_body = {}

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
            for path, ext in _iter_gfx_files(root):
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
                    abs_mesh = os.path.normpath(os.path.join(root, rel.replace("\\", os.sep)))
                    pdxmesh_defs.setdefault(name, (abs_mesh, root, is_vanilla))
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
                # entity 自带的挂点（实测 8429 个 entity 里 2160 个有，占 25.6%）：
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
                                 locators, path, root, is_vanilla))

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
            self.entries.append(IndexEntry(
                KIND_MODEL, stem, keys, path, _category_of(root, path),
                root, is_vanilla, {"pdxmesh": pdxnames}))

    def _build_particles(self, regs):
        # 注册项的 type 指向本体；本体分类优先（更贴近用户找的位置）
        body_cat = {}
        for name, system in self.library.systems.items():
            body_cat[name] = _category_of(_root_of(system.source_file, self.roots),
                                          system.source_file)
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
        for name, pdxmesh, particles, simple, locators, src, root, is_vanilla in entities:
            mesh = ""
            if pdxmesh:
                hit = pdxmesh_defs.get(pdxmesh)
                if hit:
                    mesh = hit[0]
            keys = [name.lower()]
            if pdxmesh:
                keys.append(pdxmesh.lower())
            keys.extend(p.lower() for p in simple)
            self.entries.append(IndexEntry(
                KIND_ENTITY, name, keys, src, _category_of(root, src),
                root, is_vanilla,
                {"pdxmesh": pdxmesh, "mesh": mesh, "particles": simple,
                 "events": particles, "locators": locators,
                 "has_particle": bool(simple)}))

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

    def query(self, kind, text="", disabled=(), mod_only=False, require_particle=False):
        """按分类禁用、来源与关键字过滤。disabled 为分类名集合。"""
        out = []
        for e in self.entries:
            if e.kind != kind:
                continue
            if disabled and e.category in disabled:
                continue
            if mod_only and e.is_vanilla:
                continue
            if require_particle and not e.detail.get("has_particle", True):
                continue
            if not e.matches(text):
                continue
            out.append(e)
        return out


def _root_of(file_path, roots):
    """文件属于哪个已索引根（取最长匹配，兼容嵌套）。"""
    best = ""
    target = os.path.abspath(file_path)
    for r in roots:
        ra = os.path.abspath(r)
        if target.startswith(ra + os.sep) and len(ra) > len(best):
            best = ra
    return best or (roots[0] if roots else "")
