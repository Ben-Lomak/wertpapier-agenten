"""Schnelltest ohne Internet:  python -m tests.test_core"""
import yaml

from bots import runner
from bots.tax_pt import compute_year_tax

cfg = yaml.safe_load(open("config.yaml"))["tax"]
assert compute_year_tax(1000, 0, [], 2026, cfg)[0] == 480          # kurzfristig, progressiv 48 %
assert compute_year_tax(1000, -400, [], 2026, cfg)[0] == 288        # Verlust mindert kurzfristig zuerst
assert compute_year_tax(-500, 200, [], 2026, cfg)[1][0]["amount"] == 300
ports = runner.run_backtest(synthetic=True)
assert all(p.equity_curve for p in ports)
print("OK")
