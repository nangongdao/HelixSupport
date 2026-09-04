"""Gate B (1.4): API-key rotation drill — end-to-end, leaves a ledger record.

Exercises the SEC-004 lifecycle over the real HTTP path with a scratch
database: issue a runtime key (44.1 rotate endpoint), retire it via the
credential lifecycle (bounded overlap window), prove the old key still
authenticates inside the window, then revoke through the admin endpoint
and assert the old key is refused while a surviving key still works.

Writes ``supplychain/rotation-drills.json`` (schema_version 1) with the
run outcome so the Gate B `--check-today` machinery (threat_model_gate)
can one day consume per-drill timestamps.

Usage:
    python scripts/run_rotation_drill.py [--out supplychain/rotation-drills.json]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

# Same bootstrap as the other repo-root importers (frontend_gate, visual_gate,
# readme_screenshots, …). Without it a direct `python scripts/run_rotation_drill.py` — the
# invocation the docs and runbooks document — dies on `No module named 'app'`
# unless the package happens to be installed editable, which only CI does.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.credentials import key_ref_for
from app.db._util import utc_now
from app.main import create_app
from scripts._console import use_utf8_console

logger = logging.getLogger("helix")


class RotationDrill(unittest.TestCase):
    """One scripted rotation; fails the drill when any assertion breaks."""

    def setUp(self) -> None:
        # A scratch DB per run: the drill must never touch the dev database.
        self._tmp = tempfile.TemporaryDirectory()
        self.database_path = Path(self._tmp.name) / "drill.db"
        self.admin_key = "drill-admin-key-0123456789"
        self.survivor_key = "drill-survivor-key-0123456"
        settings = Settings(
            database_path=self.database_path,
            auth_mode="api_key",
            api_keys_json=json.dumps(
                {
                    self.admin_key: {
                        "tenant_id": "demo",
                        "actor_id": "admin.user",
                        "role": "admin",
                    },
                    self.survivor_key: {
                        "tenant_id": "demo",
                        "actor_id": "survivor",
                        "role": "operator",
                    },
                }
            ),
            rate_limit_per_minute=10000,
            docs_enabled=False,
        )
        self.app = create_app(settings)
        self.client = TestClient(self.app)
        self.admin_headers = {"X-API-Key": self.admin_key, "X-Tenant-Id": "demo"}
        self.services = self.app.state.services

    def tearDown(self) -> None:
        self.client.close()
        try:
            self.services.database.close()
        except Exception:
            pass
        self._tmp.cleanup()

    def test_rotation_drill(self) -> None:
        # 1. Issue a fresh runtime key through the real admin endpoint; the
        #    secret is returned exactly once, nothing else stores it.
        issued = self.client.post("/api/admin/keys", headers=self.admin_headers)
        self.assertEqual(issued.status_code, 201, issued.text)
        new_secret = issued.json()["secret"]
        new_cred_id = key_ref_for(new_secret)[:12]
        self.assertIsInstance(new_cred_id, str)

        # A fresh runtime key is not in the deployment config, so it cannot
        # authenticate until it is promoted there (the second rotation step
        # in the 41.1b runbook). Simulate the promotion by re-creating the
        # app with the new key declared in API_KEYS_JSON — the mirror of a
        # config push that re-seeds the registry idempotently.
        self.client.close()
        self.services.database.close()
        promoted_json = json.loads(
            json.dumps(
                {
                    self.admin_key: {
                        "tenant_id": "demo",
                        "actor_id": "admin.user",
                        "role": "admin",
                    },
                    self.survivor_key: {
                        "tenant_id": "demo",
                        "actor_id": "survivor",
                        "role": "operator",
                    },
                    new_secret: {
                        "tenant_id": "demo",
                        "actor_id": "rotated-service",
                        "role": "operator",
                    },
                }
            )
        )
        settings = Settings(
            database_path=self.database_path,
            auth_mode="api_key",
            api_keys_json=json.dumps(promoted_json),
            rate_limit_per_minute=10000,
            docs_enabled=False,
        )
        self.app = create_app(settings)
        self.client = TestClient(self.app)
        self.services = self.app.state.services

        new_headers = {"X-API-Key": new_secret, "X-Tenant-Id": "demo"}
        self.assertEqual(
            self.client.get("/api/me", headers=new_headers).status_code,
            200,
            "promoted runtime key must authenticate",
        )

        # 2. Retire the old key through the lifecycle (bounded overlap
        #    window): it stays accepted until the revoke step, so an
        #    in-flight client drains gracefully.
        store = self.services.credential_store
        lifecycle = self.services.credential_lifecycle
        old_cred_id = store.get(key_ref_for(self.admin_key)[:12])["credential_id"]
        lifecycle.rotate(old_cred_id, now=utc_now(), by="admin.user")
        self.assertEqual(
            self.client.get("/api/me", headers=self.admin_headers).status_code,
            200,
            "retired key must stay authenticated inside the overlap window",
        )

        # 3. Revoke the old key through the admin endpoint (emergency
        #    path), keep the issued key and a surviving config key valid.
        revoked = self.client.post(
            f"/api/admin/keys/{old_cred_id}/revoke", headers=self.admin_headers
        )
        self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assertEqual(
            self.client.get("/api/me", headers=self.admin_headers).status_code,
            401,
            "revoked key must be refused",
        )
        self.assertEqual(
            self.client.get("/api/me", headers=new_headers).status_code,
            200,
            "the replacement key must still authenticate",
        )
        self.assertEqual(
            self.client.get(
                "/api/me", headers={"X-API-Key": self.survivor_key, "X-Tenant-Id": "demo"}
            ).status_code,
            200,
            "an unrelated surviving key must be unaffected",
        )


def _record(out_path: Path, passed: bool, details: str) -> None:
    import datetime as dt

    now = dt.datetime.now(dt.UTC).isoformat()
    if out_path.exists():
        ledger = json.loads(out_path.read_text(encoding="utf-8"))
    else:
        ledger = {"schema_version": 1, "policy": "rotation drill ledger (Gate B)"}
    ledger.setdefault("drills", []).append(
        {
            "drill_type": "credential_rotation",
            "ran_at": now,
            "passed": passed,
            "details": details[:2000],
        }
    )
    out_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("rotation drill %s recorded in %s", "PASSED" if passed else "FAILED", out_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="supplychain/rotation-drills.json")
    args = parser.parse_args()

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(RotationDrill)
    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(suite)
    out = Path(args.out)
    _record(out, result.wasSuccessful(), f"{result.testsRun} run, {len(result.failures)} failures")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    use_utf8_console()
    raise SystemExit(main())
