"""mm_v10 — two-regime market maker (monopoly + normal).

Own implementation, synthesizing the published honest (non-leak) techniques
from the top hackathon entries (octavi42 #2, ryanli #1) — credited in the KB.
The entire edge is: EXPLOIT THE EXTREMES. When a competitor side vanishes the
true price is near 0/1 and retail must trade with us at extreme ticks (monopoly
= ~60% of edge); otherwise quote just inside the competitor spread, filtered by
a volatility z-score so the arbitrageur can't pick us off.

Tunables exposed via env for autoresearch sweeps.
"""
from __future__ import annotations
import math, os
from orderbook_pm_challenge.strategy import BaseStrategy
from orderbook_pm_challenge.types import CancelAll, PlaceOrder, Side, StepState

MONO_K   = float(os.environ.get("MONO_K", "85"))    # monopoly inverse-prob size
NORM_K   = float(os.environ.get("NORM_K", "14"))    # normal-regime inverse-prob size
MAXPOS   = float(os.environ.get("MAXPOS", "3000"))
Z_WIDE   = float(os.environ.get("Z_WIDE", "0.4"))   # z threshold, wide spread
Z_TIGHT  = float(os.environ.get("Z_TIGHT", "0.8"))  # z threshold, tight spread
SIG_C    = float(os.environ.get("SIG_C", "39.9"))   # sigma_est constant
TVAR     = float(os.environ.get("TVAR", "1"))       # 1=time-varying aggressiveness on
FLOORB   = float(os.environ.get("FLOORB", "1.0"))   # extra ask boost at p<5% (floor exploit)



def _cap_bid(qty, price_tick, cash):
    res = price_tick / 100.0
    return min(qty, cash / res) if res > 0 else qty

def _cap_ask(qty, price_tick, cash):
    res = (1.0 - price_tick / 100.0)
    return min(qty, cash / res) if res > 0 else qty

class Strategy(BaseStrategy):
    def __init__(self):
        self.max_position = MAXPOS
        self.prev_mid = None
        self.vol_ema = 1.0
        self.vol_alpha = 0.10

    @staticmethod
    def _phi(prob):
        d = abs(prob - 0.5)
        if d > 0.45: return 0.20
        if d > 0.40: return 0.35
        if d > 0.35: return 0.50
        if d > 0.30: return 0.65
        if d > 0.20: return 0.82
        if d > 0.10: return 0.93
        return 1.0

    def _tvar_mult(self, state):
        if not TVAR:
            return 1.0
        frac = 1.0 - state.steps_remaining / 2000.0  # 0 early -> 1 late
        return 0.9 + 0.25 * frac  # 0.9 early, ~1.15 late

    def on_step(self, state: StepState):
        cb = state.competitor_best_bid_ticks
        ca = state.competitor_best_ask_ticks
        actions = [CancelAll()]
        tv = self._tvar_mult(state)

        # ---------- REGIME 1: MONOPOLY (a side vanished -> price near 0/1) ----------
        if cb is None or ca is None:
            net = state.yes_inventory - state.no_inventory
            mp = self.max_position
            if state.steps_remaining < 200:
                mp = max(1.0, self.max_position * state.steps_remaining / 200)
            cash = state.free_cash * 0.9

            if cb is None and ca is not None:      # price near 0 -> buy cheap YES
                prob = max(0.005, (ca - 2) / 100.0)
                base = max(20.0, MONO_K / max(0.005, prob)) * tv
                for tick in range(1, min(6, ca)):
                    frac = 1.0 if tick <= 2 else 0.5
                    sz = min(base * frac, max(0.0, mp - net))
                    sz = _cap_bid(sz, tick, cash * 0.95)
                    if sz >= 0.01:
                        cost = tick / 100.0 * sz
                        actions.append(PlaceOrder(side=Side.BUY, price_ticks=tick, quantity=round(sz, 2)))
                        net += sz; cash -= cost
                at = ca - 1 if ca >= 3 else None
                if at:
                    ss = min(base * 0.35, max(0.0, mp + (state.yes_inventory - state.no_inventory)))
                    ss = _cap_ask(ss, at, cash * 0.95)
                    if ss >= 0.01:
                        actions.append(PlaceOrder(side=Side.SELL, price_ticks=at, quantity=round(ss, 2)))
                        cash -= (1.0 - at/100.0) * ss

            elif ca is None and cb is not None:    # price near 1 -> sell expensive YES
                prob = min(0.995, (cb + 2) / 100.0)
                base = max(20.0, MONO_K / max(0.005, 1.0 - prob)) * tv
                for tick in range(99, max(94, cb), -1):
                    frac = 1.0 if tick >= 98 else 0.5
                    ss = min(base * frac, max(0.0, mp + net))
                    ss = _cap_ask(ss, tick, cash * 0.95)
                    if ss >= 0.01:
                        sc = (1.0 - tick / 100.0) * ss
                        actions.append(PlaceOrder(side=Side.SELL, price_ticks=tick, quantity=round(ss, 2)))
                        net -= ss; cash -= sc
                bt = cb + 1 if cb <= 97 else None
                if bt:
                    bs = min(base * 0.35, max(0.0, mp - (state.yes_inventory - state.no_inventory)))
                    bs = _cap_bid(bs, bt, cash * 0.95)
                    if bs >= 0.01:
                        actions.append(PlaceOrder(side=Side.BUY, price_ticks=bt, quantity=round(bs, 2)))
                        cash -= bt/100.0 * bs
            return actions

        # ---------- REGIME 2: NORMAL (both sides present) ----------
        mid = (cb + ca) / 2.0
        cs = ca - cb
        if self.prev_mid is not None:
            self.vol_ema = self.vol_alpha * abs(mid - self.prev_mid) + (1 - self.vol_alpha) * self.vol_ema
        self.prev_mid = mid

        spread_value = (cs - 2) / 2.0
        if spread_value < 0.5:
            return actions

        prob = max(0.02, min(0.98, mid / 100.0))
        phi = self._phi(prob)
        H = max(1.0, float(state.steps_remaining))
        sigma = max(phi * SIG_C / math.sqrt(H), self.vol_ema)
        z = spread_value / sigma
        if (spread_value >= 3.0 and z < Z_WIDE) or (spread_value < 3.0 and z < Z_TIGHT):
            return actions

        net = state.yes_inventory - state.no_inventory
        mp = self.max_position
        if state.steps_remaining < 200:
            mp = max(1.0, self.max_position * state.steps_remaining / 200)

        size = max(10.0, NORM_K / max(prob, 0.05)) * tv
        skew = min(0.08, 2.8 / max(5.0, size))
        bskew = int(round(net * skew)) if net > 0 else 0
        askew = int(round(abs(net) * skew)) if net < 0 else 0
        bid = max(1, cb + 1 - bskew)
        ask = min(99, ca - 1 + askew)
        if bid >= ask:
            return actions

        cash = state.free_cash * 0.9
        if bid > cb:
            bs = min(size, max(0.0, mp - net))
            bs = _cap_bid(bs, bid, cash * 0.5)
            if bs >= 0.01:
                actions.append(PlaceOrder(side=Side.BUY, price_ticks=bid, quantity=round(bs, 2)))
                cash -= bid/100.0 * bs
        if ask < ca:
            asz = size
            if prob < 0.05:
                asz *= (1.0 + FLOORB)
            asz = min(asz, max(0.0, mp + net))
            asz = _cap_ask(asz, ask, cash)
            if asz >= 0.01:
                actions.append(PlaceOrder(side=Side.SELL, price_ticks=ask, quantity=round(asz, 2)))
        return actions
