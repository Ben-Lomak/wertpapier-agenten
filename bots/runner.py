"""Backtest und tägliche Live-Simulation.

  python -m bots.runner backtest            # historischer Test ab start_date_backtest
  python -m bots.runner live                # tägliche Fortschreibung, Zustand in state/
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


def universes(cfg: dict) -> dict[str, list[str]]:
    return {a: read_universe(ROOT / c["universe"]) for a, c in cfg["agents"].items()}


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["backtest", "live"])
    ap.add_argument("--synthetic", action="store_true", help="künstliche Kurse, ohne Internet")
    a = ap.parse_args()
    (run_backtest if a.mode == "backtest" else run_live)(a.synthetic)


if __name__ == "__main__":
    main()
