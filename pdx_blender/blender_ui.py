"""
Paradox asset files, Blender import/export interface.

author : ross-g
"""

import importlib
from textwrap import wrap

import bpy  # type: ignore
from bpy.props import (  # type: ignore
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import AddonPreferences, Operator, Panel, PropertyGroup, UIList  # type: ignore
from bpy_extras.io_utils import ExportHelper, ImportHelper  # type: ignore

from .. import ENGINE_SETTINGS, IO_PDX_INFO, IO_PDX_LOG, IO_PDX_SETTINGS
from .. import pdx_i18n
from ..pdx_data import PDXData
from ..updater import github

# 本插件顶层包的模块名。这个值取决于安装方式：以扩展安装时是
# "bl_ext.<仓库名>.io_pdx_mesh"，传统 addon 安装时是 "io_pdx_mesh"。
# 偏好类的 bl_idname 必须用它——Blender 的 addons 字典就是拿这个键存偏好的，
# 写死字面量会导致其中一种装法下偏好挂空（取到 None）。官方手册
# "Extensions and Namespace > User Preferences and __package__" 也是这么要求的。
from .. import __package__ as base_package

# 粒子解析层：原版根探测直接复用 parser 里现成的实现，不重写。
from ..pdx_particles.parser import detect_game_roots, looks_like_game_root

try:
    from . import blender_import_export

    importlib.reload(blender_import_export)
    from .blender_import_export import (
        PDX_MESHINDEX,
        PDX_SHADER,
        create_shader,
        export_animfile,
        export_meshfile,
        get_mesh_index,
        import_animfile,
        import_meshfile,
        list_scene_pdx_meshes,
        set_ignore_joints,
        set_local_axis_display,
    )
except Exception as err:
    IO_PDX_LOG.error(err)
    raise


""" ====================================================================================================================
    Variables and Helper functions.
========================================================================================================================
"""


def get_material_list(self, context):
    sel_engine = context.scene.io_pdx_settings.setup_engine

    material_list = [(material, material, material) for material in ENGINE_SETTINGS[sel_engine]["material"]]
    material_list.insert(0, ("__NONE__", "", ""))

    return material_list


def get_scene_material_list(self, context):
    material_list = [(mat.name, mat.name, mat.name) for mat in bpy.data.materials if mat.get(PDX_SHADER, None)]

    return material_list


def set_engine(self, context):
    sel_engine = context.scene.io_pdx_settings.setup_engine
    IO_PDX_SETTINGS.last_set_engine = sel_engine
    IO_PDX_LOG.info("Set game engine to: '{}'".format(sel_engine))


""" ====================================================================================================================
    Operator classes called by the tool UI.
========================================================================================================================
"""


class IOPDX_OT_popup_message(Operator):
    bl_idname = "io_pdx_mesh.popup_message"
    bl_label = IO_PDX_INFO["name"]
    bl_description = "Popup Message"
    bl_options = {"REGISTER"}
    # fmt:off
    msg_text: StringProperty(
        default="NOT YET IMPLEMENTED!",
    )
    msg_icon: StringProperty(
        default="ERROR",  # options are - "ERROR", "QUESTION", "CANCEL", "INFO"
    )
    msg_width: IntProperty(
        default=300,
    )
    # fmt:on

    def execute(self, context):
        return {"FINISHED"}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=self.msg_width)

    def draw(self, context):
        self.layout.operator_context = "INVOKE_DEFAULT"

        # split text into multiple label rows if it's wider than the panel
        txt_lines = []
        for line in self.msg_text.splitlines():
            txt_lines.extend(wrap(line, self.msg_width / 6))
            txt_lines.append("")

        col = self.layout.column(align=True)
        # 空文本（如更新检查失败时的 LATEST_NOTES）会让 txt_lines 为空，取 [0] 会抛 IndexError
        col.label(text=txt_lines[0] if txt_lines else "", icon=self.msg_icon)
        for line in txt_lines[1:]:
            if line:
                col.label(text=line)
            else:
                col.separator()

        col.label(text="")


class material_popup(object):
    bl_options = {"REGISTER"}
    # fmt:off
    mat_name: StringProperty(
        name="Material name",
        default="",
    )
    mat_type: EnumProperty(
        name="Material type",
        items=get_material_list,
    )
    use_custom: BoolProperty(
        name="Custom type:",
        default=False,
    )
    custom_type: StringProperty(
        name="Shader",
        default="",
    )
    apply_mat: BoolProperty(
        name="Apply material to selected?",
        default=False,
    )
    # fmt:on


