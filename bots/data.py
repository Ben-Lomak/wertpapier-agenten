"""Kursdaten laden und in EUR umrechnen.

Liefert zwei DataFrames (Index = Datum, Spalten = Ticker):
  open_eur, close_eur  -> Kurse in EUR
  traded               -> True, wenn der Titel an diesem Tag wirklich gehandelt wurde
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SUFFIX_CCY = {
    ".DE": "EUR", ".PA": "EUR", ".AS": "EUR", ".MI": "EUR", ".MC": "EUR",
    ".BR": "EUR", ".HE": "EUR", ".LS": "EUR", ".VI": "EUR", ".IR": "EUR",
    ".L": "GBp", ".SW": "CHF", ".ST": "SEK", ".CO": "DKK", ".OL": "NOK",
    ".T": "JPY", ".HK": "HKD", ".AX": "AUD", ".TO": "CAD",
}


def currency_of(ticker: str) -> str:
    for suffix, ccy in SUFFIX_CCY.items():
        if ticker.endswith(suffix):
            return ccy
    return "USD"


def read_universe(path: str) -> list[str]:
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#")[0].strip()
            if line:
                out.append(line)
    return list(dict.fromkeys(out))


class MarketData:
    def __init__(self, open_eur: pd.DataFrame, close_eur: pd.DataFrame, traded: pd.DataFrame):
        self.open = open_eur
        self.close = close_eur
        self.traded = traded
        self.dates = close_eur.index

    def currencies(self) -> dict[str, str]:
        return {t: currency_of(t) for t in self.close.columns}


def _download(tickers: list[str], start: str, end: str | None):
    import yfinance as yf

    raw = yf.download(tickers, start=start, end=end, auto_adjust=False,
                      progress=False, group_by="column", threads=True)
    if raw.empty:
        raise RuntimeError("Keine Kursdaten erhalten. Internetverbindung oder Ticker prüfen.")
    o, c = raw["Open"], raw["Close"]
    if isinstance(o, pd.Series):
        o, c = o.to_frame(tickers[0]), c.to_frame(tickers[0])
    return o, c


def load_market(tickers: list[str], start: str, end: str | None = None) -> MarketData:
    tickers = list(dict.fromkeys(tickers))
    o, c = _download(tickers, start, end)

    ccys = sorted({currency_of(t) for t in tickers} - {"EUR"})
    fx_tickers = {ccy: f"EUR{'GBP' if ccy == 'GBp' else ccy}=X" for ccy in ccys}
    fx = None
    if fx_tickers:
        _, fx = _download(sorted(set(fx_tickers.values())), start, end)

    idx = c.index.union(fx.index) if fx is not None else c.index
    idx = idx[idx.dayofweek < 5]
    o, c = o.reindex(idx), c.reindex(idx)
    traded = c.notna() & o.notna()
    if fx is not None:
        fx = fx.reindex(idx).ffill().bfill()

    open_eur, close_eur = o.copy(), c.copy()
    for t in tickers:
        if t not in c.columns:
            continue
        ccy = currency_of(t)
        if ccy == "EUR":
            continue
        rate = fx[fx_tickers[ccy]]           # Fremdwährung pro 1 EUR
        div = rate * (100 if ccy == "GBp" else 1)
        open_eur[t] = o[t] / div
        close_eur[t] = c[t] / div

    close_eur = close_eur.ffill()
    open_eur = open_eur.where(traded).fillna(close_eur)
    # Titel ohne jegliche Daten entfernen
    keep = close_eur.columns[close_eur.notna().any()]
    return MarketData(open_eur[keep], close_eur[keep], traded[keep])


def synthetic_market(tickers: list[str], start: str = "2016-01-01", days: int = 2600,
                     seed: int = 7) -> MarketData:
    """Künstliche Kurse für Tests ohne Internet."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start=start, periods=days)
    n = len(tickers)
    drift = rng.normal(0.08, 0.12, n) / 252
    vol = rng.uniform(0.15, 0.45, n) / np.sqrt(252)
    market = rng.normal(0.0003, 0.011, days)
    # zwei Crash-Phasen einbauen
    market[1050:1080] -= 0.012
    market[1650:1800] -= 0.003
    rets = drift + market[:, None] * rng.uniform(0.6, 1.4, n) + rng.normal(0, 1, (days, n)) * vol
    close = 100 * np.exp(np.cumsum(rets, axis=0))
    gap = rng.normal(0, 0.004, (days, n))
    openp = np.vstack([close[:1], close[:-1]]) * np.exp(gap)
    c = pd.DataFrame(close, index=idx, columns=tickers)
    o = pd.DataFrame(openp, index=idx, columns=tickers)
    traded = pd.DataFrame(True, index=idx, columns=tickers)
    return MarketData(o, c, traded)
