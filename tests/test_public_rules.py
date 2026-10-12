import unittest

from core import parse, reconcile


class PublicRulesTests(unittest.TestCase):
    def test_buyer_markers_are_generic(self):
        self.assertEqual(parse._BUYER_MARKERS, ("识别号", "纳税人", "信用代码", "购买方", "买方"))

    def test_counterparty_exclusions_are_generic(self):
        self.assertEqual(parse._CP_EXCLUDE, ("银行", "税务", "国家税务"))

    def test_tax_identifier_is_rejected_but_invoice_is_retained(self):
        text = "购买方纳税人识别号: 00000000000000000001\n发票号码: 90000001"
        self.assertEqual(parse.extract_invoice_numbers(text), ["90000001"])

    def test_name_consistency_uses_matching_names(self):
        self.assertTrue(reconcile.counterparty_consistent(["合成供应方甲", "合成供应方甲"]))
        self.assertFalse(reconcile.counterparty_consistent(["", "合成供应方甲"]))


if __name__ == "__main__":
    unittest.main()
