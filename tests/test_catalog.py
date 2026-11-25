"""商品版本与 SKU 谱系的规则测试。"""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from return_disposition.catalog import (
    definition_at,
    lineage_key,
    lineage_sources,
    parse_catalog,
)
from return_disposition.input_contract import load_bundle

DATA = Path(__file__).parents[1] / "data"


class CatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = parse_catalog(load_bundle(DATA)["catalog.json"])

    def test_definition_at_order_date(self):
        v1 = definition_at(self.catalog, "chair-a", date(2026, 9, 1))
        v2 = definition_at(self.catalog, "chair-a", date(2026, 9, 10))
        self.assertEqual(v1.batch, "chair-a@v1")
        self.assertEqual(v2.batch, "chair-a@v2")
        self.assertEqual(v1.entry.dimensions, "80x70")
        self.assertEqual(v2.entry.dimensions, "78x70")

    def test_gap_is_flagged_not_filled(self):
        result = definition_at(self.catalog, "chair-a", date(2026, 7, 1))
        self.assertIsNone(result.entry)
        self.assertEqual(result.batch, "chair-a@unknown")
        self.assertTrue(result.flags)

    def test_merged_sku_lineage(self):
        self.assertEqual(lineage_key(self.catalog, "lamp-b2"), "lamp-bx")
        self.assertEqual(lineage_key(self.catalog, "lamp-c"), "lamp-bx")
        self.assertEqual(lineage_key(self.catalog, "lamp-bx"), "lamp-bx")
        self.assertEqual(lineage_key(self.catalog, "chair-a"), "chair-a")
        self.assertIn("lamp-b2", lineage_sources(self.catalog, "lamp-bx"))
        self.assertIn("lamp-c", lineage_sources(self.catalog, "lamp-bx"))


if __name__ == "__main__":
    unittest.main()
