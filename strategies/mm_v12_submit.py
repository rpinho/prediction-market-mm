"""mm_v12 — event-driven two-regime market maker (SELF-CONTAINED submission).

Same quoting logic and config as mm_v10 (base, best-generalizing on held-out seeds:
~$48.6 mean / ~$39.9 worst, 0 fails) but only rebuilds quotes when the competitor book
moves, we get filled, or in the endgame wind-down — otherwise leaves orders resting
(returns []). Orders persist across steps, so skipping unchanged steps yields identical
orders while (a) doing heavy work on ~38% of steps instead of 100% and (b) preserving
FIFO queue priority (not cancelling => more retail fills). Constants baked; no env/imports
beyond the challenge API.
"""
from __future__ import annotations
import math
from orderbook_pm_challenge.strategy import BaseStrategy
from orderbook_pm_challenge.types import CancelAll, PlaceOrder, Side, StepState

MONO_K = 85.0
NORM_K = 14.0
MAXPOS = 3000.0
Z_WIDE = 0.4
Z_TIGHT = 0.8
SIG_C = 39.9
FLOORB = 1.0


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
        self.last_cb = -999
        self.last_ca = -999

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

    def on_step(self, state: StepState):
        cb = state.competitor_best_bid_ticks
        ca = state.competitor_best_ask_ticks

        if cb is not None and ca is not None:
            mid = (cb + ca) / 2.0
            if self.prev_mid is not None:
                self.vol_ema = self.vol_alpha * abs(mid - self.prev_mid) + (1 - self.vol_alpha) * self.vol_ema
            self.prev_mid = mid

        filled = state.buy_filled_quantity > 0 or state.sell_filled_quantity > 0
        changed = (cb != self.last_cb) or (ca != self.last_ca)
        endgame = state.steps_remaining < 200
        if not (changed or filled or endgame):
            return []
        self.last_cb = cb
        self.last_ca = ca

        actions = [CancelAll()]

        # ---------- REGIME 1: MONOPOLY ----------
        if cb is None or ca is None:
            net = state.yes_inventory - state.no_inventory
            mp = self.max_position
            if state.steps_remaining < 200:
                mp = max(1.0, self.max_position * state.steps_remaining / 200)
            cash = state.free_cash * 0.9

            if cb is None and ca is not None:
                prob = max(0.005, (ca - 2) / 100.0)
                base = max(20.0, MONO_K / max(0.005, prob))
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
            elif ca is None and cb is not None:
                prob = min(0.995, (cb + 2) / 100.0)
                base = max(20.0, MONO_K / max(0.005, 1.0 - prob))
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
            return actions

        # ---------- REGIME 2: NORMAL ----------
        mid = (cb + ca) / 2.0
        spread_value = (ca - cb - 2) / 2.0
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

        size = max(10.0, NORM_K / max(prob, 0.05))
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
        if ask < ca:
            asz = size
            if prob < 0.05:
                asz *= (1.0 + FLOORB)
            asz = min(asz, max(0.0, mp + net))
            asz = _cap_ask(asz, ask, cash)
            if asz >= 0.01:
                actions.append(PlaceOrder(side=Side.SELL, price_ticks=ask, quantity=round(asz, 2)))
        return actions
