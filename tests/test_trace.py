"""追溯与批次随访测试。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from return_disposition.pipeline import run

DATA = Path(__file__).parents[1] / "data"


class TraceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run(DATA)

    def test_explain_chains_all_layers(self):
        explanation = self.report.explain("rec-ret-009")
        self.assertEqual(explanation["order_facts"]["order_id"], "ord-011")
        self.assertEqual(explanation["order_facts"]["channel"], "market")
        self.assertEqual(explanation["return_facts"]["return_id"], "ret-009")
        cs = explanation["reason_evidence"]["cs_reason"]
        self.assertEqual(cs["code"], "damage_transit")
        self.assertEqual(cs["dict_version"], "D2")
        inspection = explanation["reason_evidence"]["inspection"]
        self.assertEqual(inspection["cosmetic"], "issue")
        self.assertEqual(inspection["functional"], "unknown")
        self.assertEqual(explanation["product_version"]["batch"], "chair-a@v2")
        self.assertEqual(explanation["product_version"]["dimensions"], "78x70")
        self.assertTrue(explanation["constraints"])
        self.assertEqual(explanation["supersedes"], "dp-005")

    def test_explain_restock_shows_capacity_conflict(self):
        explanation = self.report.explain("rec-restock-chair-a")
        self.assertEqual(explanation["sku"], "chair-a")
        kinds = {c["kind"] for c in explanation["constraints"]}
        self.assertEqual(kinds, {"commitment", "stock", "arrival"})
        self.assertTrue(any("履约冲突" in a for a in explanation["alerts"]))

    def test_batch_followup_flags_dict_change(self):
        followup = self.report.followup("chair-a")
        self.assertEqual([v["batch"] for v in followup["versions"]], ["chair-a@v1", "chair-a@v2"])
        comparison = followup["comparisons"][0]
        self.assertTrue(comparison["dict_changed"])
        self.assertTrue(any("仅族级比较" in n for n in comparison["notes"]))
        size = next(f for f in comparison["families"] if f["family"] == "size")
        self.assertEqual(size["prev_qty"], 3)
        self.assertEqual(size["curr_qty"], 1)
        self.assertLess(size["curr_share"], size["prev_share"])
        self.assertIn("支持", size["verdict"])

    def test_incomparable_records_listed(self):
        self.assertTrue(any("ret-007" in item for item in self.report.incomparable_records))
        self.assertTrue(any("ret-004" in item for item in self.report.incomparable_records))


if __name__ == "__main__":
    unittest.main()
