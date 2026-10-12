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
    EnumProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup, UIList  # type: ignore

from .. import IO_PDX_INFO, IO_PDX_LOG, IO_PDX_SETTINGS
from .parser import (
    ParticleLibrary,
    _infer_root,
    detect_game_roots,
    looks_like_game_root,
    parse_entity_events,
    parse_gfx_registrations,
)
from .index import (
    KIND_ENTITY,
    KIND_MODEL,
    KIND_PARTICLE,
    PDXIndex,
)
from .simulator import PARTICLE_FPS
from .blender_preview import PreviewScene, view_rotation_key

# 活动预览列表（模块级；跨 .blend 不持久化，重开文件后重新导入即可）
_ACTIVE = []

# 视口跟随：billboard 面片要朝向「界面里的实际镜头」，而 Blender 没有「视口旋转」
# 事件，也没有 screen.areas.active（5.2 实测不存在），只能靠定时器轮询镜头角度。
# 轮询到的角度与上次相同才算「镜头停了」，此时才重写网格——拖动视角的过程中不刷，
# 避免每帧重算全部顶点（10 万粒子量级约 80ms/次）。
_VIEW_POLL_INTERVAL = 0.05
# 镜头持续运动时，pending 最多挂起多少个轮询周期，超过就强制刷一次。
# 没有这个兜底，「拖动中不刷」会退化成「一直动就一直不刷」——视口相机动画、
# fly/walk 连续导航下表现为完全不跟随（与原始 bug 同类症状）。
_VIEW_PENDING_MAX = 20                      # 20 × 0.05s ≈ 1s
# 定时器函数对象的存放槽（driver_namespace 跨模块重载/重装存活）。
# 定时器没有可枚举的注册列表，重载后旧对象只能靠这个槽找回并注销，否则会泄漏。
_VIEW_POLL_SLOT = "__io_pdx_particle_view_poll__"
_view_state = {"key": None, "pending": False, "ticks": 0}


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


def _clear_previews():
    _stop_view_poll()
    for preview in list(_ACTIVE):
        try:
            preview.destroy()
        except Exception as err:  # noqa: BLE001
            IO_PDX_LOG.error("particle preview destroy failed: %s", err)
    _ACTIVE.clear()


def _view_follow_enabled():
    """视口跟随开关（场景属性，默认开）。取不到场景时按开处理。"""
    try:
        return bool(bpy.context.scene.io_pdx_particle_face_view)
    except Exception:  # noqa: BLE001
        return True


def _view_poll():
    """轮询视口镜头，停稳后把 billboard 面片转向镜头。

    Blender 没有「视口旋转」事件，也没有 screen.areas.active，所以只能轮询
    region_3d.view_rotation。语义按用户要求：镜头**不动了**才刷新一次，拖动
    过程中不动网格。
    """
    if not _ACTIVE or not _view_follow_enabled():
        return _VIEW_POLL_INTERVAL
    try:
        key = view_rotation_key()
    except Exception:  # noqa: BLE001
        return _VIEW_POLL_INTERVAL
    if key is None:
        return _VIEW_POLL_INTERVAL          # 无视口（后台/无 GUI），什么都不做

    last = _view_state["key"]
    if last is None:
        # 首次轮询。不能假设「导入时刷的朝向」与当前镜头一致：导入完成到本函数
        # 第一次跑之间有 50ms 窗口，镜头可能在此期间移动并已停稳。无条件置
        # pending，让下一次轮询按当前镜头补刷一次（代价是一次 ~10ms 的重写，
        # 只在每次导入后发生一次）。
        _view_state["key"] = key
        _view_state["pending"] = True
        _view_state["ticks"] = 0
        return _VIEW_POLL_INTERVAL
    if key != last:
        _view_state["key"] = key            # 还在动：记下，等它停
        _view_state["pending"] = True
        _view_state["ticks"] += 1
        # 镜头持续运动时 key 永远不会等于 last，「停稳才刷」会退化成永不刷新
        # （视口相机动画、fly/walk 连续导航）。超过阈值就强制刷一次。
        if _view_state["ticks"] < _VIEW_PENDING_MAX:
            return _VIEW_POLL_INTERVAL
    if not _view_state["pending"]:
        return _VIEW_POLL_INTERVAL          # 镜头没动过，无事可做

    _view_state["pending"] = False
    _view_state["ticks"] = 0
    for preview in list(_ACTIVE):
        try:
            preview.refresh_for_view(key)
        except ReferenceError:
            _ACTIVE.remove(preview)
        except Exception as err:  # noqa: BLE001
            IO_PDX_LOG.error("particle view refresh failed: %s", err)
    return _VIEW_POLL_INTERVAL


