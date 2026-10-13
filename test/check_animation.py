"""
骨骼动画与清场机制冒烟自检。

跑法：
    & 'G:\\SteamLibrary\\steamapps\\common\\Blender\\5.2\\blender.exe' -b --factory-startup \
      --python test/check_animation.py

游戏根默认 G:\\SteamLibrary\\steamapps\\common\\Stellaris，
可用环境变量 IO_PDX_TEST_GAME 覆盖。

检查：
  1. 动画链三层解析：entity.state.animation(id) → pdxmesh{id,type} → .asset{name,file}
  2. 索引侧数量与解析率（1346 条 state 动画全解析、model 侧动画可达）
  3. 重定时：任意内嵌 fps 缩放到目标 fps 后**时长不变**、起点不漂
  4. 真实导入：entity 的 mesh + 它 state 里的 .anim，验证骨架/action/帧范围
  5. 清场：连续加载两次后骨架恰好 1 个、无残留；用户自建对象全程存活
  6. reuse_skeleton 开关：默认复用、传 False 时新建

只读游戏文件；只在内存里改场景，结尾不保存任何文件。
"""

import os
import sys
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
        return bpy.ops.preferences.addon_enable(module="io_pdx_mesh") == {"FINISHED"}
    except Exception:  # noqa: BLE001
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


def action_curves(act):
    out = []
    for layer in act.layers:
        for strip in layer.strips:
            for cb in strip.channelbags:
                out.extend(cb.fcurves)
    return out


def x_range(act):
    xs = [kp.co.x for fc in action_curves(act) for kp in fc.keyframe_points]
    return (min(xs), max(xs)) if xs else None


def reset_scene(ui):
    """清到「空场景 + 无插件残留」的状态，并留一个用户自建对象当探针。"""
    for obj in list(bpy.data.objects):
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            pass
    for act in list(bpy.data.actions):
        try:
            bpy.data.actions.remove(act)
        except Exception:  # noqa: BLE001
            pass
    bpy.context.scene.io_pdx_loaded_objects.clear()
    bpy.context.scene.io_pdx_particle_entries.clear()
    bpy.context.scene.io_pdx_anim_items.clear()
    # 用户自己的对象：清场绝不能碰它
    mesh = bpy.data.meshes.new("user_cube")
    user = bpy.data.objects.new("user_cube", mesh)
    bpy.context.scene.collection.objects.link(user)
    return user


