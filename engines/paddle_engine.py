# -*- coding: utf-8 -*-
"""
PaddleEngine：本地 PaddleOCR（PP-OCRv5, paddleocr 3.x）。
- HSV 红章抑制预处理（发票红章+防伪水印干扰）
- 文档方向分类（处理倒置/旋转扫描件）
- 复用 core.parse 做号码/金额/对方名称解析与硬校验
首次运行会从官网下载模型权重到本地（一次性），之后断网可跑（§9.3）。
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engines.base import OCREngine, RecognizeResult, EngineUnavailable
from engines.preprocess import suppress_red
from core import parse


class PaddleEngine(OCREngine):
    name = "PaddleOCR"

    def __init__(self, lang: str = "ch", use_orientation: bool = False, fast: bool = False):
        super().__init__()
        self._lang = lang
        self._use_orientation = use_orientation
        self._fast = fast      # True=PP-OCRv5 mobile 轻量模型（快 2-3×，清晰印刷票够用）
        self._ocr = None
        self.device = "cpu"    # 实际推理设备，_lazy_init 时确定

    def _detect_device(self) -> str:
        """有 GPU 用 GPU（快 5-6×），否则 CPU。"""
        try:
            import paddle
            if paddle.device.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0:
                return "gpu"
        except Exception:
            pass
        return "cpu"

    def _check(self):
        import paddleocr  # noqa
        import cv2  # noqa
        import numpy  # noqa

    def _lazy_init(self):
        if self._ocr is not None:
            return
        from paddleocr import PaddleOCR
        # enable_mkldnn=False：规避 paddle 3.3.x oneDNN 在 CPU 上的
        # "ConvertPirAttribute2RuntimeAttribute not support" 报错（关键！）
        # 方向分类按需开启（处理旋转/倒置扫描件），默认关以提速。
        import config as _cfg
        self.device = self._detect_device()
        kw = dict(
            lang=self._lang,
            device=self.device,
            enable_mkldnn=False,   # CPU 上规避 paddle3.3 oneDNN 崩溃；GPU 时此项被忽略
            text_det_limit_type="max",
            text_det_limit_side_len=_cfg.DEFAULTS["det_limit_side"],
            use_doc_orientation_classify=self._use_orientation,
            use_doc_unwarping=False,
            use_textline_orientation=self._use_orientation,
        )
        if self._fast:   # 轻量 mobile 模型，提速
            kw["text_detection_model_name"] = "PP-OCRv5_mobile_det"
            kw["text_recognition_model_name"] = "PP-OCRv5_mobile_rec"
        # 包内自带模型 → 拷到 ~/.paddlex（仅缺失时），让公司内网/断网机器免联网下载。
        self._ensure_local_models()
        self._ocr = PaddleOCR(**kw)

    @staticmethod
    def _ensure_local_models():
        """把包内 paddlex_models/ 的模型拷到 ~/.paddlex/official_models/（仅当缺失）。
        这样无网机器无需联网下载模型；用 PaddleOCR 默认加载路径，稳。"""
        import shutil
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = os.path.join(here, "paddlex_models")
        if not os.path.isdir(src):
            return
        dst = os.path.join(os.path.expanduser("~"), ".paddlex", "official_models")
        os.makedirs(dst, exist_ok=True)
        for name in os.listdir(src):
            s, d = os.path.join(src, name), os.path.join(dst, name)
            if os.path.isdir(s) and not os.path.isdir(d):
                try:
                    shutil.copytree(s, d, ignore=shutil.ignore_patterns(".cache"))
                except Exception:
                    pass

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

        texts, scores = [], []
        try:
            results = self._ocr.predict(arr)
            for res in results:
                try:
                    t = list(res["rec_texts"])
                    s = list(res["rec_scores"])
                except Exception:
                    # 兼容老接口
                    d = getattr(res, "json", {}) or {}
                    t = d.get("rec_texts", [])
                    s = d.get("rec_scores", [])
                texts.extend(t)
                scores.extend(s)
        except Exception as e:
            return RecognizeResult(
                engine=self.name, note=f"PaddleOCR 识别异常:{e}", succeeded=False)

        raw = "\n".join(texts)
        conf = float(sum(scores) / len(scores)) if scores else 0.0
        rr = parse.parse_all(raw, engine=self.name, confidence=conf)
        return rr