def _start_view_poll():
    """挂上视口轮询定时器；重复调用不会挂两份。

    注销旧对象必须靠 driver_namespace 里存的引用，不能只按当前模块的
    _view_poll 查：重载脚本/重装插件会重新 import 本模块，_view_poll 变成新
    对象，而定时器列表里留着旧对象——旧对象引用旧模块的（已空的）_ACTIVE，
    既查不到也删不掉，会越积越多。这与帧处理器遇到的坑同构（那边靠函数名 +
    来源文件辨认，定时器没有这种列表可枚举，只能自己留存引用）。
    插件自身的 unregister() 会经 _clear_previews 清掉当前模块的定时器，但清不掉
    重载前旧模块留下的那个（unregister 跑在新模块上，只认新对象）——这正是要
    在这里按槽对账的原因（实测 _ACTIVE.clear() 后定时器仍在，孤儿可达）。
    """
    fn = globals().get("_view_poll")
    if fn is None:
        return
    try:
        stale = bpy.app.driver_namespace.get(_VIEW_POLL_SLOT)
        if stale is not None and stale is not fn:
            try:
                if bpy.app.timers.is_registered(stale):
                    bpy.app.timers.unregister(stale)
            except Exception:  # noqa: BLE001
                pass
        if not bpy.app.timers.is_registered(fn):
            bpy.app.timers.register(fn, first_interval=_VIEW_POLL_INTERVAL)
        bpy.app.driver_namespace[_VIEW_POLL_SLOT] = fn
    except Exception as err:  # noqa: BLE001
        IO_PDX_LOG.error("particle view poll register failed: %s", err)


def _stop_view_poll():
    """注销轮询定时器：当前对象与 driver_namespace 里可能残留的旧对象都清掉。"""
    fns = [globals().get("_view_poll"), bpy.app.driver_namespace.get(_VIEW_POLL_SLOT)]
    for fn in fns:
        if fn is None:
            continue
        try:
            if bpy.app.timers.is_registered(fn):
                bpy.app.timers.unregister(fn)
        except Exception:  # noqa: BLE001
            pass
    try:
        bpy.app.driver_namespace.pop(_VIEW_POLL_SLOT, None)
    except Exception:  # noqa: BLE001
        pass
    _view_state["key"] = None
    _view_state["pending"] = False
    _view_state["ticks"] = 0


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
    roots = [r for r in (_infer_root(path),) if r] + _texture_roots()
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
    nested = []                      # 已解析但不渲染的嵌套子子系统，用于诊断提示
    # 种子必须在清空 entries 之前读：import 会 clear + 重建，之后读只能拿到默认值
    seed = context.scene.io_pdx_particle_seed
    for system, offset in loaded:
        try:
            preview = PreviewScene(system, library, time_offset=offset, seed=seed)
        except Exception as err:  # noqa: BLE001
            IO_PDX_LOG.error("particle preview build failed: %s (%s)", system.name, err)
            missing.append(system.name)
            continue
        _ACTIVE.append(preview)
        entry = scene.io_pdx_particle_entries.add()
        entry.name = system.name
        entry.collection = preview.collection.name
        max_end = max(max_end, preview.sim.total_duration() + offset)
        try:
            nested.extend(system.nested_subsystems())
        except Exception:  # noqa: BLE001
            pass

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

    # _pick_frame 若恰好停在该帧（模拟状态已推进到位），这里只需写网格，
    # 不必再让 refresh() 从 0 重放一遍——那正是导入耗时里最大的一笔
    # （par_01 实测 pick_frame 28.96s + refresh 重放 30.39s，合计约 60s）。
    scene.frame_set(best_frame)
    t_target = (scene.frame_current - scene.frame_start) / PARTICLE_FPS
    for preview in list(_ACTIVE):
        try:
            sim = preview.sim
            subs = [s for s in sim.subs if sim.enabled.get(s.name, True)]
            # 只要所有启用子系统都已在目标时刻，就直接刷网格
            if subs and all(abs(s.t - max(t_target, 0.0)) < 1e-6 for s in subs):
                for sp in preview.sub_previews:
                    sp.refresh(preview._view_basis())
                preview.last_count = sum(
                    len(sp.sub_sim.snapshot()) for sp in preview.sub_previews)
                continue
            preview.refresh()
        except Exception as err:  # noqa: BLE001
            IO_PDX_LOG.error("particle initial refresh failed: %s", err)

    if total_n <= 0:
        IO_PDX_LOG.warning(
            "particle preview: no particles at frame %s; scrub the timeline to see them",
            best_frame)

    # 嵌套子子系统（childsystem）已解析但不参与渲染，这里给出显式提示，
    # 避免「静默少画了一部分」这种最难排查的情况。
    # 实测全库仅 15 处 / 9 个文件，且其中 6 个星体文件是 hide=yes（本就不显示）、
    # 9 处为边际收益，故只诊断不实现。
    for _nm, _child in nested:
        IO_PDX_LOG.info(
            "particle preview: '%s' has nested subsystem '%s' (max_amount=%s) "
            "which is parsed but not rendered",
            _nm, _child.name, _child.max_amount)
    _start_view_poll()
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
# 全局索引（模型 / 粒子 / 实体）
# ----------------------------------------------------------------------------------------------------------------------

