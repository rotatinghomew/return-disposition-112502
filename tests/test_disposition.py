"""处置建议与重计划规则测试。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from return_disposition.cohorts import build_cohorts
from return_disposition.disposition import desired_action, plan
from return_disposition.evidence import build_evidence
from return_disposition.input_contract import load_bundle
from return_disposition.model import Dataset, Inspection

DATA = Path(__file__).parents[1] / "data"


def load_dataset():
    return Dataset.from_bundle(load_bundle(DATA))


class DesiredActionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset()

    def action_of(self, return_id):
        request = self.dataset.return_by_id[return_id]
        evidence = build_evidence(
            request, self.dataset.latest_inspection(return_id), self.dataset.dictionary
        )
        return desired_action(evidence)[0]

    def test_action_rules(self):
        self.assertEqual(self.action_of("ret-010"), "resell")  # 未拆封+功能外观正常
        self.assertEqual(self.action_of("ret-008"), "refurbish")  # 已拆封+正常→重封装
        self.assertEqual(self.action_of("ret-002"), "refurbish")  # 外观损坏+功能正常
        self.assertEqual(self.action_of("ret-001"), "isolate")  # 功能未检
        self.assertEqual(self.action_of("ret-009"), "isolate")  # 外观损坏但功能未检
        self.assertEqual(self.action_of("ret-011"), "isolate")  # 无到货检验


class PlanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset()
        _, cls.index = build_cohorts(cls.dataset)
        evidence_index = {rid: r.evidence for rid, r in cls.index.items()}
        cls.outcome = plan(cls.dataset, evidence_index)
        cls.recs = {r.rec_id: r for r in cls.outcome.recommendations}

    def test_replan_only_unexecuted(self):
        self.assertEqual(set(self.outcome.superseded), {"dp-004", "dp-005", "dp-007"})
        self.assertEqual(set(self.outcome.kept), {"dp-003", "dp-008"})
        self.assertEqual(set(self.outcome.immutable), {"dp-001", "dp-002", "dp-006"})
        self.assertEqual(self.recs["rec-ret-009"].supersedes, "dp-005")

    def test_capacity_constraints_recorded(self):
        rec = self.recs["rec-ret-010"]
        capacity = [c for c in rec.constraints if c.kind == "zone_capacity"]
        self.assertEqual(capacity[0].ref, "resale")
        self.assertIn("≤40", capacity[0].detail)

    def test_restock_with_arrival_conflict(self):
        rec = self.recs["rec-restock-chair-a"]
        self.assertEqual(rec.action, "restock")
        self.assertEqual(rec.quantity, 1)
        self.assertTrue(any("晚于承诺日期" in a for a in rec.alerts))
        self.assertTrue(any("已过仍未履约" in a for a in rec.alerts))
        kinds = {c.kind for c in rec.constraints}
        self.assertEqual(kinds, {"commitment", "stock", "arrival"})

    def test_shipped_exchange_not_rewritten(self):
        self.assertTrue(any("cm-003" in a and "不可改写" in a for a in self.outcome.alerts))

    def test_new_qc_conflicting_executed_only_alerts(self):
        bundle = load_bundle(DATA)
        bundle["inspections.json"] = bundle["inspections.json"] + [
            {
                "inspection_id": "ins-900",
                "return_id": "ret-005",
                "received_at": "2026-11-20",
                "seal": "opened",
                "functional": "fail",
                "cosmetic": "damaged",
                "decision": "isolate",
            }
        ]
        dataset = Dataset.from_bundle(bundle)
        _, index = build_cohorts(dataset)
        evidence_index = {rid: r.evidence for rid, r in index.items()}
        outcome = plan(dataset, evidence_index)
        # 已执行的 dp-002（再售）不被改写，只发告警。
        self.assertIn("dp-002", outcome.immutable)
        self.assertTrue(any("dp-002" in a for a in outcome.alerts))
        self.assertFalse(any(r.return_id == "ret-005" for r in outcome.recommendations))


if __name__ == "__main__":
    unittest.main()
