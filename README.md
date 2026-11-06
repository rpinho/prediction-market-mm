# Orderbook Prediction Market — a two-regime market maker

A passive market-making strategy for [Dan Robinson's Orderbook Prediction Market Challenge](https://github.com/danrobinson/prediction-market-challenge), plus the small autoresearch harness used to tune and robustness-check it.

One clean submission scored **+51.51 mean edge over 200 simulations**, rank 26 on the
live top-100 leaderboard on July 28, 2026, running the base configuration rather than a
seed-tuned one. That rank is a snapshot of the board on one day, not its current state.
Some entries above it exploited the simulation engine. The April 2026 hackathon's honest
final ranking, rescored on fresh seeds, topped out at 42.32. That is a different protocol
from the live board, so the two numbers do not compare.

## The game

One shared FIFO order book for a binary YES contract (ticks 1–99 = probability %). You're a **passive-only** market maker (place/cancel limit orders each step; you can't take). The book has a latent random-walk fair value, an **informed arbitrageur** that knows the true post-step price and picks off any wrong-side quote, **uninformed retail**, and a **static competitor ladder**. Score = **mean total edge** across randomized simulations (`edge = qty × (true_p − price)` on buys; the mirror on sells). Final inventory is irrelevant — only where each fill lands versus fair value matters.

The core tension: to earn retail you must quote tight, but the best estimate of the next fair value is the current mid, and the price is a martingale — so a tight symmetric quote is regularly on the wrong side by the time the step resolves, and the arb takes it. Most naive strategies are deeply negative.

## The edge: exploit the extremes, then quote lean

**Regime 1 — monopoly.** When a competitor side vanishes (best bid or best ask is `None`), the true price is near 0 or 1 and retail *must* trade with us at extreme ticks. Extreme-tick quotes are cheap to collateralize, so we quote a ladder with large inverse-probability size at ticks 1–5 (or 95–99). This is where the majority of the edge comes from.

**Regime 2 — normal.** When both sides are quoted, we quote just inside the competitor spread for FIFO priority, gated by a **volatility z-score** so we only quote when the excess spread is large relative to how far the mid can move (otherwise the arbitrageur picks us off). Size scales inversely with probability; inventory skew widens the heavy side.

**The event-driven insight (`mm_v12`).** Orders **persist across steps**, so cancelling and rebuilding every step is both wasteful and throws away FIFO queue priority. `mm_v12` only rebuilds quotes when the competitor book moves, we get filled, or in the endgame — otherwise it returns `[]` and leaves its orders resting. Because the quotes are a pure function of the observable state, skipping unchanged steps yields *identical* orders while doing the heavy work on a fraction of the steps. That both **preserves FIFO priority** (more retail fills) and keeps the strategy well under the evaluator's wall-clock budget.

## The method is the point: autoresearch + multi-seed robustness

The strategy itself is a synthesis of published techniques; the durable skill is the **search**. `autoresearch.py` sweeps parameter variants, each scored by `_scorer.py` in a fresh process (so env-var overrides actually take effect), and ranks by the **mean edge across multiple seed blocks** while reporting the **worst block**. The leaderboard runs one batch of randomized sims, so the goal is the multi-seed *worst case*, not a single lucky seed.

This caught the trap that matters: an axis-aligned sweep found configs scoring **+61** on the tuning seeds, but on **held-out seeds those gains vanished** and the base configuration generalized better with zero failed sims. That's textbook overfitting — the discipline of optimizing the held-out worst case is what separates a real result from a lucky one.

## Files

| File | What |
|---|---|
| `strategies/mm_v12_submit.py` | The submission — self-contained, event-driven, timeout-hardened, base config. |
| `strategies/mm_v10.py` | Env-tunable version (same logic) for autoresearch sweeps. |
| `autoresearch.py` | Coordinate-ascent parameter sweep, multi-seed, reports mean + worst block. |
| `_scorer.py` | Fresh-process scorer (one process per variant so env params apply). |

## Running it

Clone [the challenge repo](https://github.com/danrobinson/prediction-market-challenge), drop these files in, and:

```bash
uv run orderbook-pm run strategies/mm_v12_submit.py --simulations 200 --workers 4
# sweep:
python3 autoresearch.py 100 0,5000,10000,20000 coord
```

## Credit

The two-regime structure and the extreme-exploitation edge synthesize the published, non-leak techniques of two strong hackathon entries — [octavi42](https://github.com/octavi42/prediction-market-maker) and [ryanli](https://github.com/ryanli-me/paradigm-pm-challenge). ryanli's "autoresearch" writeup (generate + evaluate 1,000+ variants — the Bitter Lesson applied to a contest) is the method this repo leans into.

## License

MIT — see [LICENSE](LICENSE).
