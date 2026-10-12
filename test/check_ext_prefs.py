"""
真实扩展安装路径验证：不启用 --factory-startup，走 Blender 已装好的扩展，
确认偏好键是顶层包名、偏好真的能读写、索引走偏好而不是回退。

跑法：
    & 'G:\\SteamLibrary\\steamapps\\common\\Blender\\blender.exe' -b \
      --python test/check_ext_prefs.py

与 check_index_ui.py 的分工：那个用 --factory-startup（模块名恰为 io_pdx_mesh），
这个模拟真实使用（键为 bl_ext.user_default.io_pdx_mesh），两条路径都要通。

注意导入方式：**不能**用 `import io_pdx_mesh...`。那会沿 sys.path 注入第二份
插件副本，顶层包名是 io_pdx_mesh，而 addons 的键是 bl_ext.user_default.io_pdx_mesh，
于是 _addon_prefs() 取不到偏好——这是测试自己造的假象，不是插件的问题。
这里直接从 sys.modules 里取已加载的真实模块。
"""

import os
import sys

sys.dont_write_bytecode = True

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import bpy  # noqa: E402

GAME = os.environ.get(
    "IO_PDX_TEST_GAME", r"G:\SteamLibrary\steamapps\common\Stellaris")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  {0} {1}{2}".format("PASS" if cond else "FAIL", name,
                                "  — " + detail if detail else ""))


def real_ui_module():
    """取 addon 真实加载的 pdx_particles.ui 模块（不重新导入）。"""
    addon_keys = set(bpy.context.preferences.addons.keys())
    for name, mod in list(sys.modules.items()):
        if not name.endswith(".pdx_particles.ui"):
            continue
        top = mod.__package__.rsplit(".", 1)[0] if "." in mod.__package__ else mod.__package__
        if top in addon_keys:
            return mod
    return None


def main():
    print("extension-install preferences check")
    addons = bpy.context.preferences.addons
    keys = [k for k in addons.keys() if "io_pdx_mesh" in k]
    print("addon keys matching:", keys)
    if not keys:
        print("SKIP: addon not enabled in this profile")
        return 2

    key = keys[0]
    entry = addons.get(key)
    prefs = getattr(entry, "preferences", None)
    check("preferences object attached", prefs is not None,
          "addons[{0!r}].preferences".format(key))
    if prefs is None:
        print("\n{0} passed / {1} failed".format(len(PASS), len(FAIL)))
        return 1

    check("prefs have contract attributes",
          hasattr(prefs, "vanilla_root") and hasattr(prefs, "extra_roots"))

    UI = real_ui_module()
    check("real addon ui module loaded", UI is not None)
    if UI is None:
        print("\n{0} passed / {1} failed".format(len(PASS), len(FAIL)))
        return 1
    print("  using module:", UI.__name__)

    found = UI._addon_prefs()
    check("_addon_prefs resolves the real key", found is not None)
    # 不用 `is` 比较：bpy 的 RNA 属性每次访问可能返回新的包装对象，
    # 身份比较不可靠。改成写一个值再读回来，证明拿到的是同一个偏好数据。
    probe = "Z:\\io_pdx_prefs_identity_probe"
    old_probe = found.vanilla_root
    try:
        found.vanilla_root = probe
        check("_addon_prefs returns a live preferences object",
              UI._addon_prefs().vanilla_root == probe)
    finally:
        found.vanilla_root = old_probe

    # --- 隔离回退源，证明值确实来自偏好 ---
    # settings.json 与场景属性都能提供原版根，值恰好相同时会造成假通过，
    # 因此先把两个回退源清空，再看偏好是否仍能提供。
    return _run_prefs_checks(UI, prefs)


def _run_prefs_checks(UI, prefs):
    # 不用 `import io_pdx_mesh`：会沿 sys.path 命中另一份副本。设置对象直接从
    # 真实已加载的顶层包里取。UI.__package__ 形如 "<顶层包>.pdx_particles"，
    # 去掉末级就是顶层包（扩展模式下是 bl_ext.user_default.io_pdx_mesh）。
    real_pkg = UI.__package__.rsplit(".", 1)[0]
    settings_obj = getattr(sys.modules.get(real_pkg), "IO_PDX_SETTINGS", None)
    check("found settings object for fallback isolation", settings_obj is not None,
          real_pkg)

    scene = bpy.context.scene
    old = {
        "prefs_vanilla": prefs.vanilla_root,
        "prefs_extra": [e.path for e in prefs.extra_roots],
        "settings_vanilla": getattr(settings_obj, "particle_vanilla_root", "") if settings_obj else "",
        "scene_vanilla": getattr(scene, "io_pdx_particle_vanilla_root", ""),
    }
    try:
        # 清空所有回退源
        if settings_obj is not None:
            settings_obj.particle_vanilla_root = ""
        if hasattr(scene, "io_pdx_particle_vanilla_root"):
            scene.io_pdx_particle_vanilla_root = ""
        while prefs.extra_roots:
            prefs.extra_roots.remove(0)

        # 只放偏好：必须被读到
        prefs.vanilla_root = GAME
        extra, vanilla = UI._pref_roots()
        check("_pref_roots reads vanilla_root from prefs only",
              os.path.normcase(vanilla) == os.path.normcase(GAME),
              "vanilla={0!r} (falls back to '' when prefs are ignored)".format(vanilla))

        e = prefs.extra_roots.add()
        e.path = GAME
        extra2, _v = UI._pref_roots()
        check("_pref_roots reads extra_roots from prefs only",
              any(os.path.normcase(p) == os.path.normcase(GAME) for p in extra2),
              "extra={0}".format(extra2))

        UI._INDEX = None
        UI._INDEX_KEY = None
        idx = UI._ensure_index(force=True)
        check("index builds from preferences", idx.stats.get("model", 0) > 0,
              "stats={0}".format(idx.stats))
    finally:
        prefs.vanilla_root = old["prefs_vanilla"]
        while prefs.extra_roots:
            prefs.extra_roots.remove(0)
        for p in old["prefs_extra"]:
            prefs.extra_roots.add().path = p
        if settings_obj is not None:
            settings_obj.particle_vanilla_root = old["settings_vanilla"]
        if hasattr(scene, "io_pdx_particle_vanilla_root"):
            scene.io_pdx_particle_vanilla_root = old["scene_vanilla"]
        UI._INDEX = None
        UI._INDEX_KEY = None

    print("\n{0} passed / {1} failed".format(len(PASS), len(FAIL)))
    if FAIL:
        print("failed:", ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
