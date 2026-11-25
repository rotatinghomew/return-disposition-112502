"""证据分层与归因规则测试。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from return_disposition.evidence import (
    ISSUE,
    OK,
    UNKNOWN,
    attribute,
    build_evidence,
    hint_family_from_text,
)
from return_disposition.input_contract import load_bundle
from return_disposition.model import Dataset

DATA = Path(__file__).parents[1] / "data"


class EvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = Dataset.from_bundle(load_bundle(DATA))

    def evidence_of(self, return_id):
        request = self.dataset.return_by_id[return_id]
        return build_evidence(
            request, self.dataset.latest_inspection(return_id), self.dataset.dictionary
        )

    def test_missing_inspection_is_unknown_not_ok(self):
        evidence = self.evidence_of("ret-011")
        self.assertIsNone(evidence.inspection)
        result = attribute(evidence)
        self.assertEqual(result.functional, UNKNOWN)
        self.assertEqual(result.cosmetic, UNKNOWN)
        self.assertTrue(any("不能记为无缺陷" in note for note in result.notes))

    def test_cosmetic_damage_does_not_clear_functional(self):
        evidence = self.evidence_of("ret-009")
        self.assertEqual(evidence.inspection.cosmetic, ISSUE)
        self.assertEqual(evidence.inspection.functional, UNKNOWN)
        result = attribute(evidence)
        self.assertTrue(any("不能视为功能正常" in note for note in result.notes))

    def test_free_text_is_hint_only(self):
        evidence = self.evidence_of("ret-005")
        self.assertIsNone(evidence.cs_reason)
        self.assertEqual(evidence.free_text_family_hint, "size")
        result = attribute(evidence)
        self.assertEqual(result.family, "size")
        self.assertEqual(result.basis, ("free_text_hint",))
        self.assertFalse(result.verified_by_inspection)

    def test_unattributable_when_no_layer_has_content(self):
        result = attribute(self.evidence_of("ret-011"))
        self.assertTrue(result.unattributable)
        self.assertIsNone(result.family)

    def test_damage_verified_by_inspection(self):
        result = attribute(self.evidence_of("ret-003"))
        self.assertEqual(result.family, "damage")
        self.assertTrue(result.verified_by_inspection)

    def test_legacy_code_keeps_fallback_flag(self):
        evidence = self.evidence_of("ret-007")
        self.assertTrue(evidence.cs_reason.fallback)
        self.assertEqual(evidence.cs_reason.dict_version, "D1")
        self.assertEqual(evidence.cs_reason.expected_version, "D2")

    def test_hint_keywords(self):
        self.assertEqual(hint_family_from_text("太大了，客厅放不下"), "size")
        self.assertEqual(hint_family_from_text("颜色与页面不同"), "description")
        self.assertIsNone(hint_family_from_text("不想要了"))


if __name__ == "__main__":
    unittest.main()