class IOPDX_OT_material_create_popup(material_popup, Operator):
    bl_idname = "io_pdx_mesh.material_create_popup"
    bl_description = bl_label = "Create a PDX material"

    def check(self, context):
        return True

    def execute(self, context):
        mat_name = self.mat_name
        mat_type = self.mat_type
        if self.use_custom or mat_type == "__NONE__":
            mat_type = self.custom_type
        # create a mock PDXData object for convenience here to pass to the create_shader function
        mat_pdx = type("Material", (PDXData, object), {"shader": [mat_type]})
        shader = create_shader(mat_pdx, mat_name, None, template_only=True)
        IO_PDX_LOG.info("Created material: {0} ({1})".format(mat_name, mat_type))
        if self.apply_mat:
            selected_objs = [obj for obj in context.selected_objects if isinstance(obj.data, bpy.types.Mesh)]
            for obj in selected_objs:
                # for each selected mesh, append the new material
                obj.data.materials.append(shader)
                IO_PDX_LOG.info("Applied material: {0} to object: {1}".format(shader.name, obj.name))
        return {"FINISHED"}

    def invoke(self, context, event):
        self.mat_name = ""
        self.mat_type = "__NONE__"
        self.use_custom = False
        self.custom_type = ""
        return context.window_manager.invoke_props_dialog(self, width=350)

    def draw(self, context):
        box = self.layout.box()
        box.prop(self, "mat_name")
        mat_type = box.row()
        mat_type.prop(self, "mat_type")
        split = box.split(factor=0.3)
        col1, col2 = split.column(), split.column()
        col1.prop(self, "use_custom")
        col2.prop(self, "custom_type", text="")
        col2.enabled = False
        if self.use_custom:
            mat_type.enabled = False
            col2.enabled = True
        self.layout.separator()
        apply = self.layout.row()
        apply.prop(self, "apply_mat")
        self.layout.separator()


class IOPDX_OT_material_edit_popup(material_popup, Operator):
    bl_idname = "io_pdx_mesh.material_edit_popup"
    bl_description = bl_label = "Edit a PDX material"

    def mat_select(self, context):
        mat = bpy.data.materials[self.scene_mats]
        curr_mat = context.scene.io_pdx_material
        curr_mat.mat_name = mat.name
        curr_mat.mat_type = mat[PDX_SHADER]

    # fmt:off
    scene_mats: EnumProperty(
        name="Selected material",
        items=get_scene_material_list,
        update=mat_select,
    )
    # fmt:on

    def check(self, context):
        return True

    def execute(self, context):
        mat = bpy.data.materials[self.scene_mats]
        curr_mat = context.scene.io_pdx_material
        mat.name = curr_mat.mat_name
        mat[PDX_SHADER] = curr_mat.mat_type
        IO_PDX_LOG.info("Edited material: {0} ({1})".format(curr_mat.mat_name, curr_mat.mat_type))
        return {"FINISHED"}

    def invoke(self, context, event):
        pdx_scene_materials = get_scene_material_list(self, context)
        if pdx_scene_materials:
            if self.scene_mats in bpy.data.materials:
                self.mat_select(context)
                mat = bpy.data.materials[self.scene_mats]
                self.mat_name = mat.name
                self.custom_type = mat[PDX_SHADER]
                return context.window_manager.invoke_props_dialog(self, width=350)
            else:
                return {"CANCELLED"}
        else:
            bpy.ops.io_pdx_mesh.popup_message("INVOKE_DEFAULT", msg_text="NO PDX MATERIALS FOUND IN THE SCENE!")
            return {"CANCELLED"}

    def draw(self, context):
        curr_mat = context.scene.io_pdx_material

        self.layout.prop(self, "scene_mats")
        self.layout.separator()

        box = self.layout.box()
        box.prop(curr_mat, "mat_name")
        box.prop(curr_mat, "mat_type")
        self.layout.separator()


