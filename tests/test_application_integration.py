from __future__ import annotations

import ast
import pathlib
import unittest
from types import SimpleNamespace
from unittest.mock import ANY, Mock, patch

import tod_gui


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ApplicationIntegrationTests(unittest.TestCase):
    def test_gui_worker_injects_the_app_owned_service(self):
        queue = Mock()
        service = object()
        app = SimpleNamespace(q=queue, ocr_service=service)
        opts = object()

        with patch.object(tod_gui.TR, "run", return_value={"total": 0}) as run:
            tod_gui.App._work(app, opts, False)

        run.assert_called_once_with(opts, log=ANY, engine_service=service)
        queue.put.assert_called_once_with(("done", {"total": 0, "_open_after": False}))



if __name__ == "__main__":
    unittest.main()