MAX_LIST_ROWS = 20000   # 近万条填充实测 <10ms，留个上限只为防病态数据

# 索引与当前展示结果（模块级；跨 .blend 不持久化，重开文件会自动重建）
_INDEX = None
_INDEX_KEY = None
_SHOWN = []             # 与列表行一一对应，供加载时取回原始条目
_REBUILDING = False     # 重建分类列表期间抑制 enabled 回调（见 _category_enabled_update）

# 选帧时的模拟采样次数上限：整条时间轴按步长抽样，避免秒数很长时逐帧推进。
SAMPLE_BUDGET = 2400
# 复核候选帧的次数上限（每次要从 reset 直跳到目标帧，在上千粒子的系统上很贵，
# 实测 2400 粒子跑一次约 0.7s）。估算已经相当准，通常第一个候选就命中。
REPLAY_TRIES = 3


def _addon_prefs():
    """取本插件的偏好对象，取不到返回 None。

    扩展安装时 addons 键是 "bl_ext.user_default.io_pdx_mesh"（顶层包名），
    不是 manifest 里的短 id；按短 id 查会永远拿到 None，表现为偏好里的设置
    完全不生效但也不报错。两种装法（扩展 / 传统 addon）都要覆盖，故两级回退。
    """
    addons = bpy.context.preferences.addons
    # __package__ 在这里是 "<插件包>.pdx_particles"，去掉末级才是 addons 的键：
    # 传统安装是 "io_pdx_mesh"，扩展安装是 "bl_ext.user_default.io_pdx_mesh"。
    top = __package__.rsplit(".", 1)[0] if "." in __package__ else __package__
    for key in (top, IO_PDX_INFO["id"]):
        entry = addons.get(key)
        prefs = getattr(entry, "preferences", None) if entry is not None else None
        if prefs is not None:
            return prefs
    return None


def _pref_roots():
    """从插件偏好取 (额外索引目录, 原版根)，带回退。

    偏好由 IOPDX_AddonPreferences 提供（blender_ui.py）；旧版把根放在场景属性
    与 settings.json 里，这里一并读入，老配置不会因为改版而失效。
    """
    extra, vanilla = [], ""
    prefs = _addon_prefs()
    if prefs is not None:
        for entry in getattr(prefs, "extra_roots", []):
            p = bpy.path.abspath(entry.path) if entry.path else ""
            if p and os.path.isdir(p) and p not in extra:
                extra.append(p)
        v = getattr(prefs, "vanilla_root", "") or ""
        if v:
            v = bpy.path.abspath(v)
            if os.path.isdir(v):
                vanilla = v
    for p in _roots():
        if p not in extra and p != vanilla:
            extra.append(p)
    if not vanilla:
        vanilla = _vanilla_root()
    return extra, vanilla


def _ensure_index(force=False):
    """按当前配置建索引；配置没变就复用。

    原版根排在最后：PDXIndex 用 setdefault 收本体，先扫到的优先，mod 排前面
    才能覆盖原版同名资源。
    """
    global _INDEX, _INDEX_KEY
    extra, vanilla = _pref_roots()
    key = (tuple(extra), vanilla)
    if force or _INDEX is None or _INDEX_KEY != key:
        roots = list(extra)
        if vanilla and vanilla not in roots:
            roots.append(vanilla)
        _INDEX = PDXIndex(roots, vanilla_root=vanilla).build()
        _INDEX_KEY = key
        IO_PDX_LOG.info("asset index: %s", _INDEX.stats)
    return _INDEX


def _disabled_for(scene, kind):
    """当前分类下被禁用的目录名集合。"""
    return {d.category for d in scene.io_pdx_index_disabled if d.kind == kind}


def _set_category_enabled(scene, kind, category, enabled):
    for i, d in enumerate(scene.io_pdx_index_disabled):
        if d.kind == kind and d.category == category:
            if not enabled:
                return
            scene.io_pdx_index_disabled.remove(i)
            return
    if not enabled:
        d = scene.io_pdx_index_disabled.add()
        d.kind = kind
        d.category = category


def _detail_text(entry):
    """列表右侧的一行说明。"""
    d = entry.detail
    if entry.kind == KIND_MODEL:
        names = d.get("pdxmesh") or []
        return names[0] if names else ""
    if entry.kind == KIND_PARTICLE:
        n = d.get("type") or ""
        return n if d.get("resolved") else "{0} (no body)".format(n)
    bits = []
    if d.get("pdxmesh"):
        bits.append(d["pdxmesh"])
    if d.get("particles"):
        bits.append("{0} particle(s)".format(len(d["particles"])))
    return "  ".join(bits)


def _rebuild_categories(context):
    """按当前标签页重建分类列表，保留本类已禁用的目录。"""
    global _REBUILDING
    scene = context.scene
    kind = scene.io_pdx_asset_tab
    index = _ensure_index()
    disabled = _disabled_for(scene, kind)
    _REBUILDING = True
    try:
        scene.io_pdx_index_categories.clear()
        for name, count in index.categories(kind).items():
            item = scene.io_pdx_index_categories.add()
            item.kind = kind
            item.category = name
            item.name = name
            item.count = count
            item.enabled = name not in disabled
        scene.io_pdx_index_category_index = 0
    finally:
        _REBUILDING = False


