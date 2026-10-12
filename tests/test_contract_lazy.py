from __future__ import annotations

import base64
import os
import tempfile
import unittest
from unittest.mock import patch

from core import extract as EX
from core.materials import Material, SampleMaterials
from engines.base import Amount, RecognizeResult


ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class RecordingEngine:
    def __init__(self):
        self.payloads = []

    def recognize(self, payload):
        self.payloads.append(payload)
        return RecognizeResult(
            amounts=[Amount(42.0, "未知")],
            engine="SyntheticOCR",
            confidence=1.0,
        )


class MetadataEngine:
    def recognize(self, _payload):
        return RecognizeResult(
            doc_numbers={"发票": [], "合同": ["SYNTH-CONTRACT"], "付款单": ["SYNTH-PAY"]},
            amounts=[Amount(42.0, "未知")],
            counterparty="合成供应方",
            companies=["合成供应方有限公司"],
            confidence=0.4,
            engine="SyntheticOCR",
            conflict=True,
            note="synthetic-note",
            dates=["2026-01-01"],
        )


class SoftFailureEngine:
    def recognize(self, _payload):
        return RecognizeResult(
            engine="SyntheticOCR",
            note="图像解码失败",
            succeeded=False,
        )


class SyntheticPage:
    def get_text(self, _kind):
        return ""


class SyntheticDocument:
    def __iter__(self):
        return iter([SyntheticPage()])

    def close(self):
        pass


class ContractLazyLoadingTests(unittest.TestCase):
    def test_contract_image_is_not_recognized_during_normal_extraction(self):
        engine = RecordingEngine()
        with tempfile.TemporaryDirectory() as tmp:
            invoice_path = os.path.join(tmp, "synthetic-invoice.png")
            contract_path = os.path.join(tmp, "synthetic-contract.png")
            with open(invoice_path, "wb") as stream:
                stream.write(b"synthetic-invoice")
            with open(contract_path, "wb") as stream:
                stream.write(b"synthetic-contract")
            materials = SampleMaterials(
                sid="1",
                folder=tmp,
                materials=[
                    Material(invoice_path, "发票", "synthetic-invoice.png"),
                    Material(contract_path, "合同", "synthetic-contract.png"),
                ],
            )

            result = EX.extract_sample(materials, engine)

        self.assertEqual(len(engine.payloads), 1)
        self.assertEqual(engine.payloads[0], b"synthetic-invoice")
        self.assertFalse(result.contract_read)

    def test_contract_image_is_recognized_by_explicit_lazy_path(self):
        engine = RecordingEngine()
        with tempfile.TemporaryDirectory() as tmp:
            contract_path = os.path.join(tmp, "synthetic-contract.png")
            with open(contract_path, "wb") as stream:
                stream.write(ONE_PIXEL_PNG)
            materials = SampleMaterials(
                sid="1",
                folder=tmp,
                materials=[Material(contract_path, "合同", "synthetic-contract.png")],
            )

            amounts = EX.extract_contract_amounts(materials, engine)

        self.assertEqual(len(engine.payloads), 1)
        self.assertEqual([amount.value for amount in amounts], [42.0])
        self.assertTrue(all(amount.source == "合同" for amount in amounts))

    def test_lazy_contract_image_preserves_the_previous_full_metadata(self):
        engine = MetadataEngine()
        with tempfile.TemporaryDirectory() as tmp:
            contract_path = os.path.join(tmp, "synthetic-contract.png")
            with open(contract_path, "wb") as stream:
                stream.write(b"synthetic-contract")
            materials = SampleMaterials(
                sid="1",
                folder=tmp,
                materials=[Material(contract_path, "合同", "synthetic-contract.png")],
            )
            result = EX.extract_sample(materials, engine)

            amounts = EX.extract_contract_amounts(materials, engine, evidence=result)

        self.assertEqual([amount.value for amount in amounts], [42.0])
        self.assertEqual(result.contract_numbers, ["SYNTH-CONTRACT"])
        self.assertEqual(result.payment_numbers, ["SYNTH-PAY"])
        self.assertEqual(result.counterparties, ["合成供应方", "合成供应方有限公司"])
        self.assertEqual(result.confidence, 0.4)
        self.assertTrue(result.conflict)
        self.assertEqual(result.engines_used, {"SyntheticOCR"})
        self.assertIn("synthetic-note", result.notes[0])
        self.assertEqual(result.file_results[0].result.dates, ["2026-01-01"])
        self.assertTrue(result.contract_attempted)
        self.assertTrue(result.contract_read)
        self.assertFalse(result.contract_failed)

    def test_lazy_contract_image_failure_is_reported_as_failure_not_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_path = os.path.join(tmp, "missing-contract.png")
            materials = SampleMaterials(
                sid="1",
                folder=tmp,
                materials=[Material(missing_path, "合同", "missing-contract.png")],
            )
            result = EX.extract_sample(materials, RecordingEngine())

            amounts = EX.extract_contract_amounts(materials, RecordingEngine(), evidence=result)

        self.assertEqual(amounts, [])
        self.assertTrue(result.contract_attempted)
        self.assertFalse(result.contract_read)
        self.assertTrue(result.contract_failed)
        self.assertTrue(any("合同读取失败" in note for note in result.notes))

    def test_lazy_contract_image_soft_failure_is_not_reported_as_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            contract_path = os.path.join(tmp, "synthetic-contract.png")
            with open(contract_path, "wb") as stream:
                stream.write(b"invalid-image")
            materials = SampleMaterials(
                sid="1",
                folder=tmp,
                materials=[Material(contract_path, "合同", "synthetic-contract.png")],
            )
            result = EX.extract_sample(materials, RecordingEngine())

            amounts = EX.extract_contract_amounts(
                materials, SoftFailureEngine(), evidence=result)

        self.assertEqual(amounts, [])
        self.assertTrue(result.contract_attempted)
        self.assertFalse(result.contract_read)
        self.assertTrue(result.contract_failed)
        self.assertTrue(any("图像解码失败" in note for note in result.notes))

    def test_lazy_contract_pdf_soft_failure_is_not_reported_as_read(self):
        materials = SampleMaterials(
            sid="1",
            folder="synthetic",
            materials=[Material("synthetic-contract.pdf", "合同", "synthetic-contract.pdf")],
        )
        result = EX.SampleEvidence(sid="1")

        with patch.object(EX.fitz, "open", return_value=SyntheticDocument()), \
                patch.object(EX, "_render_png", return_value=b"synthetic-page"):
            amounts = EX.extract_contract_amounts(
                materials, SoftFailureEngine(), evidence=result)

        self.assertEqual(amounts, [])
        self.assertTrue(result.contract_attempted)
        self.assertFalse(result.contract_read)
        self.assertTrue(result.contract_failed)
        self.assertTrue(any("图像解码失败" in note for note in result.notes))


if __name__ == "__main__":
    unittest.main()
