# -*- coding: utf-8 -*-
"""
OCR 引擎调度：PaddleOCR（本地），可选 RapidOCR 切换。
"""
from __future__ import annotations
import sys, os, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engines.base import OCREngine, RecognizeResult, EngineUnavailable
from engines.paddle_engine import PaddleEngine
from engines.rapid_engine import RapidOCREngine


class HybridEngine(OCREngine):
    name = "Hybrid"

    def __init__(self, lang="ch", fast=False, ocr_backend="auto", **kw):
        super().__init__()
        self._recognize_lock = threading.RLock()
        if ocr_backend == "rapid":
            self._ocr = RapidOCREngine(fast=fast)
        else:
            self._ocr = PaddleEngine(lang=lang, fast=fast)

    def _check(self):
        return True

    @property
    def available(self) -> bool:
        return self._ocr.available

    def status(self) -> str:
        with self._recognize_lock:
            p = self._ocr
            if p.available:
                dev = p._detect_device()
                if "·" in dev:
                    be, grade = dev.split("·", 1)
                    be = {"onnxruntime": "CPU·ONNX", "openvino": "OpenVINO"}.get(be, be)
                    label = f"{be}·{grade}"
                else:
                    label = {"gpu": "GPU加速", "cpu": "CPU"}.get(dev, dev)
                return f"PaddleOCR=就绪[{label}]"
            return f"PaddleOCR=不可用({p.unavailable_reason[:40]})"

    def recognize(self, image_png: bytes) -> RecognizeResult:
        # Paddle/Rapid 的底层推理对象不假定线程安全；同一实例保守串行化。
        with self._recognize_lock:
            return self._ocr.recognize(image_png)