def _rebuild_asset_list(context):
    """按标签页、搜索词、禁用目录与 mod-only 过滤，填充列表。"""
    global _SHOWN
    scene = context.scene
    kind = scene.io_pdx_asset_tab
    index = _ensure_index()
    # 实体页只列带粒子的：本页的用途就是「模型 + 粒子一起看」
    require_particle = kind == KIND_ENTITY
    entries = index.query(
        kind,
        text=scene.io_pdx_asset_filter or "",
        disabled=_disabled_for(scene, kind),
        mod_only=scene.io_pdx_index_mod_only,
        require_particle=require_particle,
    )
    _SHOWN = entries[:MAX_LIST_ROWS]
    scene.io_pdx_asset_items.clear()
    for e in _SHOWN:
        item = scene.io_pdx_asset_items.add()
        item.name = e.name
        item.detail = _detail_text(e)
        item.category = e.category
        item.vanilla = e.is_vanilla
    total = len(entries)
    scene.io_pdx_asset_index = 0
    scene.io_pdx_index_status = "{0} shown / {1} matched  ·  index {2} files in {3}s".format(
        len(_SHOWN), total, index.stats.get("files", 0), index.stats.get("seconds", 0))
    return len(_SHOWN)


def _asset_filter_update(self, context):
    _rebuild_asset_list(context)


def _asset_tab_update(self, context):
    context.scene.io_pdx_asset_filter = ""
    _rebuild_categories(context)
    _rebuild_asset_list(context)



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
    1. 用 estimate_alive（O(1)，不做物理）整条时间轴采样，找出候选帧。不能在这
       一步真跑物理，上千粒子的系统上要十几秒。
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

    # 第 2 步：复核估算最高的几个候选。
    #
    # 贵的地方不是「试了几个候选」，而是「全程模拟跑两遍」：跑到峰值帧后若 reset()
    # 清零，调用方紧接着又要从 0 跑一遍（实测占导入总耗时的绝大部分）。所以这里
    # 按时间升序单趟推进、命中即停，把状态原样留给调用方，由它直接刷网格。
    ranked = sorted(samples, key=lambda x: -x[0])
    cand = []
    seen_k = set()
    for _, k in ranked:
        if k in seen_k:
            continue
        seen_k.add(k)
        cand.append(k)
        if len(cand) > REPLAY_TRIES:
            break
    if not cand:
        for pv in previews:
            pv.sim.reset()
        return 1, 0

    # 按时间升序单调推进一趟，在每个候选点记录真实粒子数。
    # advance 是确定性的绝对子步网格，到达同一 n_sub 即状态逐位相同
    # （已实测 75 组逐位一致），所以单趟推进与「逐个 reset 后从 0 直跳」等价。
    order = sorted(cand)
    top_k = ranked[0][1] if ranked else None
    counted = {}
    reached = None
    for k in order:
        t = k / max(fps, 1)
        for pv in previews:
            pv.sim.advance(t)
        reached = k
        counted[k] = _count_now(previews)
        # 早退：估算最高的候选已有粒子，它就是答案（选择规则是按估算降序取第一个
        # 有粒子的），不必再往后推进。
        if k == top_k and counted[k] > 0:
            break

    # 按估算值高的优先取，不能改成按时间取第一个——那会挑到更早、粒子更少的帧。
    best_k = None
    best_n = 0
    for _, k in ranked:
        if k in counted and counted[k] > 0:
            best_k, best_n = k, counted[k]
            break

    if best_k is not None and best_k == reached:
        # 恰好停在选中的时刻：把状态原样留给调用方，不要 reset。
        return best_k + 1, best_n

    # 没停在对的时刻（选中帧比推进到的更早，或全部候选都没粒子）：
    # 只能复位，调用方会重新推进。
    for pv in previews:
        pv.sim.reset()
    if best_k is not None:
        return best_k + 1, best_n     # 场景 frame_start = 1，故帧号 = 1 + t*fps
    return (order[0] if order else 1) + 1, 0


class ParticleRoot(PropertyGroup):
    path: StringProperty(name="Root", subtype="DIR_PATH")  # noqa: F821


def _category_enabled_update(self, context):
    """分类眼睛图标切换后重建列表（并记进禁用集合）。

    重建分类列表时也会写 enabled，那时必须跳过本回调：否则会往禁用集合里
    重复追加同一条。_REBUILDING 就是给这种情况用的。
    """
    if _REBUILDING:
        return
    _set_category_enabled(context.scene, self.kind, self.category, self.enabled)
    _rebuild_asset_list(context)


