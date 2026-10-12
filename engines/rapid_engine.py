# -*- coding: utf-8 -*-
"""
RapidOCREngine：本地 RapidOCR（PP-OCR 同族模型转 ONNX，跑在 ONNXRuntime / OpenVINO）。
专为**无 NVIDIA 独显的 Intel/AMD 机器**准备——避开 paddle 3.3.x 在 CPU 上
oneDNN 崩溃（被迫 enable_mkldnn=False 而变慢）的坑；onnxruntime 多核 CPU 推理更快，
**模型权重已打包进 wheel → 装好即可离线**（无需像 PaddleOCR 首次联网下模型）。

与 PaddleEngine 接口完全一致：recognize(png) → 复用 core.parse 做号码/金额/对方名解析。
红章抑制预处理复用 engines.preprocess.suppress_red。
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# OpenVINO 若被引入，关掉其使用遥测，守住"全程本地、无遥测"硬约束
os.environ.setdefault("OPENVINO_TELEMETRY_OPT_OUT", "1")

from engines.base import OCREngine, RecognizeResult, EngineUnavailable
from engines.preprocess import suppress_red
from core import parse


def _import_rapidocr():
    """优先 OpenVINO 后端(Intel 优化)，不可用则退 onnxruntime。返回 (RapidOCR类, 后端名)。"""
    try:
        from rapidocr_openvino import RapidOCR
        return RapidOCR, "openvino"
    except Exception:
        from rapidocr_onnxruntime import RapidOCR
        return RapidOCR, "onnxruntime"


class RapidOCREngine(OCREngine):
    name = "RapidOCR"

    def __init__(self, fast: bool = False):
        super().__init__()
        self._fast = fast
        self._ocr = None
        self.backend = "onnxruntime"   # 实际后端，_lazy_init 时确定
        self.device = "cpu"
        self.model_grade = ""          # server | mobile，_lazy_init 时确定

    def _server_models_present(self) -> bool:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return (os.path.exists(os.path.join(here, "models", "det_server.onnx"))
                and os.path.exists(os.path.join(here, "models", "rec_server.onnx")))

    def _detect_device(self) -> str:
        """供 status 显示：后端·模型档位（如 onnxruntime·server）。"""
        grade = self.model_grade or ("server" if (self._server_models_present() and not self._fast) else "mobile")
        return f"{self.backend}·{grade}"

    def _check(self):
        import cv2  # noqa
        import numpy  # noqa
        _import_rapidocr()  # 任一 RapidOCR 后端可导入即视为可用

    def _lazy_init(self):
        if self._ocr is not None:
            return
        RapidOCR, backend = _import_rapidocr()
        self.backend = backend
        self.model_grade = "mobile"     # RapidOCR 自带 PP-OCRv4 mobile（轻量、快）
        # 定位为"快速预览"：用默认配置追求速度。注意 mobile 模型对多发票求和
        # 场景会读错金额（实测），不可用于最终底稿；server 模型在 CPU 上 400s+/张
        # 慢到不可用，故不采用。需要准确请用默认 Paddle 后端。
        try:
            self._ocr = RapidOCR(text_score=0.4)
        except TypeError:
            self._ocr = RapidOCR()

    def recognize(self, image_png: bytes) -> RecognizeResult:
        if not self.available:
            raise EngineUnavailable(self.unavailable_reason)
        self._lazy_init()
        import cv2
        import numpy as np

        arr = cv2.imdecode(np.frombuffer(image_png, np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            return RecognizeResult(engine=self.name, note="图像解码失败", succeeded=False)
        arr = suppress_red(arr)

        try:
            out = self._ocr(arr)
            # RapidOCR 返回 (result, elapse)；result 为 [[box, text, score], ...] 或 None
            result = out[0] if isinstance(out, tuple) else out
        except Exception as e:
            return RecognizeResult(
                engine=self.name, note=f"RapidOCR 识别异常:{e}", succeeded=False)

        texts, scores = [], []
        for item in (result or []):
            try:
                texts.append(item[1])
                scores.append(float(item[2]))
            except Exception:
                continue

        raw = "\n".join(texts)
        conf = float(sum(scores) / len(scores)) if scores else 0.0
        return parse.parse_all(raw, engine=self.name, confidence=conf)
