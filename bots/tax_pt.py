"""Vereinfachtes Steuermodell Portugal (IRS, Kategorie G, Kursgewinne).

Regeln im Modell:
- Gewinne und Verluste eines Kalenderjahres werden saldiert.
- Kurzfristig (< 365 Tage gehalten): bei short_term_mode = progressive mit Grenzsteuersatz
  (englobamento obrigatório im höchsten Tarif), sonst 28 %.
- Langfristig: 28 %, abzüglich steuerfreier Anteil nach Haltedauer (2/5/8 Jahre).
- Verluste mindern zuerst kurzfristige Gewinne (höherer Satz), dann langfristige.
- Übrig bleibende Verluste werden bis zu 5 Jahre vorgetragen.
Gebühren sind in Anschaffungskosten und Verkaufserlös bereits enthalten.
"""
from __future__ import annotations


def exemption_share(holding_days: int, cfg: dict) -> float:
    years = holding_days / 365.0
    for rule in sorted(cfg.get("holding_exemptions", []), key=lambda r: -r["min_years"]):
        if years >= rule["min_years"]:
            return rule["exempt"]
    return 0.0


def split_gain(gain: float, holding_days: int, cfg: dict) -> tuple[float, float]:
    """Gibt (kurzfristig, langfristig_steuerpflichtig) zurück."""
    if holding_days < cfg["short_term_days"]:
        return gain, 0.0
    if gain > 0:
        gain *= 1 - exemption_share(holding_days, cfg)
    return 0.0, gain


def short_rate(cfg: dict) -> float:
    if cfg.get("short_term_mode", "flat") == "progressive":
        return cfg["marginal_rate"] + cfg.get("solidarity_surcharge", 0.0)
    return cfg["flat_rate"]


def compute_year_tax(st: float, lt: float, carry: list[dict], year: int, cfg: dict):
    """Steuer für ein Jahr. carry: Liste {'year', 'amount' (positiv = Verlust)}.

    Gibt (steuer, neue_carry_liste, details) zurück.
    """
    # Verluste innerhalb des Jahres verrechnen
    total = st + lt
    if st < 0 < lt:
        lt, st = max(total, 0.0), min(total, 0.0)
    elif lt < 0 < st:
        st, lt = max(total, 0.0), min(total, 0.0)

    max_age = cfg.get("loss_carryforward_years", 0)
    carry = [c for c in carry if year - c["year"] <= max_age and c["amount"] > 0]
    used = 0.0
    for c in sorted(carry, key=lambda c: c["year"]):
        for bucket in ("st", "lt"):
            avail = st if bucket == "st" else lt
            if avail <= 0 or c["amount"] <= 0:
                continue
            take = min(avail, c["amount"])
            c["amount"] -= take
            used += take
            if bucket == "st":
                st -= take
            else:
                lt -= take
    carry = [c for c in carry if c["amount"] > 1e-9]

    net_loss = -(min(st, 0) + min(lt, 0))
    if net_loss > 0 and max_age > 0:
        carry.append({"year": year, "amount": net_loss})

    tax = max(st, 0) * short_rate(cfg) + max(lt, 0) * cfg["flat_rate"]
    details = {"year": year, "taxable_short": round(max(st, 0), 2),
               "taxable_long": round(max(lt, 0), 2), "loss_carry_used": round(used, 2),
               "tax": round(tax, 2)}
    return tax, carry, details
