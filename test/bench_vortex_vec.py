# -*- coding: utf-8 -*-
"""vortex/spin 力场向量化 A/B 基准（只读测量，不改插件任何文件）。

问题：把 "vortex" "spin" 加进 simulator._VEC_FORCE_TYPES 后，纯 vortex 子系统
能否加速？加速多少？两条路径结果是否逐位一致？
（0.96.0 已加入这两个类型，本脚本用 monkeypatch 做 A/B，与插件取值无关。）

做法：
- 纯 Python 组装 pdx_particles 包导入（禁止 import bpy），打印 simulator._NP。
- 全库 scan（MOD 两个根 + 原版根），按力场类型把子系统分成
  纯 vortex（类型集合 ⊆ {vortex, spin} 且非空）/ 混合（vortex 与 point/turbulence
  共存）/ 其他 三类。
- 必测系统 magnetic_storm_2 + 全库纯 vortex 子系统按粒子峰值前 5 的系统：
  A = 默认 _VEC_FORCE_TYPES，B = monkeypatch 成
  ("turbulence","point","vortex","spin") 后重建 SystemSim 再推进同一时长，
  各 5 轮取中位（每轮重建 sim），计时口径是整个 advance 序列（真实行为，
  含 _VEC_MIN_PARTICLES=64 的门槛判断）。
- 逐位一致性：A/B 两侧各子系统 particles（list[list[float]]）逐行逐字段
  float == 精确比较，另比较 snapshot() 输出。0 差异才 PASS。

运行（必须用 Blender 自带 Python，系统 Python 没有 numpy）：
    G:\\SteamLibrary\\steamapps\\common\\Blender\\5.2\\python\\bin\\python.exe test/bench_vortex_vec.py

结果 JSON 写 %TEMP%\\pdx_vortex_bench.json。
"""

import importlib.util
import json
import os
import statistics
import sys
import time
import types

sys.dont_write_bytecode = True    # 只读测量：不给插件目录留 __pycache__

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOD_ROOT = r"C:\StellarisMod"
VANILLA_ROOT = r"G:\SteamLibrary\steamapps\common\Stellaris"
OUT_JSON = os.path.join(os.environ["TEMP"], "pdx_vortex_bench.json")
BENCH_START = time.time()
DEADLINE = BENCH_START + 18 * 60        # 脚本内硬预算：18 分钟（保证 magnetic_storm_2 优先完成）
SAMPLE_STEP = 0.5                       # 峰值采样步长（秒），只影响采样口径


def log(*a):
    print("[bench]", *a, flush=True)


