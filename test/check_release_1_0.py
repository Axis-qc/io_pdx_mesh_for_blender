"""
1.0.0 发版缺陷回归自检：把本轮修掉的缺陷逐条钉住，避免以后复发。

跑法：
    & 'G:\\SteamLibrary\\steamapps\\common\\Blender\\blender.exe' -b --factory-startup \
      --python test/check_release_1_0.py

游戏根默认 G:\\SteamLibrary\\steamapps\\common\\Stellaris，
可用环境变量 IO_PDX_TEST_GAME 覆盖。

检查：
  1. 清场只删本插件建的对象，用户自建的同名对象必须存活
  2. 导入中途失败留下的对象会被下一次清场收走
  3. 不再给 action / 材质 / 贴图设 fake user
  4. 反复加载同一模型，数据块数量不增长
  5. 改过场景帧率后导出的动画时长不变
  6. 重名骨挂点往返不偏移
  7. register / unregister 可重复执行
  8. 更新器对异常输入不抛异常

只读游戏文件；只在内存里改场景，结尾不保存任何文件。
"""

import os
import sys
import tempfile
import traceback

sys.dont_write_bytecode = True

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(ROOT))

import bpy  # noqa: E402

GAME = os.environ.get(
    "IO_PDX_TEST_GAME", r"G:\SteamLibrary\steamapps\common\Stellaris")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  {0} {1}{2}".format("PASS" if cond else "FAIL", name,
                                "  — " + detail if detail else ""))


def enable_addon():
    try:
        if bpy.ops.preferences.addon_enable(module="io_pdx_mesh") == {"FINISHED"}:
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        import io_pdx_mesh
        io_pdx_mesh.pdx_blender.register()
        return True
    except Exception:  # noqa: BLE001
        return False


def get_ui():
    for name, mod in list(sys.modules.items()):
        if name.endswith(".pdx_particles.ui"):
            return mod
    return None


def clear_scene():
    for obj in list(bpy.data.objects):
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            pass
    for db in (bpy.data.armatures, bpy.data.meshes, bpy.data.actions,
               bpy.data.materials, bpy.data.images):
        for item in list(db):
            try:
                db.remove(item)
            except Exception:  # noqa: BLE001
                pass


def datablock_counts():
    return {"objects": len(bpy.data.objects), "meshes": len(bpy.data.meshes),
            "materials": len(bpy.data.materials), "images": len(bpy.data.images),
            "actions": len(bpy.data.actions)}