class IndexCategory(PropertyGroup):
    """分类列表里的显示行（不持久化，每次由索引重建）。"""
    kind: StringProperty()      # noqa: F821
    category: StringProperty()  # noqa: F821
    name: StringProperty()      # noqa: F821  展示用目录名
    count: IntProperty()        # noqa: F821  该类条目数
    enabled: BoolProperty(default=True, update=_category_enabled_update)  # noqa: F821


class DisabledCategory(PropertyGroup):
    """被关掉的分类（无回调，只作记录）。"""
    kind: StringProperty()      # noqa: F821
    category: StringProperty()  # noqa: F821


class AssetListItem(PropertyGroup):
    """列表里的一行（对应 _SHOWN 里的一个索引条目）。"""
    name: StringProperty()      # noqa: F821
    detail: StringProperty()    # noqa: F821
    category: StringProperty()  # noqa: F821
    vanilla: BoolProperty()     # noqa: F821


class IOPDX_UL_asset_items(UIList):
    """条目列表：名字 + 右侧说明，来源是原版时标一个标记。"""

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop,
                  index, flt_flag):
        row = layout.row(align=True)
        row.label(text=item.name, icon=self._icon(context))
        row.label(text=item.detail)
        if item.vanilla:
            row.label(text="game", icon="PACKAGE")

    def _icon(self, context):
        kind = context.scene.io_pdx_asset_tab
        return {"model": "MESH_CUBE", "particle": "PARTICLE_POINT"}.get(kind, "OUTLINER_OB_EMPTY")


class IOPDX_UL_particle_roots(UIList):
    """扫描根目录列表。"""

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop,
                  index, flt_flag):
        layout.label(text=os.path.basename(item.path.rstrip("\\/")) or item.path,
                     icon="FILE_FOLDER")


class IOPDX_UL_index_categories(UIList):
    """分类列表：一眼是目录名，右侧数字是该类条目数，眼睛图标切换启用。"""

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop,
                  index, flt_flag):
        row = layout.row(align=True)
        row.label(text=item.name, icon="FILE_FOLDER")
        row.label(text=str(item.count))
        row.prop(item, "enabled", text="", icon="HIDE_OFF" if item.enabled else "HIDE_ON",
                 emboss=False)



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


class IOPDX_OT_index_rescan(Operator):
    """Rebuild the asset index from the configured folders"""
    bl_idname = "io_pdx_particles.rescan"
    bl_label = "Rescan"
    bl_description = "Rebuild the asset index (run after installing or updating mods)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        index = _ensure_index(force=True)
        _rebuild_categories(context)
        _rebuild_asset_list(context)
        if not index.stats.get("files"):
            self.report({"ERROR"}, "Nothing found — set the game folder or add a mod folder")
            return {"CANCELLED"}
        self.report({"INFO"}, "Indexed {0} files in {1}s".format(
            index.stats.get("files"), index.stats.get("seconds")))
        return {"FINISHED"}


class IOPDX_OT_index_all_categories(Operator):
    """Enable every category of the current tab"""
    bl_idname = "io_pdx_particles.all_categories"
    bl_label = "Enable all"
    bl_description = "Turn every category back on"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        kind = scene.io_pdx_asset_tab
        for i in range(len(scene.io_pdx_index_disabled) - 1, -1, -1):
            if scene.io_pdx_index_disabled[i].kind == kind:
                scene.io_pdx_index_disabled.remove(i)
        _rebuild_categories(context)
        _rebuild_asset_list(context)
        return {"FINISHED"}


class IOPDX_OT_asset_filter_update(Operator):
    """Apply the search box"""
    bl_idname = "io_pdx_particles.filter_update"
    bl_label = "Search"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        _rebuild_asset_list(context)
        return {"FINISHED"}


def _selected_entry(scene):
    """当前列表选中行对应的索引条目；没有则 None。"""
    idx = scene.io_pdx_asset_index
    if not (0 <= idx < len(_SHOWN)):
        return None
    return _SHOWN[idx]


def _load_mesh_path(path):
    """导入 .mesh（复用模型导入流程）。

    延迟 import：pdx_blender 包在导入期会回头 import 本模块，模块级互相引用会成环。
    """
    from ..pdx_blender.blender_import_export import import_meshfile
    import_meshfile(path)


def _resolve_system(index, name):
    """名字（注册名或本体名）→ 粒子本体，取不到返回 None。

    entity 事件里写的是 .gfx 注册名，本体名常与它不同
    （plasma_cannon_medium_hit_effect → plasma_cannon_hit_effect），
    必须经注册表换算，否则实体一个粒子都点不亮。
    """
    return index.system_for_registration(name)


def _load_particle_system(context, name, index):
    """按注册名或本体名建粒子预览。"""
    system = _resolve_system(index, name)
    if system is None:
        return False
    return _import_one(context, system, index.library)


def _mount_points():
    """场景里可作挂点的对象：模型导入时把 locator 建成 EMPTY，node 指向的就是它。"""
    return {o.name: o for o in bpy.data.objects if o.type == "EMPTY"}