class IOPDX_UL_mesh_index_list(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.prop(item, "name", text="", emboss=False)


class IOPDX_OT_mesh_index_actions(Operator):
    bl_idname = "io_pdx_mesh.mesh_index_actions"
    bl_description = bl_label = "Mesh index list actions"
    bl_options = {"REGISTER"}
    # fmt:off
    action: EnumProperty(
        items=(("UP", "Up", ""), ("DOWN", "Down", "")),
    )
    # fmt:on

    @classmethod
    def poll(cls, context):
        return context.scene.io_pdx_group

    def move_index(self):
        list_index = bpy.context.scene.io_pdx_group.idx
        list_length = len(bpy.context.scene.io_pdx_group.coll) - 1

        new_index = list_index + (-1 if self.action == "UP" else 1)
        bpy.context.scene.io_pdx_group.idx = max(0, min(new_index, list_length))

    def execute(self, context):
        collection = context.scene.io_pdx_group.coll
        index = context.scene.io_pdx_group.idx
        neighbor = index + (-1 if self.action == "UP" else 1)
        collection.move(neighbor, index)
        self.move_index()

        return {"FINISHED"}


class IOPDX_OT_mesh_index_popup(Operator):
    bl_idname = "io_pdx_mesh.mesh_index_popup"
    bl_description = bl_label = "Set mesh index on PDX meshes"
    bl_options = {"REGISTER"}

    def check(self, context):
        return True

    def execute(self, context):
        for i, item in enumerate(context.scene.io_pdx_group.coll):
            item.ref.data[PDX_MESHINDEX] = i
        return {"FINISHED"}

    def invoke(self, context, event):
        obj_group = context.scene.io_pdx_group

        obj_group.coll.clear()
        pdx_scenemeshes = list_scene_pdx_meshes()
        pdx_scenemeshes.sort(key=lambda obj: get_mesh_index(obj.data))

        for obj in pdx_scenemeshes:
            item = obj_group.coll.add()
            item.name = obj.name
            item.ref = obj
        return context.window_manager.invoke_props_dialog(self, width=200)

    def draw(self, context):
        obj_group = context.scene.io_pdx_group
        row = self.layout.row()
        row.template_list("IOPDX_UL_mesh_index_list", "", obj_group, "coll", obj_group, "idx", rows=8)

        col = row.column(align=True)
        col.operator("io_pdx_mesh.mesh_index_actions", icon="TRIA_UP", text="").action = "UP"
        col.operator("io_pdx_mesh.mesh_index_actions", icon="TRIA_DOWN", text="").action = "DOWN"
        self.layout.separator()


class IOPDX_OT_import_mesh(Operator, ImportHelper):
    bl_idname = "io_pdx_mesh.import_mesh"
    bl_description = bl_label = "Import PDX mesh"
    bl_options = {"REGISTER", "UNDO"}

    # ImportHelper mixin class uses these
    filename_ext = ".mesh"
    # fmt:off
    filter_glob: StringProperty(
        default="*.mesh",
        options={"HIDDEN"},
        maxlen=255,
    )
    filepath: StringProperty(
        name="Import file Path",
        maxlen=1024,
    )
    chk_mesh: BoolProperty(
        name="Import mesh",
        description="Import mesh",
        default=True,
    )
    chk_skel: BoolProperty(
        name="Import skeleton",
        description="Import skeleton",
        default=True,
    )
    chk_locs: BoolProperty(
        name="Import locators",
        description="Import locators",
        default=True,
    )
    chk_joinmats: BoolProperty(
        name="Join materials",
        description="Join materials",
        default=True,
    )
    chk_bonespace: BoolProperty(
        name="Convert bone orientation - WARNING",
        description="Convert bone orientation - WARNING: this re-orients bones authored at source and will BREAK ALL "
                    "EXISTING ANIMATIONS. Only use this option if you are going to fully re-animate the model.",
        default=False,
    )
    # fmt:on

    def draw(self, context):
        box = self.layout.box()
        box.label(text="Settings:", icon="IMPORT")
        box.prop(self, "chk_mesh")
        if self.chk_mesh:
            mesh_settings = box.box()
            split = mesh_settings.split(factor=0.1)
            _, col = split.column(), split.column()
            col.prop(self, "chk_joinmats")
        box.prop(self, "chk_skel")
        box.prop(self, "chk_locs")
        # box.prop(self, 'chk_bonespace')  # TODO: works but overcomplicates things, disabled for now

    def execute(self, context):
        try:
            import_meshfile(
                self.filepath,
                imp_mesh=self.chk_mesh,
                imp_skel=self.chk_skel,
                imp_locs=self.chk_locs,
                join_materials=self.chk_joinmats,
                bonespace=self.chk_bonespace,
            )
            self.report({"INFO"}, pdx_i18n.rpt("[io_pdx_mesh] Finished importing {}").format(self.filepath))
            IO_PDX_SETTINGS.last_import_mesh = self.filepath

        except Exception as err:
            IO_PDX_LOG.warning("FAILED to import {0}".format(self.filepath))
            IO_PDX_LOG.error(err)
            self.report({"WARNING"}, "Mesh import failed!")
            self.report({"ERROR"}, str(err))
            raise

        return {"FINISHED"}

    def invoke(self, context, event):
        self.filepath = IO_PDX_SETTINGS.last_import_mesh or ""
        context.window_manager.fileselect_add(self)

        return {"RUNNING_MODAL"}


class IOPDX_OT_import_anim(Operator, ImportHelper):
    bl_idname = "io_pdx_mesh.import_anim"
    bl_description = bl_label = "Import PDX animation"
    bl_options = {"REGISTER", "UNDO"}

    # ImportHelper mixin class uses these
    filename_ext = ".anim"
    # fmt:off
    filter_glob: StringProperty(
        default="*.anim",
        options={"HIDDEN"},
        maxlen=255,
    )
    filepath: StringProperty(
        name="Import file Path",
        maxlen=1024,
    )
    int_start: IntProperty(
        name="Start frame",
        description="Start frame",
        default=1,
    )
    chk_legacy_keys: BoolProperty(
        name="Legacy keyframe method",
        description="Write keyframes the slow way (frame by frame, forcing a scene update). "
        "Only tick this if the default fast method gives a wrong result on this rig",
        default=False,
    )
    # fmt:on

    def draw(self, context):
        box = self.layout.box()
        box.label(text="Settings:", icon="IMPORT")
        box.prop(self, "int_start")
        box.prop(self, "chk_legacy_keys")

    def execute(self, context):
        try:
            import_animfile(
                self.filepath,
                frame_start=self.int_start,
                keyframe_mode="legacy" if self.chk_legacy_keys else "fast",
            )
            self.report({"INFO"}, pdx_i18n.rpt("[io_pdx_mesh] Finsihed importing {}").format(self.filepath))
            IO_PDX_SETTINGS.last_import_anim = self.filepath

        except Exception as err:
            IO_PDX_LOG.warning("FAILED to import {0}".format(self.filepath))
            IO_PDX_LOG.error(err)
            self.report({"WARNING"}, "Animation import failed!")
            self.report({"ERROR"}, str(err))
            raise

        return {"FINISHED"}

    def invoke(self, context, event):
        self.filepath = IO_PDX_SETTINGS.last_import_anim or ""
        context.window_manager.fileselect_add(self)

        return {"RUNNING_MODAL"}


class IOPDX_OT_export_mesh(Operator, ExportHelper):
    bl_idname = "io_pdx_mesh.export_mesh"
    bl_description = bl_label = "Export PDX mesh"
    bl_options = {"REGISTER", "UNDO"}

    # ExportHelper mixin class uses these
    filename_ext = ".mesh"
    # fmt:off
    filter_glob: StringProperty(
        default="*.mesh",
        options={"HIDDEN"},
        maxlen=255,
    )
    filepath: StringProperty(
        name="Export file Path",
        maxlen=1024,
    )
    chk_mesh: BoolProperty(
        name="Meshes",
        description="Export meshes",
        default=True,
    )
    chk_mesh_blendshape: BoolProperty(
        name="As blendshape",
        description="Export meshes as blendshapes",
        default=False,
    )
    chk_skel: BoolProperty(
        name="Skeleton",
        description="Export related armatures",
        default=True,
    )
    chk_locs: BoolProperty(
        name="Locators",
        description="Export empties data",
        default=True,
    )
    chk_selected: BoolProperty(
        name="Selected only",
        description="Filter export by selection",
        default=False,
    )
    chk_debug: BoolProperty(
        name="[debug options]",
        description="Non-standard options",
        default=False,
    )
    chk_split_vtx: BoolProperty(
        name="Split all vertices",
        description="Splits all vertices (per triangle) during export",
        default=False,
    )
    ddl_sort_vtx: EnumProperty(
        name="Sort vertices",
        description="Sort all vertex data by id during export",
        items=(
            ("+", "Incr", "Ascending id sort"),
            ("~", "Native", "Blender native order"),
            ("-", "Decr", "Descending id sort")
        ),
        default="+",
    )
    chk_plain_txt: BoolProperty(
        name="Also export plain text",
        description="Exports a plain text file along with binary",
        default=False,
    )
    # fmt:on

    def draw(self, context):
        box = self.layout.box()
        box.label(text="Settings:", icon="EXPORT")
        box.prop(self, "chk_mesh")
        if self.chk_mesh:
            mesh_settings = box.box()
            split = mesh_settings.split(factor=0.1)
            _, col = split.column(), split.column()
            col.prop(self, "chk_mesh_blendshape")
        box.prop(self, "chk_skel")
        box.prop(self, "chk_locs")
        box.prop(self, "chk_selected")
        box.prop(self, "chk_debug")
        if self.chk_debug:
            debug_settings = box.box()
            split = debug_settings.split(factor=0.1)
            _, col = split.column(), split.column()
            col.alignment = "RIGHT"
            col.prop(self, "chk_split_vtx")
            col.prop(self, "ddl_sort_vtx")
            col.prop(self, "chk_plain_txt")

    def execute(self, context):
        try:
            export_meshfile(
                self.filepath,
                exp_mesh=self.chk_mesh,
                exp_skel=self.chk_skel,
                exp_locs=self.chk_locs,
                exp_selected=self.chk_selected,
                as_blendshape=self.chk_mesh_blendshape,
                debug_mode=self.chk_debug,
                split_verts=self.chk_split_vtx,
                sort_verts=self.ddl_sort_vtx,
                plain_txt=self.chk_plain_txt,
            )
            self.report({"INFO"}, pdx_i18n.rpt("[io_pdx_mesh] Finsihed exporting {}").format(self.filepath))
            IO_PDX_SETTINGS.last_export_mesh = self.filepath

        except Exception as err:
            IO_PDX_LOG.warning("FAILED to export {0}".format(self.filepath))
            IO_PDX_LOG.error(err)
            self.report({"WARNING"}, "Mesh export failed!")
            self.report({"ERROR"}, str(err))
            raise

        return {"FINISHED"}

    def invoke(self, context, event):
        self.filepath = IO_PDX_SETTINGS.last_export_mesh or ""
        context.window_manager.fileselect_add(self)

        return {"RUNNING_MODAL"}


class IOPDX_OT_export_anim(Operator, ExportHelper):
    bl_idname = "io_pdx_mesh.export_anim"
    bl_description = bl_label = "Export PDX animation"
    bl_options = {"REGISTER", "UNDO"}

    # ExportHelper mixin class uses these
    filename_ext = ".anim"
    # fmt:off
    filter_glob: StringProperty(
        default="*.anim",
        options={"HIDDEN"},
        maxlen=255,
    )
    filepath: StringProperty(
        name="Export file Path",
        maxlen=1024,
    )
    int_start: IntProperty(
        name="Start frame",
        description="Custom start frame",
        default=1,
    )
    int_end: IntProperty(
        name="End frame",
        description="Custom end frame",
        default=100,
    )
    chk_uniform_scale: BoolProperty(
        name="Uniform scale only",
        description="Exports only uniform scale animation data, newer games support non-uniformly scaled bones",
        default=True,
    )
    chk_debug: BoolProperty(
        name="[debug options]",
        description="Non-standard options",
        default=False,
    )
    chk_plain_txt: BoolProperty(
        name="Also export plain text",
        description="Exports a plain text file along with binary",
        default=False,
    )
    # fmt:on

    def draw(self, context):
        settings = context.scene.io_pdx_export

        box = self.layout.box()
        box.label(text="Settings:", icon="EXPORT")
        box.prop(settings, "custom_range")
        if settings.custom_range:
            range_settings = box.box()
            range_settings.use_property_split = True
            col = range_settings.column()
            col.prop(self, "int_start")
            col.prop(self, "int_end")
        box.prop(self, "chk_uniform_scale")
        box.prop(self, "chk_debug")
        if self.chk_debug:
            debug_settings = box.box()
            split = debug_settings.split(factor=0.1)
            _, col = split.column(), split.column()
            col.alignment = "RIGHT"
            col.prop(self, "chk_plain_txt")

    def execute(self, context):
        settings = context.scene.io_pdx_export

        try:
            if settings.custom_range:
                start, end = self.int_start, self.int_end
            else:
                start, end = context.scene.frame_start, context.scene.frame_end

            export_animfile(
                self.filepath,
                frame_start=start,
                frame_end=end,
                debug_mode=self.chk_debug,
                uniform_scale=self.chk_uniform_scale,
                plain_txt=self.chk_plain_txt,
            )

            self.report({"INFO"}, pdx_i18n.rpt("[io_pdx_mesh] Finsihed exporting {}").format(self.filepath))
            IO_PDX_SETTINGS.last_export_anim = self.filepath

        except Exception as err:
            IO_PDX_LOG.warning("FAILED to export {0}".format(self.filepath))
            IO_PDX_LOG.error(err)
            self.report({"WARNING"}, "Animation export failed!")
            self.report({"ERROR"}, str(err))
            raise

        return {"FINISHED"}

    def invoke(self, context, event):
        self.filepath = IO_PDX_SETTINGS.last_export_anim or ""
        context.window_manager.fileselect_add(self)

        return {"RUNNING_MODAL"}


class IOPDX_OT_show_axis(Operator):
    bl_idname = "io_pdx_mesh.show_axis"
    bl_description = bl_label = "Show / hide local axis"
    bl_options = {"REGISTER"}

    # fmt:off
    show: BoolProperty(
        default=True,
    )
    data_type: EnumProperty(
        name="Data type",
        items=(
            ("EMPTY", "Empty", "Empty", 1),
            ("ARMATURE", "Armature", "Armature", 2)
        ),
    )
    # fmt:on

    def execute(self, context):
        set_local_axis_display(self.show, self.data_type)
        return {"FINISHED"}


class IOPDX_OT_ignore_bone(Operator):
    bl_idname = "io_pdx_mesh.ignore_bone"
    bl_description = bl_label = "Ignore / Unignore selected bones"
    bl_options = {"REGISTER"}

    # fmt:off
    state: BoolProperty(
        default=False,
    )
    # fmt:on

    def execute(self, context):
        set_ignore_joints(self.state)
        return {"FINISHED"}


""" ====================================================================================================================
    UI classes for the import/export tool.
========================================================================================================================
"""


class PDXUI(object):
    bl_category = "PDX Blender Tools"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"


class IOPDX_PT_PDXblender_info(PDXUI, Panel):
    # bl_idname = 'panel.io_pdx_mesh.help'
    bl_label = "Info"
    bl_options = {"HIDE_HEADER"}
    panel_order = 0
    # 主面板（粒子/模型/实体浏览器）固定在最上，这一组顺次排在它后面
    bl_order = 1

    def draw(self, context):
        self.layout.label(text=pdx_i18n.iface("PDX Blender Tools - v{}").format(github.CURRENT_VERSION), icon="TOOL_SETTINGS")

        row = self.layout.row(align=True)
        split = row.split(factor=0.85, align=True)
        col1, col3 = split.column(align=True), split.column(align=True)
        # 只有确实查到线上版本、且线上比本地新时才给更新按钮；
        # 检查失败时 LATEST_VERSION 是 0.0/None，照旧画会谎报「UPDATE - v0.0」。
        if github.AT_LATEST is False and github.UPDATE_KNOWN:
            split = col1.split(factor=0.4, align=True)
            col1, col2 = split.column(align=True), split.column(align=True)
            btn_txt = pdx_i18n.iface("UPDATE - v{}").format(github.LATEST_VERSION)
            if hasattr(github, 'LATEST_URL') and isinstance(github.LATEST_URL, dict):
                update_url = github.LATEST_URL.get("blender", github.LATEST_RELEASE)
            else:
                update_url = github.LATEST_RELEASE
            col2.operator("wm.url_open", icon="OUTLINER_OB_LIGHT", text=btn_txt).url = str(update_url)
        col1.operator("wm.url_open", icon="FUND", text="Donate",
                      text_ctxt=pdx_i18n.CTX).url = str(IO_PDX_INFO["sponsor_url"])
        popup = col3.operator("io_pdx_mesh.popup_message", icon="INFO", text="")
        popup.msg_text = github.LATEST_NOTES
        popup.msg_icon = "INFO"
        popup.msg_width = 450


class IOPDX_PT_PDXblender_file(PDXUI, Panel):
    # bl_idname = 'panel.io_pdx_mesh.file'
    bl_label = "File"
    panel_order = 1
    bl_order = 2

    def draw(self, context):
        self.layout.label(text="Import:", icon="IMPORT")
        row = self.layout.row(align=True)
        row.operator("io_pdx_mesh.import_mesh", icon="MESH_CUBE", text="Load mesh ...")
        row.operator("io_pdx_mesh.import_anim", icon="RENDER_ANIMATION", text="Load anim ...")

        self.layout.label(text="Export:", icon="EXPORT")
        row = self.layout.row(align=True)
        row.operator("io_pdx_mesh.export_mesh", icon="MESH_CUBE", text="Save mesh ...")
        row.operator("io_pdx_mesh.export_anim", icon="RENDER_ANIMATION", text="Save anim ...")


class IOPDX_PT_PDXblender_tools(PDXUI, Panel):
    # bl_idname = 'panel.io_pdx_mesh.tools'
    bl_label = "Tools"
    panel_order = 2
    bl_order = 3

    def draw(self, context):
        col = self.layout.column(align=True)

        col.label(text="PDX materials:")
        row = col.row(align=True)
        row.operator("io_pdx_mesh.material_create_popup", icon="MATERIAL", text="Create")
        row.operator("io_pdx_mesh.material_edit_popup", icon="SHADING_TEXTURE", text="Edit")
        col.separator()

        col.label(text="PDX bones:")
        row = col.row(align=True)
        op_ignore_bone = row.operator("io_pdx_mesh.ignore_bone", icon="GROUP_BONE", text="Ignore bones")
        op_ignore_bone.state = True
        op_unignore_bone = row.operator("io_pdx_mesh.ignore_bone", icon="BONE_DATA", text="Unignore bones")
        op_unignore_bone.state = False
        col.separator()

        # col.label(text='PDX animations:')
        # row = col.row(align=True)
        # row.operator('io_pdx_mesh.popup_message', icon='IPO_BEZIER', text='Create')
        # row.operator('io_pdx_mesh.popup_message', icon='NORMALIZE_FCURVES', text='Edit')
        # col.separator()

        col.label(text="PDX meshes:")
        row = col.row(align=True)
        row.operator("io_pdx_mesh.mesh_index_popup", icon="SORTALPHA", text="Set mesh order")


class IOPDX_PT_PDXblender_display(PDXUI, Panel):
    # bl_idname = 'panel.io_pdx_mesh.display'
    bl_label = "Display"
    bl_options = {"DEFAULT_CLOSED"}
    panel_order = 3
    bl_order = 4

    def draw(self, context):
        col = self.layout.column(align=True)

        col.label(text="Display local axes:")
        row = col.row(align=True)
        op_show_bone_axis = row.operator("io_pdx_mesh.show_axis", icon="OUTLINER_OB_ARMATURE", text="Show on bones")
        op_show_bone_axis.show = True
        op_show_bone_axis.data_type = "ARMATURE"
        op_hide_bone_axis = row.operator("io_pdx_mesh.show_axis", icon="OUTLINER_DATA_ARMATURE", text="Hide on bones")
        op_hide_bone_axis.show = False
        op_hide_bone_axis.data_type = "ARMATURE"
        row = col.row(align=True)
        op_show_loc_axis = row.operator("io_pdx_mesh.show_axis", icon="OUTLINER_OB_EMPTY", text="Show on empties")
        op_show_loc_axis.show = True
        op_show_loc_axis.data_type = "EMPTY"
        op_hide_loc_axis = row.operator("io_pdx_mesh.show_axis", icon="OUTLINER_DATA_EMPTY", text="Hide on empties")
        op_hide_loc_axis.show = False
        op_hide_loc_axis.data_type = "EMPTY"


class IOPDX_PT_PDXblender_setup(PDXUI, Panel):
    # bl_idname = 'panel.io_pdx_mesh.setup'
    bl_label = "Setup"
    bl_options = {"DEFAULT_CLOSED"}
    panel_order = 4
    bl_order = 5

    def draw(self, context):
        settings = context.scene.io_pdx_settings

        self.layout.prop(settings, "setup_engine")
        row = self.layout.row(align=True)
        row.label(text="Animation:")
        row.prop(context.scene.render, "fps", text="FPS")


class IOPDX_PT_PDXblender_help(PDXUI, Panel):
    # bl_idname = 'panel.io_pdx_mesh.help'
    bl_label = "Help"
    bl_parent_id = "IOPDX_PT_PDXblender_info"
    bl_options = {"DEFAULT_CLOSED"}
    panel_order = 5
    bl_order = 6

    def draw(self, context):
        col = self.layout.column(align=True)

        col.operator("wm.url_open", icon="QUESTION", text="Addon Wiki").url = IO_PDX_INFO["doc_url"]
        col.operator("wm.url_open", icon="QUESTION", text="Paradox forums").url = IO_PDX_INFO["forum_url"]
        col.operator("wm.url_open", icon="QUESTION", text="Source code").url = IO_PDX_INFO["website"]


""" ====================================================================================================================
    Add-on preferences: where the game and any extra folders live.

    These are read by the asset index (pdx_particles/index.py) through
    bpy.context.preferences.addons[<module>].preferences, so the property names below are
    part of that contract and must not be renamed.
========================================================================================================================
"""


def get_addon_preferences():
    """Return this add-on's AddonPreferences, or None when it isn't registered/available.

    The key held in preferences.addons depends on how the add-on was installed: an
    extension installs as "bl_ext.<repo>.io_pdx_mesh", a legacy add-on as "io_pdx_mesh".
    Both are tried so callers work either way.
    """
    addons = bpy.context.preferences.addons
    for key in (base_package, IO_PDX_INFO.get("id", "")):
        if not key:
            continue
        addon = addons.get(key)
        if addon is not None and addon.preferences is not None:
            return addon.preferences
    return None


# fmt:off
class PDXIndexRoot(PropertyGroup):
    # Sorting only: pdx_blender/__init__.py registers in panel_order order, and a
    # PropertyGroup must be registered before the AddonPreferences that references it
    # (otherwise CollectionProperty raises "expected an RNA type ... missing bl_rna").
    panel_order = -1

    path: StringProperty(
        name="Folder",
        description="Folder to index for models, particles and entities",
        subtype="DIR_PATH",
        default="",
    )


class IOPDX_UL_index_root_list(UIList):
    """Extra index folders; the path is edited inline in the row."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.prop(item, "path", text="", emboss=False, icon="FILE_FOLDER")


class IOPDX_AddonPreferences(AddonPreferences):
    bl_idname = base_package
    # Registered last (see PDXIndexRoot): every class this one references must exist first.
    panel_order = 100

    vanilla_root: StringProperty(
        name="Game folder",
        description="Root folder of the base game install; used to resolve assets that "
                    "mods reference but do not ship",
        subtype="DIR_PATH",
        default="",
    )
    extra_roots: CollectionProperty(
        type=PDXIndexRoot,
    )
    extra_root_index: IntProperty(
        default=0,
    )

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text="Game folder:", icon="FILE_FOLDER")
        row = box.row(align=True)
        row.prop(self, "vanilla_root", text="")
        row.operator("io_pdx_mesh.detect_game_root", icon="VIEWZOOM", text="Detect")

        box.separator()
        box.label(text="Extra folders to index:", icon="FILE_FOLDER")
        row = box.row()
        row.template_list("IOPDX_UL_index_root_list", "", self, "extra_roots",
                          self, "extra_root_index", rows=3)
        col = row.column(align=True)
        col.operator("io_pdx_mesh.add_index_root", icon="ADD", text="")
        col.operator("io_pdx_mesh.remove_index_root", icon="REMOVE", text="")

        # Status line. Prefer what the index layer reports on the scene; fall back to a
        # count of configured folders. Neither is required, so drawing never fails here.
        status = ""
        scene = getattr(context, "scene", None)
        if scene is not None:
            status = getattr(scene, "io_pdx_index_status", "") or ""
        if not status:
            n_extra = len([entry for entry in self.extra_roots if entry.path])
            status = pdx_i18n.iface("{0} game folder, {1} extra folder(s) configured").format(
                "1" if self.vanilla_root else "no", n_extra)
        box.label(text=status, icon="INFO")
# fmt:on


class IOPDX_OT_detect_game_root(Operator):
    bl_idname = "io_pdx_mesh.detect_game_root"
    bl_description = bl_label = "Find the game install folder"
    bl_options = {"REGISTER"}

    def execute(self, context):
        prefs = get_addon_preferences()
        if prefs is None:
            self.report({"WARNING"}, "Add-on preferences are not available!")
            return {"CANCELLED"}

        try:
            roots = detect_game_roots()
        except Exception as err:  # noqa: BLE001
            self.report({"WARNING"}, pdx_i18n.rpt("Game folder detection failed: {0}").format(err))
            return {"CANCELLED"}

        if not roots:
            self.report({"WARNING"}, "No game install found - set the game folder manually")
            return {"CANCELLED"}

        # detect_game_roots() sorts usable roots first, but the rest are still returned so
        # the user can point at a game whose layout we don't recognise.
        pick = roots[0]
        prefs.vanilla_root = pick
        if looks_like_game_root(pick):
            self.report({"INFO"}, pdx_i18n.rpt("Game folder: {0}").format(pick))
        else:
            self.report({"WARNING"},
                        pdx_i18n.rpt("Found {0} but it has no gfx/particles or gfx/models - "
                        "set the folder manually").format(pick))
        return {"FINISHED"}


class IOPDX_OT_add_index_root(Operator):
    bl_idname = "io_pdx_mesh.add_index_root"
    bl_description = bl_label = "Add a folder to index"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        prefs = get_addon_preferences()
        if prefs is None:
            self.report({"WARNING"}, "Add-on preferences are not available!")
            return {"CANCELLED"}

        # Left empty on purpose: the user types or picks the folder in the list row.
        prefs.extra_roots.add()
        prefs.extra_root_index = len(prefs.extra_roots) - 1
        return {"FINISHED"}


class IOPDX_OT_remove_index_root(Operator):
    bl_idname = "io_pdx_mesh.remove_index_root"
    bl_description = bl_label = "Remove the selected folder"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        prefs = get_addon_preferences()
        if prefs is None:
            self.report({"WARNING"}, "Add-on preferences are not available!")
            return {"CANCELLED"}

        index = prefs.extra_root_index
        if not 0 <= index < len(prefs.extra_roots):
            self.report({"WARNING"}, "Select a folder first")
            return {"CANCELLED"}

        prefs.extra_roots.remove(index)
        prefs.extra_root_index = max(0, index - 1)
        return {"FINISHED"}
