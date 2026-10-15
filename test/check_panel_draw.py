"""
面板 draw() 冒烟：真正调用 draw()，在只读上下文中检出「写 ID 属性」这类错误。

跑法：
    & 'G:\\SteamLibrary\\steamapps\\common\\Blender\\blender.exe' -b --factory-startup \
      --python test/check_panel_draw.py

这个检查是补课：此前的索引/UI 测试都直接调 _rebuild_*()，**从没真正执行过
draw()**，于是「draw 里写 Scene 属性」这种必现错误全套测试都放过了，实际表现
是面板从标签页往后整段空白（异常截断了绘制）。

用假的 layout 调真实 draw()，layout 的方法全部可调用且返回自身，因此能走完整条
代码路径；真出异常就会被捕获并报 FAIL。
"""

import os
import sys
import traceback
import types

sys.dont_write_bytecode = True

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(ROOT))

import bpy  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  {0} {1}{2}".format("PASS" if cond else "FAIL", name,
                                "  — " + detail if detail else ""))


def enable_addon():
    try:
        return bpy.ops.preferences.addon_enable(module="io_pdx_mesh") == {"FINISHED"}
    except Exception:  # noqa: BLE001
        try:
            import io_pdx_mesh
            io_pdx_mesh.pdx_blender.register()
            return True
        except Exception:  # noqa: BLE001
            return False


class FakeLayout(object):
    """把 layout 的所有调用都吞掉并返回自身，其余属性当标量。"""

    def __init__(self):
        self.text = ""
        self.icon = ""

    def __getattr__(self, name):
        def f(*a, **kw):
            return FakeLayout()
        return f


def make_panel_self():
    import types
    return types.SimpleNamespace(layout=FakeLayout())


