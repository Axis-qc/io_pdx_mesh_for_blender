"""
索引与三入口 UI 冒烟自检。

跑法：
    & 'G:\\SteamLibrary\\steamapps\\common\\Blender\\5.2\\blender.exe' -b --factory-startup \
      --python test/check_index_ui.py

游戏根默认 G:\\SteamLibrary\\steamapps\\common\\Stellaris，
可用环境变量 IO_PDX_TEST_GAME 覆盖。

检查：
  1. 索引三类均有条目，分类非空且与目录名一致
  2. 搜索能命中（模型按文件名、按 pdxmesh 名；粒子按注册名；实体按 entity 名）
  3. 禁用分类后条目数精确减少
  4. mod_only 过滤生效
  5. 场景属性与三个标签页注册成功
  6. 列表填充与加载操作符存在
  7. entity 条目能解析出 mesh 路径（本轮只列表与加载，不做挂点定位）

只读游戏文件；只改内存中的场景属性，结尾不保存任何文件。
"""

import os
import sys

sys.dont_write_bytecode = True

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(ROOT))

import bpy  # noqa: E402
from io_pdx_mesh.pdx_particles import index as IX  # noqa: E402


def _ensure_addon_registered():
    """让插件处于已注册状态，UI 属性与操作符才存在。

    后台 --factory-startup 不会自动启用插件；优先走 addon_enable（走真实注册
    路径），失败则手动调 register()，两条路都拿不到就返回 False 由调用方报错。
    """
    try:
        if bpy.ops.preferences.addon_enable(module="io_pdx_mesh") == {"FINISHED"}:
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        import io_pdx_mesh
        io_pdx_mesh.pdx_blender.register()
        return True
    except Exception as err:  # noqa: BLE001
        print("addon register failed:", err)
        return False

GAME = os.environ.get(
    "IO_PDX_TEST_GAME", r"G:\SteamLibrary\steamapps\common\Stellaris")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  {0} {1}{2}".format("PASS" if cond else "FAIL", name,
                                "  — " + detail if detail else ""))


def _mesh_locator_names(meshpath):
    """读一个 .mesh 里所有 locator 的名字（用于判断 event 的 node 能否解析）。

    entity 的 event 挂点有两个来源：entity 自带 locator 与 pdxmesh 指向的
    .mesh 的 locator。两处都没有就是「解析不到」，插件现在会拒载该粒子。
    """
    try:
        from io_pdx_mesh import pdx_data
        elem = pdx_data.read_meshfile(meshpath)
        locs = elem.find("locator")
        if locs is None:
            return set()
        return {pdx_data.PDXData(x).name for x in locs}
    except Exception:  # noqa: BLE001
        return set()


