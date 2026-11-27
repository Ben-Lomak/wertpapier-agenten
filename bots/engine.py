"""Tagesablauf eines Agenten. Identisch für Backtest und Live-Simulation.

Pro Handelstag:
  1. Orders vom Vortag zur heutigen Eröffnung ausführen
  2. Jahreswechsel: portugiesische Steuer für das Vorjahr abrechnen
  3. Depot zum Schlusskurs bewerten
  4. Risikomanager: Kill-Switch, Trailing-Stops, Marktfilter
  5. An Rebalancing-Tagen: Strategie wählt Titel, Orders für morgen erzeugen
"""
from __future__ import annotations

import pandas as pd

from .data import MarketData
from .portfolio import Portfolio
from .strategies import STRATEGIES


def is_rebalance_day(dates: pd.DatetimeIndex, i: int, rhythm: str) -> bool:
    if rhythm == "daily" or i == 0:
        return True
    d, prev = dates[i], dates[i - 1]
    if rhythm == "weekly":
        return d.isocalendar()[1] != prev.isocalendar()[1]
    if rhythm == "monthly":
        return d.month != prev.month
    raise ValueError(f"Unbekannter Rhythmus: {rhythm}")


def step(p: Portfolio, md: MarketData, i: int, cfg: dict, universe: list[str]):
    dates = md.dates
    date = dates[i]
    ds = date.strftime("%Y-%m-%d")
    acfg = cfg["agents"][p.agent]
    bcfg = cfg["brokers"][p.broker]
    rcfg = cfg["risk"]
    tcfg = cfg["tax"]
    ccys = md.currencies()
    opens, closes, traded = md.open.iloc[i], md.close.iloc[i], md.traded.iloc[i]

    # 2. Jahreswechsel
    if p.last_date and p.last_date[:4] != ds[:4]:
        p.settle_year(int(p.last_date[:4]), tcfg)

    # 1. Ausführung zur Eröffnung, Verkäufe zuerst
    still_pending = []
    for o in sorted(p.pending, key=lambda o: o["side"] != "sell"):
        t = o["ticker"]
        if t not in traded.index or not bool(traded[t]):
            still_pending.append(o)
            continue
        px = float(opens[t])
        if o["side"] == "sell":
            q = p.qty(t) if o.get("all") else o["qty"]
            p.sell(t, q, px, ccys[t], bcfg, ds, o["reason"], tcfg)
        else:
            amount = o["amount"]
            if o.get("weight") is not None:
                eq = p.equity(md.open.iloc[i].fillna(md.close.iloc[i]))
                amount = max(0.0, o["weight"] * eq - p.qty(t) * px)
            amount = min(amount, p.cash - 5.0)
            if amount >= rcfg["min_order_eur"] * 0.5:
                p.buy(t, amount, px, ccys[t], bcfg, ds, o["reason"])
    p.pending = still_pending

    # 3. Bewertung
    prices = closes
    eq = p.equity(prices)
    for t in p.holdings():
        p.pos_peak[t] = max(p.pos_peak.get(t, 0.0), float(prices[t]))
    p.peak_equity = max(p.peak_equity, eq)
    p.last_date = ds
    p.equity_curve.append([ds, round(eq, 2)])

    def queue_sell(t, reason):
        if not any(o["ticker"] == t and o["side"] == "sell" for o in p.pending):
            p.pending.append({"side": "sell", "ticker": t, "all": True, "reason": reason})

    # 4a. Kapital-Boden: endgültiger Stopp
    if p.halted:
        return
    floor = p.start_capital * (1 - rcfg["capital_floor_pct"])
    if eq < floor:
        p.pending = []
        for t in p.holdings():
            queue_sell(t, f"Kapital-Boden {floor:,.0f} € unterschritten: Agent gestoppt")
        p.halted = True
        p.kill_switch_events.append({"date": ds, "equity": round(eq, 2), "type": "floor"})
        return

    # 4b. Kill-Switch
    if p.cooldown_left > 0:
        p.cooldown_left -= 1
        if p.cooldown_left == 0:
            p.peak_equity = eq        # Neustart mit aktuellem Stand als Basis
        return
    if eq < p.peak_equity * (1 - rcfg["max_drawdown_pct"]):
        p.pending = [o for o in p.pending if o["side"] == "sell"]
        for t in p.holdings():
            queue_sell(t, f"Kill-Switch: Depot {eq / p.peak_equity - 1:.1%} unter Höchststand")
        p.cooldown_left = rcfg["cooldown_days"]
        p.kill_switch_events.append({"date": ds, "equity": round(eq, 2)})
        return

    # 4c. Trailing-Stops
    for t in p.holdings():
        if float(prices[t]) < p.pos_peak[t] * (1 - rcfg["trailing_stop_pct"]):
            queue_sell(t, f"Trailing-Stop {rcfg['trailing_stop_pct']:.0%}")
            if t not in p.stopped_out:
                p.stopped_out.append(t)

    # Steuer-Nachzahlung, falls Cash negativ: anteilig verkaufen
    if p.cash < 0 and p.holdings() and not any(o["reason"] == "Steuerzahlung" for o in p.pending):
        mv = p.market_value(prices)
        for t, q in p.holdings().items():
            share = min(1.0, (-p.cash * 1.03) / mv)
            p.pending.append({"side": "sell", "ticker": t, "qty": q * share,
                              "reason": "Steuerzahlung"})

    if len(p.equity_curve) > 1 and not is_rebalance_day(dates, i, acfg["rebalance"]):
        return

    # 4d. Marktfilter
    bench = cfg["benchmark"]
    n = rcfg["market_filter_sma"]
    if bench in md.close.columns and i + 1 >= n:
        b = md.close[bench]
        if float(b.iloc[i]) < float(b.iloc[i + 1 - n:i + 1].mean()):
            for t in p.holdings():
                queue_sell(t, f"Marktfilter: Weltindex unter {n}-Tage-Schnitt")
            p.pending = [o for o in p.pending if o["side"] == "sell"]
            return

    # 5. Strategie
    blocked = set(p.stopped_out) | {o["ticker"] for o in p.pending if o["side"] == "sell" and o.get("all")}
    p.stopped_out = []
    picks = [(t, s) for t, s in STRATEGIES[acfg["strategy"]](md.close, i, universe, acfg)
             if t not in blocked]
    targets = {t for t, _ in picks}
    weight = min(rcfg["max_position_pct"], (1 - rcfg["cash_buffer_pct"]) / max(acfg["positions"], 1))
    held = p.holdings()
    for t in held:
        if t not in targets:
            queue_sell(t, "Nicht mehr unter den Top-Titeln")
    for t, score in picks:
        cur = held.get(t, 0.0) * float(prices[t]) / eq if eq > 0 else 0
        if t in held:
            diff = weight - cur
            if diff > rcfg["drift_threshold"]:
                p.pending.append({"side": "buy", "ticker": t, "amount": 0, "weight": weight,
                                  "reason": f"Aufstocken auf {weight:.0%}"})
            elif diff < -rcfg["drift_threshold"]:
                q = held[t] * (-diff / cur)
                p.pending.append({"side": "sell", "ticker": t, "qty": q,
                                  "reason": f"Reduzieren auf {weight:.0%}"})
        elif weight * eq >= rcfg["min_order_eur"]:
            p.pending.append({"side": "buy", "ticker": t, "amount": 0, "weight": weight,
                              "reason": f"Neu: Score {score:.2f}"})
