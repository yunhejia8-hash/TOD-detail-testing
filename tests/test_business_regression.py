from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

import tod_reconcile as TR
from core import reconcile as RC
from engines.base import RecognizeResult
from support import CapturingWorkpaper, evidence, sample


class NoopEngine:
    available = True

    def status(self):
        return "SyntheticOCR=ready"

    def recognize(self, _payload):
        return RecognizeResult(engine="SyntheticOCR", confidence=1.0)


class NoopService:
    def __init__(self):
        self.engine = NoopEngine()

    def acquire(self, **_kwargs):
        return self.engine, False


class BusinessRuleRegressionTests(unittest.TestCase):
    def test_cli_main_keeps_the_existing_single_argument_run_call(self):
        opts = object()
        with patch.object(TR, "build_args", return_value=opts), patch.object(TR, "run") as run:
            TR.main()

        run.assert_called_once_with(opts, log=print)

    def test_three_state_semantics_are_unchanged(self):
        matched = RC.AmountResult(matched=True, matched_value=100.0, kind="单张", delta=0.0)
        mismatch = RC.AmountResult(matched=False, kind="无金额候选", delta=None)
        good_summary = RC.SummaryResult(score=1.0)

        consistent = RC.judge(
            matched,
            good_summary,
            soft_summary=False,
            cp_consistent=False,
            threshold=0.6,
            conflict=False,
            has_key_number=True,
            low_conf=False,
        )
        doubtful = RC.judge(
            mismatch,
            good_summary,
            soft_summary=False,
            cp_consistent=False,
            threshold=0.6,
            conflict=False,
            has_key_number=True,
            low_conf=False,
        )
        inconsistent = RC.judge(
            matched,
            good_summary,
            soft_summary=False,
            cp_consistent=False,
            threshold=0.6,
            conflict=False,
            has_key_number=False,
            low_conf=False,
        )

        self.assertEqual(consistent.state, "✅一致")
        self.assertEqual(doubtful.state, "⚠️存疑")
        self.assertEqual(inconsistent.state, "❌不一致")

    def test_task_evidence_mutable_state_is_not_shared(self):
        first = evidence("1")
        second = evidence("2")
        first.amounts.append(first.amounts[0])
        first.invoice_numbers.append("SYNTH-EXTRA")

        self.assertEqual(len(second.amounts), 1)
        self.assertEqual(second.invoice_numbers, ["SYNTH-2"])

    def test_contract_failure_is_not_reported_as_contract_read(self):
        ev = evidence("1", amount=None)
        ev.contract_attempted = True
        ev.contract_failed = True
        ev.notes.append("synthetic-contract.png:合同读取失败:synthetic")
        row = TR._row(
            sample("1"),
            ev,
            RC.AmountResult(matched=False, kind="无金额候选"),
            RC.SummaryResult(),
            RC.Verdict("⚠️存疑", "金额差异"),
        )

        self.assertEqual(row["amt_consistency"], "合同读取失败")
        self.assertIn("合同读取失败", row["reason"])

    def test_partial_contract_failure_is_reported_explicitly(self):
        ev = evidence("1", amount=None)
        ev.contract_attempted = True
        ev.contract_read = True
        ev.contract_failed = True

        row = TR._row(
            sample("1"),
            ev,
            RC.AmountResult(matched=False, kind="无金额候选"),
            RC.SummaryResult(),
            RC.Verdict("⚠️存疑", "金额差异"),
        )

        self.assertEqual(row["amt_consistency"], "合同部分读取失败且仍未对上")

    def test_only_consistent_samples_are_written_and_result_shape_stays_compatible(self):
        samples = [
            sample("1", 100.0, "合成供应方"),
            sample("2", 100.0, "合成供应方"),
            sample("3", 100.0, "合成供应方"),
        ]
        CapturingWorkpaper.reset(samples)
        synthetic_materials = {
            sid: type(
                "Materials",
                (),
                {"materials": [type("Material", (), {"mtype": "发票"})()]},
            )()
            for sid in ("1", "2", "3")
        }

        def extract(materials, _engine):
            sid = next(key for key, value in synthetic_materials.items() if value is materials)
            if sid == "1":
                return evidence(sid, amount=100.0, invoice_number=True)
            if sid == "2":
                return evidence(sid, amount=80.0, invoice_number=True)
            return evidence(sid, amount=100.0, invoice_number=False)

        with tempfile.TemporaryDirectory() as tmp:
            opts = TR.make_opts(input_dir=tmp, workpaper=os.path.join(tmp, "synthetic.xlsx"))
            with (
                patch.object(TR, "Workpaper", CapturingWorkpaper),
                patch.object(TR.M, "discover", return_value=synthetic_materials),
                patch.object(TR.EX, "extract_sample", side_effect=extract),
                patch.object(TR.RP, "write_xlsx", return_value=None),
                patch.object(TR.RP, "write_md", return_value=None),
            ):
                result = TR.run(opts, log=lambda _msg: None, engine_service=NoopService())

        self.assertEqual(
            {"ok": result["ok"], "warn": result["warn"], "bad": result["bad"]},
            {"ok": 1, "warn": 1, "bad": 1},
        )
        self.assertEqual(set(CapturingWorkpaper.fills_history[-1]), {"1"})
        self.assertEqual(
            set(result),
            {
                "ok",
                "warn",
                "bad",
                "total",
                "out_xlsx",
                "rep_xlsx",
                "rep_md",
                "status",
                "engine_available",
                "out_dir",
                "_column_hits",
            },
        )


if __name__ == "__main__":
    unittest.main()
