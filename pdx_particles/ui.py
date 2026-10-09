"""
PDX particle preview UI: import operator, sidebar panel, frame handler.

 工作流：扫描根目录（可多个，原版 + 各 mod）→ 列出所有 pdxparticle 注册项，
 按注册名搜索过滤 → 选中即预览；也可直接从文件装载（.gfx / .asset）。
 拖时间轴或播放即预览；改 .asset 后点「重载」秒看效果。

 author : Axis-qc (fork of ross-g/io_pdx_mesh 0.91.2)
"""

import math
import os

import bpy  # type: ignore
from bpy.app.handlers import persistent  # type: ignore
from bpy.props import (  # type: ignore
    BoolProperty,
    CollectionProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup, UIList  # type: ignore

from .. import IO_PDX_LOG, IO_PDX_SETTINGS
from .parser import (
    ParticleLibrary,
    build_catalog,
    detect_game_roots,
    looks_like_game_root,
    parse_entity_events,
    parse_gfx_registrations,
)
from .simulator import PARTICLE_FPS
from .blender_preview import PreviewScene

# 活动预览列表（模块级；跨 .blend 不持久化，重开文件后重新导入即可）
_ACTIVE = []


def _roots():
    """场景里配置的扫描根（按顺序，前面的优先）。只驱动目录扫描与列表。"""
    out = []
    for scene in bpy.data.scenes:
        for entry in getattr(scene, "io_pdx_particle_roots", []):
            p = bpy.path.abspath(entry.path) if entry.path else ""
            if p and os.path.isdir(p) and p not in out:
                out.append(p)
    return out


def _vanilla_root():
    """原版（游戏）根目录：只用于回退解析贴图，不参与扫描列表。

    mod 的粒子经常引用原版贴图（实测 stellaris_godie 的 br_star_explosion 引用的
    6 张贴图全部只在原版里）。只加 mod 目录时这些贴图找不到，粒子会没纹理。

    存 IO_PDX_SETTINGS（settings.json），跨 .blend 保留；默认留空——本机路径
    不能写死进要分发的插件，改为让用户指定或按 Detect 自动查 Steam。
    注意场景属性 io_pdx_particle_vanilla_root 仍然优先，兼容旧值。
    """
    for scene in bpy.data.scenes:
        v = getattr(scene, "io_pdx_particle_vanilla_root", "")
        if v:
            p = bpy.path.abspath(v)
            if os.path.isdir(p):
                return p
    v = getattr(IO_PDX_SETTINGS, "particle_vanilla_root", "") or ""
    if v:
        p = bpy.path.abspath(v)
        if os.path.isdir(p):
            return p
    return ""


def _set_vanilla_root(path):
    """写入原版根（settings.json，跨文件保留）。"""
    IO_PDX_SETTINGS.particle_vanilla_root = path or ""
    for scene in bpy.data.scenes:
        if hasattr(scene, "io_pdx_particle_vanilla_root"):
            try:
                scene.io_pdx_particle_vanilla_root = ""
            except Exception:  # noqa: BLE001
                pass


def _texture_roots():
    """贴图查找路径 = 扫描根 + 原版根（去重，扫描根优先）。"""
    out = list(_roots())
    v = _vanilla_root()
    if v and v not in out:
        out.append(v)
    return out


def _mod_root(source_file):
    """从源文件向上找含 gfx 的目录。"""
    p = os.path.dirname(os.path.abspath(source_file))
    for _ in range(4):
        if os.path.isdir(os.path.join(p, "gfx")):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return ""


def _clear_previews():
    for preview in list(_ACTIVE):
        try:
            preview.destroy()
        except Exception as err:  # noqa: BLE001
            IO_PDX_LOG.error("particle preview destroy failed: %s", err)
    _ACTIVE.clear()


@persistent
def _frame_handler(scene, depsgraph=None):
    """每帧驱动粒子模拟。

    必须加 @persistent：Blender 在加载 .blend 文件时会清空所有非 persistent 的
    处理器（官方文档 bpy.app.handlers.persistent 条目）。没有它，用户每打开一次
    工程文件处理器就消失，表现为「粒子又不动了」——而模块重载导致的旧对象残留
    是另一回事，两者都要处理，只按函数名匹配救不了这一条。
    """
    if not _ACTIVE:
        return
    for preview in list(_ACTIVE):
        try:
            preview.refresh()
        except ReferenceError:
            _ACTIVE.remove(preview)
        except Exception as err:  # noqa: BLE001
            IO_PDX_LOG.error("particle refresh failed: %s", err)


# ----------------------------------------------------------------------------------------------------------------------
# Property group（每行一个已装载系统）
# ----------------------------------------------------------------------------------------------------------------------

class ParticlePreviewEntry(PropertyGroup):
    collection: StringProperty()  # noqa: F821

    def _visible_update(self, context):
        for preview in list(_ACTIVE):
            if preview.collection is not None and preview.collection.name == self.collection:
                try:
                    preview.collection.hide_viewport = not self.visible
                    preview.collection.hide_render = not self.visible
                except ReferenceError:
                    pass
                return

    visible: BoolProperty(default=True, update=_visible_update)  # noqa: F821


# ----------------------------------------------------------------------------------------------------------------------
# Operators
# ----------------------------------------------------------------------------------------------------------------------

def _load_systems(path):
    """解析文件 → (loaded[(system, offset)], missing[])。"""
    roots = [r for r in (_mod_root(path),) if r] + _texture_roots()
    seen = []
    for r in roots:
        if r not in seen:
            seen.append(r)
    library = ParticleLibrary(seen)
    library.scan()

    loaded, missing = [], []
    ext = os.path.splitext(path)[1].lower()
    if ext == ".gfx":
        for reg in parse_gfx_registrations(path):
            system = library.systems.get(reg["type"])
            if system is None:
                missing.append(reg["type"])
                continue
            loaded.append((system, 0.0))
    else:
        from .parser import SYSTEM_KEYS, PdxParticleSystem, parse_file
        file_systems = []
        try:
            for top in parse_file(path):
                if top.key in SYSTEM_KEYS:
                    file_systems.append(PdxParticleSystem(top, path))
        except Exception as err:  # noqa: BLE001
            IO_PDX_LOG.error("particle parse failed: %s (%s)", path, err)
        if file_systems:
            # 直接以该文件为准（比 library 缓存新）
            for system in file_systems:
                loaded.append((system, 0.0))
        else:
            for entry in parse_entity_events(path):
                for ev in entry["events"]:
                    system = library.systems.get(ev["particle"])
                    if system is None:
                        missing.append(ev["particle"])
                        continue
                    loaded.append((system, ev["time"]))
    return loaded, missing, library


def _import_file(context, path):
    """装载一个源文件的预览。成功返回 True。"""
    loaded, missing, library = _load_systems(path)
    if not loaded:
        IO_PDX_LOG.error("no particle systems found in %s (missing: %s)", path, ", ".join(missing) or "-")
        return False
    return _import_loaded(context, loaded, missing, library)


def _import_loaded(context, loaded, missing, library):
    """已解析好的 (system, offset) 列表 → 建预览、选帧、出画面。"""
    scene = context.scene
    # 帧与秒的换算用固定基准 PARTICLE_FPS（60）：粒子数据里的时间量都是秒，
    # 场景 render.fps 只是显示属性，不能拿它换算（否则同一份粒子在不同 fps
    # 的场景里速度不同，低发射速率的粒子还会因为时间轴过短而永远不出现）。
    fps = PARTICLE_FPS

    max_end = 0.0
    total_n = 0
    for system, offset in loaded:
        try:
            preview = PreviewScene(system, library, time_offset=offset)
        except Exception as err:  # noqa: BLE001
            IO_PDX_LOG.error("particle preview build failed: %s (%s)", system.name, err)
            missing.append(system.name)
            continue
        _ACTIVE.append(preview)
        entry = scene.io_pdx_particle_entries.add()
        entry.name = system.name
        entry.collection = preview.collection.name
        max_end = max(max_end, preview.sim.total_duration() + offset)

    if not _ACTIVE:
        return False

    scene.frame_start = 1
    scene.frame_end = max(2, 1 + int(math.ceil(max_end * fps)))
    if missing:
        IO_PDX_LOG.warning("particle import missing: %s", ", ".join(sorted(set(missing))))

    # 网格建出来时所有顶点都塌在原点（零面积），要靠 refresh 才展开。导入本身不动
    # 帧号，帧处理器不会触发，所以这里必须主动刷。选帧不能瞎选：发射有 start 与累计
    # 过程，第 2 帧常常一颗粒子都还没有，停在那种帧上看到的就是一个点。
    best_frame, total_n = _pick_frame(list(_ACTIVE), fps, max_end)
    best_frame = max(scene.frame_start, min(best_frame, scene.frame_end))

    scene.frame_set(best_frame)
    for preview in list(_ACTIVE):
        try:
            preview.refresh()
        except Exception as err:  # noqa: BLE001
            IO_PDX_LOG.error("particle initial refresh failed: %s", err)

    if total_n <= 0:
        IO_PDX_LOG.warning(
            "particle preview: no particles at frame %s; scrub the timeline to see them",
            best_frame)
    return True


class IOPDX_OT_import_particle(Operator):
    """Load PDX particle preview (.gfx registration / .asset particle system / entity .asset)"""
    bl_idname = "io_pdx_particles.import"
    bl_label = "Load particles ..."
    bl_description = "Load PDX particles (.gfx/.asset) and build a scrub-able preview"
    bl_options = {"REGISTER", "UNDO"}

    filter_glob: StringProperty(default="*.gfx;*.asset", options={"HIDDEN"})  # noqa: F821
    filepath: StringProperty(subtype="FILE_PATH")  # noqa: F821

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        path = bpy.path.abspath(self.filepath)
        if not os.path.isfile(path):
            self.report({"ERROR"}, "File not found: {0}".format(path))
            return {"CANCELLED"}

        scene = context.scene
        scene.io_pdx_particle_entries.clear()
        _clear_previews()
        ok = _import_file(context, path)
        if not ok:
            self.report({"ERROR"}, "No particle systems found in {0}".format(os.path.basename(path)))
            return {"CANCELLED"}
        self.report({"INFO"}, "Loaded {0} particle system(s)".format(len(_ACTIVE)))
        return {"FINISHED"}


class IOPDX_OT_particle_reload(Operator):
    """Reload and rebuild all loaded particle previews from their source files"""
    bl_idname = "io_pdx_particles.reload"
    bl_label = "Reload from source"
    bl_description = "Re-parse the source files and rebuild previews (调参后刷新)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if not _ACTIVE:
            self.report({"WARNING"}, "No particle previews loaded")
            return {"CANCELLED"}
        files = list(dict.fromkeys(p.system.source_file for p in _ACTIVE if p.system.source_file))
        _clear_previews()
        context.scene.io_pdx_particle_entries.clear()
        ok = 0
        for f in files:
            res = _import_file(context, f)
            if res:
                ok += 1
        self.report({"INFO"}, "Reloaded {0}/{1} file(s)".format(ok, len(files)))
        return {"FINISHED"}


class IOPDX_OT_particle_clear(Operator):
    """Remove all particle preview objects and collections"""
    bl_idname = "io_pdx_particles.clear"
    bl_label = "Clear previews"
    bl_description = "Delete all particle preview objects/collections"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        n = len(_ACTIVE)
        _clear_previews()
        context.scene.io_pdx_particle_entries.clear()
        self.report({"INFO"}, "Removed {0} preview(s)".format(n))
        return {"FINISHED"}


class IOPDX_OT_particle_ref_sphere(Operator):
    """Show the reference sphere (planet-size wireframe, r=30)"""
    bl_idname = "io_pdx_particles.ref_sphere"
    bl_label = "Reference sphere"
    bl_description = "Show wireframe sphere at planet scale (r=30 units) for size reference"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        for preview in _ACTIVE:
            preview.show_ref_sphere(True)
        return {"FINISHED"}


class IOPDX_OT_particle_ref_sphere_hide(Operator):
    """Hide the reference sphere"""
    bl_idname = "io_pdx_particles.ref_sphere_hide"
    bl_label = "Reference sphere"
    bl_description = "Hide the reference sphere"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        for preview in _ACTIVE:
            preview.show_ref_sphere(False)
        return {"FINISHED"}


# ----------------------------------------------------------------------------------------------------------------------
# 目录扫描与注册项列表
# ----------------------------------------------------------------------------------------------------------------------

# 扫描结果（模块级；UIList 只显示过滤后的子集，全量留在这里）
_CATALOG = []
_LIBRARY = None
MAX_LIST_ROWS = 20000   # 近万条填充实测 <10ms，留个上限只为防病态数据

# 选帧时的模拟采样次数上限：整条时间轴按步长抽样，避免秒数很长时逐帧推进。
SAMPLE_BUDGET = 2400
# 复核候选帧的次数上限（每次要从 reset 直跳到目标帧，在上千粒子的系统上很贵，
# 实测 2400 粒子跑一次约 0.7s）。估算已经相当准，通常第一个候选就命中。
REPLAY_TRIES = 3


def _count_now(previews):
    """所有预览当前可见的粒子总数（走 snapshot，含 alpha/size 过滤，准确但贵）。"""
    n = 0
    for pv in previews:
        for s in pv.sim.subs:
            if pv.sim.enabled.get(s.name, True):
                n += len(s.snapshot())
    return n


def _pick_frame(previews, fps, max_end):
    """挑一帧「所有已装载系统加起来粒子最多」的帧，返回 (帧号, 粒子数)。

    网格建出来时顶点全塌在原点，只有 refresh 才展开。导入本身不动帧号，帧处理
    器不会触发，必须主动刷一次。而第 2 帧常常一颗粒子都还没发射（发射有 start
    与累计过程），停在那种帧上看到的就是一个点——所以要按模拟结果选帧。

    两步走，兼顾正确与开销：
    1. 用 estimate_alive（O(1)，不做物理）整条时间轴采样，找出候选帧。原先这里
       真跑物理，在上千粒子的系统上要 18 秒，是导入卡顿的主因。
    2. 对少量候选做一次真正的 reset+直跳复核，取第一个真有粒子的。

    第 2 步不能省：refresh() 是「reset 后一次性跳到目标帧」，与扫描时的推进路径
    可能不同（浮点），在发射阈值附近会给出不同答案。但候选只有几个，成本可控。
    """
    if not previews:
        return 1, 0
    total_frames = max(1, int(max(max_end, 0.0) * max(fps, 1)))
    stride = max(1, total_frames // SAMPLE_BUDGET)

    # 第 1 步：O(1) 估算扫描，纯算术，不碰物理
    samples = []
    for k in range(1, total_frames + 1, stride):
        t = k / max(fps, 1)
        est = 0.0
        for pv in previews:
            for s in pv.sim.subs:
                if pv.sim.enabled.get(s.name, True):
                    est += s.estimate_alive(t)
        samples.append((est, k))

    if not samples:
        samples = [(0.0, 1)]
    best_est = max(s[0] for s in samples)
    if best_est <= 0.0:
        return 1, 0

    # 第 2 步：复核估算最高的几个候选
    ranked = sorted(samples, key=lambda x: -x[0])
    seen_k = set()
    tries = 0
    for _, k in ranked:
        if k in seen_k:
            continue
        seen_k.add(k)
        tries += 1
        if tries > REPLAY_TRIES:
            break
        t = k / max(fps, 1)
        for pv in previews:
            pv.sim.reset()
            pv.sim.advance(t)
        n = _count_now(previews)
        if n > 0:
            for pv in previews:
                pv.sim.reset()
            return k + 1, n     # 场景 frame_start = 1，故帧号 = 1 + t*fps

    for pv in previews:
        pv.sim.reset()
    return ranked[0][1] + 1, 0


def _rebuild_list(scene):
    """按搜索词把 _CATALOG 过滤进场景列表。"""
    text = (scene.io_pdx_particle_filter or "").strip().lower()
    scene.io_pdx_particle_items.clear()
    shown = 0
    for e in _CATALOG:
        if text and text not in e.name.lower() and text not in (e.type or "").lower():
            continue
        item = scene.io_pdx_particle_items.add()
        item.name = e.name
        item.type = e.type or ""
        item.source = os.path.basename(e.source_file or "")
        item.resolved = e.resolved
        shown += 1
        if shown >= MAX_LIST_ROWS:
            break
    return shown


def _filter_update(self, context):
    _rebuild_list(context.scene)


def _scan_roots(context):
    """按配置的根目录扫描，填充 _CATALOG。返回 (条目数, 解析数)。"""
    global _CATALOG, _LIBRARY
    roots = _roots()
    if not roots:
        _CATALOG, _LIBRARY = [], None
        return 0, 0
    # 索引要连原版根一起建：列表只列扫描根里的注册项，但贴图解析与本体回退
    # 都可能落在原版目录，且 mod 粒子常引用原版贴图。
    _CATALOG, _LIBRARY = build_catalog(roots, roots_for_index=_texture_roots())
    n_res = sum(1 for e in _CATALOG if e.resolved)
    return len(_CATALOG), n_res


def _library_now():
    """当前扫描根 + 原版根对应的索引；根变了就重建。

    典型顺序是先 Scan（此时只加了 mod 目录）再按 Detect 配原版根。缓存的索引里
    没有新根，贴图会一直解析不到，所以这里比对根列表，不一致就重建。
    """
    global _LIBRARY
    roots_now = _texture_roots()
    if _LIBRARY is None or list(_LIBRARY.roots) != roots_now:
        _LIBRARY = ParticleLibrary(roots_now).scan()
    return _LIBRARY


class ParticleRoot(PropertyGroup):
    path: StringProperty(name="Root", subtype="DIR_PATH")  # noqa: F821


class ParticleListItem(PropertyGroup):
    type: StringProperty()      # noqa: F821
    source: StringProperty()    # noqa: F821
    resolved: BoolProperty()    # noqa: F821


class IOPDX_UL_particle_roots(UIList):
    """扫描根目录列表。"""

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop,
                  index, flt_flag):
        layout.label(text=os.path.basename(item.path.rstrip("\\/")) or item.path,
                     icon="FILE_FOLDER")


class IOPDX_UL_particle_catalog(UIList):
    """注册项列表：显示注册名，右侧标出无本体的条目。"""

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop,
                  index, flt_flag):
        row = layout.row(align=True)
        ic = "PARTICLE_POINT" if item.resolved else "ERROR"
        row.label(text=item.name, icon=ic)
        row.label(text=item.source)


