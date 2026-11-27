"""Simuliertes Depot mit Broker-Kosten, FIFO-Lots und Steuerbuchhaltung."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

from . import tax_pt


@dataclass
class Portfolio:
    name: str
    agent: str
    broker: str
    cash: float
    start_capital: float
    start_date: str
    lots: dict = field(default_factory=dict)          # ticker -> [{qty, cost_per_share, date}]
    pos_peak: dict = field(default_factory=dict)      # ticker -> höchster Schlusskurs seit Kauf
    pending: list = field(default_factory=list)       # Orders für die nächste Eröffnung
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)  # [date, equity]
    realized: list = field(default_factory=list)      # Gewinne des laufenden Jahres
    tax_history: list = field(default_factory=list)
    loss_carry: list = field(default_factory=list)
    peak_equity: float = 0.0
    cooldown_left: int = 0
    stopped_out: list = field(default_factory=list)   # bis zum nächsten Rebalancing gesperrt
    last_date: str | None = None
    month_key: str | None = None
    orders_this_month: int = 0
    fees_paid: float = 0.0
    taxes_paid: float = 0.0
    kill_switch_events: list = field(default_factory=list)
    halted: bool = False                              # Kapital-Boden erreicht, Agent dauerhaft gestoppt

    # ---------------- Persistenz ----------------
    def save(self, folder: str):
        Path(folder).mkdir(parents=True, exist_ok=True)
        with open(Path(folder) / f"{self.name}.json", "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, indent=1)

    @classmethod
    def load(cls, folder: str, name: str):
        p = Path(folder) / f"{name}.json"
        if not p.exists():
            return None
        with open(p, encoding="utf-8") as fh:
            return cls(**json.load(fh))

    # ---------------- Bewertung ----------------
    def qty(self, t: str) -> float:
        return sum(l["qty"] for l in self.lots.get(t, []))

    def holdings(self) -> dict:
        return {t: self.qty(t) for t in self.lots if self.qty(t) > 1e-9}

    def market_value(self, prices) -> float:
        return sum(q * float(prices[t]) for t, q in self.holdings().items())

    def equity(self, prices) -> float:
        return self.cash + self.market_value(prices)

    # ---------------- Kosten ----------------
    def _fees(self, notional: float, ccy: str, bcfg: dict, date: str) -> float:
        mk = date[:7]
        if mk != self.month_key:
            self.month_key, self.orders_this_month = mk, 0
        self.orders_this_month += 1
        fee = 0.0
        if self.orders_this_month > bcfg["free_orders_per_month"]:
            fee += bcfg["fee_per_order"]
            if bcfg["commission_pct"] > 0:
                fee += max(notional * bcfg["commission_pct"], bcfg["min_commission"])
        if ccy != "EUR":
            fee += notional * bcfg["fx_fee_pct"]
        return fee

    @staticmethod
    def _fill_price(price: float, side: str, bcfg: dict) -> float:
        adj = bcfg["spread_pct"] / 2 + bcfg["slippage_pct"]
        return price * (1 + adj) if side == "buy" else price * (1 - adj)

    # ---------------- Ausführung ----------------
    def buy(self, t: str, amount_eur: float, price: float, ccy: str, bcfg: dict, date: str, reason: str):
        amount_eur = min(amount_eur, self.cash)
        if amount_eur <= 1:
            return
        fill = self._fill_price(price, "buy", bcfg)
        fee = self._fees(amount_eur, ccy, bcfg, date)
        invest = amount_eur - fee
        if invest <= 0:
            return
        q = invest / fill
        self.cash -= amount_eur
        self.fees_paid += fee
        self.lots.setdefault(t, []).append(
            {"qty": q, "cost_per_share": amount_eur / q, "date": date})
        self.pos_peak[t] = max(self.pos_peak.get(t, 0.0), price)
        self.trades.append({"date": date, "side": "KAUF", "ticker": t, "qty": round(q, 4),
                            "price": round(fill, 4), "amount": round(amount_eur, 2),
                            "fee": round(fee, 2), "reason": reason})

    def sell(self, t: str, q: float, price: float, ccy: str, bcfg: dict, date: str,
             reason: str, tax_cfg: dict):
        have = self.qty(t)
        q = min(q, have)
        if q <= 1e-9:
            return
        fill = self._fill_price(price, "sell", bcfg)
        gross = q * fill
        fee = self._fees(gross, ccy, bcfg, date)
        net = gross - fee
        self.cash += net
        self.fees_paid += fee

        # FIFO
        remaining, gain_total = q, 0.0
        net_per_share = net / q
        new_lots = []
        for lot in self.lots[t]:
            if remaining <= 1e-12:
                new_lots.append(lot)
                continue
            take = min(lot["qty"], remaining)
            gain = take * (net_per_share - lot["cost_per_share"])
            days = _days_between(lot["date"], date)
            st, lt = tax_pt.split_gain(gain, days, tax_cfg)
            self.realized.append({"date": date, "ticker": t, "gain": gain, "days": days,
                                  "st": st, "lt": lt})
            gain_total += gain
            remaining -= take
            if lot["qty"] - take > 1e-9:
                new_lots.append({**lot, "qty": lot["qty"] - take})
        self.lots[t] = new_lots
        if not new_lots:
            del self.lots[t]
            self.pos_peak.pop(t, None)
        self.trades.append({"date": date, "side": "VERKAUF", "ticker": t, "qty": round(q, 4),
                            "price": round(fill, 4), "amount": round(net, 2),
                            "fee": round(fee, 2), "gain": round(gain_total, 2), "reason": reason})

    # ---------------- Steuern ----------------
    def ytd_tax_estimate(self, tax_cfg: dict, year: int) -> float:
        st = sum(r["st"] for r in self.realized)
        lt = sum(r["lt"] for r in self.realized)
        carry = [dict(c) for c in self.loss_carry]
        tax, _, _ = tax_pt.compute_year_tax(st, lt, carry, year, tax_cfg)
        return tax

    def settle_year(self, year: int, tax_cfg: dict):
        st = sum(r["st"] for r in self.realized)
        lt = sum(r["lt"] for r in self.realized)
        tax, self.loss_carry, details = tax_pt.compute_year_tax(st, lt, self.loss_carry, year, tax_cfg)
        self.cash -= tax
        self.taxes_paid += tax
        self.peak_equity -= tax          # Steuerzahlung ist kein Kursverlust
        details["realized_gain"] = round(sum(r["gain"] for r in self.realized), 2)
        self.tax_history.append(details)
        self.realized = []
        return tax

    def liquidation_value_after_tax(self, prices, tax_cfg: dict, year: int, bcfg: dict) -> float:
        """Was bliebe übrig, wenn heute alles verkauft und die Jahressteuer bezahlt wäre."""
        st = sum(r["st"] for r in self.realized)
        lt = sum(r["lt"] for r in self.realized)
        value = self.cash
        for t, lots in self.lots.items():
            px = self._fill_price(float(prices[t]), "sell", bcfg)
            for lot in lots:
                gross = lot["qty"] * px
                value += gross
                g = gross - lot["qty"] * lot["cost_per_share"]
                s, l = tax_pt.split_gain(g, _days_between(lot["date"], self.last_date or lot["date"]), tax_cfg)
                st, lt = st + s, lt + l
            value -= bcfg["fee_per_order"] + (bcfg["min_commission"] if bcfg["commission_pct"] else 0)
        tax, _, _ = tax_pt.compute_year_tax(st, lt, [dict(c) for c in self.loss_carry], year, tax_cfg)
        return value - tax


def _days_between(a: str, b: str) -> int:
    from datetime import date
    y1, m1, d1 = map(int, a[:10].split("-"))
    y2, m2, d2 = map(int, b[:10].split("-"))
    return (date(y2, m2, d2) - date(y1, m1, d1)).days
