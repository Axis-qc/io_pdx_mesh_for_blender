"""本地化检查：界面串是否都有词条、词条是否有孤儿、占位符是否一致。

跑法：
    & 'G:\\SteamLibrary\\steamapps\\common\\Blender\\blender.exe' -b --factory-startup \
      --python test/check_i18n.py

为什么两个来源都要用：
  * RNA 层（面板标题、算子名、属性名/tooltip、枚举项）用 **运行时自省** 枚举。
    这是权威来源，手写 AST 扫描器曾整批漏掉枚举项（items= 走辅助函数、
    项元组里首元素是 Name 不是常量），漏掉的正是界面标签页那几行。
  * text= / report() 这类绘制与消息串无法从 RNA 取，只能扫源码。

判定标准是**运行时取值**，不是「表里有这个键」：把语言切到 zh_HANS，逐条查表，
必须得到与英文原文不同的结果；切回 en_US 必须回落到英文原文。
"""
import ast
import io
import os
import re
import sys

sys.dont_write_bytecode = True

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(ROOT))

import bpy  # noqa: E402

PASS, FAIL = [], []
CJK = re.compile(r"[\u2e80-\u2fff\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")
PH = re.compile(r"\{[0-9]*\}|%[sdf]")


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  {0} {1}{2}".format("PASS" if cond else "FAIL", name,
                                "  — " + detail if detail else ""))


def enable_addon():
    for mod in ("io_pdx_mesh", "bl_ext.user_default.io_pdx_mesh"):
        try:
            if bpy.ops.preferences.addon_enable(module=mod) == {"FINISHED"}:
                return True
        except Exception:  # noqa: BLE001
            pass
    try:
        import io_pdx_mesh  # noqa: F401
        io_pdx_mesh.pdx_blender.register()
        return True
    except Exception:  # noqa: BLE001
        return False


def load_i18n_module():
    for name in ("io_pdx_mesh.pdx_i18n", "bl_ext.user_default.io_pdx_mesh.pdx_i18n"):
        if name in sys.modules:
            return sys.modules[name]
    for name, mod in list(sys.modules.items()):
        if name.endswith(".pdx_i18n"):
            return mod
    return None


def own_module_file(owner):
    try:
        return getattr(owner, "__module__", "") or ""
    except Exception:  # noqa: BLE001
        return ""


def is_ours(rna):
    """RNA defined by this add-on (module starts with the add-on package)."""
    for cls in (rna.owner,):
        pass
    return True


# --------------------------------------------------------------------------- #
# 1. RNA 层：运行时自省枚举本插件定义的界面串
# --------------------------------------------------------------------------- #
def rna_surface():
    """Return {msgid: set(role)} for every RNA label/description we define.

    Introspection notes (each learned the hard way):
      * `prop.srna` is always None, and `bl_rna.properties["bl_label"].default`
        is "" - the class label must be read off the class (`cls.bl_label`).
      * A class's own properties are the keys in `cls.__annotations__`; the RNA
        property table also carries everything inherited from Operator/Panel.
      * Enum item labels are only reachable through `prop.enum_items`.
    """
    found = {}
    pkg_prefixes = ("io_pdx_mesh", "bl_ext.user_default.io_pdx_mesh")

    def note(msgid, role):
        if msgid:
            found.setdefault(msgid, set()).add(role)

    def own_cls(cls):
        mod = getattr(cls, "__module__", "") or ""
        return any(mod.startswith(p) for p in pkg_prefixes)

    # classes defined by this add-on (panels, operators, property groups, UIList)
    for name in dir(bpy.types):
        cls = getattr(bpy.types, name)
        if not isinstance(cls, type) or not hasattr(cls, "bl_rna"):
            continue
        if not own_cls(cls):
            continue

        lbl = getattr(cls, "bl_label", None)
        if isinstance(lbl, str) and lbl:
            note(lbl, "class_label")
        desc = getattr(cls, "bl_description", None)
        if isinstance(desc, str) and desc:
            note(desc, "class_desc")

        rna = cls.bl_rna
        ann = getattr(cls, "__annotations__", {}) or {}
        for key in ann:
            prop = rna.properties.get(key)
            if prop is None:
                continue
            # skip names Blender auto-derived from the identifier (not authored)
            auto = key.replace("_", " ").title()
            if prop.name and prop.name != auto:
                note(prop.name, "property_name")
            if prop.description:
                note(prop.description, "property_desc")
            if prop.type == "ENUM":
                for item in prop.enum_items:
                    if item.name:
                        note(item.name, "enum_label")
                    if item.description:
                        note(item.description, "enum_desc")

    # Scene-level properties this add-on registers at runtime (io_pdx_* prefix)
    for key, prop in bpy.types.Scene.bl_rna.properties.items():
        if not key.startswith("io_pdx"):
            continue
        if prop.name:
            note(prop.name, "property_name")
        if prop.description:
            note(prop.description, "property_desc")
        if prop.type == "ENUM":
            for item in prop.enum_items:
                if item.name:
                    note(item.name, "enum_label")
                if item.description:
                    note(item.description, "enum_desc")
    return found