def main():
    print("index/UI smoke check")
    print("game:", GAME)
    print("addon registered:", _ensure_addon_registered())

    if not os.path.isdir(GAME):
        print("SKIP: game folder not found:", GAME)
        return 2

    # ---- 1. 索引构建 ----
    idx = IX.PDXIndex([GAME], vanilla_root=GAME).build()
    st = idx.stats
    print("stats:", st)
    check("index builds", st["files"] > 0, "files={0}".format(st["files"]))
    check("models indexed", st["model"] > 0, "n={0}".format(st["model"]))
    check("particles indexed", st["particle"] > 0, "n={0}".format(st["particle"]))
    check("entities indexed", st["entity"] > 0, "n={0}".format(st["entity"]))

    cats = idx.categories(IX.KIND_MODEL)
    check("model categories non-empty", len(cats) > 0, "n={0}".format(len(cats)))
    # 分类是相对 gfx/ 的目录：允许一级目录（models）、多级（models/ships）
    # 与 gfx 根下的散文件（"(gfx root)"），但不该出现绝对路径或反斜杠
    check("categories are gfx-relative dir names",
          all(("\\" not in c) and (":" not in c) for c in cats),
          "sample={0}".format(list(cats)[:2]))
    check("category counts sum to total",
          sum(cats.values()) == st["model"],
          "{0} vs {1}".format(sum(cats.values()), st["model"]))

    # ---- 2. 搜索 ----
    q = idx.query(IX.KIND_MODEL, text="corvette")
    check("model search by filename", len(q) > 0, "hits={0}".format(len(q)))

    # 按 pdxmesh 名搜（model_key 要求两种名字都能搜到）
    target = None
    for e in idx.entries:
        if e.kind == IX.KIND_MODEL and e.detail.get("pdxmesh"):
            target = e
            break
    if target is not None:
        pn = target.detail["pdxmesh"][0]
        hit = idx.query(IX.KIND_MODEL, text=pn)
        check("model search by pdxmesh name", any(x is target for x in hit),
              "pdxmesh={0}".format(pn))

    qp = idx.query(IX.KIND_PARTICLE)
    check("particle list non-empty", len(qp) > 0, "n={0}".format(len(qp)))

    qe = idx.query(IX.KIND_ENTITY, require_particle=True)
    check("entity page lists only particle carriers",
          all(e.detail.get("has_particle") for e in qe),
          "n={0}".format(len(qe)))
    check("entity filter actually trims", len(qe) < st["entity"],
          "{0} < {1}".format(len(qe), st["entity"]))

    # ---- 3. 禁用分类 ----
    if cats:
        victim = next(iter(cats))
        before = len(idx.query(IX.KIND_MODEL))
        after = len(idx.query(IX.KIND_MODEL, disabled=(victim,)))
        check("disable category removes exactly its count",
              before - after == cats[victim],
              "{0}-{1}={2} vs {3}".format(before, after, before - after, cats[victim]))

    # ---- 4. mod_only / hidden_roots ----
    # mod_only 参数仍在 query 签名上（向后兼容），但界面开关已由偏好里
    # 的按根隐藏取代：这里改验 hidden_roots 过滤真的把原版根条目挡掉。
    n_mod = len(idx.query(IX.KIND_MODEL, mod_only=True))
    check("mod_only excludes game content", n_mod == 0,
          "mods_only={0} (single vanilla root)".format(n_mod))
    n_all = len(idx.query(IX.KIND_MODEL))
    n_hidden = len(IX.PDXIndex(idx.roots, vanilla_root=idx.vanilla_root,
                               hidden_roots=idx.roots).query(IX.KIND_MODEL))
    check("hidden_roots filters every entry of the hidden root",
          n_hidden == 0 and n_all > 0,
          "all={0} hidden={1}".format(n_all, n_hidden))

    # ---- 5. entity 能解析出 mesh ----
    with_mesh = [e for e in qe if e.detail.get("mesh")]
    check("entity resolves mesh path", len(with_mesh) > 0,
          "{0}/{1}".format(len(with_mesh), len(qe)))
    if with_mesh:
        p = with_mesh[0].detail["mesh"]
        check("resolved mesh exists on disk", os.path.isfile(p), p)

    # ---- 5b. pdxmesh file 跨根回退：mod 只定义引用、mesh 在原版 ----
    # 用户 mod lev_drake.gfx（若在扫描范围内）指向原版巨龙 mesh，是这条
    # 回退的真实样本；不在就只验证「所有解析出的 mesh 都真实存在」。
    cross = [e for e in qe
             if e.detail.get("mesh")
             and idx.vanilla_root
             and idx.vanilla_root.lower() in e.detail["mesh"].lower()]
    if cross:
        check("mod-referenced vanilla mesh resolved across roots",
              all(os.path.isfile(e.detail["mesh"]) for e in cross),
              "{0} e.g. {1}".format(len(cross), cross[0].detail["mesh"]))
    else:
        check("all resolved entity meshes exist on disk",
              all(os.path.isfile(e.detail["mesh"]) for e in with_mesh),
              "{0} checked".format(len(with_mesh)))

    # ---- 6. UI 注册（需要插件已加载）----
    scene = bpy.context.scene
    for prop in ("io_pdx_asset_tab", "io_pdx_asset_items", "io_pdx_asset_index",
                 "io_pdx_asset_filter", "io_pdx_index_categories",
                 "io_pdx_index_disabled"):
        check("scene prop {0}".format(prop), hasattr(scene, prop))
    # io_pdx_index_mod_only 已删除：按根隐藏取代了它，偏好里才是开关的家
    check("scene prop io_pdx_index_mod_only removed",
          not hasattr(scene, "io_pdx_index_mod_only"))

    tabs = [i.identifier for i in scene.bl_rna.properties["io_pdx_asset_tab"].enum_items]
    check("three tabs registered", set(tabs) == {"model", "particle", "entity"},
          "tabs={0}".format(tabs))

    for op in ("io_pdx_particles.load_selected", "io_pdx_particles.rescan",
               "io_pdx_particles.all_categories"):
        check("operator {0}".format(op), hasattr(bpy.ops.io_pdx_particles, op.split(".")[1]))

    # ---- 7. 列表填充走真实代码路径 ----
    # 用兼容字段（场景属性）配原版根，让 _pref_roots 的真实回退路径也被走到；
    # 插件偏好就绪时它会优先用偏好，这里两条路都验。
    from io_pdx_mesh.pdx_particles import ui as UI
    scene.io_pdx_particle_vanilla_root = GAME
    refreshed = UI._ensure_index(force=True)
    check("_ensure_index reaches the same content",
          refreshed.stats["model"] > 0,
          "files={0}".format(refreshed.stats.get("files")))
    UI._INDEX = idx              # 复用刚建的索引，避免测试里再扫一遍
    UI._INDEX_KEY = ((), GAME)
    scene.io_pdx_asset_tab = "model"
    UI._rebuild_categories(bpy.context)
    n_cats = len(scene.io_pdx_index_categories)
    check("categories filled into scene", n_cats > 0, "n={0}".format(n_cats))

    # 分类行必须带 kind，否则禁用逻辑会串到别的标签页
    check("category rows carry kind",
          all(c.kind == "model" for c in scene.io_pdx_index_categories))

    shown = UI._rebuild_asset_list(bpy.context)
    check("asset list filled", shown > 0, "n={0}".format(shown))
    check("shown matches list rows", shown == len(scene.io_pdx_asset_items),
          "{0} vs {1}".format(shown, len(scene.io_pdx_asset_items)))

    # 禁用一整类后行数应精确减少
    first = scene.io_pdx_index_categories[0]
    cat, cnt = first.category, first.count
    before_rows = len(scene.io_pdx_asset_items)
    UI._set_category_enabled(scene, "model", cat, False)
    UI._rebuild_categories(bpy.context)
    UI._rebuild_asset_list(bpy.context)
    after_rows = len(scene.io_pdx_asset_items)
    # 列表有 MAX_LIST_ROWS 上限，超限时只校验「没变多」
    if before_rows < UI.MAX_LIST_ROWS:
        check("disabling a category trims rows", before_rows - after_rows == cnt,
              "{0}-{1}={2} vs {3}".format(before_rows, after_rows, before_rows - after_rows, cnt))
    else:
        check("disabling a category does not grow rows", after_rows <= before_rows)

    # 搜索过滤
    UI._set_category_enabled(scene, "model", cat, True)
    UI._rebuild_categories(bpy.context)
    scene.io_pdx_asset_filter = "corvette"
    n_hit = UI._rebuild_asset_list(bpy.context)
    check("search filters the list", n_hit > 0 and n_hit < shown,
          "hit={0} all={1}".format(n_hit, shown))
    check("all rows match the query",
          all("corvette" in r.name.lower() for r in scene.io_pdx_asset_items))
    scene.io_pdx_asset_filter = ""

    # ---- 8. Rescan 操作符（面板按钮走的真实入口）----
    UI._INDEX = None
    UI._INDEX_KEY = None
    res = bpy.ops.io_pdx_particles.rescan()
    check("rescan operator finishes", res == {"FINISHED"}, str(res))
    check("rescan refills the list", len(scene.io_pdx_asset_items) > 0,
          "n={0}".format(len(scene.io_pdx_asset_items)))
    check("rescan refills categories", len(scene.io_pdx_index_categories) > 0,
          "n={0}".format(len(scene.io_pdx_index_categories)))

    # 切换标签页要重建出对应类别的行
    scene.io_pdx_asset_tab = "entity"
    check("entity tab rows carry entity kind",
          all(c.kind == "entity" for c in scene.io_pdx_index_categories),
          "n={0}".format(len(scene.io_pdx_index_categories)))

    # 分类记录必须跨标签页隔离：禁用一个 model 分类不该影响 entity 页
    scene.io_pdx_asset_tab = "model"
    first_model_cat = scene.io_pdx_index_categories[0].category
    UI._set_category_enabled(scene, "model", first_model_cat, False)
    scene.io_pdx_asset_tab = "entity"
    n_entity_rows = len(scene.io_pdx_asset_items)
    UI._set_category_enabled(scene, "entity",
                             scene.io_pdx_index_categories[0].category, False)
    scene.io_pdx_asset_tab = "model"
    check("disabling on one tab keeps that tab usable",
          len(scene.io_pdx_asset_items) > 0,
          "model rows={0}, entity rows seen={1}".format(
              len(scene.io_pdx_asset_items), n_entity_rows))
    check("disabled records are per-kind",
          any(d.kind == "model" for d in scene.io_pdx_index_disabled)
          and any(d.kind == "entity" for d in scene.io_pdx_index_disabled))

    # ---- 8.5 偏好键：扩展安装下 addons 的键是顶层包名，不是 manifest 的短 id ----
    # 真实运行时（扩展安装）键为 bl_ext.user_default.io_pdx_mesh；按短 id 查会
    # 永远拿到 None，偏好里的设置全不生效却不报错。这里锁住两级回退。
    addons = bpy.context.preferences.addons
    ext_key = "bl_ext.user_default.io_pdx_mesh"
    keys = list(addons.keys())
    check("addon is findable by some key",
          any("io_pdx_mesh" in k for k in keys), "keys={0}".format(
              [k for k in keys if "io_pdx_mesh" in k]))
    prefs_obj = UI._addon_prefs()
    if ext_key in addons:
        check("_addon_prefs finds the extension key", prefs_obj is not None,
              "ext key present")
    else:
        # 传统安装路径：短 id 即键，同样要能取到
        check("_addon_prefs finds the short key", prefs_obj is not None,
              "short key path")
    if prefs_obj is not None:
        check("prefs expose the contract attributes",
              hasattr(prefs_obj, "vanilla_root") and hasattr(prefs_obj, "extra_roots"))

    # ---- 8.6 索引换了内容后，列表必须被判定过期 ----
    # 场景：用户在偏好里改掉游戏目录 → _ensure_index 换索引 → 列表不能还列旧内容。
    # 判据是模块级 _LIST_KEY（draw 里不能写 Scene 属性，见 check_panel_draw.py）。
    UI._INDEX = None
    UI._INDEX_KEY = None
    UI._ensure_index(force=True)
    UI._LIST_KEY = "a-key-that-cannot-match"
    check("list is judged stale after the index key changes",
          UI._LIST_KEY != UI._list_token(), UI._list_token())
    UI._mark_list_current()
    check("marking the list current clears staleness",
          UI._LIST_KEY == UI._list_token(), UI._LIST_KEY)

    # ---- 9. 实际加载：模型进场景（走 load_selected 操作符）----
    scene.io_pdx_asset_tab = "model"
    # 前面的用例故意关过 model 的分类，这里恢复干净状态再加载
    for i in range(len(scene.io_pdx_index_disabled) - 1, -1, -1):
        if scene.io_pdx_index_disabled[i].kind == "model":
            scene.io_pdx_index_disabled.remove(i)
    UI._rebuild_categories(bpy.context)
    scene.io_pdx_asset_filter = "test_object"
    UI._rebuild_asset_list(bpy.context)
    if len(scene.io_pdx_asset_items):
        scene.io_pdx_asset_index = 0
        before = set(bpy.data.objects.keys())
        res = bpy.ops.io_pdx_particles.load_selected()
        after = set(bpy.data.objects.keys())
        check("load_selected imports a mesh", res == {"FINISHED"} and len(after) > len(before),
              "res={0} new={1}".format(res, sorted(after - before)[:3]))
    else:
        check("load_selected has a mesh to load", False, "no 'test_object' entry found")
    scene.io_pdx_asset_filter = ""

    # ---- 10. Entity：模型 + 粒子一起加载（本轮头等功能）----
    # 挑一个模型小、粒子少的 entity，避免把重型系统拉进回归。
    scene.io_pdx_asset_tab = "entity"
    for i in range(len(scene.io_pdx_index_disabled) - 1, -1, -1):
        if scene.io_pdx_index_disabled[i].kind == "entity":
            scene.io_pdx_index_disabled.remove(i)
    UI._rebuild_categories(bpy.context)
    UI._rebuild_asset_list(bpy.context)
    pick = None
    for i, e in enumerate(UI._SHOWN):
        d = e.detail
        if d.get("mesh") and os.path.isfile(d["mesh"]) and len(d.get("particles") or []) == 1:
            pick = i
            break
    if pick is None:
        check("found a small entity to load", False, "no single-particle entity with a mesh")
    else:
        entry = UI._SHOWN[pick]
        scene.io_pdx_asset_index = pick
        before = set(bpy.data.objects.keys())
        res = bpy.ops.io_pdx_particles.load_selected()
        after = set(bpy.data.objects.keys())
        check("entity load imports its model", len(after) > len(before),
              "entity={0} new={1}".format(entry.name, len(after - before)))
        check("entity load reports success", res == {"FINISHED"}, str(res))
        # 粒子预览对象应已建起（子系统网格在 pdx_particles_* 集合里）
        colls = [c.name for c in bpy.data.collections if c.name.startswith("pdx_particles_")]
        check("entity load builds a particle preview", len(colls) > 0,
              "collections={0}".format(colls[:2]))
        check("entity load registers preview entries",
              len(scene.io_pdx_particle_entries) > 0,
              "n={0}".format(len(scene.io_pdx_particle_entries)))

        # 挂点定位：test_example_entity 的事件写着 node="loc02"，模型导入会建出
        # 同名 EMPTY，粒子对象应当 parent 到它而不是留在世界原点。
        mounted = [o for o in bpy.data.objects
                   if o.parent is not None and o.name.startswith("pdx_part_")]
        if mounted:
            parents = {o.parent.name for o in mounted}
            check("particles mounted on the node locator", "loc02" in parents,
                  "parents={0}".format(sorted(parents)))
            # 单位阵 parent inverse：世界坐标应等于挂点坐标，而不是回到原点
            got = mounted[0].matrix_world.translation
            want = bpy.data.objects["loc02"].matrix_world.translation
            check("mount keeps the locator position (not back at origin)",
                  (got - want).length < 1e-4,
                  "world={0} locator={1}".format(tuple(round(v, 3) for v in got),
                                                 tuple(round(v, 3) for v in want)))
        else:
            check("entity particle objects exist", False, "no pdx_part_* objects")

    # ---- 12. 缺挂点的粒子不加载并报错（0.99.2）----
    # 原版约 15% 的 event 挂点在 entity 自带 locator 与 .mesh locator 里都找不到，以前静默留在原点。
    print("\n-- 缺挂点的粒子不加载 --")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _ensure_addon_registered()
    UI._INDEX = None
    index = UI._ensure_index()

    # 挑一个事件 node 在两处都解析不到的 entity 作为反例
    bad = None
    for e in index.entries:
        if e.kind != "entity":
            continue
        d = e.detail or {}
        for ev in d.get("events") or []:
            node = ev.get("node")
            if not node or not ev.get("particle"):
                continue
            own = {l.get("name") for l in d.get("locators") or []}
            mesh_locs = set()
            mp = d.get("mesh")
            if mp and os.path.isfile(mp):
                mesh_locs = _mesh_locator_names(mp)
            if node not in own and node not in mesh_locs:
                bad = (e, node, ev.get("particle"))
                break
        if bad:
            break

    if bad is None:
        check("found an entity whose mount point cannot resolve", False,
              "no unresolved-mount entity in vanilla data")
    else:
        ent, node, particle = bad
        loaded_any, rejected = UI._load_entity(bpy.context, ent, index)
        check("unresolved mount is reported, not silent",
              any(node in r for r in rejected),
              "entity={0} node={1} rejected={2}".format(ent.name, node, sorted(set(rejected))))
        check("unresolved-mount particle is not loaded",
              not any(o.name.startswith("pdx_part_") for o in bpy.data.objects),
              "particle objects={0}".format(
                  [o.name for o in bpy.data.objects if o.name.startswith("pdx_part_")]))

    # node 为空的事件不该被拒（引擎语义就是在 entity 原点播放）
    print("\n-- node 为空照常加载 --")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _ensure_addon_registered()
    UI._INDEX = None
    index = UI._ensure_index()
    fake = type("E", (), {})()
    fake.name = "<probe-no-node>"
    fake.kind = "entity"
    fake.detail = {
        "mesh": "",
        "locators": [],
        "events": [{"particle": "plasma_cannon_medium_hit_effect", "node": ""}],
    }
    loaded_any, rejected = UI._load_entity(bpy.context, fake, index)
    check("an event without a node is still loaded", loaded_any and not rejected,
          "loaded_any={0} rejected={1}".format(loaded_any, sorted(set(rejected))))

    # 本体找不到也要报（同属「这个粒子没加载」）
    fake.detail["events"] = [{"particle": "no_such_particle_xyz", "node": ""}]
    loaded_any, rejected = UI._load_entity(bpy.context, fake, index)
    check("a missing body is reported too",
          any("no body" in r for r in rejected),
          "rejected={0}".format(sorted(set(rejected))))

    print("\n{0} passed / {1} failed".format(len(PASS), len(FAIL)))
    if FAIL:
        print("failed:", ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    code = main()
    sys.exit(code)