class IOPDX_OT_particle_add_root(Operator):
    """Add a directory to scan for particle registrations"""
    bl_idname = "io_pdx_particles.add_root"
    bl_label = "Add folder ..."
    bl_description = "Add a folder (game root or mod root) to scan for particles"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="DIR_PATH")  # noqa: F821

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        p = bpy.path.abspath(self.filepath)
        if not os.path.isdir(p):
            self.report({"ERROR"}, "Not a folder: {0}".format(p))
            return {"CANCELLED"}
        for e in context.scene.io_pdx_particle_roots:
            if bpy.path.abspath(e.path) == p:
                self.report({"INFO"}, "Already in the list")
                return {"CANCELLED"}
        context.scene.io_pdx_particle_roots.add().path = p
        return {"FINISHED"}


class IOPDX_OT_particle_remove_root(Operator):
    """Remove the selected folder from the scan list"""
    bl_idname = "io_pdx_particles.remove_root"
    bl_label = "Remove"
    bl_description = "Remove the selected folder from the scan list"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        roots = scene.io_pdx_particle_roots
        idx = scene.io_pdx_particle_root_index
        if not (0 <= idx < len(roots)):
            self.report({"WARNING"}, "Select a folder first")
            return {"CANCELLED"}
        roots.remove(idx)
        scene.io_pdx_particle_root_index = max(0, idx - 1)
        return {"FINISHED"}


