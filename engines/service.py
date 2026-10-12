# -*- coding: utf-8 -*-
"""应用级 OCR 引擎生命周期服务。

服务只缓存 OCR 配置和引擎对象，不保存任务路径、识别文本、金额或证据。
"""
from __future__ import annotations

import threading

from engines.hybrid import HybridEngine


class OCREngineService:
    """在一个应用进程内复用兼容引擎，并提供显式释放边界。"""

    def __init__(self, max_cache_size=4):
        self._engines = {}
        self._lock = threading.RLock()
        self._max_cache_size = max(1, int(max_cache_size))
        self._created = 0
        self._reused = 0

    @staticmethod
    def normalize_backend(ocr_backend: str) -> str:
        # 保持 HybridEngine 的既有兼容行为：只有 rapid 走 Rapid，其余走 Paddle。
        return "rapid" if str(ocr_backend).lower() == "rapid" else "paddle"

    def acquire(self, *, lang="ch", fast=False, ocr_backend="auto"):
        backend = self.normalize_backend(ocr_backend)
        key = (backend, str(lang), bool(fast))
        with self._lock:
            engine = self._engines.get(key)
            if engine is not None:
                self._reused += 1
                return engine, False
            if len(self._engines) >= self._max_cache_size:
                raise RuntimeError(
                    f"OCR 引擎缓存已达到容量上限 {self._max_cache_size}，请先结束或清理应用服务"
                )
            engine = HybridEngine(lang=key[1], fast=key[2], ocr_backend=backend)
            self._engines[key] = engine
            self._created += 1
            return engine, True

    @property
    def cache_size(self) -> int:
        with self._lock:
            return len(self._engines)

    def stats(self) -> dict:
        with self._lock:
            return {
                "cache_size": len(self._engines),
                "created": self._created,
                "reused": self._reused,
            }

    def clear(self):
        """释放应用持有的强引用；正在执行的任务仍持有自己的局部引用。"""
        with self._lock:
            self._engines.clear()
