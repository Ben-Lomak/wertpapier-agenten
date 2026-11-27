"""Auswahlregeln der beiden Agenten. Nutzt nur Daten bis einschließlich 'i' (kein Blick in die Zukunft)."""
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


def momentum(close: pd.DataFrame, i: int, universe: list[str], cfg: dict) -> list[tuple[str, float]]:
    cols = [t for t in universe if t in close.columns]
    c = close[cols]
    mom = _momentum(c, i, cfg["lookback_days"], cfg["skip_days"])
    ok = _above_sma(c, i, cfg["trend_sma"])
    mom = mom[(mom > 0) & ok.reindex(mom.index).fillna(False)]
    top = mom.sort_values(ascending=False).head(cfg["positions"])
    return [(t, float(s)) for t, s in top.items()]


def sustainable(close: pd.DataFrame, i: int, universe: list[str], cfg: dict) -> list[tuple[str, float]]:
    cols = [t for t in universe if t in close.columns]
    c = close[cols]
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
    top = df["score"].sort_values(ascending=False).head(cfg["positions"])
    return [(t, float(s)) for t, s in top.items()]


STRATEGIES = {"momentum": momentum, "sustainable": sustainable}