class IOPDX_OT_particle_detect_game(Operator):
    """Find the game install folder automatically (Steam libraries)"""
    bl_idname = "io_pdx_particles.detect_game"
    bl_label = "Detect"
    bl_description = ("Look for Paradox game installs in the Steam libraries and use the "
                      "first one as the texture fallback root")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        roots = detect_game_roots()
        if not roots:
            self.report({"WARNING"},
                        "No game install found — set the folder manually")
            return {"CANCELLED"}
        # detect_game_roots 已把含 gfx/particles 或 gfx/models 的排在前面：各游戏
        # 目录布局不同（Victoria 3 的资源在 game/gfx 下），对粒子无效的要排除掉。
        pick = roots[0]
        _set_vanilla_root(pick)
        if looks_like_game_root(pick):
            self.report({"INFO"}, "Found: {0}".format(pick))
        else:
            self.report({"WARNING"},
                        "Found {0} but it has no gfx/particles or gfx/models — "
                        "pick the right folder manually".format(pick))
        return {"FINISHED"}


class IOPDX_OT_particle_add_vanilla(Operator):
    """Add the detected game folder to the scan list as well"""
    bl_idname = "io_pdx_particles.add_vanilla_to_scan"
    bl_label = "Add game to scan list"
    bl_description = "Also list the vanilla game's own particles (they show up as extra rows)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        root = _vanilla_root()
        if not root:
            self.report({"WARNING"}, "No game folder set — press Detect first")
            return {"CANCELLED"}
        scene = context.scene
        for e in scene.io_pdx_particle_roots:
            if bpy.path.abspath(e.path) == root:
                self.report({"INFO"}, "Already in the scan list")
                return {"CANCELLED"}
        scene.io_pdx_particle_roots.add().path = root
        return {"FINISHED"}