def _make_locator_empty(name, xyz):
    """按 entity 自带的 locator 建一个 EMPTY 挂点。

    坐标换算与 .mesh 导入一致：引擎 (x, y, z) → Blender (x, z, y)（SPACE_MATRIX）。
    """
    from mathutils import Matrix, Vector  # type: ignore
    # 复用 mesh 导入的同一套空间矩阵，避免两处各写一份换算
    from ..pdx_blender.blender_import_export import SPACE_MATRIX
    obj = bpy.data.objects.new(name, None)
    obj.empty_display_type = "PLAIN_AXES"
    obj.empty_display_size = 0.4
    bpy.context.scene.collection.objects.link(obj)
    # 先按引擎轴序平移，再整体换到 Blender 空间
    local = Matrix.Translation(Vector((float(xyz[0]), float(xyz[1]), float(xyz[2]))))
    obj.matrix_world = SPACE_MATRIX @ local
    obj.rotation_mode = "XYZ"
    return obj


def _load_entity(context, entry, index):
    """entity → 导入模型 + 把粒子挂到 node 指定的挂点上。

    事件的 particle 是 .gfx 注册名（经 system_for_registration 换算本体），
    node 是挂点名；名字匹配场景里的 EMPTY（模型导入时由 locator 建出），
    命中就把粒子挂上去，没写明 node 或找不到对应挂点时留在原点并记日志。
    """
    detail = entry.detail
    mesh = detail.get("mesh") or ""
    loaded_any = False
    if mesh and os.path.isfile(mesh):
        try:
            _load_mesh_path(mesh)
            loaded_any = True
        except Exception as err:  # noqa: BLE001
            IO_PDX_LOG.error("entity mesh import failed: %s (%s)", mesh, err)

    scene = context.scene
    scene.io_pdx_particle_entries.clear()
    _clear_previews()

    mounts = _mount_points()
    # entity 自带的 locator 不在 .mesh 里，只导模型不会建出它们；有 node 指向
    # 这些名字时先补建 EMPTY，否则粒子找不到挂点只能留在原点。
    for loc in detail.get("locators") or []:
        lname = loc.get("name") or ""
        if not lname or lname in mounts:
            continue
        try:
            mounts[lname] = _make_locator_empty(lname, loc.get("position") or (0, 0, 0))
        except Exception as err:  # noqa: BLE001
            IO_PDX_LOG.error("locator create failed: %s (%s)", lname, err)
    events = detail.get("events") or [{"particle": p} for p in detail.get("particles") or []]
    # 先把 (本体, 挂点名, 起播延迟) 收齐再一次装载：逐个 _import_loaded 会各自
    # 重挑一遍显示帧并反复重置时间轴范围，多事件 entity 会慢很多且只有最后一个生效。
    plan, unmatched = [], []
    for ev in events:
        pname = ev.get("particle") or ""
        system = _resolve_system(index, pname)
        if system is None:
            unmatched.append(pname)
            continue
        try:
            offset = float(ev.get("time") or 0.0)
        except (TypeError, ValueError):
            offset = 0.0
        plan.append((system, offset, ev.get("node") or "", pname))

    if plan:
        loaded = [(system, offset) for system, offset, _node, _n in plan]
        if _import_loaded(context, loaded, [], index.library):
            loaded_any = True
        # 挂点按「本体名 → 该本体的 node 序列」对齐，而不是按 _ACTIVE 与 plan 的
        # 下标：_import_loaded 只在建成功后追加 _ACTIVE，下标对不齐；同一个本体
        # 出现多次时按出现顺序取。
        nodes_by_system = {}
        for system, _offset, node, _n in plan:
            nodes_by_system.setdefault(system.name, []).append(node)
        for preview in _ACTIVE:
            queue = nodes_by_system.get(preview.system.name) or []
            node = queue.pop(0) if queue else ""
            if not node:
                continue
            empty = mounts.get(node)
            if empty is not None:
                preview.set_mount(empty)
            else:
                unmatched.append("{0}@{1}".format(preview.system.name, node))

    if unmatched:
        IO_PDX_LOG.warning("entity particle/mount not found: %s",
                           ", ".join(sorted(set(unmatched))))
    return loaded_any