def main():
    print("animation + clear-scope check")
    if not enable_addon():
        print("SKIP: could not enable addon")
        return 2
    if not os.path.isdir(GAME):
        print("SKIP: game root not found:", GAME)
        return 2

    from io_pdx_mesh.pdx_particles import index as IX
    from io_pdx_mesh.pdx_particles.simulator import scene_fps

    ui = get_ui()
    check("ui module loaded", ui is not None)
    if ui is None:
        return 1

    # ---------------- 1/2. 索引侧动画链 ----------------
    print("\n== 1. 动画链解析 ==")
    idx = IX.PDXIndex([GAME], vanilla_root=GAME).build()
    check("index built", idx.stats.get("files", 0) > 0, str(idx.stats))
    check("pdxmesh animation map populated", len(idx.pdxmesh_anims) > 0,
          "n={0}".format(len(idx.pdxmesh_anims)))
    check("animation registrations found", len(idx.anim_defs) > 0,
          "n={0}".format(len(idx.anim_defs)))

    ents = [e for e in idx.entries if e.kind == IX.KIND_ENTITY]
    with_anim = [e for e in ents if e.detail.get("animations")]
    states = [a for e in ents for a in (e.detail.get("animations") or [])]
    resolved = [a for a in states if a.get("file")]
    check("entities carry animations", len(with_anim) > 0,
          "{0}/{1}".format(len(with_anim), len(ents)))
    check("state animations all resolve to a file",
          len(states) > 0 and len(resolved) == len(states),
          "{0}/{1}".format(len(resolved), len(states)))
    check("every animation row has a type",
          all(a.get("type") for a in states))
    check("resolved anim files exist on disk",
          all(os.path.isfile(a["file"]) for a in resolved[:50]),
          "checked 50")
    check("entity page includes animation-only entities",
          any(e.detail.get("has_anim") and not e.detail.get("has_particle")
              for e in ents))

    models = [e for e in idx.entries if e.kind == IX.KIND_MODEL]
    m_anim = [e for e in models if e.detail.get("animations")]
    check("models carry animations", len(m_anim) > 0,
          "{0}/{1}".format(len(m_anim), len(models)))
    check("model animation rows resolve",
          all(a.get("file") for e in m_anim for a in e.detail["animations"]))

    q = idx.query(IX.KIND_ENTITY, require_content=True)
    check("require_content widens the entity page",
          len(q) > sum(1 for e in ents if e.detail.get("has_particle")),
          "{0} vs particles-only {1}".format(
              len(q), sum(1 for e in ents if e.detail.get("has_particle"))))

    # ---------------- 3. 重定时数学 ----------------
    print("\n== 2. 重定时（时长不变） ==")
    from io_pdx_mesh.pdx_blender.blender_import_export import (
        action_frame_range,
        import_animfile,
        import_meshfile,
        retime_action,
        PDX_ANIM_FPS_PROP,
    )

    # 找一个真实带骨架的 entity 作为被测对象
    target = None
    for e in with_anim:
        m = e.detail.get("mesh") or ""
        if m and os.path.isfile(m):
            target = e
            break
    check("found an entity with mesh + animation", target is not None)

    if target is not None:
        anim = next(a for a in target.detail["animations"] if a.get("file"))
        scene = bpy.context.scene
        scene.render.fps = 24
        scene.render.fps_base = 1.0
        import_meshfile(target.detail["mesh"], reuse_skeleton=False)
        rig = next((o for o in bpy.data.objects if o.type == "ARMATURE"), None)
        check("mesh import creates an armature", rig is not None)

        if rig is not None:
            # 走生产路径：retime_to 让导入器把动画重定时到场景 fps，且不改场景 fps
            import_animfile(anim["file"], retime_to=24.0)
            act = rig.animation_data.action
            rng = action_frame_range(act)
            check("retime_to imports an action with keys",
                  rng is not None and rng[1] > rng[0], str(rng))
            check("retime_to keeps the scene fps",
                  abs(scene_fps(scene) - 24.0) < 1e-9,
                  "fps={0}".format(scene_fps(scene)))
            check("retime_to records the target fps on the rig",
                  abs(float(rig[PDX_ANIM_FPS_PROP]) - 24.0) < 1e-9,
                  "stored={0}".format(rig.get(PDX_ANIM_FPS_PROP)))

            # 该文件内嵌 fps 与目标不同，时长按 24fps 解释应等于文件自身时长
            base_fps = float(rig[PDX_ANIM_FPS_PROP])
            original_duration = (rng[1] - rng[0]) / 24.0
            check("retimed duration is in a sane range",
                  0.0 < original_duration < 600.0,
                  "{0:.3f}s".format(original_duration))

            for target_fps in (30.0, 23.976, 60.0):
                factor = target_fps / base_fps
                retime_action(act, factor)
                rng2 = action_frame_range(act)
                got = (rng2[1] - rng2[0]) / target_fps
                check("retime keeps the duration @{0}fps".format(target_fps),
                      abs(got - original_duration) < 1e-6,
                      "{0:.6f}s vs {1:.6f}s".format(got, original_duration))
                check("retime keeps the start frame @{0}fps".format(target_fps),
                      abs(rng2[0] - rng[0]) < 1e-9,
                      "{0!r} vs {1!r}".format(rng2[0], rng[0]))
                # 缩回去，给下一次循环一个干净基线
                retime_action(act, base_fps / target_fps)

    # ---------------- 4. 真实导入走 UI 的加载路径 ----------------
    print("\n== 3. 加载路径 + 清场 ==")
    user = reset_scene(ui)

    # 选一个带 mesh + 动画的 entity
    scene = bpy.context.scene
    scene.io_pdx_asset_tab = IX.KIND_ENTITY
    scene.io_pdx_asset_filter = target.name if target is not None else ""
    idx2 = ui._ensure_index(force=True)
    check("_ensure_index reused the same content",
          idx2.stats.get("files") == idx.stats.get("files"))
    ui._rebuild_categories(bpy.context)
    ui._rebuild_asset_list(bpy.context)
    scene.io_pdx_asset_index = 0
    ui._rebuild_anim_list(bpy.context)
    check("animation list filled after selecting an entity",
          len(scene.io_pdx_anim_items) > 0,
          "n={0}".format(len(scene.io_pdx_anim_items)))

    # 连续加载两次同一个 entity，检查残留。
    # 注意一个 .mesh 可以合法地建出多个骨架：文件里一个 <object> 下挂多个
    # <mesh>，每个自带 skeleton（move_indicator.mesh 实测 3 个）。所以不写死
    # 「恰好 1 个骨架」，而是断言「重载后数量不增长」——那才是残留的定义。
    res1 = bpy.ops.io_pdx_particles.load_selected()
    check("first load finishes", res1 == {"FINISHED"}, str(res1))
    n_rig_1 = len([o for o in bpy.data.objects if o.type == "ARMATURE"])
    loaded_1 = len(scene.io_pdx_loaded_objects)
    check("first load creates armature(s)", n_rig_1 >= 1, "n={0}".format(n_rig_1))
    check("first load records what it created", loaded_1 > 0, "n={0}".format(loaded_1))
    check("user object survives the first load",
          bpy.data.objects.get("user_cube") is not None)

    res2 = bpy.ops.io_pdx_particles.load_selected()
    check("second load finishes", res2 == {"FINISHED"}, str(res2))
    n_rig_2 = len([o for o in bpy.data.objects if o.type == "ARMATURE"])
    check("reloading the same entity adds no residue",
          n_rig_2 == n_rig_1, "{0} -> {1}".format(n_rig_1, n_rig_2))
    check("user object survives the second load",
          bpy.data.objects.get("user_cube") is not None)
    check("loaded-object ledger did not grow on reload",
          len(scene.io_pdx_loaded_objects) == loaded_1,
          "{0} -> {1}".format(loaded_1, len(scene.io_pdx_loaded_objects)))

    # 切到另一个 entity：应把上一个的对象全部清掉。
    # 不能用对象名判断：导入的骨架默认都叫 io_pdx_rig，新 entity 会合法地重新
    # 用这个名字，按名字看会把它误判成上一个的残留。改比「对象总数」——切换后
    # 的数量必须等于「干净场景里直接加载 B」的数量，多出来的才是残留。
    others = [e for e in with_anim
              if e.name != (target.name if target is not None else "")
              and (e.detail.get("mesh") or "")
              and os.path.isfile(e.detail.get("mesh") or "")]
    if others:
        other = others[0]

        def load_named(entry_name):
            scene.io_pdx_asset_filter = entry_name
            ui._rebuild_asset_list(bpy.context)
            scene.io_pdx_asset_index = 0
            ui._rebuild_anim_list(bpy.context)
            res = bpy.ops.io_pdx_particles.load_selected()
            return res, len(bpy.data.objects)

        # A→B 切换（当前场景里已经装着 A）
        res3, n_after_switch = load_named(other.name)
        check("switching entity finishes", res3 == {"FINISHED"}, str(res3))
        check("user object survives the switch",
              bpy.data.objects.get("user_cube") is not None)

        # 干净场景里直接加载 B，作为对照
        reset_scene(ui)
        res_fresh, n_fresh = load_named(other.name)
        check("fresh load of the second entity finishes",
              res_fresh == {"FINISHED"}, str(res_fresh))
        check("switching leaves no residue from the previous entity",
              n_after_switch == n_fresh,
              "{0} objects after switch vs {1} on a fresh load".format(
                  n_after_switch, n_fresh))

    # ---------------- 5. 动画列表可加载 ----------------
    print("\n== 4. 动画 Load 按钮 ==")
    scene.io_pdx_anim_index = 0
    ui._rebuild_anim_list(bpy.context)
    if scene.io_pdx_anim_items:
        n_actions_before = len(bpy.data.actions)
        res = bpy.ops.io_pdx_particles.load_anim()
        check("load_anim finishes", res == {"FINISHED"}, str(res))
        check("an action was created", len(bpy.data.actions) > n_actions_before,
              "{0} -> {1}".format(n_actions_before, len(bpy.data.actions)))
        rig = next((o for o in bpy.data.objects if o.type == "ARMATURE"), None)
        if rig is not None and rig.animation_data and rig.animation_data.action:
            rng = action_frame_range(rig.animation_data.action)
            check("loaded action has keys", rng is not None and rng[1] > rng[0],
                  str(rng))
            # 时长应与文件内嵌 fps 下的时长一致
            stored = float(rig.get(PDX_ANIM_FPS_PROP) or 0.0)
            expect = (rng[1] - rng[0]) / (stored or 1.0)
            check("loaded animation duration is plausible",
                  0.0 < expect < 600.0, "{0:.3f}s".format(expect))
        # 再点一次：应能切换，不报错
        res = bpy.ops.io_pdx_particles.load_anim()
        check("load_anim can be pressed again", res == {"FINISHED"}, str(res))

        # 时间轴取并集：动画播完后不能把粒子预览的时间轴截短；
        # 末帧要够到最后一个关键帧。这里必须用带小数的末关键帧来验：
        # 小数 <0.5 时 round 会取到比关键帧更小的整数（198.408→198，末尾采样
        # 看不到），所以实现用 ceil。先直接测这条纯函数（含 .408 这个真实回退
        # 样本），再验生产路径确实用上了它。
        check("_union_frame_end ceilings a fractional animation end",
              ui._union_frame_end(198.408, 0) == 199,
              "got {0}".format(ui._union_frame_end(198.408, 0)))
        check("_union_frame_end would catch a round() regression",
              ui._union_frame_end(198.408, 0) > int(round(198.408)),
              "round={0}".format(int(round(198.408))))
        check("_union_frame_end keeps the longer side",
              ui._union_frame_end(50.2, 300) == 300
              and ui._union_frame_end(300.2, 50) == 301,
              "{0} / {1}".format(ui._union_frame_end(50.2, 300),
                                 ui._union_frame_end(300.2, 50)))

        scene = bpy.context.scene
        rig = next((o for o in bpy.data.objects if o.type == "ARMATURE"), None)
        if rig is not None and rig.animation_data and rig.animation_data.action:
            rng = action_frame_range(rig.animation_data.action)
            check("timeline reaches the last keyframe",
                  scene.frame_end >= rng[1],
                  "frame_end={0} anim_end={1:.3f}".format(scene.frame_end, rng[1]))

    # ---------------- 6. reuse_skeleton 开关 ----------------
    print("\n== 5. reuse_skeleton 开关 ==")
    # 骨架复用只在「骨名匹配的唯一骨架」时发生，所以这里必须用一个只含单个
    # 骨架的 .mesh：move_indicator 那种一个 <object> 下挂 3 个带 skeleton 的
    # shape 会一次建出 3 个，第二次导入时匹配到的骨架不止一个，本来就不会复用。
    # 模型索引条目的 mesh 路径在 .path 上（不是 detail["mesh"]）。
    candidates = []
    if target is not None:
        candidates.append(target.detail["mesh"])
    candidates.extend(e.path for e in models[:400] if e.path)
    single_mesh = None
    for cand in candidates:
        if not cand or not os.path.isfile(cand):
            continue
        reset_scene(ui)
        try:
            import_meshfile(cand, reuse_skeleton=False)
        except Exception:  # noqa: BLE001
            continue
        if len([o for o in bpy.data.objects if o.type == "ARMATURE"]) == 1:
            single_mesh = cand
            break

    check("found a single-skeleton mesh for the reuse test", single_mesh is not None,
          os.path.basename(single_mesh) if single_mesh else "-")

    if single_mesh:
        reset_scene(ui)
        import_meshfile(single_mesh, reuse_skeleton=False)
        check("reuse_skeleton=False creates one armature",
              len([o for o in bpy.data.objects if o.type == "ARMATURE"]) == 1)
        import_meshfile(single_mesh, reuse_skeleton=False)
        check("reuse_skeleton=False does not reuse (new armature again)",
              len([o for o in bpy.data.objects if o.type == "ARMATURE"]) == 2,
              "n={0}".format(len([o for o in bpy.data.objects
                                  if o.type == "ARMATURE"])))

        reset_scene(ui)
        import_meshfile(single_mesh)
        check("default import creates one armature",
              len([o for o in bpy.data.objects if o.type == "ARMATURE"]) == 1)
        import_meshfile(single_mesh)
        check("default import reuses the existing armature",
              len([o for o in bpy.data.objects if o.type == "ARMATURE"]) == 1,
              "n={0}".format(len([o for o in bpy.data.objects
                                  if o.type == "ARMATURE"])))

    print("\n{0} passed / {1} failed".format(len(PASS), len(FAIL)))
    if FAIL:
        print("failed:", ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        traceback.print_exc()
        code = 1
    sys.exit(code)