class IOPDX_OT_particle_scan(Operator):
    """Scan all listed folders and list every registered particle"""
    bl_idname = "io_pdx_particles.scan"
    bl_label = "Scan"
    bl_description = "Scan the folders above and list all registered particles"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        total, resolved = _scan_roots(context)
        if total == 0:
            _rebuild_list(scene)
            self.report({"ERROR"}, "Nothing found — add a game or mod folder first")
            return {"CANCELLED"}
        _rebuild_list(scene)
        self.report({"INFO"}, "{0} particles ({1} without a body)".format(
            total, total - resolved))
        return {"FINISHED"}


class IOPDX_OT_particle_preview_item(Operator):
    """Preview the selected particle"""
    bl_idname = "io_pdx_particles.preview_item"
    bl_label = "Preview selected"
    bl_description = "Build a preview for the selected particle"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        idx = scene.io_pdx_particle_index
        if not _CATALOG:
            self.report({"ERROR"}, "Scan first")
            return {"CANCELLED"}
        item = scene.io_pdx_particle_items[idx] if 0 <= idx < len(scene.io_pdx_particle_items) else None
        if item is None:
            self.report({"WARNING"}, "Select a particle in the list")
            return {"CANCELLED"}

        target = None
        for e in _CATALOG:
            if e.name == item.name and os.path.basename(e.source_file) == item.source:
                target = e
                break
        if target is None:
            self.report({"ERROR"}, "Entry not found")
            return {"CANCELLED"}
        if target.system is None:
            self.report({"ERROR"}, "No body found for type '{0}'".format(target.type))
            return {"CANCELLED"}

        scene.io_pdx_particle_entries.clear()
        _clear_previews()
        ok = _import_one(context, target.system, _library_now())
        if not ok:
            self.report({"ERROR"}, "Preview build failed for {0}".format(target.name))
            return {"CANCELLED"}
        self.report({"INFO"}, "Previewing {0}".format(target.name))
        return {"FINISHED"}