def load_pkg():
    """纯 Python 组装 pdx_particles 包：parser、simulator，不碰 bpy。"""
    assert "bpy" not in sys.modules
    pkg = types.ModuleType("pdx_particles")
    pkg.__path__ = [os.path.join(REPO, "pdx_particles")]
    sys.modules["pdx_particles"] = pkg

    def load(name):
        path = os.path.join(REPO, "pdx_particles", name + ".py")
        spec = importlib.util.spec_from_file_location("pdx_particles." + name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["pdx_particles." + name] = mod
        spec.loader.exec_module(mod)
        return mod

    return load("parser"), load("simulator")


def est_peak_alive(ssim, t_cap=30.0):
    """O(1) 估算子系统在 [0, t_cap] 的存活粒子峰值（选 top5 用）。"""
    best = 0.0
    t = 0.0
    while t <= t_cap + 1e-9:
        v = ssim.estimate_alive(t)
        if v > best:
            best = v
        t += 1.5
    return best


def classify(parser, lib):
    """全库子系统按力场类型分类，返回统计与纯 vortex 明细。"""
    stats = {"subs_total": 0, "pure_vortex": 0, "mixed": 0, "other": 0,
             "systems_with_pure_vortex": 0, "init_errors": 0}
    pure = []          # [(system_name, sub_index, sub_name, force_types, est_peak)]
    from pdx_particles.simulator import SubsystemSim
    for sys_name in sorted(lib.systems):
        system = lib.systems[sys_name]
        sys_has_pure = False
        for i, sub in enumerate(system.subsystems):
            if sub.hide:
                continue
            stats["subs_total"] += 1
            try:
                sim = SubsystemSim(system, sub, i)
                ftypes = sorted({f.type for f in sim.forces})
            except Exception as err:  # noqa: BLE001
                stats["init_errors"] += 1
                log("  SubsystemSim init failed:", sys_name, sub.name, repr(err))
                continue
            if not ftypes:
                stats["other"] += 1
                continue
            fset = set(ftypes)
            if fset <= {"vortex", "spin"}:
                stats["pure_vortex"] += 1
                sys_has_pure = True
                pure.append({
                    "system": sys_name, "sub_index": i, "sub": sub.name,
                    "force_types": ftypes,
                    "est_peak": round(est_peak_alive(sim), 1),
                })
            elif fset & {"point", "turbulence"}:
                stats["mixed"] += 1
            else:
                stats["other"] += 1
        if sys_has_pure:
            stats["systems_with_pure_vortex"] += 1
    return stats, pure


def run_round(system, t_end, simulator, sample_peaks=False):
    """建 SystemSim 并推进到 t_end，返回 (耗时秒, 峰值表{index: n})。"""
    sim = simulator.SystemSim(system)
    peaks = {}
    t0 = time.perf_counter()
    t = SAMPLE_STEP
    while t < t_end:
        sim.advance(t)
        if sample_peaks:
            for s in sim.subs:
                n = len(s.particles)
                if n > peaks.get(s.index, 0):
                    peaks[s.index] = n
        t += SAMPLE_STEP
    sim.advance(t_end)
    return time.perf_counter() - t0, peaks, sim


def capture_state(sim):
    """深拷贝各子系统 particles，供 A/B 逐位比较。"""
    return {s.index: [list(p) for p in s.particles] for s in sim.subs}


def diff_particles(pa, pb):
    """逐行逐字段 float == 精确比较，返回 (行数A, 行数B, 差异字段数, 最大绝对偏差)。"""
    rows_a, rows_b = len(pa), len(pb)
    n = min(rows_a, rows_b)
    bad = 0
    maxdev = 0.0
    for i in range(n):
        ra, rb = pa[i], pb[i]
        m = min(len(ra), len(rb))
        for j in range(m):
            va, vb = ra[j], rb[j]
            if va != vb:
                bad += 1
                d = abs(va - vb)
                if d > maxdev:
                    maxdev = d
    return rows_a, rows_b, bad, maxdev


def diff_snapshot(sa, sb):
    """snapshot() 输出逐字段比较（tuple 列表）。"""
    da = {name: rows for name, rows in sa}
    db = {name: rows for name, rows in sb}
    bad = 0
    maxdev = 0.0
    if set(da) != set(db):
        bad += 10 ** 9
    for name in da:
        ra, rb = da[name], db.get(name, [])
        n = min(len(ra), len(rb))
        if len(ra) != len(rb):
            bad += abs(len(ra) - len(rb)) * 100
        for i in range(n):
            va, vb = ra[i], rb[i]
            if len(va) != len(vb):
                bad += 1
                continue
            for j in range(len(va)):
                if va[j] != vb[j]:
                    bad += 1
                    try:
                        d = abs(float(va[j]) - float(vb[j]))
                    except Exception:  # noqa: BLE001
                        d = float("inf")
                    if d > maxdev:
                        maxdev = d
    return bad, maxdev


def bench_case(simulator, lib, sys_name, sub_names, rounds=5):
    """对单个系统做 A/B：返回结果 dict。计时单位是整个系统的 advance 序列。

    A 侧显式钉死为 ("turbulence","point")：0.96.0 起 _VEC_FORCE_TYPES 已含
    vortex/spin，若按「当前值」当 A，会变成 A≡B 测不出东西。
    """
    system = lib.systems[sys_name]
    t_end = min(simulator.SystemSim(system).total_duration(), 30.0)
    orig = simulator._VEC_FORCE_TYPES

    # 单轮探测耗时，动态决定轮数（单轮 >20s 时降到 3 轮保预算）
    t_probe, _, _ = run_round(system, t_end, simulator)
    rounds = rounds if t_probe < 20.0 else 3

    ta, peaks, sim_a = [], {}, None
    simulator._VEC_FORCE_TYPES = ("turbulence", "point")   # A 侧固定为不含 vortex
    try:
        for r in range(rounds):
            if time.time() > DEADLINE and ta:
                log("  deadline hit, stop A rounds at", r)
                break
            dt, pk, sim = run_round(system, t_end, simulator, sample_peaks=(r == rounds - 1))
            ta.append(dt)
            peaks = pk
            sim_a = sim
    finally:
        simulator._VEC_FORCE_TYPES = orig
    state_a = capture_state(sim_a)
    snap_a = sim_a.snapshot()

    tb, sim_b = [], None
    try:
        simulator._VEC_FORCE_TYPES = ("turbulence", "point", "vortex", "spin")
        for r in range(rounds):
            if time.time() > DEADLINE and tb:
                log("  deadline hit, stop B rounds at", r)
                break
            dt, _, sim = run_round(system, t_end, simulator)
            tb.append(dt)
            sim_b = sim
    finally:
        simulator._VEC_FORCE_TYPES = orig

    state_b = capture_state(sim_b)
    snap_b = sim_b.snapshot()

    per_sub = []
    bitwise_pass = True
    snap_pass = True
    for s in sim_a.subs:
        ra, rb = state_a[s.index], state_b.get(s.index, [])
        rows_a, rows_b, bad, maxdev = diff_particles(ra, rb)
        ok = bad == 0 and rows_a == rows_b
        if s.name in sub_names:
            per_sub.append({
                "sub": s.name, "rows_a": rows_a, "rows_b": rows_b,
                "diff_fields": bad, "max_abs_dev": maxdev, "bitwise_pass": ok,
            })
            bitwise_pass = bitwise_pass and ok
    sbad, smaxdev = diff_snapshot(snap_a, snap_b)
    snap_pass = sbad == 0

    med_a = statistics.median(ta) * 1000.0 if ta else float("nan")
    med_b = statistics.median(tb) * 1000.0 if tb else float("nan")
    return {
        "system": sys_name,
        "pure_vortex_subs": sub_names,
        "duration_s": t_end,
        "rounds": [len(ta), len(tb)],
        "a_ms_median": round(med_a, 1),
        "b_ms_median": round(med_b, 1),
        "speedup_b_over_a": round(med_a / med_b, 3) if med_b else None,
        "real_peak_particles": {s.name: peaks.get(s.index, len(state_a[s.index]))
                                for s in sim_a.subs if s.name in sub_names},
        "bitwise": {
            "pass": bitwise_pass and snap_pass,
            "per_sub": per_sub,
            "snapshot_diff_fields": sbad,
            "snapshot_max_abs_dev": smaxdev,
        },
        "rounds_a_s": [round(x, 3) for x in ta],
        "rounds_b_s": [round(x, 3) for x in tb],
    }


def main():
    t0 = time.time()
    parser, simulator = load_pkg()
    log("numpy available:", simulator._NP is not None,
        getattr(simulator._NP, "__version__", None))
    if simulator._NP is None:
        log("FATAL: numpy 不可用，向量化路径整体失效，A/B 无意义。"
            "请用 Blender 自带 Python 运行本脚本。")
        return 1

    # ---------- 1) 全库扫描 ----------
    roots = [os.path.join(MOD_ROOT, "stellaris_godie"),
             os.path.join(MOD_ROOT, "hjsbfxr"),
             VANILLA_ROOT]
    lib = parser.ParticleLibrary(roots).scan()
    log("scan done: files={0} systems={1} in {2:.1f}s".format(
        lib.files_scanned, len(lib.systems), time.time() - t0))

    # ---------- 2) 子系统分类 ----------
    stats, pure = classify(parser, lib)
    log("classification:", stats)
    pure_sorted = sorted(pure, key=lambda e: -e["est_peak"])
    ge64 = sum(1 for e in pure if e["est_peak"] >= 64)
    log("pure vortex subs with est_peak>=64: {0}/{1}".format(ge64, len(pure)))

    # ---------- 3) 选必测系统 ----------
    case_sys = []
    ms2_key = None
    for k in sorted(lib.systems):
        if k == "magnetic_storm_2" or k.startswith("magnetic_storm_2"):
            ms2_key = k
            break
    if ms2_key is None:
        log("FATAL: magnetic_storm_2 not found in library")
    else:
        case_sys.append(ms2_key)
    for e in pure_sorted:
        if len(case_sys) >= 6:
            break
        if e["system"] not in case_sys:
            case_sys.append(e["system"])
    log("cases:", case_sys)

    # 每个系统的纯 vortex 子系统名
    pure_by_sys = {}
    for e in pure:
        pure_by_sys.setdefault(e["system"], []).append(e["sub"])

    # ---------- 4) 逐系统 A/B ----------
    cases = []
    for sys_name in case_sys:
        if time.time() > DEADLINE:
            log("deadline hit before case", sys_name)
            break
        log("bench", sys_name, "pure_subs=", pure_by_sys.get(sys_name, []))
        try:
            res = bench_case(simulator, lib, sys_name, pure_by_sys.get(sys_name, []))
        except Exception as err:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            res = {"system": sys_name, "error": repr(err)}
        cases.append(res)
        log("  -> A={0}ms B={1}ms speedup={2} bitwise_pass={3}".format(
            res.get("a_ms_median"), res.get("b_ms_median"),
            res.get("speedup_b_over_a"),
            res.get("bitwise", {}).get("pass")))

    # ---------- 5) 汇总 ----------
    out = {
        "meta": {
            "python": sys.version,
            "numpy": getattr(simulator._NP, "__version__", None),
            "roots": roots,
            "files_scanned": lib.files_scanned,
            "systems_total": len(lib.systems),
            "elapsed_s": round(time.time() - BENCH_START, 1),
            "finished_cleanly": time.time() <= DEADLINE,
            "sample_step_s": SAMPLE_STEP,
            "t_cap_s": 30.0,
        },
        "classification": dict(stats, pure_peak_ge64=ge64,
                               top10_pure_by_est_peak=pure_sorted[:10]),
        "cases": cases,
    }
    with open(OUT_JSON, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    log("JSON written:", OUT_JSON)
    log("total elapsed: {0:.1f}s".format(time.time() - BENCH_START))
    return 0


if __name__ == "__main__":
    code = main()
    sys.exit(code)
