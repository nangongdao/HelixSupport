"""Web Chat client shell, framing policy and widget token boundaries."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


class WidgetClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "widget-client.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _client(self, ancestors=("'self'",)) -> TestClient:
        settings = Settings(
            database_path=self.db_path,
            auth_mode="demo",
            api_keys_json=json.dumps({}),
            docs_enabled=False,
            widget_frame_ancestors=ancestors,
        )
        return TestClient(create_app(settings))

    def test_widget_shell_allows_only_configured_ancestors(self) -> None:
        with self._client(("'self'", "https://help.example")) as client:
            response = client.get("/widget")
            self.assertEqual(response.status_code, 200)
            self.assertIn("widget-app.js", response.text)
            self.assertNotIn("X-Frame-Options", response.headers)
            self.assertIn(
                "frame-ancestors 'self' https://help.example",
                response.headers["content-security-policy"],
            )

            operator = client.get("/")
            self.assertEqual(operator.headers["x-frame-options"], "DENY")
            self.assertIn("frame-ancestors 'none'", operator.headers["content-security-policy"])

    def test_widget_frame_ancestor_validation_rejects_non_origin(self) -> None:
        with self.assertRaises(ValueError):
            Settings(widget_frame_ancestors=()).validate()
        with self.assertRaises(ValueError):
            Settings(widget_frame_ancestors=("https://help.example/path",)).validate()
        with self.assertRaises(ValueError):
            Settings(widget_frame_ancestors=("https://help.example; script-src *",)).validate()
        with self.assertRaises(ValueError):
            Settings(widget_frame_ancestors=("https://help.example'",)).validate()
        with self.assertRaises(ValueError):
            Settings(widget_frame_ancestors=("https://help.example:not-a-port",)).validate()
        with self.assertRaises(ValueError):
            Settings(widget_frame_ancestors=("https://name@help.example",)).validate()
        with self.assertRaises(ValueError):
            Settings(
                widget_frame_ancestors=("https://*.example",),
                app_env="production",
                auth_mode="api_key",
                api_keys_json='{"key":{"tenant_id":"demo"}}',
            ).validate()

    def test_env_normalizes_bare_self_frame_source(self) -> None:
        with patch.dict(
            os.environ, {"WIDGET_FRAME_ANCESTORS": "self,https://help.example"}, clear=True
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.widget_frame_ancestors, ("'self'", "https://help.example"))

    def test_widget_shell_is_cache_revalidated(self) -> None:
        with self._client() as client:
            response = client.get("/widget")
            self.assertEqual(response.headers["cache-control"], "no-cache")


if __name__ == "__main__":
    unittest.main()