class IOPDX_OT_asset_load(Operator):
    """Load whatever the selected list entry points at"""
    bl_idname = "io_pdx_particles.load_selected"
    bl_label = "Load selected"
    bl_description = "Load the selected model / particle / entity"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        entry = _selected_entry(scene)
        if entry is None:
            self.report({"WARNING"}, "Select an entry in the list first")
            return {"CANCELLED"}
        index = _ensure_index()

        if entry.kind == KIND_MODEL:
            try:
                _load_mesh_path(entry.path)
            except Exception as err:  # noqa: BLE001
                self.report({"ERROR"}, "Mesh import failed: {0}".format(err))
                return {"CANCELLED"}
            self.report({"INFO"}, "Loaded model {0}".format(entry.name))
            return {"FINISHED"}

        if entry.kind == KIND_PARTICLE:
            typ = entry.detail.get("type") or ""
            scene.io_pdx_particle_entries.clear()
            _clear_previews()
            if not _load_particle_system(context, typ, index):
                self.report({"ERROR"}, "No body found for type '{0}'".format(typ))
                return {"CANCELLED"}
            self.report({"INFO"}, "Previewing {0}".format(entry.name))
            return {"FINISHED"}

        ok = _load_entity(context, entry, index)
        if not ok:
            self.report({"ERROR"}, "Entity '{0}' has no loadable mesh or particle".format(entry.name))
            return {"CANCELLED"}
        n = len(entry.detail.get("particles") or [])
        self.report({"INFO"}, "Loaded entity {0} ({1} particle(s))".format(entry.name, n))
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

        # ---- 三个入口：模型 / 粒子 / 实体 ----
        row = layout.row(align=True)
        row.prop(scene, "io_pdx_asset_tab", expand=True)

        # ---- 索引状态与重建 ----
        index = _ensure_index()
        # 两个都要判：分类列表为空（首次打开 / 重开 .blend 后模块级索引丢了），
        # 或索引本身换了内容（用户在偏好里改了游戏目录或增删了 mod 目录）——
        # 后者若只看「列表是否为空」会漏掉，表现为面板还列着旧目录的内容。
        token = repr(_INDEX_KEY)
        if not scene.io_pdx_index_categories or scene.io_pdx_index_token != token:
            scene.io_pdx_index_token = token
            _rebuild_categories(context)
            _rebuild_asset_list(context)
        row = layout.row(align=True)
        row.label(text="Game folder:", icon="FILE_FOLDER")
        row.operator("io_pdx_particles.rescan", icon="FILE_REFRESH", text="Rescan")
        if not index.stats.get("files"):
            box = layout.box()
            box.label(text="No folder configured yet.", icon="INFO")
            box.label(text="Set it in Preferences > Add-ons > IO PDX Mesh,")
            box.label(text="or add a mod folder there, then Rescan.")
            return

        # ---- 搜索 ----
        layout.prop(scene, "io_pdx_asset_filter", text="", icon="VIEWZOOM")

        # ---- 分类（可整类禁用）----
        box = layout.box()
        head = box.row(align=True)
        n_dis = len(_disabled_for(scene, scene.io_pdx_asset_tab))
        head.label(text="Categories ({0} off):".format(n_dis), icon="FILE_FOLDER")
        head.operator("io_pdx_particles.all_categories", text="All", icon="HIDE_OFF")
        rows = box.row(align=True)
        rows.template_list("IOPDX_UL_index_categories", "", scene,
                           "io_pdx_index_categories", scene, "io_pdx_index_category_index",
                           rows=5)
        box.prop(scene, "io_pdx_index_mod_only", text="Mod only")
        box.label(text="Untick a category to hide it", icon="INFO")

        # ---- 条目列表 ----
        layout.label(text=scene.io_pdx_index_status, icon="INFO")
        layout.template_list("IOPDX_UL_asset_items", "", scene,
                             "io_pdx_asset_items", scene, "io_pdx_asset_index", rows=8)
        row = layout.row(align=True)
        row.operator("io_pdx_particles.load_selected", icon="IMPORT",
                     text="Load selected")
        layout.operator("io_pdx_particles.import", icon="FILEBROWSER",
                        text="Load from file ...")

        # 种子改完要 Reload 才生效（不做成自动触发，重建预览代价不小）
        row = layout.row(align=True)
        row.prop(scene, "io_pdx_particle_seed", text="Seed")
        row.operator("io_pdx_particles.reload", text="", icon="FILE_REFRESH")

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
                # 「平铺」只在没有随机扰动时成立：pitch_var 非零意味着每颗粒子
                # 的基角不同（如 { 90 90 } 的 beam），只有部分粒子是平的，不能
                # 整条标成 flat(90)。有扰动时把范围一起标出来。
                if abs(sub.particle_pitch[0] - 90.0) < 1.0:
                    facing = ("flat(90)" if sub.particle_pitch[1] < 1.0
                              else "flat(90)+-{0:g}".format(sub.particle_pitch[1]))
                if not sub.billboard and abs(sub.particle_pitch[1]) >= 1.0:
                    facing += " jitter"
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
        layout.prop(scene, "io_pdx_particle_face_view", icon="ORIENTATION_VIEW")
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
    IndexCategory,
    DisabledCategory,
    AssetListItem,
    IOPDX_UL_particle_roots,
    IOPDX_UL_index_categories,
    IOPDX_UL_asset_items,
    IOPDX_OT_import_particle,
    IOPDX_OT_particle_reload,
    IOPDX_OT_particle_clear,
    IOPDX_OT_particle_ref_sphere,
    IOPDX_OT_particle_ref_sphere_hide,
    IOPDX_OT_particle_add_root,
    IOPDX_OT_particle_remove_root,
    IOPDX_OT_particle_detect_game,
    IOPDX_OT_particle_add_vanilla,
    IOPDX_OT_index_rescan,
    IOPDX_OT_index_all_categories,
    IOPDX_OT_asset_filter_update,
    IOPDX_OT_asset_load,
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
    """加载文件后把帧处理器与视口轮询补回来。

    帧处理器本身是 @persistent 的，加载文件不会被清掉；但「用户先加载文件、插件
    处理器恰好不在」的情况仍会发生（例如先开文件后启用插件、或别的插件脚本
    清过列表）。这里做一次廉价自愈，保证任何时序下都有一个在位的处理器。
    注意：_ACTIVE 是模块级状态，加载文件后它仍指向上一份文件的预览对象，那些
    对象已经随旧文件释放，帧处理器会通过 ReferenceError 自动剔除。

    视口轮询是 bpy.app.timers 注册的，**加载文件会把它清掉**（帧处理器因
    @persistent 存活，定时器没有这个机制）。不清查就会「粒子还在动但视角不再
    跟随」，因此这里一并补回——只在确实还有活动预览时补，否则会留下一个永远
    空转的定时器（导入时会自己启动，见 _import_loaded）。
    """
    if _frame_handler not in bpy.app.handlers.frame_change_post:
        stale = _find_stale_handlers()
        if not stale:       # 已有一份（可能是旧对象），不必再加
            bpy.app.handlers.frame_change_post.append(_frame_handler)
            IO_PDX_LOG.info("particle frame handler re-attached after file load")
    if _ACTIVE:
        _start_view_poll()


