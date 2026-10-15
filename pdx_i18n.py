"""
PDX add-on UI translations.

All user-facing text is keyed here by the English source string written in the
code (the msgid). Blender resolves a msgid under different *contexts* depending
on which widget draws it, so the same entry is registered under all of them:

  "*"              panel titles, property names, layout labels, tooltips,
                   self.report() messages
  "Operator"       operator button text and operator class labels
  CTX              our own namespace

CTX exists because an add-on entry registered under "*" is shadowed by
Blender's built-in catalog whenever the msgid also exists there (measured:
"Create" still resolved to 创建 with an add-on entry present). Strings we share
with Blender are therefore tagged at their draw sites with CTX, which makes our
wording authoritative instead of Blender's.

Strings built with str.format() are a separate case: Blender only looks up
plain literals drawn by the UI, and a formatted result is an ordinary runtime
string, so those call sites translate the template first via iface()/rpt().
"""

import bpy  # type: ignore

# Our own translation context; keeps our wording authoritative for strings that
# Blender's built-in catalog translates too.
CTX = "io_pdx_mesh"

# msgid -> Simplified Chinese. The msgid must match the source literal exactly;
# placeholders ({0}, {}) have to be kept verbatim.
_ZH = {
    "   unused keys: {0}": "   未使用的键：{0}",
    "(deleted)": "（已删除）",
    "1 unit = 1; scrub timeline / play": "1 单位 = 1；拖动时间轴 / 播放",
    "[debug options]": "[调试选项]",
    "[io_pdx_mesh] Finished importing {}": "[io_pdx_mesh] 已导入 {}",
    "[io_pdx_mesh] Finsihed exporting {}": "[io_pdx_mesh] 已导出 {}",
    "[io_pdx_mesh] Finsihed importing {}": "[io_pdx_mesh] 已导入 {}",
    "Add a folder (game root or mod root) to scan for particles":
        "添加要扫描粒子的目录（游戏根目录或 mod 根目录）",
    "Add a folder to index": "添加要索引的目录",
    "Add folder ...": "添加目录 ...",
    "Add game to scan list": "把游戏目录加入扫描列表",
    "Add-on preferences are not available!": "插件偏好设置不可用！",
    "Addon Wiki": "插件 Wiki",
    "All": "全部",
    "Already in the list": "已在列表中",
    "Already in the scan list": "已在扫描列表中",
    "Also export plain text": "同时导出纯文本",
    "Also list the vanilla game's own particles (they show up as extra rows)":
        "同时列出原版游戏自带的粒子（会显示为额外条目）",
    "anim:{0}": "动画:{0}",
    "Animation export failed!": "动画导出失败！",
    "Animation file not found: {0}": "找不到动画文件：{0}",
    "Animation import failed!": "动画导入失败！",
    "Animation import failed: {0}": "动画导入失败：{0}",
    "Animation:": "动画:",
    "Animations ({0}):": "动画（{0}）：",
    "Apply material to selected?": "把材质应用到选中对象？",
    "As blendshape": "作为混合变形导出",
    "Building list ...": "正在建立列表 ...",
    "Building index ...": "正在建立索引 ...",
    "Categories ({0} off):": "分类（已关 {0}）：",
    "Clear": "清空",
    "Clear loaded": "清除已加载",
    "Convert bone orientation - WARNING": "转换骨骼朝向 - 警告",
    "Convert bone orientation - WARNING: this re-orients bones authored at source "
    "and will BREAK ALL EXISTING ANIMATIONS. Only use this option if you are going "
    "to fully re-animate the model.":
        "转换骨骼朝向 - 警告：这会重定向源文件里骨骼的朝向，并破坏所有现有动画。"
        "只有在你打算完全重做该模型的动画时才使用。",
    "Create": "创建",
    "Create a PDX material": "新建 PDX 材质",
    "Custom end frame": "自定义结束帧",
    "Custom range": "自定义范围",
    "Custom start frame": "自定义起始帧",
    "Custom type:": "自定义类型：",
    "Data type": "数据类型",
    "Delete every object this addon loaded (previews and models)":
        "删除本插件加载的所有对象（预览与模型）",
    "Detect": "自动检测",
    "Display": "显示",
    "Display local axes:": "显示局部轴：",
    "Donate": "捐赠",
    "Edit": "编辑",
    "Edit a PDX material": "编辑 PDX 材质",
    "Emitters ({0}, what actually loaded):": "发射器（{0} 个，实际加载的）：",
    "Emitters": "发射器",
    "Expand the emitter diagnostics list": "展开发射器诊断列表",
    "Enable all": "全部启用",
    "End frame": "结束帧",
    "Engine": "引擎",
    "Entity '{0}' has no loadable mesh or particle":
        "实体 '{0}' 没有可加载的网格或粒子",
    "Export empties data": "导出空物体数据",
    "Export file Path": "导出文件路径",
    "Export meshes": "导出网格",
    "Export meshes as blendshapes": "把网格作为混合变形导出",
    "Export PDX animation": "导出 PDX 动画",
    "Export PDX mesh": "导出 PDX 网格",
    "Export related armatures": "导出相关骨架",
    "Exports a plain text file along with binary": "除二进制外再导出一份纯文本",
    "Exports only uniform scale animation data, newer games support non-uniformly "
    "scaled bones": "只导出等比缩放的动画数据；较新的游戏支持非等比缩放的骨骼",
    "Extra folders to index:": "额外索引目录：",
    "Face the viewport": "朝向视口",
    "File:": "文件：",
    "UPDATE - v{}": "更新 - v{}",
    "{0} game folder, {1} extra folder(s) configured": "已配置 {0} 个游戏目录、{1} 个附加目录",
    "Skipped folders ({0}):": "已跳过的目录（{0} 个）：",
    "{0} x{1}": "{0} ×{1}",
    "File not found: {0}": "找不到文件：{0}",
    "Filter export by selection": "只导出选中对象",
    "Find the game install folder": "查找游戏安装目录",
    "Folder": "目录",
    "Folder to index for models, particles and entities":
        "要索引模型、粒子与实体的目录",
    "Found {0} but it has no gfx/particles or gfx/models - set the folder manually":
        "找到 {0}，但其中没有 gfx/particles 或 gfx/models - 请手动设置目录",
    "Found {0} but it has no gfx/particles or gfx/models — pick the right folder manually":
        "找到 {0}，但其中没有 gfx/particles 或 gfx/models — 请手动选择正确目录",
    "Found: {0}": "已找到：{0}",
    "FPS": "帧率",
    "game": "原版",
    "Game folder": "游戏目录",
    "Game folder detection failed: {0}": "游戏目录检测失败：{0}",
    "Game folder:": "游戏目录:",
    "Game folder: {0}": "游戏目录：{0}",
    "Game install folder; used only to resolve textures a mod does not ship. "
    "Leave empty or press Detect to find it automatically":
        "游戏安装目录；仅用于解析 mod 未自带的贴图。留空或点「自动检测」自动查找",
    "Help": "帮助",
    "Hidden": "隐藏",
    "Hide this folder's entries from the asset list. The folder is still indexed "
    "and still used to resolve textures":
        "在资产列表中隐藏该目录的条目。目录仍参与索引，并继续用于解析贴图",
    "Hide the game folder's entries from the asset list. The folder is still "
    "indexed and still used to resolve textures":
        "在资产列表中隐藏游戏目录的条目。目录仍参与索引，并继续用于解析贴图",
    "Hide on bones": "在骨骼上隐藏",
    "Hide on empties": "在空物体上隐藏",
    "Ignore / Unignore selected bones": "忽略 / 取消忽略选中骨骼",
    "Ignore bones": "忽略骨骼",
    "Import file Path": "导入文件路径",
    "Import locators": "导入挂点",
    "Import mesh": "导入网格",
    "Import PDX animation": "导入 PDX 动画",
    "Import PDX mesh": "导入 PDX 网格",
    "Import skeleton": "导入骨架",
    "Indexed {0} files in {1}s": "已索引 {0} 个文件，用时 {1} 秒",
    "Info": "信息",
    "Join materials": "合并材质",
    "Legacy keyframe method": "旧版关键帧方式",
    "Load paths:": "加载路径：",
    "Open Preferences": "打开偏好设置",
    "Open the add-on preferences where the game folder and extra index folders are "
    "configured": "打开插件偏好设置，游戏目录与额外索引目录在那里配置",
    "Preferences unavailable": "偏好设置不可用",
    "Game folder not set": "未设置游戏目录",
    "No extra folders configured": "未配置额外目录",
    "Load anim ...": "加载动画 ...",
    "Load animation": "加载动画",
    "Load from file ...": "从文件加载 ...",
    "Load mesh ...": "加载网格 ...",
    "Load particles ...": "加载粒子 ...",
    "Load PDX particles (.gfx/.asset) and build a scrub-able preview":
        "加载 PDX 粒子（.gfx/.asset）并生成可拖动时间轴的预览",
    "Load selected": "加载选中项",
    "Load the selected model / particle / entity": "加载选中的模型 / 粒子 / 实体",
    "Load the selected skeletal animation onto the scene armature (retimed to the "
    "scene fps so its duration is unchanged)":
        "把选中的骨骼动画加载到场景骨架上（按场景帧率重定时，时长不变）",
    "Loaded animation {0}": "已加载动画 {0}",
    "Loaded entity {0} ({1} particle(s))": "已加载实体 {0}（{1} 个粒子）",
    "{0} (no body)": "{0}（无本体）",
    "{0} particle(s)": "{0} 个粒子",
    "no file": "无文件",
    "Nothing to list on this tab.": "此标签页没有可列出的内容。",
    "Switch tab, clear the search, or Rescan.": "切换标签页、清空搜索，或重新扫描。",
    "{0} shown / {1} matched  ·  index {2} files in {3}s":
        "显示 {0} / 匹配 {1}  ·  索引 {2} 个文件耗时 {3} 秒",
    "Loaded model {0}": "已加载模型 {0}",
    "Loaded {0} particle system(s)": "已加载 {0} 个粒子系统",
    "Loaded: {0}": "已加载：{0}",
    "Locators": "挂点",
    "Look for Paradox game installs in the Steam libraries and use the first one "
    "as the texture fallback root":
        "在 Steam 库中查找 Paradox 游戏安装，并用找到的第一个作为贴图回退目录",
    "Material name": "材质名",
    "Material type": "材质类型",
    "Mesh export failed!": "网格导出失败！",
    "Mesh import failed!": "网格导入失败！",
    "Mesh import failed: {0}": "网格导入失败：{0}",
    "Mesh index list actions": "网格序号列表操作",
    "Meshes": "网格",
    "No armature in the scene — load a model first":
        "场景里没有骨架 — 请先加载模型",
    "No body found for type '{0}'": "类型 '{0}' 找不到对应本体",
    "No folder configured yet.": "尚未配置目录。",
    "No game folder set — press Detect first": "未设置游戏目录 — 请先点「自动检测」",
    "No game install found - set the game folder manually":
        "未找到游戏安装目录 - 请手动设置游戏目录",
    "No game install found — set the folder manually":
        "未找到游戏安装目录 — 请手动设置目录",
    "No particle previews loaded": "尚未加载任何粒子预览",
    "No particle systems found in {0}": "{0} 中未找到粒子系统",
    "No particles at this frame — scrub the timeline":
        "当前帧没有粒子 — 请拖动时间轴",
    "Non-standard options": "非标准选项",
    "Not a folder: {0}": "不是目录：{0}",
    "Nothing found — set the game folder or add a mod folder":
        "什么都没找到 — 请设置游戏目录或添加 mod 目录",
    "or add a mod folder there, then Rescan.": "或在其中添加 mod 目录，然后重新扫描。",
    "Paradox forums": "Paradox 论坛",
    "Particles:": "粒子：",
    "Particles": "粒子",
    "Particles not loaded (missing mount point or body): {0}":
        "以下粒子未加载（挂点或本体缺失）：{0}",
    "Quad shading": "面片着色",
    "Quads": "面片",
    "How particle quads are shaded: blended, dithered or fully opaque":
        "粒子面片的着色方式：混合、抖动镂空或完全不透明",
    "PDX Blender Tools": "PDX Blender 工具",
    "PDX Blender Tools - v{}": "PDX Blender 工具 - v{}",
    "PDX Tools": "PDX 工具",
    "PDX bones:": "PDX 骨骼：",
    "PDX materials:": "PDX 材质：",
    "PDX meshes:": "PDX 网格：",
    "pdx pointer": "pdx 指针",
    "Popup Message": "弹窗提示",
    "Preview not live — Load or Reload again": "预览未生效 — 请重新加载或重载",
    "Previewing {0}": "正在预览 {0}",
    "Random seed for per-particle variation. Same seed gives the same result every "
    "time; change it to reshuffle every system":
        "每个粒子随机变化的种子。同一 seed 每次结果相同；改它可让所有系统重新随机",
    "Re-parse the source files and rebuild previews":
        "重新解析源文件并重建预览",
    "Rebuild the asset index (run after installing or updating mods)":
        "重建资产索引（安装或更新 mod 后运行）",
    "Reload": "重载",
    "Reload from source": "从源文件重载",
    "Reloaded {0}/{1} file(s)": "已重载 {0}/{1} 个文件",
    "Remove": "移除",
    "Remove the selected folder": "移除选中目录",
    "Remove the selected folder from the scan list": "从扫描列表中移除选中目录",
    "Removed {0} preview(s), {1} object(s)": "已移除 {0} 个预览、{1} 个对象",
    "Rescan": "重新扫描",
    "Root": "根目录",
    "Root folder of the base game install; used to resolve assets that mods "
    "reference but do not ship":
        "游戏本体的根目录；用于解析 mod 引用但未自带的资产",
    "Save anim ...": "保存动画 ...",
    "Save mesh ...": "保存网格 ...",
    "Scene fps changed — Load again to retime":
        "场景帧率已改变 — 请重新加载以重定时",
    "Search": "搜索",
    "Seed": "随机种",
    "Select a folder first": "请先选择一个目录",
    "Select an animation in the list first": "请先在列表中选择一个动画",
    "Select an entry in the list first": "请先在列表中选择一个条目",
    "Selected material": "选中的材质",
    "Selected only": "仅选中对象",
    "Set it in Preferences > Add-ons > IO PDX Mesh,":
        "请在「偏好设置 > 插件 > IO PDX Mesh」中设置，",
    "Set mesh index on PDX meshes": "设置 PDX 网格的序号",
    "Set mesh order": "设置网格顺序",
    "Settings:": "设置：",
    "Settings": "设置",
    "Shader": "着色器",
    "Show / hide local axis": "显示 / 隐藏局部轴",
    "Show on bones": "在骨骼上显示",
    "Show on empties": "在空物体上显示",
    "Show only entries whose name, pdxmesh name or particle name contains this":
        "只显示名称、pdxmesh 名或粒子名包含该字串的条目",
    "Skeleton": "骨架",
    "Sort all vertex data by id during export": "导出时按 id 排序所有顶点数据",
    "Sort vertices": "排序顶点",
    "Source code": "源代码",
    "Split all vertices": "拆分所有顶点",
    "Splits all vertices (per triangle) during export":
        "导出时拆分所有顶点（按三角形）",
    "Start frame": "起始帧",
    "Tab": "标签页",
    "This entry has no skeletal animation": "该条目没有骨骼动画",
    "Turn billboard particles toward the 3D viewport camera (off = keep using the "
    "scene camera / fixed fallback)":
        "让立牌粒子朝向 3D 视口镜头（关闭则继续使用场景相机 / 固定回退）",
    "Turn every category back on": "重新启用所有分类",
    "Uniform scale only": "仅等比缩放",
    "Unignore bones": "取消忽略骨骼",
    "Untick a category to hide it": "取消勾选分类可隐藏它",
    "Vanilla root": "原版根目录",
    "What to browse and load": "浏览与加载的来源",
    "Write keyframes the slow way (frame by frame, forcing a scene update). Only "
    "tick this if the default fast method gives a wrong result on this rig":
        "用旧方式写关键帧（逐帧并强制刷新场景）。只有默认的快速方式在该骨架上"
        "结果不正确时才勾选",
    "{0}  ({1} emitters)": "{0}  （{1} 个发射器）",
    "{0} particles at frame {1}": "第 {1} 帧有 {0} 个粒子",
    "{0}: {1}  pos{2}  {3}": "{0}: {1}  位置{2}  {3}",
    # Enum item labels (tabs, mesh-index actions, data type, vertex sort).
    # "Particles" is already defined above and is reused by the tab.
    "Models": "模型",
    "Entity": "实体",
    "Up": "上移",
    "Down": "下移",
    "Incr": "升序",
    "Native": "原生顺序",
    "Decr": "降序",
    "Empty": "空物体",
    "Armature": "骨架",
    "Blended": "混合",
    "Dithered": "抖动",
    "Opaque": "不透明",
    # Enum item descriptions
    "Load a .mesh on its own": "单独加载 .mesh",
    "Preview a particle system on its own": "单独预览粒子系统",
    "Load a model together with its mount-point particles":
        "连同挂点粒子一起加载模型",
    "Ascending id sort": "按 id 升序排序",
    "Blender native order": "Blender 原生顺序",
    "Descending id sort": "按 id 降序排序",
    "Semi-transparent, closest to how the effect looks in game":
        "半透明，最接近实机观感",
    "Still cut out by alpha, but dithered edges and no blending order":
        "仍按 alpha 镂空，但边缘是抖动硬边，不做混合排序",
    "Ignore alpha: each quad becomes a solid square, easiest to see position and size":
        "忽略 alpha：每片面片成实心方块，最容易看清位置与大小",
}