def main():
    print("1.0.0 release regression check")
    if not enable_addon():
        print("SKIP: could not enable addon")
        return 2
    if not os.path.isdir(GAME):
        print("SKIP: game root not found:", GAME)
        return 2

    ui = get_ui()
    check("ui module loaded", ui is not None)
    if ui is None:
        return 1

    from io_pdx_mesh.pdx_blender.blender_import_export import (
        action_frame_range, export_animfile, export_meshfile, import_animfile,
        import_meshfile,
    )
    import io_pdx_mesh.pdx_data as pdx_data
    from mathutils import Vector

    mesh_path = os.path.join(
        GAME, "gfx", "models", "ships", "megastructures", "star_eater",
        "star_eater_01.mesh")
    anim_path = os.path.join(
        GAME, "gfx", "models", "infernal_01_mining_station_frame_death.anim")
    anim_mesh = os.path.join(
        GAME, "gfx", "models", "infernal_01_mining_station_frame.mesh")

    # ---------------- 1. 清场不删用户对象 ----------------
    print("\n== 1. 清场只删本插件建的对象 ==")
    clear_scene()
    # 模拟插件加载了一个对象
    plugin_mesh = bpy.data.meshes.new("pdx_part_data")
    plugin_obj = bpy.data.objects.new("part1", plugin_mesh)
    bpy.context.scene.collection.objects.link(plugin_obj)
    ui._mark_loaded(bpy.context, set())
    check("plugin object carries the owner tag", bool(plugin_obj.get(ui.OWNER_PROP)))

    # 用户删掉它，再自建一个同名对象（Blender 会复用被释放的名字）
    bpy.data.objects.remove(plugin_obj, do_unlink=True)
    user_mesh = bpy.data.meshes.new("user_data")
    user_obj = bpy.data.objects.new("part1", user_mesh)
    bpy.context.scene.collection.objects.link(user_obj)
    check("Blender reused the freed name (test premise)",
          user_obj.name == "part1", "name={0}".format(user_obj.name))

    removed = ui._clear_loaded(bpy.context)
    check("clear does not delete a user object with the same name",
          "part1" in bpy.data.objects,
          "removed={0} objects={1}".format(removed, list(bpy.data.objects.keys())))

    # ---------------- 2. 失败残留会被收走 ----------------
    print("\n== 2. 失败留下的对象下次会被清掉 ==")
    clear_scene()
    before = ui._snapshot_objects()
    half = bpy.data.meshes.new("halfmade")
    half_obj = bpy.data.objects.new("halfmade", half)
    bpy.context.scene.collection.objects.link(half_obj)
    # 模拟「导入中途失败」：finally 路径也要记账
    ui._mark_loaded(bpy.context, before)
    check("objects from a failed import are recorded",
          bool(half_obj.get(ui.OWNER_PROP)))
    ui._clear_loaded(bpy.context)
    check("a failed import leaves no residue", "halfmade" not in bpy.data.objects)

    # ---------------- 3/4. 资源不泄漏 ----------------
    print("\n== 3. 反复加载不堆积资源 ==")
    if not os.path.isfile(mesh_path):
        check("test mesh available", False, mesh_path)
    else:
        counts = []
        for _ in range(3):
            ui._clear_previews()
            ui._clear_loaded(bpy.context)
            before = ui._snapshot_objects()
            try:
                ui._load_mesh_path(mesh_path, reuse_skeleton=False)
            finally:
                ui._mark_loaded(bpy.context, before)
            counts.append(datablock_counts())
        check("repeated loads do not grow datablocks",
              counts[0] == counts[1] == counts[2],
              "{0} -> {1} -> {2}".format(counts[0], counts[1], counts[2]))
        check("no imported material keeps a fake user",
              not any(m.use_fake_user for m in bpy.data.materials),
              "fake_user materials: {0}".format(
                  [m.name for m in bpy.data.materials if m.use_fake_user]))
        check("no imported image keeps a fake user",
              not any(i.use_fake_user for i in bpy.data.images),
              "fake_user images: {0}".format(
                  [i.name for i in bpy.data.images if i.use_fake_user]))

    # ---------------- 5. 改帧率后时长不变 ----------------
    print("\n== 5. 改过场景帧率后导出的动画时长不变 ==")
    if not (os.path.isfile(anim_path) and os.path.isfile(anim_mesh)):
        check("animation fixtures available", False, anim_path)
    else:
        src_info = pdx_data.read_meshfile(anim_path).find("info")
        src_dur = int(src_info.attrib["sa"][0]) / float(src_info.attrib["fps"][0])
        out_anim = os.path.join(tempfile.gettempdir(), "io_pdx_fps_check.anim")
        scene = bpy.context.scene
        rows = []
        for retime, later in ((None, 60), (24.0, 60), (60.0, 24)):
            clear_scene()
            scene.render.fps = 30
            scene.render.fps_base = 1.0
            import_meshfile(anim_mesh, imp_mesh=False, imp_skel=True, imp_locs=False)
            kwargs = {"retime_to": retime} if retime else {}
            import_animfile(anim_path, frame_start=1, **kwargs)
            rig = next(o for o in bpy.context.scene.objects if o.type == "ARMATURE")
            span = action_frame_range(rig.animation_data.action)
            # 用户事后改场景帧率（不重新导入）
            scene.render.fps = later
            scene.render.fps_base = 1.0
            end = int(span[1]) + (1 if span[1] % 1 else 0)
            export_animfile(out_anim, frame_start=1, frame_end=end)
            info = pdx_data.read_meshfile(out_anim).find("info")
            dur = int(info.attrib["sa"][0]) / float(info.attrib["fps"][0])
            rows.append((retime, later, dur,
                         abs(dur - src_dur) / src_dur * 100.0))
        check("exported duration survives a scene fps change",
              all(r[3] < 1.0 for r in rows),
              "; ".join("retime={0} scene={1:g} -> {2:.3f}s ({3:.2f}%)".format(*r)
                        for r in rows))

    # ---------------- 6. 重名骨挂点往返 ----------------
    print("\n== 6. 重名骨挂点往返不偏移 ==")
    if not os.path.isfile(mesh_path):
        check("duplicate-bone mesh available", False, mesh_path)
    else:
        out_mesh = os.path.join(tempfile.gettempdir(), "io_pdx_dupe_check.mesh")

        def locator_positions():
            return {o.name: list(o.matrix_world.translation)
                    for o in bpy.context.scene.objects if o.type == "EMPTY"}

        clear_scene()
        # 用默认导入：star_eater_01 有两个带骨架的 shape，reuse=False 会建出两个骨架，
        # 而「只导骨架」的导出路径要求场景里只有一个骨架。默认复用能得到一个。
        import_meshfile(mesh_path)
        first = locator_positions()
        check("duplicate-bone mesh imported its locators", len(first) > 0,
              "n={0}".format(len(first)))
        export_meshfile(out_mesh, exp_mesh=False, exp_skel=True, exp_locs=True)
        clear_scene()
        import_meshfile(out_mesh)
        second = locator_positions()
        common = [k for k in first if k in second]
        drift = max(((Vector(first[k]) - Vector(second[k])).length for k in common),
                    default=None)
        check("duplicate-bone round trip keeps locators in place",
              len(common) == len(first) and drift is not None and drift < 1e-3,
              "n={0}/{1} max_drift={2}".format(
                  len(common), len(first),
                  "-" if drift is None else "{0:.6f}".format(drift)))

    # ---------------- 7. register / unregister 可重复 ----------------
    print("\n== 7. register / unregister 可重复执行 ==")
    ok = True
    detail = ""
    for round_index in range(2):
        try:
            bpy.ops.preferences.addon_disable(module="io_pdx_mesh")
        except Exception as err:  # noqa: BLE001
            ok = False
            detail = "disable {0}: {1}".format(round_index, err)
            break
        try:
            bpy.ops.preferences.addon_enable(module="io_pdx_mesh")
        except Exception as err:  # noqa: BLE001
            ok = False
            detail = "enable {0}: {1}".format(round_index, err)
            break
    check("disable/enable survives repetition", ok, detail)
    check("scene properties are back after re-enable",
          hasattr(bpy.context.scene, "io_pdx_asset_tab"))

    # ---------------- 8. 更新器容错 ----------------
    print("\n== 8. 更新器对异常输入不抛异常 ==")
    updater = sys.modules.get("io_pdx_mesh.updater")
    if updater is None:
        for name, mod in list(sys.modules.items()):
            if name.endswith(".updater"):
                updater = mod
                break
    check("updater module available", updater is not None)
    if updater is not None:
        original = updater.Github_API.get_data

        def probe(label, releases, last_check=None):
            settings = updater.IO_PDX_SETTINGS
            saved = settings.__dict__.get("last_update_check")
            try:
                updater.Github_API.get_data = staticmethod(
                    lambda url, time=1.0: releases)
                if last_check is None:
                    settings.__dict__.pop("last_update_check", None)
                else:
                    settings.__dict__["last_update_check"] = last_check
                updater.Github_API(owner="x", repo="y")
                return None
            except Exception as err:  # noqa: BLE001
                return "{0}: {1}".format(type(err).__name__, err)
            finally:
                updater.Github_API.get_data = original
                if saved is None:
                    settings.__dict__.pop("last_update_check", None)
                else:
                    settings.__dict__["last_update_check"] = saved

        cases = [
            ("no releases at all", [], None),
            ("v-prefixed tag", [{"tag_name": "v1.0", "assets": [],
                                 "published_at": "2026-01-01T00:00:00Z", "body": "x"}], None),
            ("three-part tag", [{"tag_name": "0.99.1", "assets": [],
                                 "published_at": "2026-01-01T00:00:00Z", "body": "x"}], None),
            ("missing assets key", [{"tag_name": "0.91",
                                     "published_at": "2026-01-01T00:00:00Z", "body": "x"}], None),
            ("stale date format", [{"tag_name": "0.91", "assets": [],
                                    "published_at": "2026-01-01T00:00:00Z", "body": "x"}],
             "2026/10/13"),
        ]
        failures = []
        for label, releases, last_check in cases:
            err = probe(label, releases, last_check)
            if err:
                failures.append("{0} -> {1}".format(label, err))
        check("update check tolerates malformed API responses", not failures,
              "; ".join(failures))

    print("\n{0} passed / {1} failed".format(len(PASS), len(FAIL)))
    if FAIL:
        print("failed:", ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        code = 1
    sys.exit(code)
