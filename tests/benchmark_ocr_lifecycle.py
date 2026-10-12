from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from support import SyntheticEngine


def legacy(iterations, init_delay):
    SyntheticEngine.reset()
    started = time.perf_counter()
    for _ in range(iterations):
        engine = SyntheticEngine()
        time.sleep(init_delay)
        engine.recognize(b"synthetic")
    return {
        "seconds": time.perf_counter() - started,
        "engine_objects": SyntheticEngine.created,
        "model_initializations": SyntheticEngine.initialized,
    }


def service_reuse(iterations, init_delay):
    from engines import service as service_module

    class DelayedSyntheticEngine(SyntheticEngine):
        def recognize(self, payload):
            if not self._initialized:
                time.sleep(init_delay)
            return super().recognize(payload)

    SyntheticEngine.reset()
    started = time.perf_counter()
    with patch.object(service_module, "HybridEngine", DelayedSyntheticEngine):
        service = service_module.OCREngineService()
        for _ in range(iterations):
            engine, _created = service.acquire(ocr_backend="auto")
            engine.recognize(b"synthetic")
    return {
        "seconds": time.perf_counter() - started,
        "engine_objects": SyntheticEngine.created,
        "model_initializations": SyntheticEngine.initialized,
    }


def main():
    parser = argparse.ArgumentParser(description="Synthetic OCR lifecycle microbenchmark")
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--init-delay", type=float, default=0.02)
    parser.add_argument("--legacy-only", action="store_true")
    args = parser.parse_args()

    result = {"iterations": args.iterations, "legacy": legacy(args.iterations, args.init_delay)}
    if not args.legacy_only:
        result["service"] = service_reuse(args.iterations, args.init_delay)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
