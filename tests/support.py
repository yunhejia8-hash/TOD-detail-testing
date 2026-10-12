from __future__ import annotations

from types import SimpleNamespace

from core.extract import SampleEvidence
from engines.base import Amount, RecognizeResult


class SyntheticEngine:
    """不加载模型、不保存业务内容的可计数假引擎。"""

    created = 0
    initialized = 0
    recognized = 0

    def __init__(self, lang="ch", fast=False, ocr_backend="auto", **_kwargs):
        SyntheticEngine.created += 1
        self.lang = lang
        self.fast = bool(fast)
        self.ocr_backend = ocr_backend
        self._initialized = False

    @classmethod
    def reset(cls):
        cls.created = 0
        cls.initialized = 0
        cls.recognized = 0

    @property
    def available(self):
        return True

    def status(self):
        return "SyntheticOCR=ready"

    def recognize(self, _image_png):
        if not self._initialized:
            SyntheticEngine.initialized += 1
            self._initialized = True
        SyntheticEngine.recognized += 1
        return RecognizeResult(engine="SyntheticOCR", confidence=1.0)


class CapturingWorkpaper:
    samples = []
    fills_history = []

    def __init__(self, path, sheet="", expect_overrides=None):
        self.path = path
        self.sheet = sheet
        self.expect_overrides = expect_overrides or {}

    @classmethod
    def reset(cls, samples):
        cls.samples = list(samples)
        cls.fills_history = []

    def analyze(self):
        return SimpleNamespace(
            sheet="Synthetic",
            header_row=1,
            columns={},
            warnings=[],
            samples=list(type(self).samples),
        )

    def write_filled(self, _layout, fills, _path, overwrite=False):
        snapshot = {sid: dict(values) for sid, values in fills.items()}
        type(self).fills_history.append(snapshot)


def sample(sid="1", amount=100.0, summary="合成供应方"):
    return SimpleNamespace(
        sid=str(sid),
        amount=amount,
        summary=summary,
        sample_date="",
    )


def evidence(
    sid="1",
    *,
    amount=100.0,
    invoice_number=True,
    counterparty="合成供应方",
):
    ev = SampleEvidence(sid=str(sid))
    if invoice_number:
        ev.invoice_numbers.append(f"SYNTH-{sid}")
    if amount is not None:
        ev.amounts.append(Amount(float(amount), "不含税", "发票"))
        ev.amount_by_source["发票"].add(float(amount))
    if counterparty:
        ev.counterparties.append(counterparty)
    ev.confidence = 1.0
    return ev
