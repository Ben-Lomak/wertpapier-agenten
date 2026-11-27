"""Erzeugt das Dashboard (HTML) und eine Markdown-Zusammenfassung."""
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

SERIES = ["--series-1", "--series-2", "--series-3", "--series-4", "--series-5", "--series-6"]


def _metrics(p, md, cfg):
    curve = pd.Series({pd.Timestamp(d): v for d, v in p.equity_curve})
    if curve.empty:
        return {}
    last_prices = md.close.loc[:pd.Timestamp(p.last_date)].iloc[-1]
    year = int(p.last_date[:4])
    bcfg, tcfg = cfg["brokers"][p.broker], cfg["tax"]
    peak = curve.cummax()
    dd = float((curve / peak - 1).min())
    years = max((curve.index[-1] - curve.index[0]).days / 365.25, 1 / 365)
    total = curve.iloc[-1] / p.start_capital - 1
    cagr = (curve.iloc[-1] / p.start_capital) ** (1 / years) - 1 if years >= 1 else None
    after_tax = p.liquidation_value_after_tax(last_prices, tcfg, year, bcfg)
    return {
        "value": curve.iloc[-1], "total": total, "cagr": cagr, "max_dd": dd,
        "after_tax": after_tax, "fees": p.fees_paid, "taxes": p.taxes_paid,
        "ytd_tax": p.ytd_tax_estimate(tcfg, year), "trades": len(p.trades),
        "positions": len(p.holdings()), "cash": p.cash,
        "kill": len(p.kill_switch_events), "cooldown": p.cooldown_left,
    }


def _bench_curve(md, cfg, start, end, capital):
    b = cfg["benchmark"]
    if b not in md.close.columns:
        return []
    s = md.close[b].loc[pd.Timestamp(start):pd.Timestamp(end)].dropna()
    if s.empty:
        return []
    s = s / s.iloc[0] * capital
    return [[d.strftime("%Y-%m-%d"), round(float(v), 2)] for d, v in s.items()]


def _eur(x):
    return f"{x:,.0f} €".replace(",", ".")


def _pct(x):
    return "n/a" if x is None else f"{x * 100:+.1f} %".replace(".", ",")