# --------------------------------------------------------------------------- #
# 2. 源码层：text= / report() 这类 RNA 取不到的串
# --------------------------------------------------------------------------- #
SRC_FILES = [
    "pdx_particles/ui.py",
    "pdx_blender/blender_ui.py",
    "pdx_blender/blender_import_export.py",
    "pdx_particles/parser.py",
    "pdx_particles/blender_preview.py",
    "pdx_particles/index.py",
    "pdx_particles/simulator.py",
    "pdx_blender/__init__.py",
    "pdx_data.py",
    "settings.py",
    "updater.py",
]
TEXT_KW_CALLS = {"label", "operator", "prop", "menu", "operator_menu_enum",
                 "prop_search", "template_list"}
PROP_CTORS = {
    "StringProperty", "BoolProperty", "IntProperty", "FloatProperty",
    "EnumProperty", "PointerProperty", "CollectionProperty",
    "FloatVectorProperty", "IntVectorProperty", "StringVectorProperty",
}


def _cname(node):
    if isinstance(node, ast.Call):
        f = node.func
        return f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
    return ""


def _build_src_surface():
    """Every user-facing string written in the source, with its role.

    Walks the tree structurally rather than scanning bare string constants: the
    naive version missed enum items (an `items=` value is a Name or a Tuple,
    never a Constant) and composed strings after wrapping (the `.format()`
    receiver becomes a Call, not a Constant). Both surfaced as bogus orphans.
    """
    found = {}

    def note(msgid, role, where):
        if isinstance(msgid, str) and msgid:
            found.setdefault(msgid, set()).add(role)

    def const_str(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    def unwrap_template(node):
        """msgid of a string template, whether raw or wrapped in iface()/rpt().

        Composed strings appear as `<template>.format(...)` and, once wrapped,
        as `pdx_i18n.iface("<template>").format(...)`, so both the `.format()`
        and the iface/rpt layer have to be peeled off.
        """
        s = const_str(node)
        if s is not None:
            return s
        if isinstance(node, ast.Call) and _cname(node) in ("iface", "rpt") and node.args:
            return const_str(node.args[0])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "format":
            return unwrap_template(node.func.value)
        return None

    def emit_enum_items(items_node, helpers, where):
        seqs = []
        if isinstance(items_node, ast.Call):
            fn = helpers.get(_cname(items_node))
            if fn is not None:
                for r in ast.walk(fn):
                    if isinstance(r, ast.Return) and r.value is not None:
                        seqs.append(r.value)
        else:
            seqs.append(items_node)
        for seq in seqs:
            if not isinstance(seq, (ast.List, ast.Tuple)):
                continue
            for el in seq.elts:
                if not isinstance(el, ast.Tuple) or len(el.elts) < 2:
                    continue
                # item shape: (identifier, label, description[, icon])
                note(const_str(el.elts[1]), "enum_label", where)
                if len(el.elts) > 2:
                    note(const_str(el.elts[2]), "enum_desc", where)

    for rel in SRC_FILES:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        src = io.open(path, encoding="utf-8").read()
        tree = ast.parse(src)
        parents = {}
        for n in ast.walk(tree):
            for c in ast.iter_child_nodes(n):
                parents[id(c)] = n
        helpers = {f.name: f for f in tree.body if isinstance(f, ast.FunctionDef)}

        # ---- keyword arguments: RNA declarations and layout overrides ----
        for n in ast.walk(tree):
            if not isinstance(n, ast.keyword) or n.arg is None:
                continue
            kw_parent = parents.get(id(n))
            on = _cname(kw_parent) if isinstance(kw_parent, ast.Call) else ""
            where = "%s:%d" % (rel, getattr(n.value, "lineno", 0))
            arg = n.arg
            if arg == "items" and on == "EnumProperty":
                emit_enum_items(n.value, helpers, where)
            elif arg in ("name", "bl_label") and on in PROP_CTORS:
                note(const_str(n.value), "property_name", where)
            elif arg == "description" and on in PROP_CTORS:
                note(const_str(n.value), "property_desc", where)
            elif arg in ("text", "label") and on in TEXT_KW_CALLS:
                note(unwrap_template(n.value), "layout_text", where)
            elif arg == "description" and on in TEXT_KW_CALLS:
                note(const_str(n.value), "layout_desc", where)

        # ---- class-body bl_label / bl_description ----
        for n in ast.walk(tree):
            if not isinstance(n, ast.Assign):
                continue
            for t in n.targets:
                nm = t.attr if isinstance(t, ast.Attribute) else (
                    t.id if isinstance(t, ast.Name) else "")
                if nm == "bl_label":
                    note(const_str(n.value), "class_label", "%s:%d" % (rel, n.lineno))
                elif nm == "bl_description":
                    note(const_str(n.value), "class_desc", "%s:%d" % (rel, n.lineno))
                elif nm == "bl_category":
                    # the sidebar tab title shared by every panel of this add-on
                    note(const_str(n.value), "category", "%s:%d" % (rel, n.lineno))

        # ---- self.report() messages (raw, wrapped, or %-formatted) ----
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and _cname(n) == "report"):
                continue
            where = "%s:%d" % (rel, n.lineno)
            for a in n.args:
                msg = unwrap_template(a)
                if msg is None and isinstance(a, ast.BinOp) and isinstance(a.op, ast.Mod):
                    msg = const_str(a.left)
                note(msg, "report", where)
            for kw in n.keywords:
                if kw.arg == "message":
                    note(unwrap_template(kw.value), "report", where)

        # ---- layouts labelled "anim:..." style via label() keyword only ----
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and _cname(n) in ("iface", "rpt") and n.args:
                kw_parent = parents.get(id(n))
                if isinstance(kw_parent, ast.keyword) and kw_parent.arg in ("text", "label"):
                    pass  # already handled by the keyword pass
    return found


