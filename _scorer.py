"""Fresh-process scorer: reads mm_v10 params from os.environ (set by the parent),
runs run_batch over given seed blocks, prints 'mean worst fails' as JSON. One process
per variant guarantees the strategy module is imported with the current env."""
import sys, json, statistics as st
from orderbook_pm_challenge import run_batch

def main():
    strat = sys.argv[1]
    sims = int(sys.argv[2])
    seeds = [int(x) for x in sys.argv[3].split(",")]
    blocks = []; fails = 0
    for s in seeds:
        r = run_batch(strategy_path=strat, n_simulations=sims, seed_start=s, workers=4)
        ok = [x for x in r.simulation_results if not x.failed]
        fails += len(r.simulation_results) - len(ok)
        if not ok:
            print(json.dumps({"mean": None, "worst": None, "fails": 999})); return
        blocks.append(st.mean([x.total_edge for x in ok]))
    print(json.dumps({"mean": st.mean(blocks), "worst": min(blocks), "fails": fails,
                      "blocks": [round(b, 2) for b in blocks]}))

if __name__ == "__main__":
    main()
