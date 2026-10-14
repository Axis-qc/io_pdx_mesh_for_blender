"""index.py 单文件自检：根去重、分类兜底、静默跳过、跨根归属、粒子过滤。

跑法：
    & 'G:\\SteamLibrary\\steamapps\\common\\Blender\\5.2\\blender.exe' -b --factory-startup \
      --python test/check_index_only.py

输出每行以 PDXPROBE 开头，便于从 Blender 启动日志里过滤。
只读游戏文件；临时目录只在系统 TEMP 下建一个空壳用于验证回报。
"""

import logging
import os
import sys
import tempfile

sys.dont_write_bytecode = True

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(ROOT))

logging.basicConfig(level=logging.WARNING,
                    format="PDXPROBE LOG %(levelname)s: %(message)s", stream=sys.stdout)

from io_pdx_mesh.pdx_particles import index as IX  # noqa: E402

GAME = os.environ.get(
    "IO_PDX_TEST_GAME", r"G:\SteamLibrary\steamapps\common\Stellaris")


def line(label, value):
    print("PDXPROBE {0}: {1}".format(label, value))


def probe_dedup():
    print("PDXPROBE ===== [1] 根去重（大小写变体） =====")
    one = IX.PDXIndex([GAME]).build()
    line("single stats", one.stats)
    two = IX.PDXIndex([GAME, GAME.upper()]).build()
    line("double stats", two.stats)
    line("double roots", two.roots)
    line("files/files equal single", one.stats["files"] == two.stats["files"])
    line("particle/particle equal single", one.stats["particle"] == two.stats["particle"])
    line("entity/entity equal single", one.stats["entity"] == two.stats["entity"])
    return one


def probe_category():
    print("PDXPROBE ===== [2] gfx 根下的分类名 =====")
    line("gfx/foo.mesh", repr(IX._category_of(GAME, os.path.join(GAME, "gfx", "foo.mesh"))))
    line("gfx/models/x.mesh", repr(IX._category_of(GAME, os.path.join(GAME, "gfx", "models", "x.mesh"))))
    line("gfx/models/ships/x.mesh",
         repr(IX._category_of(GAME, os.path.join(GAME, "gfx", "models", "ships", "x.mesh"))))


def probe_silent():
    print("PDXPROBE ===== [3] 目录不存在 / 无 gfx / gfx 下无可索引文件 =====")
    ghost = os.path.join(tempfile.gettempdir(), "pdx_no_such_root_xyz")
    line("ghost exists", os.path.isdir(ghost))
    ghost_skipped = []
    line("_iter_gfx_files(ghost)", list(IX._iter_gfx_files(ghost, ghost_skipped)))
    line("ghost skipped", ghost_skipped)
    ghost_index = IX.PDXIndex([ghost])
    line("ghost roots accepted", ghost_index.roots)
    ghost_index.build()
    line("ghost skipped", getattr(ghost_index, "skipped", "<no attribute>"))
    line("ghost stats", ghost_index.stats)

    no_gfx = IX.PDXIndex([ROOT])
    no_gfx.build()
    line("no-gfx skipped", getattr(no_gfx, "skipped", "<no attribute>"))

    shell = os.path.join(tempfile.gettempdir(), "pdx_probe_shell")
    try:
        os.makedirs(os.path.join(shell, "gfx"), exist_ok=True)
        with open(os.path.join(shell, "gfx", "only_texture.dds"), "wb") as fh:
            fh.write(b"dds")
        tex = IX.PDXIndex([shell]).build()
        line("textures-only stats", tex.stats)
        line("textures-only skipped", getattr(tex, "skipped", "<no attribute>"))
    except OSError as err:  # noqa: BLE001
        line("textures-only skipped (temp not writable)", err)


def probe_workshop():
    print("PDXPROBE ===== [3b] 真实创意工坊根（只读） =====")
    base = os.environ.get(
        "IO_PDX_TEST_WORKSHOP",
        r"G:\SteamLibrary\steamapps\workshop\content\281990")
    if not os.path.isdir(base):
        line("workshop base missing", base)
        return
    mods = sorted(os.path.join(base, d) for d in os.listdir(base)
                  if os.path.isdir(os.path.join(base, d)))
    line("workshop mod dirs", len(mods))
    idx = IX.PDXIndex(mods).build()
    line("workshop stats", idx.stats)
    reasons = {}
    for item in idx.skipped:
        reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
    line("skipped by reason", reasons)
    line("skipped roots",
         [os.path.basename(i["root"]) for i in idx.skipped
          if i["reason"] == "no indexed files under gfx"])


def probe_root_of():
    print("PDXPROBE ===== [4] 域外文件的根归属 =====")
    line("outside", repr(IX._root_of(r"Z:\elsewhere\gfx\x.mesh", [GAME])))
    line("inside", repr(IX._root_of(os.path.join(GAME, "gfx", "models", "x.mesh"), [GAME])))
    line("case variant inside",
         repr(IX._root_of(os.path.join(GAME.upper(), "gfx", "models", "x.mesh"), [GAME])))


def probe_require_particle(idx):
    print("PDXPROBE ===== [5] require_particle 与缺键条目 =====")
    probe = IX.IndexEntry(IX.KIND_ENTITY, "probe_no_key", ["probe_no_key"], "",
                          "(probe)", GAME, False, {"pdxmesh": ""})
    idx.entries.append(probe)
    line("probe detail has has_particle key", "has_particle" in probe.detail)
    hits = idx.query(IX.KIND_ENTITY, require_particle=True)
    line("probe passes require_particle", any(e is probe for e in hits))
    line("require_particle hits", len(hits))
    line("all real hits carry the key",
         all(e.detail.get("has_particle") for e in hits if e is not probe))


def main():
    print("PDXPROBE index-only probe")
    line("game", GAME)
    if not os.path.isdir(GAME):
        print("PDXPROBE SKIP: game folder not found")
        return 2
    idx = probe_dedup()
    probe_category()
    probe_silent()
    probe_workshop()
    probe_root_of()
    probe_require_particle(idx)
    print("PDXPROBE done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