def _tab_items():
    return [
        (KIND_MODEL, "Models", "Load a .mesh on its own"),
        (KIND_PARTICLE, "Particles", "Preview a particle system on its own"),
        (KIND_ENTITY, "Entity", "Load a model together with its mount-point particles"),
    ]


def register():
    IO_PDX_LOG.info("Loading particle preview.")
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.io_pdx_particle_entries = CollectionProperty(type=ParticlePreviewEntry)
    bpy.types.Scene.io_pdx_particle_roots = CollectionProperty(type=ParticleRoot)
    bpy.types.Scene.io_pdx_particle_root_index = IntProperty(default=0)
    bpy.types.Scene.io_pdx_particle_face_view = BoolProperty(  # type: ignore
        name="Face the viewport",
        description="Turn billboard particles toward the 3D viewport camera "
                    "(off = keep using the scene camera / fixed fallback)",
        default=True,
    )
    bpy.types.Scene.io_pdx_particle_vanilla_root = StringProperty(  # type: ignore
        name="Vanilla root",
        description="Game install folder; used only to resolve textures a mod does not ship. "
                    "Leave empty or press Detect to find it automatically",
        subtype="DIR_PATH",
        default="",
    )
    bpy.types.Scene.io_pdx_particle_seed = IntProperty(  # type: ignore
        name="Seed",
        description="Random seed for per-particle variation. Same seed gives the "
                    "same result every time; change it to reshuffle every system",
        default=0,
    )
    # 三入口标签页 + 索引列表
    bpy.types.Scene.io_pdx_asset_tab = EnumProperty(  # type: ignore
        name="Tab",
        description="What to browse and load",
        items=_tab_items(),
        default=KIND_MODEL,
        update=_asset_tab_update,
    )
    bpy.types.Scene.io_pdx_asset_items = CollectionProperty(type=AssetListItem)
    bpy.types.Scene.io_pdx_asset_index = IntProperty(default=0)
    bpy.types.Scene.io_pdx_asset_filter = StringProperty(  # type: ignore
        name="Search",
        description="Show only entries whose name, pdxmesh name or particle name contains this",
        default="",
        update=_asset_filter_update,
    )
    bpy.types.Scene.io_pdx_index_categories = CollectionProperty(type=IndexCategory)
    bpy.types.Scene.io_pdx_index_category_index = IntProperty(default=0)
    bpy.types.Scene.io_pdx_index_disabled = CollectionProperty(type=DisabledCategory)
    bpy.types.Scene.io_pdx_index_mod_only = BoolProperty(  # type: ignore
        name="Mod only",
        description="Hide entries that come from the game install and keep only mod content",
        default=False,
        update=_asset_filter_update,
    )
    bpy.types.Scene.io_pdx_index_status = StringProperty(default="")  # type: ignore
    # 列表对应的索引标识；与当前索引不一致时 draw() 会重建列表
    bpy.types.Scene.io_pdx_index_token = StringProperty(default="")  # type: ignore
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
    if hasattr(bpy.types.Scene, "io_pdx_particle_face_view"):
        del bpy.types.Scene.io_pdx_particle_face_view
    del bpy.types.Scene.io_pdx_particle_vanilla_root
    for name in ("io_pdx_asset_tab", "io_pdx_asset_items", "io_pdx_asset_index",
                 "io_pdx_asset_filter", "io_pdx_index_categories",
                 "io_pdx_index_category_index", "io_pdx_index_disabled",
                 "io_pdx_index_mod_only", "io_pdx_index_status",
                 "io_pdx_index_token"):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