def write_report(ports, md, cfg, path, title, summary_path=None, live=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows, series = [], []
    for k, p in enumerate(ports):
        m = _metrics(p, md, cfg)
        if not m:
            continue
        label = f"{cfg['agents'][p.agent]['label']} · {cfg['brokers'][p.broker]['label']}"
        rows.append((label, p, m, SERIES[k % len(SERIES)]))
        series.append({"name": label, "color": SERIES[k % len(SERIES)], "data": p.equity_curve})
    if ports and ports[0].equity_curve:
        bc = _bench_curve(md, cfg, ports[0].equity_curve[0][0], ports[0].equity_curve[-1][0],
                          cfg["start_capital_eur"])
        if bc:
            series.append({"name": "MSCI World (Vergleich, ohne Kosten/Steuern)",
                           "color": "--bench", "data": bc, "dashed": True})

    # Kennzahlen-Tabelle
    trs = []
    for label, p, m, col in rows:
        status = "Pause nach Kill-Switch" if m["cooldown"] else ("investiert" if m["positions"] else "Cash")
        trs.append(f"""<tr><td><span class="sw" style="background:var({col})"></span>{html.escape(label)}</td>
<td>{_eur(m['value'])}</td><td>{_pct(m['total'])}</td><td>{_pct(m['cagr'])}</td>
<td>{_pct(m['max_dd'])}</td><td>{_eur(m['after_tax'])}</td><td>{_eur(m['fees'])}</td>
<td>{_eur(m['taxes'])}</td><td>{m['trades']}</td><td>{m['positions']} · {status}</td></tr>""")

    # Positionen und letzte Trades
    blocks = []
    for label, p, m, col in rows:
        prices = md.close.loc[:pd.Timestamp(p.last_date)].iloc[-1]
        pos = []
        for t, q in sorted(p.holdings().items()):
            cost = sum(l["qty"] * l["cost_per_share"] for l in p.lots[t])
            val = q * float(prices[t])
            pos.append(f"<tr><td>{t}</td><td>{_eur(val)}</td><td>{_pct(val / cost - 1)}</td>"
                       f"<td>{p.lots[t][0]['date']}</td></tr>")
        tr = []
        for t in reversed(p.trades[-12:]):
            g = f"{t['gain']:+.0f} €" if "gain" in t else ""
            tr.append(f"<tr><td>{t['date']}</td><td>{t['side']}</td><td>{t['ticker']}</td>"
                      f"<td>{_eur(t['amount'])}</td><td>{t['fee']:.2f} €</td><td>{g}</td>"
                      f"<td>{html.escape(t['reason'])}</td></tr>")
        taxes = "".join(f"<tr><td>{h['year']}</td><td>{_eur(h['realized_gain'])}</td>"
                        f"<td>{_eur(h['taxable_short'])}</td><td>{_eur(h['taxable_long'])}</td>"
                        f"<td>{_eur(h['tax'])}</td></tr>" for h in p.tax_history)
        blocks.append(f"""<details><summary><span class="sw" style="background:var({col})"></span>{html.escape(label)}</summary>
<h4>Positionen</h4><table><tr><th>Titel</th><th>Wert</th><th>G/V</th><th>seit</th></tr>{''.join(pos) or '<tr><td colspan=4>keine</td></tr>'}</table>
<h4>Letzte Trades</h4><table><tr><th>Datum</th><th>Art</th><th>Titel</th><th>Betrag</th><th>Gebühr</th><th>Gewinn</th><th>Grund</th></tr>{''.join(tr) or '<tr><td colspan=7>noch keine</td></tr>'}</table>
<h4>Steuern Portugal</h4><table><tr><th>Jahr</th><th>Realisiert</th><th>kurzfristig stpfl.</th><th>langfristig stpfl.</th><th>Steuer</th></tr>{taxes or '<tr><td colspan=5>noch kein abgeschlossenes Jahr</td></tr>'}</table>
<p class="note">Steuer laufendes Jahr (geschätzt): {_eur(m['ytd_tax'])} · Kill-Switch ausgelöst: {m['kill']}×</p>
</details>""")

    note = ("Simulation mit virtuellem Geld. Kosten nach Broker-Preisliste, Spread und Slippage geschätzt. "
            "Steuer nach vereinfachtem PT-Modell. Keine Anlageberatung.")
    if not live:
        note += (" Backtest-Hinweis: Das Universum besteht aus heutigen Titeln. Firmen, die in der Vergangenheit "
                 "abgestürzt oder verschwunden sind, fehlen. Ergebnisse sind daher zu optimistisch.")

    doc = TEMPLATE.replace("{{TITLE}}", title)\
        .replace("{{UPDATED}}", datetime.now().strftime("%d.%m.%Y %H:%M"))\
        .replace("{{ROWS}}", "".join(trs))\
        .replace("{{BLOCKS}}", "".join(blocks))\
        .replace("{{NOTE}}", note)\
        .replace("{{DATA}}", json.dumps(series))
    path.write_text(doc, encoding="utf-8")

    if summary_path:
        lines = [f"## {title} · Stand {ports[0].last_date if ports else ''}", "",
                 "| Depot | Wert | Rendite | max. Drawdown | nach Steuer (Liquidation) | Gebühren | Steuern | Trades |",
                 "|---|---|---|---|---|---|---|---|"]
        for label, p, m, _ in rows:
            lines.append(f"| {label} | {_eur(m['value'])} | {_pct(m['total'])} | {_pct(m['max_dd'])} | "
                         f"{_eur(m['after_tax'])} | {_eur(m['fees'])} | {_eur(m['taxes'])} | {m['trades']} |")
        Path(summary_path).parent.mkdir(parents=True, exist_ok=True)
        Path(summary_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


TEMPLATE = r"""<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wertpapier-Agenten · {{TITLE}}</title>
<style>
.viz-root{color-scheme:light;--surface-1:#fff;--surface-2:#f6f6f4;--text-primary:#191919;--text-secondary:#666;
--grid:#e6e6e6;--series-1:#257de0;--series-2:#f47230;--series-3:#00b894;--series-4:#f2a600;--series-5:#ee7fac;--series-6:#008a17;--bench:#8a8a8a}
@media (prefers-color-scheme:dark){.viz-root{color-scheme:dark;--surface-1:#191919;--surface-2:#222;--text-primary:#efefef;
--text-secondary:#9c9c9c;--grid:#333;--series-1:#338ced;--series-2:#e76624;--series-3:#00a888;--series-4:#d28e05;--series-5:#e24d8b;--series-6:#0aac23;--bench:#8a8a8a}}
*{box-sizing:border-box}body{margin:0}
.viz-root{background:var(--surface-1);color:var(--text-primary);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Helvetica Neue",Arial,sans-serif;
min-height:100vh;padding:40px 24px}
main{max-width:1120px;margin:0 auto}
h1{font-size:28px;font-weight:600;margin:0 0 4px;letter-spacing:-.01em}
.meta{color:var(--text-secondary);margin:0 0 32px}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--text-secondary);font-weight:600;margin:40px 0 12px}
h4{margin:20px 0 6px;font-size:14px}
.chart{position:relative;background:var(--surface-1)}
svg{display:block;width:100%;height:auto;overflow:visible}
.legend{display:flex;flex-wrap:wrap;gap:8px 20px;margin:0 0 12px;font-size:13px;color:var(--text-secondary)}
.legend span{display:inline-flex;align-items:center;gap:6px}
.sw{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:8px;vertical-align:baseline}
.tip{position:absolute;pointer-events:none;background:var(--surface-1);border:1px solid var(--grid);border-radius:8px;
padding:8px 10px;font-size:12px;box-shadow:0 4px 16px rgba(0,0,0,.12);display:none;white-space:nowrap}
.tip b{display:block;margin-bottom:4px}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
th{text-align:left;color:var(--text-secondary);font-weight:500;border-bottom:1px solid var(--grid);padding:6px 8px}
td{border-bottom:1px solid var(--grid);padding:7px 8px}
.wrap{overflow-x:auto}
details{background:var(--surface-2);border-radius:10px;padding:14px 18px;margin:10px 0}
summary{cursor:pointer;font-weight:600}
.note{color:var(--text-secondary);font-size:12px}
</style></head>
<body><div class="viz-root"><main>
<h1>Wertpapier-Agenten · {{TITLE}}</h1>
<p class="meta">Aktualisiert {{UPDATED}} · Startkapital je Depot 10.000 € · Steuerwohnsitz Portugal</p>

<h2>Depotwert in EUR</h2>
<div class="legend" id="legend"></div>
<div class="chart" id="chart"><div class="tip" id="tip"></div></div>

<h2>Kennzahlen</h2>
<div class="wrap"><table>
<tr><th>Depot</th><th>Wert</th><th>Rendite</th><th>p.a.</th><th>max. Drawdown</th><th>nach Steuer*</th><th>Gebühren</th><th>Steuern bezahlt</th><th>Trades</th><th>Status</th></tr>
{{ROWS}}
</table></div>
<p class="note">* Wert, wenn heute alles verkauft und die Jahressteuer abgezogen wäre.</p>

<h2>Details je Depot</h2>
{{BLOCKS}}
<p class="note" style="margin-top:32px">{{NOTE}}</p>
</main></div>
<script>
const S = {{DATA}};
const root = document.querySelector('.viz-root');
const css = v => getComputedStyle(root).getPropertyValue(v).trim();
const W = 1120, H = 380, M = {l: 64, r: 16, t: 10, b: 28};
const all = S.flatMap(s => s.data);
if (all.length > 1) {
  const dates = [...new Set(all.map(d => d[0]))].sort();
  const t0 = Date.parse(dates[0]), t1 = Date.parse(dates[dates.length - 1]) || t0 + 1;
  let lo = Math.min(...all.map(d => d[1])), hi = Math.max(...all.map(d => d[1]));
  const pad = (hi - lo) * 0.08 || 100; lo -= pad; hi += pad;
  const x = d => M.l + (Date.parse(d) - t0) / (t1 - t0 || 1) * (W - M.l - M.r);
  const y = v => M.t + (1 - (v - lo) / (hi - lo)) * (H - M.t - M.b);
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', 'Depotwert je Agent über die Zeit');
  const el = (n, a) => { const e = document.createElementNS(ns, n); for (const k in a) e.setAttribute(k, a[k]); svg.appendChild(e); return e; };
  const fmt = v => Math.round(v).toLocaleString('de-DE') + ' €';
  for (let k = 0; k <= 4; k++) {
    const v = lo + (hi - lo) * k / 4;
    el('line', {x1: M.l, x2: W - M.r, y1: y(v), y2: y(v), stroke: css('--grid'), 'stroke-width': 1});
    const t = el('text', {x: M.l - 8, y: y(v) + 4, 'text-anchor': 'end', 'font-size': 11, fill: css('--text-secondary')});
    t.textContent = fmt(v);
  }
  const yrs = [...new Set(dates.map(d => d.slice(0, 4)))];
  const step = Math.ceil(yrs.length / 10);
  yrs.forEach((yr, i) => {
    if (i % step) return;
    const d = dates.find(d => d.startsWith(yr)); const xx = x(d);
    const t = el('text', {x: xx, y: H - 6, 'font-size': 11, fill: css('--text-secondary')}); t.textContent = yrs.length > 1 ? yr : d;
  });
  if (yrs.length === 1) { const t = el('text', {x: W - M.r, y: H - 6, 'font-size': 11, 'text-anchor': 'end', fill: css('--text-secondary')}); t.textContent = dates[dates.length-1]; }
  el('line', {x1: M.l, x2: W - M.r, y1: y(10000), y2: y(10000), stroke: css('--text-secondary'), 'stroke-width': 1, 'stroke-dasharray': '2 4'});
  S.forEach(s => {
    const d = s.data.map((p, i) => (i ? 'L' : 'M') + x(p[0]).toFixed(1) + ' ' + y(p[1]).toFixed(1)).join('');
    el('path', {d, fill: 'none', stroke: css(s.color), 'stroke-width': 2, 'stroke-linejoin': 'round',
      'stroke-dasharray': s.dashed ? '5 4' : 'none'});
    const lg = document.getElementById('legend');
    lg.insertAdjacentHTML('beforeend', `<span><i class="sw" style="background:${css(s.color)}"></i>${s.name}</span>`);
  });
  const cross = el('line', {y1: M.t, y2: H - M.b, stroke: css('--text-secondary'), 'stroke-width': 1, opacity: 0});
  const dots = S.map(s => el('circle', {r: 4, fill: css(s.color), stroke: css('--surface-1'), 'stroke-width': 2, opacity: 0}));
  const box = document.getElementById('chart'); box.appendChild(svg);
  const tip = document.getElementById('tip');
  const maps = S.map(s => new Map(s.data.map(p => [p[0], p[1]])));
  svg.addEventListener('mousemove', ev => {
    const r = svg.getBoundingClientRect();
    const px = (ev.clientX - r.left) / r.width * W;
    const t = t0 + (px - M.l) / (W - M.l - M.r) * (t1 - t0);
    let best = dates[0]; for (const d of dates) { if (Date.parse(d) <= t) best = d; else break; }
    const xx = x(best); cross.setAttribute('x1', xx); cross.setAttribute('x2', xx); cross.setAttribute('opacity', .5);
    let rowsH = '';
    S.forEach((s, i) => {
      const v = maps[i].get(best);
      if (v === undefined) { dots[i].setAttribute('opacity', 0); return; }
      dots[i].setAttribute('cx', xx); dots[i].setAttribute('cy', y(v)); dots[i].setAttribute('opacity', 1);
      rowsH += `<div><i class="sw" style="background:${css(s.color)}"></i>${s.name}: ${fmt(v)}</div>`;
    });
    tip.innerHTML = `<b>${best.split('-').reverse().join('.')}</b>${rowsH}`;
    tip.style.display = 'block';
    const left = (xx / W) * r.width; tip.style.left = Math.min(left + 14, r.width - tip.offsetWidth - 4) + 'px'; tip.style.top = '10px';
  });
  svg.addEventListener('mouseleave', () => { tip.style.display = 'none'; cross.setAttribute('opacity', 0); dots.forEach(d => d.setAttribute('opacity', 0)); });
} else {
  document.getElementById('chart').insertAdjacentHTML('beforeend', '<p class="note">Noch zu wenig Daten für eine Kurve. Der erste Handelstag wurde gebucht.</p>');
}
</script></body></html>
"""
