# -*- coding: utf-8 -*-
"""CHECKPOINT_STRIDE 两档基准：240 vs 60（纯测量，不改插件任何文件）。

背景：simulator.CHECKPOINT_STRIDE 决定回退关键帧的间隔。间隔越小回退越快、
内存越多。本脚本对全库最重的 16 个系统逐个测两档的内存与回退耗时，为取值
提供实测依据（0.96.0 定为 60，即由此脚本出数据拍板）。

纯 Python 运行（importlib 手工组装 pdx_particles 包，不 import bpy）：

    python test/bench_checkpoint_stride.py

口径与折衷：
- 内存用 tracemalloc current：粒子表本身两档结构相同，两档差值即检查点开销。
- 回退耗时在本机系统 Python（无 numpy）上是标量路径的上界；插件实际在
  Blender 内走 numpy 向量化路径，但加速比是同路径对比，仍然有效。
- 单系统峰值 >3000 粒时回退只测 1 轮，其余 5 轮取中位（万粒系统单档
  advance 要数分钟，控制总预算）。
- 1 帧回退加速比强依赖目标帧 n mod stride 的余数，出现 ≈1x 属余数巧合，
  不是回退无效；1 秒级回退才是稳定指标。
- 每测完一个系统立即把全量结果落盘，中途断了数据不丢（断点续跑：已测完
  两档的系统直接作种子，不重测）。

产出：%TEMP%\\pdx_stride_bench.json（UTF-8）。
"""

import importlib.util
import json
import os
import statistics
import sys
import time
import tracemalloc
import types

sys.dont_write_bytecode = True    # 只读测量：不给插件目录留 __pycache__

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T0 = time.perf_counter()
MANDATORY = ("magnetic_storm_2_file", "kaiju_gobble_planet_file")
STRIDES = (240, 60)
SUB_DT = 0.1
MOD_ROOTS = [r"C:\StellarisMod\stellaris_godie", r"C:\StellarisMod\hjsbfxr"]
VANILLA_ROOT = r"G:\SteamLibrary\steamapps\common\Stellaris"
TEMP = os.environ.get("TEMP", os.path.join(os.path.expanduser("~"), "AppData", "Local", "Temp"))
JSON_PATH = os.path.join(TEMP, "pdx_stride_bench.json")
EXTRA_BUDGET_S = 2400.0     # 必含完成后，附加系统的预算（秒）；随时可断（增量落盘）

RESULT = {"meta": {}, "scan_top20": [], "systems": {}, "aggregates": {}, "extrapolation": {}}


def log(*args):
    print("[bench %7.1fs]" % (time.perf_counter() - T0), *args, flush=True)


def dump():
    RESULT["meta"]["elapsed_s"] = round(time.perf_counter() - T0, 1)
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(RESULT, f, ensure_ascii=False, indent=1)


# ---------- 0) 种子：上次跑完的系统直接复用，不重测 ----------

seed = {}
if os.path.isfile(JSON_PATH):
    try:
        with open(JSON_PATH, encoding="utf-8") as f:
            old = json.load(f)
        for name, e in old.get("systems", {}).items():
            if "stride240" in e and "stride60" in e:
                seed[name] = e
        if seed:
            log("种子：%s，已测系统 %d 个" % (JSON_PATH, len(seed)))
    except Exception as err:  # noqa: BLE001
        log("!! 种子读取失败 %s：%r" % (JSON_PATH, err))
RESULT["systems"].update(seed)

# ---------- 1) 纯 Python 导入（绕过含 bpy 的 __init__.py） ----------