def _import_one(context, system, library):
    """装载单个系统的预览（列表选中即预览走这条）。"""
    loaded = [(system, 0.0)]
    return _import_loaded(context, loaded, [], library)


# ----------------------------------------------------------------------------------------------------------------------
# Panel（同一侧栏类别，独立面板，不动 blender_ui.py）
# ----------------------------------------------------------------------------------------------------------------------

class IOPDX_PT_PDXparticles(Panel):
    bl_label = "Particles"
    bl_category = "PDX Blender Tools"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # ---- 扫描根目录 ----
        layout.label(text="Scan folders:", icon="FILE_FOLDER")
        rows = layout.row(align=True)
        rows.template_list("IOPDX_UL_particle_roots", "", scene, "io_pdx_particle_roots",
                           scene, "io_pdx_particle_root_index", rows=3)
        btns = rows.column(align=True)
        btns.operator("io_pdx_particles.add_root", icon="ADD", text="")
        btns.operator("io_pdx_particles.remove_root", icon="REMOVE", text="")
        layout.operator("io_pdx_particles.scan", icon="VIEWZOOM", text="Scan")

        # 原版根：只用于回退找 mod 没自带的贴图。默认空，靠 Detect 或手填，
        # 插件里不写死任何本机路径，装到别人机器上也是干净的。
        box2 = layout.box()
        box2.label(text="Game folder (texture fallback):", icon="FILE_FOLDER")
        row = box2.row(align=True)
        row.prop(scene, "io_pdx_particle_vanilla_root", text="")
        row.operator("io_pdx_particles.detect_game", icon="VIEWZOOM", text="Detect")
        if _vanilla_root():
            box2.operator("io_pdx_particles.add_vanilla_to_scan",
                          icon="ADD", text="Also list game's own particles")
        else:
            box2.label(text="Unset — only mod textures resolve", icon="INFO")

        # ---- 注册项列表 ----
        if _CATALOG:
            layout.prop(scene, "io_pdx_particle_filter", text="", icon="VIEWZOOM")
            layout.label(text="{0} shown / {1} registered".format(
                len(scene.io_pdx_particle_items), len(_CATALOG)))
            layout.template_list("IOPDX_UL_particle_catalog", "", scene,
                                 "io_pdx_particle_items", scene, "io_pdx_particle_index",
                                 rows=6)
            layout.operator("io_pdx_particles.preview_item", icon="PARTICLES",
                            text="Preview selected")
        elif scene.io_pdx_particle_items:
            # 重开 .blend 后模块级扫描结果就没了，但场景里的行还在
            layout.label(text="Stale list — press Scan again", icon="ERROR")
        else:
            layout.label(text="No scan yet — add a folder, then Scan", icon="INFO")

        layout.separator()
        layout.operator("io_pdx_particles.import", icon="FILEBROWSER",
                        text="Load from file ...")

        if not _ACTIVE:
            # 模块级 _ACTIVE 不持久化：重开 .blend、或 Blender 重载脚本后，
            # 场景里可能还留着上次的预览对象，但已经没人驱动它们了。
            if scene.io_pdx_particle_entries:
                layout.label(text="Preview not live — Load or Reload again", icon="ERROR")
            return

        box = layout.box()
        for preview in _ACTIVE:
            row = box.row(align=True)
            entry = scene.io_pdx_particle_entries.get(preview.system.name)
            icon = "HIDE_OFF" if (entry and entry.visible) else "HIDE_ON"
            row.prop(entry, "visible", text="", icon=icon, toggle=True) if entry else None
            try:
                n_sub = len(preview.sim.subs)
                row.label(text="{0}  ({1} emitters)".format(preview.system.name, n_sub),
                          icon="PARTICLE_POINT")
            except ReferenceError:
                row.label(text="(deleted)", icon="ERROR")

        # 当前帧可见粒子数：为 0 时提示拖时间轴，而不是让用户对着一个点发呆
        shown = sum(getattr(p, "last_count", 0) for p in _ACTIVE)
        if shown:
            layout.label(text="{0} particles at frame {1}".format(shown, scene.frame_current),
                         icon="PARTICLE_POINT")
        else:
            layout.label(text="No particles at this frame — scrub the timeline",
                         icon="INFO")

        # ---- 子系统诊断：把「实际生效的参数」摊开 ----
        # 各特征分别走不同代码路径（发射器形状、位置偏移、立牌朝向、动画通道），
        # 任何一处没加载，光看视口很难判断是哪一项。这里直接列出解析后真正生效的
        # 值，便于逐项核对，而不用去猜。
        diag = layout.box()
        diag.label(text="Emitters (what actually loaded):", icon="INFO")
        for preview in _ACTIVE:
            diag.label(text=preview.system.name)
            try:
                subs = preview.sim.subs
            except ReferenceError:
                continue
            for s in subs:
                sub = s.sub
                if sub.emitter_type == "box":
                    emit = "box x{0:g} y{1:g} z{2:g}".format(
                        s.box_half_x * 2, s.box_half_y * 2, s.box_half_z * 2)
                elif sub.emitter_type == "sphere":
                    emit = "sphere r{0:g}".format(s.sphere_r)
                else:
                    emit = "point"
                pos = "({0:g},{1:g},{2:g})".format(*sub.position)
                facing = "billboard" if sub.billboard else "fixed"
                if abs(sub.particle_pitch[0] - 90.0) < 1.0 and sub.particle_pitch[1] < 1.0:
                    facing = "flat(90)"
                anims = [n for n in (s.size_anim, s.alpha_anim,
                                     s.anim_r, s.anim_g, s.anim_b) if n is not None]
                anim_txt = ",".join(a.name for a in anims) if anims else "-"
                row = diag.row(align=True)
                row.label(text="{0}: {1}  pos{2}  {3}".format(
                    sub.name[:16], emit, pos, facing))
                row.label(text="anim:{0}".format(anim_txt[:22]))
                if sub.unknown_keys:
                    diag.label(text="   unused keys: {0}".format(
                        ", ".join(sorted(sub.unknown_keys))[:40]), icon="ERROR")

        col = layout.column(align=True)
        row = col.row(align=True)
        row.operator("io_pdx_particles.ref_sphere", text="Show ref sphere")
        row.operator("io_pdx_particles.ref_sphere_hide", text="Hide")
        row = layout.row(align=True)
        row.operator("io_pdx_particles.reload", icon="FILE_REFRESH", text="Reload")
        row.operator("io_pdx_particles.clear", icon="TRASH", text="Clear")
        layout.label(text="1 unit = 1; scrub timeline / play", icon="TIME")


