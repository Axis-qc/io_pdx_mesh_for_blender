"""
PDX particle preview UI: import operator, sidebar panel, frame handler.

 工作流：File 类别下点「Load particles ...」选 .gfx / .asset → 自动解析、建预览集合、
 设时间轴范围 → 拖时间轴或播放即预览；改 .asset 后点「重载」秒看效果。
 支持同时装载多个系统（如整个 .gfx 的全部注册），按系统整行显隐。

 author : Axis-qc (fork of ross-g/io_pdx_mesh 0.91.2)
"""

import math
import os

import bpy  # type: ignore
from bpy.props import (  # type: ignore
    BoolProperty,
    CollectionProperty,
    StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup  # type: ignore

from .. import IO_PDX_LOG, IO_PDX_INFO
from .parser import ParticleLibrary, parse_entity_events, parse_gfx_registrations
from .blender_preview import PreviewScene

# 活动预览列表（模块级；跨 .blend 不持久化，重开文件后重新导入即可）
_ACTIVE = []


def _vanilla_root():
    for scene in bpy.data.scenes:
        v = getattr(scene, "io_pdx_particle_vanilla_root", "")
        if v and os.path.isdir(v):
            return v
    return ""


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


def _frame_handler(scene, depsgraph):
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
    roots = [r for r in (_mod_root(path), _vanilla_root()) if r]
    library = ParticleLibrary(roots)
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

    scene = context.scene
    fps = scene.render.fps or 24

    max_end = scene.frame_end / fps if scene.frame_end else 1.0
    for system, offset in loaded:
        try:
            preview = PreviewScene(system, library, display_scale=scene.io_pdx_particle_scale, time_offset=offset)
        except Exception as err:  # noqa: BLE001
            IO_PDX_LOG.error("particle preview build failed: %s (%s)", system.name, err)
            missing.append(system.name)
            continue
        _ACTIVE.append(preview)
        entry = scene.io_pdx_particle_entries.add()
        entry.name = system.name
        entry.collection = preview.collection.name
        max_end = max(max_end, preview.sim.total_duration() + offset)

    scene.frame_start = 1
    scene.frame_end = 1 + int(math.ceil(max_end * fps))
    if missing:
        IO_PDX_LOG.warning("particle import missing: %s", ", ".join(sorted(set(missing))))
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
    """Toggle the reference sphere (planet-size wireframe, r=30)"""
    bl_idname = "io_pdx_particles.ref_sphere"
    bl_label = "Reference sphere"
    bl_description = "Toggle wireframe sphere at planet scale (r=30 units) for size reference"
    bl_options = {"REGISTER", "UNDO"}

    show: bool  # noqa: F821

    def execute(self, context):
        for preview in _ACTIVE:
            preview.show_ref_sphere(self.show)
        return {"FINISHED"}


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

        layout.operator("io_pdx_particles.import", icon="PARTICLES", text="Load particles ...")

        if not _ACTIVE:
            if scene.io_pdx_particle_entries:
                layout.label(text="Stale preview — reload a file", icon="ERROR")
            return

        box = layout.box()
        for i, preview in enumerate(_ACTIVE):
            row = box.row(align=True)
            entry = scene.io_pdx_particle_entries.get(preview.system.name)
            icon = "HIDE_OFF" if (entry and entry.visible) else "HIDE_ON"
            row.prop(entry, "visible", text="", icon=icon, toggle=True) if entry else None
            try:
                n_sub = len(preview.sim.subs)
                row.label(text="{0}  ({1} emitters)".format(preview.system.name, n_sub), icon="PARTICLE_POINT")
            except ReferenceError:
                row.label(text="(deleted)", icon="ERROR")

        col = layout.column(align=True)
        col.prop(scene, "io_pdx_particle_scale", text="Display scale")
        row = col.row(align=True)
        row.operator("io_pdx_particles.ref_sphere", text="Show ref sphere").show = True
        row.operator("io_pdx_particles.ref_sphere", text="Hide").show = False
        row = layout.row(align=True)
        row.operator("io_pdx_particles.reload", icon="FILE_REFRESH", text="Reload")
        row.operator("io_pdx_particles.clear", icon="TRASH", text="Clear")
        layout.label(text="Scrub timeline / play to preview", icon="TIME")


# ----------------------------------------------------------------------------------------------------------------------
# register / unregister
# ----------------------------------------------------------------------------------------------------------------------

classes = (
    ParticlePreviewEntry,
    IOPDX_OT_import_particle,
    IOPDX_OT_particle_reload,
    IOPDX_OT_particle_clear,
    IOPDX_OT_particle_ref_sphere,
    IOPDX_PT_PDXparticles,
)


def register():
    IO_PDX_LOG.info("Loading particle preview.")
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.io_pdx_particle_entries = CollectionProperty(type=ParticlePreviewEntry)
    bpy.types.Scene.io_pdx_particle_scale = (
        bpy.props.FloatProperty(  # type: ignore
            name="Display scale",
            description="Uniform preview scale only (does NOT change internal proportions)",
            default=0.02, min=0.0001, max=10.0, soft_min=0.001,
        )
    )
    bpy.types.Scene.io_pdx_particle_vanilla_root = (
        bpy.props.StringProperty(  # type: ignore
            name="Vanilla root",
            description="Stellaris install root, used to resolve vanilla particle textures",
            subtype="DIR_PATH",
            default="G:\\SteamLibrary\\steamapps\\common\\Stellaris",
        )
    )
    if _frame_handler not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(_frame_handler)


def unregister():
    if _frame_handler in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_frame_handler)
    _clear_previews()
    del bpy.types.Scene.io_pdx_particle_entries
    del bpy.types.Scene.io_pdx_particle_scale
    del bpy.types.Scene.io_pdx_particle_vanilla_root
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