def _excluded(msgid):
    """Strings we deliberately do not translate.

      * the add-on's own name/tagline from the manifest - Blender does not
        translate extension metadata, and "IO PDX Mesh" is a brand name
      * Scene property names Blender derived from the identifier itself (a
        property declared without `name=` shows its identifier; translating it
        would rename an internal field in the UI)
    """
    try:
        info = _load_manifest()
    except Exception:  # noqa: BLE001
        info = {}
    for key in ("name", "tagline"):
        if info.get(key) and msgid == info[key]:
            return True
    if msgid.startswith("io_pdx") and re.fullmatch(r"[a-z0-9_]+", msgid):
        return True
    return False


def _load_manifest():
    global _MANIFEST
    try:
        return _MANIFEST
    except NameError:
        pass
    try:
        import tomllib
    except ModuleNotFoundError:
        from ..external import tomllib  # type: ignore
    with open(os.path.join(ROOT, "blender_manifest.toml"), "rb") as fh:
        _MANIFEST = tomllib.load(fh)
    return _MANIFEST


class _RecordingLayout:
    """Stand-in for a UILayout that records every text it is asked to draw.

    Real draw() code calls label(text=...)/operator(..., text=...)/prop(...);
    this captures those arguments so the test can assert what would actually
    appear, rather than only that a lookup returns something.
    """

    def __init__(self, sink):
        self._sink = sink
        self.text = ""
        self.icon = ""

    def __getattr__(self, name):
        def f(*a, **kw):
            for key in ("text", "label"):
                val = kw.get(key)
                if isinstance(val, str) and val:
                    self._sink.append(val)
            return _RecordingLayout(self._sink)
        return f

    def __call__(self, *a, **kw):
        return _RecordingLayout(self._sink)

    def __bool__(self):
        return True


def _collect_drawn_text(lang=None):
    """Run the real particle panel draw() and return the text it emits."""
    import types
    ui = None
    for name, mod in list(sys.modules.items()):
        if name.endswith(".pdx_particles.ui") and hasattr(mod, "IOPDX_PT_PDXparticles"):
            ui = mod
            break
    if ui is None:
        return []

    prefs = bpy.context.preferences.view
    old = prefs.language
    sink = []
    try:
        if lang:
            prefs.language = lang
        panel = types.SimpleNamespace(layout=_RecordingLayout(sink))
        # make sure the list is populated so more branches get drawn
        try:
            ui._rebuild_categories(bpy.context)
            ui._rebuild_asset_list(bpy.context)
            ui._mark_list_current()
        except Exception:  # noqa: BLE001
            pass
        for tab in ("model", "particle", "entity"):
            try:
                bpy.context.scene.io_pdx_asset_tab = tab
                ui.IOPDX_PT_PDXparticles.draw(panel, bpy.context)
            except Exception:  # noqa: BLE001
                pass
    finally:
        prefs.language = old
    return sink


