# Wertpapier-Agenten

Zwei simulierte Trading-Agenten mit je 10.000 € virtuellem Kapital. Kein echtes Geld, keine Broker-Anbindung.

| Agent | Logik | Rhythmus |
|---|---|---|
| Momentum Global | Top 8 nach 12-Monats-Performance (letzter Monat ausgeklammert), nur Titel über ihrem 100-Tage-Schnitt | wöchentlich |
| Nachhaltig | Top 10 aus einer SRI-Liste, Mischung aus Momentum (60 %) und niedriger Schwankung (40 %) | monatlich |

Jeder Agent läuft doppelt: einmal mit N26-Kosten, einmal mit Revolut-Kosten. Das ergibt vier Depots.

## Risikoregeln (für beide Agenten)

1. Höchstens 15 % des Depots pro Titel
2. Trailing-Stop: Verkauf bei 12 % unter dem Höchststand seit Kauf
3. Marktfilter: Weltindex unter seinem 200-Tage-Schnitt = alles in Cash
4. Kill-Switch: Depot 15 % unter Höchststand = alles verkaufen, 30 Handelstage Pause
5. Kapital-Boden: Depot unter 7.500 € = Agent stoppt endgültig

Regel 5 ist die Grenze gegen den Totalverlust. Kurslücken über Nacht können den Verkauf etwas tiefer auslösen.

## Kosten und Steuern

- **N26:** 0,90 € pro Order, keine Fremdwährungsgebühr
- **Revolut Standard:** 1 Freitrade pro Monat, danach 0,25 %, mindestens 1 €
- **Beide:** angenommener Spread 0,15 % und Slippage 0,05 % pro Order
- **Portugal (IRS):** Gewinne aus Positionen unter 365 Tagen werden mit deinem Grenzsteuersatz besteuert (englobamento obrigatório, Standard 48 %), längere Haltedauer mit 28 %. Verluste werden im Jahr verrechnet und 5 Jahre vorgetragen. Abrechnung jeweils zum Jahreswechsel.

Alle Werte stehen in `config.yaml` und lassen sich dort ändern, etwa dein tatsächlicher Grenzsteuersatz oder ein anderes Kontomodell.

## Einrichten auf GitHub (empfohlen)

GitHub speichert den Code und rechnet die Simulation jeden Abend automatisch weiter. Dein Rechner muss dafür nicht laufen.

1. Auf github.com ein neues **privates** Repository anlegen, z. B. `wertpapier-agenten`
2. Alle Dateien aus diesem Ordner hochladen (Web-Oberfläche: "Add file" → "Upload files", den Ordner `.github` mit hochladen)
3. Im Repository unter **Settings → Actions → General → Workflow permissions** "Read and write permissions" aktivieren
4. Unter **Actions** den Workflow **Backtest** öffnen und "Run workflow" klicken. Nach ein paar Minuten liegt der Bericht als Download unter dem Lauf.
5. Den Workflow **Live-Simulation (täglich)** einmal manuell starten. Ab dann läuft er Mo–Fr um 22:15 UTC von selbst.

**Ergebnisse ansehen:** Jeder Lauf zeigt unter Actions eine Tabelle mit allen vier Depots. Das volle Dashboard liegt als `docs/index.html` im Repository und als Download am Lauf. Mit GitHub Pro lässt sich `docs/` zusätzlich als private Webseite veröffentlichen (Settings → Pages).

## Lokal ausführen (Mac)

```bash
cd wertpapier-agenten
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m bots.runner backtest     # Bericht in reports/backtest.html
python -m bots.runner live         # schreibt state/ und docs/index.html fort
python -m tests.test_core          # Selbsttest ohne Internet
```

## Wichtig zu wissen

- **Backtest ist zu optimistisch.** Die Titellisten bestehen aus heutigen Gewinnern. Firmen, die früher abgestürzt sind, fehlen.
- **Erst die Live-Simulation zählt.** Sie trifft Entscheidungen ohne Wissen über die Zukunft. 6 bis 12 Monate laufen lassen.
- **Maßstab ist der MSCI World.** Schlägt ein Agent nach Kosten und Steuern den einfachen ETF nicht, lohnt sich der Aufwand nicht.
- **Kursdaten von Yahoo Finance** sind kostenlos, aber inoffiziell. Fällt ein Abruf aus, holt der nächste Lauf die fehlenden Tage nach.
- Vereinfachtes Steuermodell, kein Ersatz für Steuerberatung. Dividenden sind nicht enthalten. Keine Anlageberatung.

## Aufbau

```
config.yaml                 alle Parameter
universes/*.txt             Titellisten je Agent
bots/data.py                Kurse laden, in EUR umrechnen
bots/strategies.py          Auswahlregeln der Agenten
bots/engine.py              Tagesablauf inkl. Risikomanager
bots/portfolio.py           Depot, Gebühren, FIFO, Steuerbuchhaltung
bots/tax_pt.py              Steuermodell Portugal
bots/report.py              Dashboard
bots/runner.py              Backtest und Live-Modus
state/                      gespeicherter Zustand der vier Depots
.github/workflows/          automatische Läufe
```
