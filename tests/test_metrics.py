"""队列与指标测试：重复开单、结构校正、复购删失、无法归因。"""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from return_disposition.catalog import definition_at
from return_disposition.cohorts import build_cohorts
from return_disposition.input_contract import load_bundle
from return_disposition.metrics import (
    mix_adjustment,
    period_cells,
    reason_distribution,
    repurchase_impact,
    return_rate,
    unattributable_share,
)
from return_disposition.model import Dataset

DATA = Path(__file__).parents[1] / "data"


class MetricsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = Dataset.from_bundle(load_bundle(DATA))
        cls.cohorts, cls.index = build_cohorts(cls.dataset)
        cls.by_label = {c.key.label(): c for c in cls.cohorts}

    def test_duplicate_rma_excluded_but_kept(self):
        cohort = self.by_label["web×chair-a@v1"]
        self.assertEqual(cohort.sold_qty, 5)
        self.assertEqual(cohort.returned_qty, 3)
        self.assertEqual([r.request.return_id for r in cohort.duplicates], ["ret-004"])
        self.assertAlmostEqual(return_rate(cohort), 3 / 5)

    def test_free_text_hint_counts_as_unverified(self):
        dist = reason_distribution(self.by_label["web×chair-a@v1"])
        self.assertEqual(dist["size"]["qty"], 2)
        self.assertEqual(dist["size"]["verified"], 0)
        self.assertEqual(dist["damage"]["verified"], 1)

    def test_unattributable_share(self):
        missing, total = unattributable_share(self.by_label["web×chair-a@v2"])
        self.assertEqual((missing, total), (1, 2))

    def test_mix_decomposition_identity(self):
        active = [r.request for c in self.cohorts for r in c.active]
        batch_of = lambda o: definition_at(self.dataset.catalog, o.sku, o.ordered_at).batch
        report = mix_adjustment(period_cells(self.dataset.orders, active, batch_of))
        self.assertEqual(report.reference_period, "2026-08")
        sep = next(s for s in report.periods if s.period == "2026-09")
        ref = report.periods[0]
        crude_diff = sep.crude_rate - ref.crude_rate
        self.assertAlmostEqual(
            crude_diff, sep.mix_effect + sep.rate_effect + sep.residual, places=9
        )
        # 9 月粗率下降主要来自订单结构（促销期渠道/批次构成），不是率恶化。
        self.assertLess(sep.mix_effect, 0)
        self.assertLess(abs(sep.rate_effect), abs(sep.mix_effect))
        self.assertTrue(sep.low_confidence_cells)

    def test_repurchase_with_censoring(self):
        active = [r.request for c in self.cohorts for r in c.active]
        report = repurchase_impact(self.dataset.orders, active, date(2026, 11, 25))
        returned = report.returned_group
        self.assertEqual(set(returned.repurchased), {"u01", "u04", "u06"})
        self.assertIn("u10", returned.censored)
        self.assertIn("u12", returned.censored)
        self.assertAlmostEqual(returned.rate, 1.0)
        baseline = report.baseline_group
        self.assertEqual(set(baseline.customers), {"u05", "u09", "u11"})
        self.assertEqual(baseline.repurchased, ("u09",))
        self.assertIn("u11", baseline.censored)
        self.assertAlmostEqual(baseline.rate, 0.5)


if __name__ == "__main__":
    unittest.main()