def main():
    print("i18n coverage check")
    if not enable_addon():
        print("SKIP: could not enable addon")
        return 2
    i18n = load_i18n_module()
    check("pdx_i18n module loaded", i18n is not None)
    if i18n is None:
        return 1

    table = dict(i18n._ZH)
    check("translation table is non-trivial", len(table) > 150, "n={0}".format(len(table)))

    src_found = _build_src_surface()
    rna = rna_surface()

    expected = dict(src_found)
    for msgid, roles in rna.items():
        if _excluded(msgid):
            continue
        expected.setdefault(msgid, set()).update(roles)

    # ---- 1. 覆盖：每个界面串都要有词条 ----
    missing = sorted(m for m in expected if m not in table)
    check("every user-facing string has a translation", not missing,
          "{0} missing: {1}".format(len(missing), missing[:8]))

    # ---- 2. 孤儿：词条必须对应真实界面串 ----
    orphan = sorted(m for m in table if m not in expected)
    check("no orphan translation entries", not orphan,
          "{0} orphan: {1}".format(len(orphan), orphan[:8]))

    # ---- 3. 占位符一致 ----
    bad_ph = []
    for msgid, text in table.items():
        if sorted(PH.findall(msgid)) != sorted(PH.findall(text)):
            bad_ph.append((msgid, text))
    check("placeholders match between source and translation", not bad_ph,
          "{0} mismatched: {1}".format(len(bad_ph), bad_ph[:4]))

    # ---- 4. 界面串里不得出现中文（中文只应存在于译文表）----
    cjk_hits = ["%s %s %r" % (sorted(roles), m, m) for m, roles in expected.items()
                if CJK.search(m)]
    check("no Chinese left in user-facing source strings", not cjk_hits,
          "; ".join(cjk_hits[:4]))

    # ---- 5. 运行时取值：zh_HANS 下必须译出，且每条都变 ----
    prefs = bpy.context.preferences.view
    old_lang = prefs.language
    untranslated = []
    try:
        prefs.language = "zh_HANS"
        for msgid in sorted(table):
            got = bpy.app.translations.pgettext_iface(msgid)
            if got == msgid:
                untranslated.append(msgid)
    finally:
        prefs.language = old_lang
    check("every entry resolves under zh_HANS at runtime", not untranslated,
          "{0} unresolved: {1}".format(len(untranslated), untranslated[:8]))

    # ---- 6. 切回英文必须回落原文 ----
    fallback_bad = []
    try:
        prefs.language = "en_US"
        for msgid in sorted(table)[:60]:
            got = bpy.app.translations.pgettext_iface(msgid)
            if got != msgid:
                fallback_bad.append((msgid, got))
    finally:
        prefs.language = old_lang
    check("falls back to English when the locale is not translated", not fallback_bad,
          "{0} wrong: {1}".format(len(fallback_bad), fallback_bad[:4]))

    # ---- 7. 撞词串用的是自有上下文（否则会被 Blender 词表抢走）----
    our_ctx = getattr(i18n, "CTX", "io_pdx_mesh")
    stolen = []
    try:
        prefs.language = "zh_HANS"
        for msgid in sorted(table):
            ours = bpy.app.translations.pgettext_iface(msgid, our_ctx)
            if ours == msgid:
                stolen.append(msgid)
    finally:
        prefs.language = old_lang
    check("every entry also resolves under our own context", not stolen,
          "{0} missing: {1}".format(len(stolen), stolen[:8]))

    # ---- 8. 真跑一遍面板绘制，确认画出来的字确实是中文 ----
    # 前面几条只证明「查表能查到」，这条证明「界面真的用了译文的字」：
    # 用一个记录型 layout 调真实 draw()，收集所有 text= 参数。
    #
    # 注意 lang 必须显式传：不传就沿用当前语言（跑测试时通常是 en_US），
    # 会得到「一条中文都没有」的假失败。
    drawn_zh = _collect_drawn_text(lang="zh_HANS")
    cjk_drawn = [t for t in drawn_zh if CJK.search(t)]
    check("panel draw() emits Chinese text under zh_HANS",
          len(drawn_zh) > 0 and len(cjk_drawn) >= 5,
          "drawn={0} cjk={1} sample={2}".format(len(drawn_zh), len(cjk_drawn), cjk_drawn[:4]))

    drawn_en = _collect_drawn_text(lang="en_US")
    cjk_en = [t for t in drawn_en if CJK.search(t)]
    check("panel draw() emits no Chinese under en_US",
          len(drawn_en) > 0 and not cjk_en,
          "drawn={0} cjk={1} sample={2}".format(len(drawn_en), len(cjk_en), cjk_en[:4]))

    # 手工包过 pdx_i18n.iface()/rpt() 的组合串不走 RNA 自动翻译，
    # 必须自己变中文——上面那条 cjk>=5 主要就是它们在兜底。
    wrapped_zh = [t for t in drawn_zh if t.startswith(("分类（", "动画（"))]
    check("composed strings are translated in the drawn output", len(wrapped_zh) >= 1,
          "sample={0}".format(wrapped_zh[:3]))

    print("\n{0} passed / {1} failed".format(len(PASS), len(FAIL)))
    print("surface: rna={0} src={1} expected={2} table={3}".format(
        len(rna), len(src_found), len(expected), len(table)))
    if FAIL:
        print("failed:", ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
