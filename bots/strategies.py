"""Auswahlregeln der Agenten. Nutzt nur Daten bis einschließlich 'i' (kein Blick in die Zukunft).

Jede Strategie gibt eine nach Attraktivität sortierte Liste [(ticker, score)] zurück.
Die Engine nimmt daraus die Top-N und lässt bestehende Positionen im Rangpuffer liegen.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _momentum(close: pd.DataFrame, i: int, lookback: int, skip: int) -> pd.Series:
    if i - skip - lookback < 0:
        return pd.Series(dtype=float)
    now = close.iloc[i - skip]
    then = close.iloc[i - skip - lookback]
    return (now / then - 1).dropna()


def _above_sma(close: pd.DataFrame, i: int, n: int) -> pd.Series:
    if i + 1 < n:
        return pd.Series(False, index=close.columns)
    window = close.iloc[i + 1 - n:i + 1]
    return close.iloc[i] > window.mean()


def _cols(close, universe):
    return close[[t for t in universe if t in close.columns]]


def momentum(close, i, universe, cfg):
    c = _cols(close, universe)
    mom = _momentum(c, i, cfg["lookback_days"], cfg["skip_days"])
    if cfg.get("blend_lookbacks"):
        # Mittel aus mehreren Zeitfenstern ist stabiler als ein einzelnes
        parts = [_momentum(c, i, lb, cfg["skip_days"]) for lb in cfg["blend_lookbacks"]]
        mom = pd.concat(parts, axis=1).mean(axis=1).dropna()
    ok = _above_sma(c, i, cfg["trend_sma"])
    mom = mom[(mom > 0) & ok.reindex(mom.index).fillna(False)]
    return [(t, float(s)) for t, s in mom.sort_values(ascending=False).items()]


def sustainable(close, i, universe, cfg):
    c = _cols(close, universe)
    mom = _momentum(c, i, cfg["lookback_days"], cfg["skip_days"])
    if mom.empty or i < cfg["vol_days"] + 1:
        return []
    rets = np.log(c.iloc[i - cfg["vol_days"]:i + 1]).diff().dropna(how="all")
    vol = rets.std().reindex(mom.index)
    ok = _above_sma(c, i, cfg["trend_sma"]).reindex(mom.index).fillna(False)
    df = pd.DataFrame({"mom": mom, "vol": vol})[(mom > 0) & ok].dropna()
    if df.empty:
        return []
    w = cfg.get("momentum_weight", 0.6)
    df["score"] = w * df["mom"].rank(pct=True) + (1 - w) * (-df["vol"]).rank(pct=True)
    return [(t, float(s)) for t, s in df["score"].sort_values(ascending=False).items()]


def dual_momentum(close, i, universe, cfg):
    """ETF-Rotation: die stärksten ETFs mit positivem Trend, sonst sicherer Hafen."""
    c = _cols(close, universe)
    parts = [_momentum(c, i, lb, 0) for lb in cfg.get("blend_lookbacks", [252])]
    mom = pd.concat(parts, axis=1).mean(axis=1).dropna()
    good = mom[mom > 0].sort_values(ascending=False)
    if not good.empty:
        return [(t, float(s)) for t, s in good.items()]
    d = cfg.get("defensive")
    if d and d in close.columns:
        dm = _momentum(close[[d]], i, 126, 0)
        if not dm.empty and dm.iloc[0] > 0:
            return [(d, float(dm.iloc[0]))]
    return []


def trend_etf(close, i, universe, cfg):
    """Ein ETF, gehalten solange er über seinem gleitenden Schnitt liegt."""
    t = universe[0]
    if t not in close.columns or i + 1 < cfg["trend_sma"]:
        return []
    s = close[t]
    sma = float(s.iloc[i + 1 - cfg["trend_sma"]:i + 1].mean())
    px = float(s.iloc[i])
    held = cfg.get("_held", False)
    band = cfg.get("band", 0.0)          # Puffer gegen ständiges Rein und Raus
    if px > sma * (1 + band) or (held and px > sma * (1 - band)):
        return [(t, 1.0)]
    return []


def buyhold(close, i, universe, cfg):
    return [(t, 1.0) for t in universe if t in close.columns and not np.isnan(close[t].iloc[i])]


STRATEGIES = {"momentum": momentum, "sustainable": sustainable, "dual_momentum": dual_momentum,
              "trend_etf": trend_etf, "buyhold": buyhold}