# ----------------------------------------------------------------------------------------------------------------------
# register / unregister
# ----------------------------------------------------------------------------------------------------------------------

classes = (
    ParticlePreviewEntry,
    ParticleRoot,
    ParticleListItem,
    IOPDX_UL_particle_roots,
    IOPDX_UL_particle_catalog,
    IOPDX_OT_import_particle,
    IOPDX_OT_particle_reload,
    IOPDX_OT_particle_clear,
    IOPDX_OT_particle_ref_sphere,
    IOPDX_OT_particle_ref_sphere_hide,
    IOPDX_OT_particle_add_root,
    IOPDX_OT_particle_remove_root,
    IOPDX_OT_particle_detect_game,
    IOPDX_OT_particle_add_vanilla,
    IOPDX_OT_particle_scan,
    IOPDX_OT_particle_preview_item,
    IOPDX_PT_PDXparticles,
)


def _find_stale_handlers():
    """按函数名 + 来源文件找出本模块遗留的帧处理器。

    两件事都要防：
    - 重载脚本 / 重装插件会重新 import 本模块，_frame_handler 变成新对象，而列表
      里留着旧对象。此时按对象身份判断会漏掉旧的，register 再挂一个、unregister
      又删不掉，最后残留一个引用旧模块空 _ACTIVE 的处理器。
    - 现在处理器是 @persistent 的，会跨文件存活，所以必须能按名字认出来并去重，
      否则重载一次就多一份，越积越多。
    判定用名字 + __module__ 或 co_filename 归属，避免误删别人的同名函数。
    """
    stale = []
    for h in list(bpy.app.handlers.frame_change_post):
        if getattr(h, "__name__", "") != "_frame_handler":
            continue
        mod = getattr(h, "__module__", "") or ""
        fn = getattr(getattr(h, "__code__", None), "co_filename", "") or ""
        if ("pdx_particles" in mod or "pdx_particles" in fn.replace("\\", "/")):
            stale.append(h)
    return stale


