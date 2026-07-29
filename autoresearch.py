"""autoresearch — multi-seed robustness sweep over mm_v10 params (default/leaderboard config).

Spawns _scorer.py as a fresh process per variant (via the venv python directly, no `uv`
overhead) with params set in the env, so each variant's strategy module is imported with the
right constants. Scores a variant as the MEAN edge across seed blocks and reports the WORST
block (robustness): the leaderboard runs 200 randomized sims once, so optimize the multi-seed
average, not one lucky seed.

Usage: python3 autoresearch.py <sims_per_block> <seed1,seed2,...> <mode>
  mode = baseline | coord
"""
from __future__ import annotations
import json, os, subprocess, sys, statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(HERE, ".venv", "bin", "python")
STRAT = "strategies/mm_v10.py"
BASE = dict(MONO_K=85.0, NORM_K=14.0, MAXPOS=3000.0, Z_WIDE=0.4, Z_TIGHT=0.8,
            SIG_C=39.9, TVAR=1.0, FLOORB=1.0)

def score(params, sims, seeds):
    env = dict(os.environ)
    for k, v in params.items():
        env[k] = str(v)
    out = subprocess.run([PY, "_scorer.py", STRAT, str(sims), ",".join(map(str, seeds))],
                         capture_output=True, text=True, env=env, cwd=HERE)
    try:
        d = json.loads(out.stdout.strip().splitlines()[-1])
    except Exception:
        return None, None, 999
    return d["mean"], d["worst"], d["fails"]

def fmt(p):
    return " ".join(f"{k}={p[k]}" for k in p if p[k] != BASE.get(k))

def main():
    sims = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    seeds = [int(x) for x in sys.argv[2].split(",")] if len(sys.argv) > 2 else [0, 5000, 10000, 20000]
    mode = sys.argv[3] if len(sys.argv) > 3 else "baseline"

    bm, bw, bf = score(BASE, sims, seeds)
    print(f"BASE  mean={bm:+.2f} worst={bw:+.2f} fails={bf}  ({sims}x{len(seeds)} seeds {seeds})", flush=True)
    if mode == "baseline":
        return

    best = dict(BASE); best_mean = bm; best_min = bw
    grid = {
        "MONO_K":  [60, 75, 85, 100, 120, 150, 200],
        "NORM_K":  [6, 9, 12, 14, 18, 24, 32],
        "Z_WIDE":  [0.15, 0.25, 0.4, 0.55, 0.7, 0.9],
        "Z_TIGHT": [0.4, 0.6, 0.8, 1.0, 1.3, 1.7],
        "SIG_C":   [20, 28, 34, 39.9, 48, 60],
        "MAXPOS":  [1500, 2200, 3000, 4000, 6000, 9000],
        "FLOORB":  [0.0, 0.5, 1.0, 2.0, 3.5],
    }
    for _pass in range(2):
        for pk, pvals in grid.items():
            for pv in pvals:
                cand = dict(best); cand[pk] = pv
                if cand == best:
                    continue
                m, mn, f = score(cand, sims, seeds)
                if m is None:
                    print(f"  {pk}={pv:<8} FAIL", flush=True); continue
                flag = ""
                if m > best_mean + 0.05 and mn > best_min - 4.0 and f == 0:
                    best = cand; best_mean = m; best_min = mn; flag = "  <== NEW BEST"
                print(f"  {pk}={pv:<8} mean={m:+.2f} worst={mn:+.2f} fails={f}{flag}", flush=True)
        print(f"-- pass {_pass} best: mean={best_mean:+.2f} worst={best_min:+.2f}  {fmt(best)}", flush=True)
    print(f"\nBEST mean={best_mean:+.2f} worst={best_min:+.2f}\n  {json.dumps(best)}", flush=True)

if __name__ == "__main__":
    main()
