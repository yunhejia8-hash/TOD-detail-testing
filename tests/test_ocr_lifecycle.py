from __future__ import annotations

import importlib
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import tod_reconcile as TR
from engines.base import RecognizeResult
from engines.hybrid import HybridEngine
from support import CapturingWorkpaper, SyntheticEngine, evidence, sample


def load_service_module():
    return importlib.import_module("engines.service")


class OCREngineServiceTests(unittest.TestCase):
    def setUp(self):
        SyntheticEngine.reset()

    def test_repeated_acquire_creates_one_compatible_engine(self):
        service_module = load_service_module()
        with patch.object(service_module, "HybridEngine", SyntheticEngine):
            service = service_module.OCREngineService()
            first, first_created = service.acquire(ocr_backend="auto", fast=False)
            second, second_created = service.acquire(ocr_backend="paddle", fast=False)

        self.assertIs(first, second)
        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(SyntheticEngine.created, 1)
        self.assertEqual(service.cache_size, 1)

    def test_backend_and_model_configurations_are_not_mixed(self):
        service_module = load_service_module()
        with patch.object(service_module, "HybridEngine", SyntheticEngine):
            service = service_module.OCREngineService()
            paddle, _ = service.acquire(ocr_backend="paddle", fast=False)
            auto, _ = service.acquire(ocr_backend="auto", fast=False)
            rapid, _ = service.acquire(ocr_backend="rapid", fast=False)
            paddle_fast, _ = service.acquire(ocr_backend="paddle", fast=True)

        self.assertIs(paddle, auto)
        self.assertIsNot(paddle, rapid)
        self.assertIsNot(paddle, paddle_fast)
        self.assertEqual(SyntheticEngine.created, 3)

    def test_concurrent_first_acquire_creates_once(self):
        service_module = load_service_module()

        class SlowSyntheticEngine(SyntheticEngine):
            def __init__(self, *args, **kwargs):
                time.sleep(0.02)
                super().__init__(*args, **kwargs)

        engines = []
        created_flags = []
        barrier = threading.Barrier(8)

        with patch.object(service_module, "HybridEngine", SlowSyntheticEngine):
            service = service_module.OCREngineService()

            def acquire():
                barrier.wait()
                engine, created = service.acquire(ocr_backend="auto")
                engines.append(engine)
                created_flags.append(created)

            threads = [threading.Thread(target=acquire) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(len({id(engine) for engine in engines}), 1)
        self.assertEqual(sum(created_flags), 1)
        self.assertEqual(SlowSyntheticEngine.created, 1)

    def test_clear_releases_cached_references(self):
        service_module = load_service_module()
        with patch.object(service_module, "HybridEngine", SyntheticEngine):
            service = service_module.OCREngineService()
            first, _ = service.acquire(ocr_backend="rapid")
            service.clear()
            second, created = service.acquire(ocr_backend="rapid")

        self.assertIsNot(first, second)
        self.assertTrue(created)
        self.assertEqual(service.cache_size, 1)

    def test_cache_has_a_hard_configuration_limit(self):
        service_module = load_service_module()
        with patch.object(service_module, "HybridEngine", SyntheticEngine):
            service = service_module.OCREngineService(max_cache_size=2)
            service.acquire(ocr_backend="paddle", fast=False)
            service.acquire(ocr_backend="rapid", fast=False)
            with self.assertRaisesRegex(RuntimeError, "容量上限"):
                service.acquire(ocr_backend="paddle", fast=True)


class SharedInferenceLockTests(unittest.TestCase):
    def test_shared_hybrid_engine_serializes_recognition(self):
        class SlowBackend:
            def __init__(self, *_args, **_kwargs):
                self.available = True
                self.active = 0
                self.max_active = 0
                self.guard = threading.Lock()

            def recognize(self, _payload):
                with self.guard:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                time.sleep(0.03)
                with self.guard:
                    self.active -= 1
                return RecognizeResult(engine="slow")

        backend = SlowBackend()
        with patch("engines.hybrid.PaddleEngine", return_value=backend):
            engine = HybridEngine(ocr_backend="paddle")

        barrier = threading.Barrier(2)

        def recognize():
            barrier.wait()
            engine.recognize(b"synthetic")

        threads = [threading.Thread(target=recognize) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(backend.max_active, 1)

    def test_inference_exception_releases_lock_for_next_call(self):
        class FlakyBackend:
            available = True

            def __init__(self):
                self.calls = 0

            def recognize(self, _payload):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("synthetic inference failure")
                return RecognizeResult(engine="recovered")

        backend = FlakyBackend()
        with patch("engines.hybrid.PaddleEngine", return_value=backend):
            engine = HybridEngine(ocr_backend="paddle")

        with self.assertRaisesRegex(RuntimeError, "inference failure"):
            engine.recognize(b"first")
        result = engine.recognize(b"second")

        self.assertEqual(result.engine, "recovered")
        self.assertEqual(backend.calls, 2)


class ApplicationRunReuseTests(unittest.TestCase):
    def setUp(self):
        SyntheticEngine.reset()
        CapturingWorkpaper.reset([sample()])

    def _patch_run_dependencies(self, extract_side_effect):
        synthetic_materials = {
            "1": type("Materials", (), {"materials": [object()]})()
        }
        return (
            patch.object(TR, "Workpaper", CapturingWorkpaper),
            patch.object(TR.M, "discover", return_value=synthetic_materials),
            patch.object(TR.EX, "extract_sample", side_effect=extract_side_effect),
            patch.object(TR.RP, "write_xlsx", return_value=None),
            patch.object(TR.RP, "write_md", return_value=None),
        )

    def test_two_run_calls_reuse_one_initialized_engine(self):
        service_module = load_service_module()

        def extract(_materials, engine):
            engine.recognize(b"synthetic")
            return evidence()

        with tempfile.TemporaryDirectory() as tmp:
            opts = TR.make_opts(input_dir=tmp, workpaper=os.path.join(tmp, "synthetic.xlsx"))
            patches = self._patch_run_dependencies(extract)
            with patch.object(service_module, "HybridEngine", SyntheticEngine):
                service = service_module.OCREngineService()
                with patches[0], patches[1], patches[2], patches[3], patches[4]:
                    first = TR.run(opts, log=lambda _msg: None, engine_service=service)
                    second = TR.run(opts, log=lambda _msg: None, engine_service=service)

        self.assertEqual(first["total"], 1)
        self.assertEqual(second["total"], 1)
        self.assertEqual(SyntheticEngine.created, 1)
        self.assertEqual(SyntheticEngine.initialized, 1)
        self.assertEqual(SyntheticEngine.recognized, 2)

    def test_failed_first_task_does_not_break_second_task(self):
        service_module = load_service_module()
        calls = 0

        def extract(_materials, engine):
            nonlocal calls
            calls += 1
            engine.recognize(b"synthetic")
            if calls == 1:
                raise RuntimeError("synthetic first-task failure")
            return evidence()

        with tempfile.TemporaryDirectory() as tmp:
            opts = TR.make_opts(input_dir=tmp, workpaper=os.path.join(tmp, "synthetic.xlsx"))
            patches = self._patch_run_dependencies(extract)
            with patch.object(service_module, "HybridEngine", SyntheticEngine):
                service = service_module.OCREngineService()
                with patches[0], patches[1], patches[2], patches[3], patches[4]:
                    with self.assertRaisesRegex(RuntimeError, "first-task"):
                        TR.run(opts, log=lambda _msg: None, engine_service=service)
                    result = TR.run(opts, log=lambda _msg: None, engine_service=service)

        self.assertEqual(result["ok"], 1)
        self.assertEqual(SyntheticEngine.created, 1)
        self.assertEqual(SyntheticEngine.initialized, 1)


if __name__ == "__main__":
    unittest.main()