@persistent
def _load_post_handler(dummy=None):
    """加载文件后把帧处理器补回来。

    处理器本身是 @persistent 的，加载文件不会被清掉；但「用户先加载文件、插件
    处理器恰好不在」的情况仍会发生（例如先开文件后启用插件、或别的插件脚本
    清过列表）。这里做一次廉价自愈，保证任何时序下都有一个在位的处理器。
    注意：_ACTIVE 是模块级状态，加载文件后它仍指向上一份文件的预览对象，那些
    对象已经随旧文件释放，帧处理器会通过 ReferenceError 自动剔除。
    """
    if _frame_handler in bpy.app.handlers.frame_change_post:
        return
    stale = _find_stale_handlers()
    if stale:
        return          # 已有一份（可能是旧对象），不必再加
    bpy.app.handlers.frame_change_post.append(_frame_handler)
    IO_PDX_LOG.info("particle frame handler re-attached after file load")


def register():
    IO_PDX_LOG.info("Loading particle preview.")
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.io_pdx_particle_entries = CollectionProperty(type=ParticlePreviewEntry)
    bpy.types.Scene.io_pdx_particle_roots = CollectionProperty(type=ParticleRoot)
    bpy.types.Scene.io_pdx_particle_root_index = IntProperty(default=0)
    bpy.types.Scene.io_pdx_particle_items = CollectionProperty(type=ParticleListItem)
    bpy.types.Scene.io_pdx_particle_index = IntProperty(default=0)
    bpy.types.Scene.io_pdx_particle_filter = StringProperty(  # type: ignore
        name="Filter",
        description="Show only registrations whose name or type contains this text",
        default="",
        update=_filter_update,
    )
    bpy.types.Scene.io_pdx_particle_vanilla_root = StringProperty(  # type: ignore
        name="Vanilla root",
        description="Game install folder; used only to resolve textures a mod does not ship. "
                    "Leave empty or press Detect to find it automatically",
        subtype="DIR_PATH",
        default="",
    )
    # 帧处理器：先清掉同名残留（重载/重装会留下引用旧模块的旧对象），再挂当前的。
    # append 前用 _find_stale_handlers 去重是必要的：处理器现在是 @persistent 的，
    # 会跨文件存活，不去重就会越积越多。
    for h in _find_stale_handlers():
        try:
            bpy.app.handlers.frame_change_post.remove(h)
        except ValueError:
            pass
    bpy.app.handlers.frame_change_post.append(_frame_handler)
    if _load_post_handler not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post_handler)


def unregister():
    for h in _find_stale_handlers():
        try:
            bpy.app.handlers.frame_change_post.remove(h)
        except ValueError:
            pass
    for fn in (_load_post_handler,):
        try:
            bpy.app.handlers.load_post.remove(fn)
        except ValueError:
            pass
    _clear_previews()
    del bpy.types.Scene.io_pdx_particle_entries
    del bpy.types.Scene.io_pdx_particle_roots
    del bpy.types.Scene.io_pdx_particle_root_index
    del bpy.types.Scene.io_pdx_particle_items
    del bpy.types.Scene.io_pdx_particle_index
    del bpy.types.Scene.io_pdx_particle_filter
    del bpy.types.Scene.io_pdx_particle_vanilla_root
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
