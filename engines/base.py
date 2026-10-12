# -*- coding: utf-8 -*-
"""
引擎抽象接口与统一结果数据结构。

所有引擎接收"单页图像的 PNG 字节"，返回 RecognizeResult。
这样 PyMuPDF 渲染出的页图可同时喂给 PaddleOCR（解码为 ndarray）和
Qwen3-VL（base64 编码），无需 poppler。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict


class EngineUnavailable(Exception):
    """引擎不可用（依赖未装 / 本地服务未启动）。捕获后走优雅降级。"""


@dataclass
class Amount:
    value: float
    kind: str = "未知"        # 含税 | 不含税 | 未知
    source: str = ""          # 来源材料类型：发票 | 付款单 | 合同

    def __repr__(self):
        return f"{self.value:.2f}({self.source or '?'}/{self.kind})"


@dataclass
class RecognizeResult:
    raw_text: str = ""
    doc_numbers: Dict[str, List[str]] = field(
        default_factory=lambda: {"发票": [], "合同": [], "付款单": []})
    amounts: List[Amount] = field(default_factory=list)
    counterparty: str = ""
    companies: List[str] = field(default_factory=list)
    confidence: float = 0.0
    engine: str = ""
    passed_validation: bool = False   # 是否至少有一个号码过硬校验
    conflict: bool = False            # 双引擎都过校验但结果不同（§6.5 存疑）
    note: str = ""
    dates: List[str] = field(default_factory=list)  # OCR 识别到的日期（YYYY-MM-DD 或 YYYY/MM/DD）
    succeeded: bool = True       # False 表示引擎已返回，但图像解码或推理实际失败

    def all_numbers_flat(self) -> List[str]:
        out = []
        for v in self.doc_numbers.values():
            out.extend(v)
        return out


class OCREngine:
    """引擎基类。子类需实现 _check()（可用性）与 recognize()。"""
    name: str = "base"

    def __init__(self):
        self._available = None

    @property
    def available(self) -> bool:
        if self._available is None:
            try:
                self._check()
                self._available = True
            except Exception as e:          # noqa
                self._available = False
                self._unavailable_reason = str(e)
        return self._available

    @property
    def unavailable_reason(self) -> str:
        return getattr(self, "_unavailable_reason", "")

    def _check(self):
        """子类检查依赖/服务是否就绪，未就绪抛 EngineUnavailable。"""
        raise NotImplementedError

    def recognize(self, image_png: bytes) -> RecognizeResult:
        raise NotImplementedError