# Registered under every context a site can legitimately be drawn in; extra
# entries are harmless and keep the three from drifting apart.
_CONTEXTS = ("*", "Operator", CTX)

TRANSLATIONS = {
    "zh_HANS": {
        (ctx, msgid): text
        for ctx in _CONTEXTS
        for msgid, text in _ZH.items()
    }
}


def msgids():
    """Every msgid owned by this add-on (the English source text)."""
    return set(_ZH)


def iface(msgid):
    """Translate an interface string before formatting.

    Needed for messages built with str.format(): Blender only looks up plain
    literals drawn by the UI, and the formatted result is a runtime string, so
    the template has to be translated here first. Falls back to the msgid if the
    string is missing from the catalog - drawing must never break over a
    translation.
    """
    try:
        return bpy.app.translations.pgettext_iface(msgid, CTX)
    except Exception:  # noqa: BLE001
        return msgid


def rpt(msgid):
    """Same as iface() for operator reports.

    This one goes through pgettext_rpt, so it honours Blender's "Translate
    Reports" preference: with that preference switched off the English source
    text is returned even though the surrounding panel is translated.
    """
    try:
        return bpy.app.translations.pgettext_rpt(msgid, CTX)
    except Exception:  # noqa: BLE001
        return msgid


def register():
    # register() raises if the module name is already present (add-on reload);
    # drop a stale entry first.
    try:
        bpy.app.translations.unregister(__name__)
    except Exception:  # noqa: BLE001
        pass
    bpy.app.translations.register(__name__, TRANSLATIONS)


def unregister():
    try:
        bpy.app.translations.unregister(__name__)
    except Exception:  # noqa: BLE001 - already gone is fine
        pass
