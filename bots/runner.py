"""Backtest und tägliche Live-Simulation.

  python -m bots.runner backtest            # historischer Test ab start_date_backtest
  python -m bots.runner live                # tägliche Fortschreibung, Zustand in state/
  python -m bots.runner research            # Varianten aus research.yaml vergleichen
  python -m bots.runner backtest --synthetic  # Test ohne Internet
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yaml

from .data import load_market, read_universe, synthetic_market
from .engine import step
from .portfolio import Portfolio
from .report import write_report

ROOT = Path(__file__).resolve().parent.parent
WARMUP_DAYS = 420   # Kalendertage Vorlauf für 252-Tage-Momentum und 200-Tage-Schnitt


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def resolve_universe(u) -> list[str]:
    return list(u) if isinstance(u, list) else read_universe(ROOT / u)


def universes(cfg: dict) -> dict[str, list[str]]:
    return {a: resolve_universe(c["universe"]) for a, c in cfg["agents"].items()}


def all_tickers(cfg: dict, unis: dict) -> list[str]:
    out = [cfg["benchmark"]]
    for u in unis.values():
        out += u
    return list(dict.fromkeys(out))


def new_portfolios(cfg: dict, start: str) -> list[Portfolio]:
    out = []
    for agent in cfg["agents"]:
        for broker in cfg["brokers"]:
            out.append(Portfolio(name=f"{agent}_{broker}", agent=agent, broker=broker,
                                 cash=cfg["start_capital_eur"], start_capital=cfg["start_capital_eur"],
                                 start_date=start, peak_equity=cfg["start_capital_eur"]))
    return out


def get_market(cfg, unis, start, synthetic):
    tickers = all_tickers(cfg, unis)
    if synthetic:
        return synthetic_market(tickers, start=start)
    return load_market(tickers, start=start)


def run_backtest(synthetic: bool = False):
    cfg = load_config()
    unis = universes(cfg)
    start = cfg["start_date_backtest"]
    data_start = (pd.Timestamp(start) - timedelta(days=WARMUP_DAYS)).strftime("%Y-%m-%d")
    md = get_market(cfg, unis, data_start, synthetic)
    first = int(md.dates.searchsorted(pd.Timestamp(start))) if not synthetic else 300
    ports = new_portfolios(cfg, md.dates[first].strftime("%Y-%m-%d"))
    for i in range(first, len(md.dates)):
        for p in ports:
            step(p, md, i, cfg, unis[p.agent])
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    path = write_report(ports, md, cfg, out / "backtest.html", title="Backtest",
                        summary_path=out / "backtest_summary.md")
    with open(out / "backtest_trades.json", "w", encoding="utf-8") as fh:
        json.dump({p.name: p.trades for p in ports}, fh, indent=1)
    print(f"Backtest fertig: {path}")
    return ports


def run_live(synthetic: bool = False):
    cfg = load_config()
    unis = universes(cfg)
    state = ROOT / "state"
    ports = []
    today = date.today().strftime("%Y-%m-%d")
    for p in new_portfolios(cfg, today):
        loaded = Portfolio.load(state, p.name)
        ports.append(loaded or p)
    data_start = (date.today() - timedelta(days=WARMUP_DAYS + 30)).strftime("%Y-%m-%d")
    md = get_market(cfg, unis, data_start, synthetic)

    for p in ports:
        if p.last_date:
            todo = [i for i, d in enumerate(md.dates) if d.strftime("%Y-%m-%d") > p.last_date]
        else:
            todo = [len(md.dates) - 1]       # neues Depot startet mit dem letzten Handelstag
            p.start_date = md.dates[-1].strftime("%Y-%m-%d")
        for i in todo:
            step(p, md, i, cfg, unis[p.agent])
        p.save(state)

    docs = ROOT / "docs"
    write_report(ports, md, cfg, docs / "index.html", title="Live-Simulation",
                 summary_path=ROOT / "reports" / "live_summary.md", live=True)
    print("Live-Simulation aktualisiert.")
    return ports


def run_research(synthetic: bool = False):
    cfg = load_config()
    with open(ROOT / "research.yaml", encoding="utf-8") as fh:
        rcfg = yaml.safe_load(fh)
    variants = rcfg["variants"]
    unis = {k: resolve_universe(v["universe"]) for k, v in variants.items()}
    tickers = [cfg["benchmark"]] + [t for u in unis.values() for t in u]
    tickers += [v["defensive"] for v in variants.values() if v.get("defensive")]
    start = cfg["start_date_backtest"]
    data_start = (pd.Timestamp(start) - timedelta(days=WARMUP_DAYS)).strftime("%Y-%m-%d")
    md = (synthetic_market(list(dict.fromkeys(tickers)), start=data_start) if synthetic
          else load_market(tickers, start=data_start))
    missing = [t for t in dict.fromkeys(tickers) if t not in md.close.columns]
    first = int(md.dates.searchsorted(pd.Timestamp(start))) if not synthetic else 300
    sd = md.dates[first].strftime("%Y-%m-%d")
    ports = {}
    for k in variants:
        ports[k] = Portfolio(name=k, agent=k, broker="n26", cash=cfg["start_capital_eur"],
                             start_capital=cfg["start_capital_eur"], start_date=sd,
                             peak_equity=cfg["start_capital_eur"])
    for i in range(first, len(md.dates)):
        for k, p in ports.items():
            step(p, md, i, cfg, unis[k], variants[k])

    def cagr(curve, a, b):
        s = curve.loc[a:b]
        if len(s) < 50:
            return None
        yrs = (s.index[-1] - s.index[0]).days / 365.25
        return (s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1

    rows = []
    last = md.close.iloc[-1]
    year = int(md.dates[-1].strftime("%Y"))
    for k, p in ports.items():
        c = pd.Series({pd.Timestamp(d): v for d, v in p.equity_curve})
        dd = float((c / c.cummax() - 1).min())
        at = p.liquidation_value_after_tax(last, cfg["tax"], year, cfg["brokers"]["n26"])
        yrs = (c.index[-1] - c.index[0]).days / 365.25
        rows.append({
            "Variante": variants[k]["label"], "key": k,
            "Endwert": c.iloc[-1], "nach Steuer": at,
            "p.a. nach Steuer": (at / p.start_capital) ** (1 / yrs) - 1,
            "2016-20 p.a.": cagr(c, "2016", "2020"), "2021-26 p.a.": cagr(c, "2021", "2026"),
            "max DD": dd, "Trades": len(p.trades), "Gebühren": p.fees_paid, "Steuern": p.taxes_paid,
            "Ret/DD": ((at / p.start_capital) ** (1 / yrs) - 1) / abs(dd) if dd else None,
        })
    rows.sort(key=lambda r: -r["nach Steuer"])
    f = lambda x: "n/a" if x is None else f"{x * 100:+.1f} %"
    e = lambda x: f"{x:,.0f} €".replace(",", ".")
    out = [f"## Variantenvergleich {sd} bis {md.dates[-1]:%Y-%m-%d} (N26-Kosten, PT-Steuern)", "",
           "| Variante | Endwert | nach Steuer | p.a. n. St. | 2016-20 | 2021-26 | max DD | Ret/DD | Trades | Gebühren | Steuern |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r['Variante']} | {e(r['Endwert'])} | {e(r['nach Steuer'])} | {f(r['p.a. nach Steuer'])} | "
                   f"{f(r['2016-20 p.a.'])} | {f(r['2021-26 p.a.'])} | {f(r['max DD'])} | "
                   f"{'n/a' if r['Ret/DD'] is None else round(r['Ret/DD'], 2)} | "
                   f"{r['Trades']} | {e(r['Gebühren'])} | {e(r['Steuern'])} |")
    if missing:
        out += ["", f"Ohne Kursdaten: {', '.join(missing)}"]
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "research.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))
    return ports


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["backtest", "live", "research"])
    ap.add_argument("--synthetic", action="store_true", help="künstliche Kurse, ohne Internet")
    a = ap.parse_args()
    {"backtest": run_backtest, "live": run_live, "research": run_research}[a.mode](a.synthetic)


if __name__ == "__main__":
    main()
