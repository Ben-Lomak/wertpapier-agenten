"""Was-wäre-wenn: Einmalanlage in einzelne Titel, gerechnet in EUR.

python -m tools.waswaere 2012-01-03 NVDA=5000 TSLA=5000
"""
import sys

import pandas as pd
import yfinance as yf

from bots.data import currency_of

start = sys.argv[1]
legs = [a.split("=") for a in sys.argv[2:]]
out = [f"## Was wäre wenn: Anlage am {start}", "",
       "| Titel | Einsatz | Kurs Start (EUR) | Kurs heute (EUR) | Wert heute | Faktor | p.a. | schlimmster Einbruch |",
       "|---|---|---|---|---|---|---|---|"]
total_in = total_now = 0
curves = []
for t, amt in legs:
    amt = float(amt)
    px = yf.download(t, start=start, auto_adjust=True, progress=False)["Close"].squeeze().dropna()
    ccy = currency_of(t)
    if ccy != "EUR":
        fx = yf.download(f"EUR{'GBP' if ccy == 'GBp' else ccy}=X", start=start, progress=False)["Close"].squeeze()
        fx = fx.reindex(px.index).ffill().bfill()
        px = px / fx / (100 if ccy == "GBp" else 1)
    val = amt * px / px.iloc[0]
    curves.append(val.rename(t))
    yrs = (px.index[-1] - px.index[0]).days / 365.25
    dd = float((val / val.cummax() - 1).min())
    out.append(f"| {t} | {amt:,.0f} € | {px.iloc[0]:.2f} | {px.iloc[-1]:.2f} | {val.iloc[-1]:,.0f} € | "
               f"{val.iloc[-1] / amt:,.0f}x | {(val.iloc[-1] / amt) ** (1 / yrs) - 1:+.1%} | {dd:.0%} |")
    total_in += amt
    total_now += val.iloc[-1]
tot = pd.concat(curves, axis=1).ffill().sum(axis=1)
out.append(f"| **Summe** | {total_in:,.0f} € | | | **{total_now:,.0f} €** | {total_now / total_in:,.0f}x | | "
           f"{float((tot / tot.cummax() - 1).min()):.0%} |")
yearly = tot.groupby(tot.index.year).last()
out += ["", "Depotwert jeweils zum Jahresende:", "", "| Jahr | Wert |", "|---|---|"]
out += [f"| {y} | {v:,.0f} € |" for y, v in yearly.items()]
out += ["", f"Kursstand: {tot.index[-1]:%d.%m.%Y}. Splits und Dividenden berücksichtigt, vor Kosten und Steuern."]
import re
de = lambda line: re.sub(r"[-+]?\d[\d,]*\.?\d*", lambda m: m.group(0).replace(",", "X").replace(".", ",").replace("X", "."), line) if line.startswith("|") else line
open("reports/waswaere.md", "w", encoding="utf-8").write("\n".join(de(l) for l in out) + "\n")
print("\n".join(out))
