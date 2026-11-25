"""原因字典版本化解释的规则测试。"""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from return_disposition.dictionary import ONE_TO_ONE, ReasonDictionary
from return_disposition.input_contract import load_bundle

DATA = Path(__file__).parents[1] / "data"


class DictionaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dictionary = ReasonDictionary.from_raw(load_bundle(DATA)["reason_dictionary.json"])

    def test_version_boundary_is_start_inclusive(self):
        self.assertEqual(self.dictionary.version_at(date(2026, 9, 9)).version, "D1")
        self.assertEqual(self.dictionary.version_at(date(2026, 9, 10)).version, "D2")

    def test_native_code_uses_current_version(self):
        result = self.dictionary.interpret("size_small", date(2026, 10, 20))
        self.assertFalse(result.fallback)
        self.assertEqual(result.dict_version, "D2")
        self.assertEqual(result.family, "size")

    def test_legacy_code_falls_back_with_flag(self):
        result = self.dictionary.interpret("size", date(2026, 9, 12))
        self.assertTrue(result.fallback)
        self.assertEqual(result.dict_version, "D1")
        self.assertEqual(result.expected_version, "D2")
        self.assertIn("不可直接比较", result.note)

    def test_unknown_code_is_not_interpretable(self):
        result = self.dictionary.interpret("nope", date(2026, 9, 12))
        self.assertIsNone(result.family)
        self.assertIn("无法解释", result.note)

    def test_split_mapping_is_family_level_only(self):
        self.assertEqual(self.dictionary.comparability("D1", "D2", "size"), "family_only")
        self.assertEqual(self.dictionary.comparability("D1", "D2", "damage"), ONE_TO_ONE)
        self.assertEqual(self.dictionary.comparability("D1", "D1", "size"), "same")


if __name__ == "__main__":
    unittest.main()