def load_package():
    pkg = types.ModuleType("pdx_particles")
    pkg.__path__ = [os.path.join(REPO, "pdx_particles")]
    pkg.__package__ = "pdx_particles"
    sys.modules["pdx_particles"] = pkg
    mods = {}
    for name in ("parser", "simulator"):
        path = os.path.join(REPO, "pdx_particles", name + ".py")
        spec = importlib.util.spec_from_file_location("pdx_particles." + name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        mods[name] = mod
    return mods["parser"], mods["simulator"]


parser, simulator = load_package()
log("simulator loaded, _NP =", simulator._NP,
    "（None = 纯 Python 标量路径；内存两档结构相同对比公平，耗时为上界，加速比有效）")
log("simulator.CHECKPOINT_STRIDE（插件现行值，测量时会被临时覆盖）=",
    simulator.CHECKPOINT_STRIDE)

# ---------- 2) 全库扫描 + 峰值扫描（top20 / 代表集排序） ----------

t0 = time.perf_counter()
lib = parser.ParticleLibrary(MOD_ROOTS + [VANILLA_ROOT]).scan()
log("scan done: files=%d systems=%d (%.1fs)"
    % (lib.files_scanned, len(lib.systems), time.perf_counter() - t0))
RESULT["meta"]["files_scanned"] = lib.files_scanned
RESULT["meta"]["n_systems"] = len(lib.systems)

t0 = time.perf_counter()
rows = []   # (name, n_subs, peak_alive, t_at_peak)
names = sorted(lib.systems)
for idx, name in enumerate(names):
    sim = simulator.SystemSim(lib.systems[name])
    tmax = sim.total_duration()
    n = max(2, int(tmax / SUB_DT))
    peak, pt = 0.0, 0.0
    for k in range(1, n + 1):
        t = tmax * k / n
        alive = 0.0
        for s in sim.subs:
            alive += s.estimate_alive(t)
        if alive > peak:
            peak, pt = alive, t
    rows.append((name, len(sim.subs), peak, pt))
log("peak scan done (%.1fs)" % (time.perf_counter() - t0))

rows.sort(key=lambda r: -r[2])
RESULT["scan_top20"] = [
    {"name": n, "subs": s, "peak_alive_est": round(p, 1)} for n, s, p, _ in rows[:20]]
peak_est = {n: p for n, _, p, _ in rows}
log("top10 峰值：%s" % ", ".join("%s(~%.0f)" % (n, p) for n, _, p, _ in rows[:10]))


def find_name(want):
    if want in lib.systems:
        return want
    low = want.lower()
    for n in names:
        if low in n.lower():
            return n
    return None


mandatory = []
for want in MANDATORY + ("par_01",):
    n = find_name(want)
    if n:
        mandatory.append(n)
    else:
        log("!! 未找到必含系统：%s" % want)

order = [n for n in mandatory if n not in RESULT["systems"]]
order += [n for n, _, _, _ in rows[:15] if n not in RESULT["systems"] and n not in order]
log("测量顺序（%d 个，必含优先，已完成跳过）：%s" % (len(order), order))

# ---------- 3) 测量 ----------

def mem_measure(system, stride):
    simulator.CHECKPOINT_STRIDE = stride
    tracemalloc.start()
    sim = simulator.SystemSim(system)
    sim.advance(sim.total_duration())
    peak, cur = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    out = {
        "mem_peak_bytes": peak, "mem_cur_bytes": cur,
        "ckpt_entries": sum(len(s._ckpt) for s in sim.subs),
        "ckpt_stored": sum(len(s._ckpt) - 1 for s in sim.subs),
        "ckpt_rows": sum(len(c[2]) for s in sim.subs for c in s._ckpt),
        "live_rows": sum(len(s.particles) for s in sim.subs),
        "total_duration_s": round(sim.total_duration(), 3),
    }
    del sim
    return out


def replay_substeps(n_target, stride):
    return (n_target % stride) if n_target > 0 else 0


def rewind_measure(system, stride, reps):
    simulator.CHECKPOINT_STRIDE = stride
    sim = simulator.SystemSim(system)
    T = min(sim.total_duration(), 30.0)
    sim.advance(T)
    n_T = int(T * 120.0 + 1e-9)
    out = {"T_s": round(T, 3), "reps": reps,
           "replay_frame_substeps": replay_substeps(n_T - 2, stride),
           "replay_sec_substeps": replay_substeps(n_T - 120, stride),
           "one_frame_ms": [], "one_sec_ms": []}
    for _ in range(reps):
        t0 = time.perf_counter()
        sim.advance(T - 1.0 / simulator.PARTICLE_FPS)
        out["one_frame_ms"].append((time.perf_counter() - t0) * 1000.0)
        sim.advance(T)
    for _ in range(reps):
        t0 = time.perf_counter()
        sim.advance(T - 1.0)
        out["one_sec_ms"].append((time.perf_counter() - t0) * 1000.0)
        sim.advance(T)
    del sim
    out["one_frame_ms_median"] = statistics.median(out["one_frame_ms"])
    out["one_sec_ms_median"] = statistics.median(out["one_sec_ms"])
    return out


def measure_system(name):
    system = lib.systems[name]
    entry = {"n_subs": len(system.subsystems)}
    reps = 1 if peak_est.get(name, 0.0) > 3000 else 5
    live_ref = None
    for stride in STRIDES:
        log("  [%s] stride=%d 内存测量（advance 到 total_duration，reps=%d）..."
            % (name, stride, reps))
        m = mem_measure(system, stride)
        log("  [%s] stride=%d 回退耗时（T=%.1fs 后各 %d 轮）..."
            % (name, stride, min(m["total_duration_s"], 30.0), reps))
        r = rewind_measure(system, stride, reps)
        entry["stride%d" % stride] = {**m, "rewind": r}
        log("  [%s] stride=%d peak=%.2fMB cur=%.2fMB ckpt_stored=%d ckpt_rows=%d "
            "回退1帧=%.1fms 回退1s=%.1fms"
            % (name, stride, m["mem_peak_bytes"] / 1048576.0,
               m["mem_cur_bytes"] / 1048576.0, m["ckpt_stored"],
               m["ckpt_rows"], r["one_frame_ms_median"], r["one_sec_ms_median"]))
        if live_ref is None:
            live_ref = m["live_rows"]
        elif live_ref != m["live_rows"]:
            entry["live_rows_mismatch"] = (live_ref, m["live_rows"])
            log("  !! [%s] 两档 live_rows 不一致：%s" % (name, (live_ref, m["live_rows"])))
    m24, m60 = entry["stride240"], entry["stride60"]
    entry["delta"] = {
        "cur_mb": (m60["mem_cur_bytes"] - m24["mem_cur_bytes"]) / 1048576.0,
        "peak_mb": (m60["mem_peak_bytes"] - m24["mem_peak_bytes"]) / 1048576.0,
    }
    r24, r60 = m24["rewind"], m60["rewind"]
    entry["speedup"] = {
        "one_frame": (r24["one_frame_ms_median"] / r60["one_frame_ms_median"]
                      if r60["one_frame_ms_median"] > 0 else None),
        "one_sec": (r24["one_sec_ms_median"] / r60["one_sec_ms_median"]
                    if r60["one_sec_ms_median"] > 0 else None),
    }
    return entry


done_now, failed = [], []
for name in order:
    if done_now and name not in mandatory and (time.perf_counter() - T0) > EXTRA_BUDGET_S:
        log("!! 附加预算用完，停止（本轮完成 %d 个：%s）" % (len(done_now), done_now))
        RESULT["meta"]["budget_skipped"] = [n for n in order if n not in RESULT["systems"]]
        break
    log("== 系统 %s ==" % name)
    try:
        RESULT["systems"][name] = measure_system(name)
        done_now.append(name)
    except Exception as err:  # noqa: BLE001
        failed.append((name, repr(err)))
        log("!! [%s] 测量失败：%r" % (name, err))
    dump()   # 每系统增量落盘

# ---------- 4) 汇总：单系统净增 / top10 合计净增 / 全库外推（估算） ----------

deltas = {n: RESULT["systems"][n]["delta"]["cur_mb"] for n in RESULT["systems"]}
top10 = [n for n, _, _, _ in rows[:10]]
agg_top10 = {n: round(deltas[n], 2) for n in top10 if n in deltas}
missing_top10 = [n for n in top10 if n not in deltas]
RESULT["aggregates"] = {
    "note": "净增 = stride60 - stride240 的 tracemalloc current（粒子表本身两档相同，差值即 ckpt 开销）",
    "top10_names": top10,
    "top10_delta_mb_measured": agg_top10,
    "top10_missing": missing_top10,
    "top10_total_delta_mb": round(sum(agg_top10.values()), 2),
    "top10_total_delta_note": "top10 重系统同时装载的合计净增（估算：未测成员按已测同峰值档近似为 0 计入会低估，已单列 missing）",
    "single_max_delta_mb": round(max(deltas.values()), 2) if deltas else None,
    "single_max_delta_system": max(deltas, key=deltas.get) if deltas else None,
}
vals = list(deltas.values())
if vals:
    n_sys = len(lib.systems)
    RESULT["extrapolation"] = {
        "note": "估算：代表集 ckpt 净增（cur，60-240）均值/中位数 × 全库系统数；实际随各系统粒子峰值与时长分布变化",
        "measured_n": len(vals),
        "mean_delta_mb_per_system": round(statistics.mean(vals), 4),
        "median_delta_mb_per_system": round(statistics.median(vals), 4),
        "n_systems": n_sys,
        "extrapolated_total_mb_mean": round(statistics.mean(vals) * n_sys, 1),
        "extrapolated_total_mb_median": round(statistics.median(vals) * n_sys, 1),
    }

RESULT["meta"].update({
    "stride_values": list(STRIDES),
    "python": sys.version.split()[0],
    "numpy": bool(simulator._NP),
    "scalar_path_note": "无 numpy 时走纯 Python 标量路径；内存两档同为 list 结构对比公平，"
                        "回退耗时为上界（插件实际在 Blender 内走 numpy 向量化路径），加速比同路径对比有效",
    "seeded": sorted(seed),
    "measured_this_run": done_now,
    "failed": ["%s: %r" % f for f in failed],
})
dump()
log("JSON 已写入 %s" % JSON_PATH)
log("总耗时 %.1fs；本轮完成 %s；失败 %d 个；库内已测共 %d 个"
    % (RESULT["meta"]["elapsed_s"], done_now, len(failed), len(RESULT["systems"])))