def main():
    print("panel draw smoke check")
    if not enable_addon():
        print("SKIP: could not enable addon")
        return 2

    ui = None
    for name, mod in list(sys.modules.items()):
        if name.endswith(".pdx_particles.ui"):
            ui = mod
            break
    check("ui module loaded", ui is not None)
    if ui is None:
        return 1

    scene = bpy.context.scene

    # 干净状态：清掉场景里的列表与禁用记录，模拟首次打开
    scene.io_pdx_index_categories.clear()
    scene.io_pdx_index_disabled.clear()
    scene.io_pdx_asset_items.clear()
    ui._LIST_KEY = None
    ui._REBUILD_SCHEDULED = False

    # ---- 0. 首次打开时不在这里同步建索引，只排定时器 ----
    # 同步建索引实测要 3.1-3.4 秒（全库），会让侧栏首次展开冻住。
    ui._INDEX = None
    ui._INDEX_KEY = None
    ui._INDEX_SCHEDULED = False
    ui._LIST_BUILT = False
    try:
        ui.IOPDX_PT_PDXparticles.draw(make_panel_self(), bpy.context)
        check("first open defers the index build instead of blocking", ui._INDEX_SCHEDULED is True)
        check("first open leaves the index unbuilt", ui._INDEX is None)
    except Exception:
        check("first open defers the index build instead of blocking", False,
              traceback.format_exc().strip().splitlines()[-1])

    # 后续步骤需要一个已建好的索引：同步建一次并标记成当前
    # （上面排的那个定时器在测试进程里不会跑到，这里显式建并复位标志）
    ui._INDEX_SCHEDULED = False
    ui._ensure_index()
    ui._rebuild_categories(bpy.context)
    ui._rebuild_asset_list(bpy.context)
    ui._mark_list_current()

    # ---- 1. 首次打开：draw 必须不抛异常 ----
    try:
        ui.IOPDX_PT_PDXparticles.draw(make_panel_self(), bpy.context)
        check("draw() runs without raising (first open)", True)
    except Exception:
        check("draw() runs without raising (first open)", False,
              traceback.format_exc().strip().splitlines()[-1])

    # ---- 2. draw 不能写 ID 属性 ----
    # 直接验证：draw 期间对 scene 的写入会被 Blender 拒绝（报错文本见 UI 日志）。
    # 这里用静态检查抓「draw 函数体里对 scene 赋值」。
    import inspect
    src = inspect.getsource(ui.IOPDX_PT_PDXparticles.draw)
    bad = []
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("#") or "==" in s or "!=" in s:
            continue
        if s.startswith(("scene.", "context.scene.")) and "=" in s:
            bad.append(s)
    check("draw() contains no Scene writes", not bad, "; ".join(bad))

    # ---- 3. 列表过期时只登记定时器，不在 draw 里重建 ----
    scene.io_pdx_index_categories.clear()
    ui._LIST_KEY = "a-key-that-cannot-match"
    ui._REBUILD_SCHEDULED = False
    try:
        ui.IOPDX_PT_PDXparticles.draw(make_panel_self(), bpy.context)
        check("draw() survives a stale list", True)
    except Exception:
        check("draw() survives a stale list", False,
              traceback.format_exc().strip().splitlines()[-1])
    check("stale list schedules a deferred rebuild", ui._REBUILD_SCHEDULED is True)

    # ---- 4. 定时器回调真的把列表建起来 ----
    # 直接跑那个闭包：从计时器注册表拿不到函数，改为同步调用等价路径
    ui._rebuild_categories(bpy.context)
    ui._rebuild_asset_list(bpy.context)
    ui._mark_list_current()
    check("categories filled", len(scene.io_pdx_index_categories) > 0,
          "n={0}".format(len(scene.io_pdx_index_categories)))
    check("asset items filled", len(scene.io_pdx_asset_items) > 0,
          "n={0}".format(len(scene.io_pdx_asset_items)))
    check("list key marked current", ui._LIST_KEY == ui._list_token(),
          "key={0}".format(ui._LIST_KEY))

    # ---- 5. 列表就不再算过期，draw 走正常分支 ----
    before = ui._REBUILD_SCHEDULED
    ui._REBUILD_SCHEDULED = False
    ui.IOPDX_PT_PDXparticles.draw(make_panel_self(), bpy.context)
    check("fresh list does not reschedule", ui._REBUILD_SCHEDULED is False)

    # ---- 6. 三个标签页都画得出来 ----
    for tab in ("model", "particle", "entity"):
        scene.io_pdx_asset_tab = tab
        try:
            ui.IOPDX_PT_PDXparticles.draw(make_panel_self(), bpy.context)
            check("draw() runs for tab {0}".format(tab), True)
        except Exception:
            check("draw() runs for tab {0}".format(tab), False,
                  traceback.format_exc().strip().splitlines()[-1])

    # ---- 7. 无配置时也不能抛（走「没设目录」的早退分支）----
    old_vanilla = scene.io_pdx_particle_vanilla_root
    old_prefs = None
    try:
        scene.io_pdx_particle_vanilla_root = ""
        ui._INDEX = None
        ui._INDEX_KEY = None
        ui._addon_prefs_override = None
    except Exception:
        pass
    try:
        ui.IOPDX_PT_PDXparticles.draw(make_panel_self(), bpy.context)
        check("draw() survives an unconfigured state", True)
    except Exception:
        check("draw() survives an unconfigured state", False,
              traceback.format_exc().strip().splitlines()[-1])
    finally:
        scene.io_pdx_particle_vanilla_root = old_vanilla
        ui._INDEX = None
        ui._INDEX_KEY = None

    # ---- 8. 全局设置必须与「加载了什么」无关 ----
    # FakeLayout 把 label/prop/operator 全吞掉，所以「这块到底画没画」用它是测不到的：
    # FPS 与引擎一度被放在主面板的 `if not _ACTIVE: return` 之后，没加载粒子时
    # 整个消失而测试全绿。这里换成记录型 layout，按调用参数判定。
    class _Rec(object):
        def __init__(self, sink):
            self.sink = sink

        def __getattr__(self, name):
            def f(*a, **kw):
                if name in ("label", "operator", "prop"):
                    if kw.get("text"):
                        self.sink.append(kw["text"])
                    if len(a) > 1 and isinstance(a[1], str):
                        self.sink.append(a[1])
                return _Rec(self.sink)
            return f

    def _drawn_text(panel_cls):
        sink = []
        panel_cls.draw(types.SimpleNamespace(layout=_Rec(sink)), bpy.context)
        return sink

    old_active = list(ui._ACTIVE)
    try:
        ui._ACTIVE = []                       # 没加载任何粒子
        txt = _drawn_text(ui.IOPDX_PT_PDXsettings)
        check("settings panel draws with nothing loaded", len(txt) > 0,
              "n={0}".format(len(txt)))
        check("settings panel exposes the scene fps", "fps" in txt, "drawn={0}".format(txt))
        check("settings panel exposes the engine", "setup_engine" in txt)
        check("settings panel exposes material/bone/mesh tools",
              "Create" in txt and "Ignore bones" in txt and "Set mesh order" in txt,
              "drawn={0}".format(txt))
        check("settings panel exposes the quad shading switch",
              "io_pdx_particle_shade" in txt, "drawn={0}".format(txt))
        # 主面板不该再重复画这些
        main_txt = _drawn_text(ui.IOPDX_PT_PDXparticles)
        check("main panel no longer duplicates the global settings",
              "fps" not in main_txt and "setup_engine" not in main_txt,
              "main n={0}".format(len(main_txt)))
    finally:
        ui._ACTIVE = old_active

    print("\n{0} passed / {1} failed".format(len(PASS), len(FAIL)))
    if FAIL:
        print("failed:", ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
