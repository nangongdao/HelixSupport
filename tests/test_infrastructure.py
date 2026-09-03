"""Tests for migration framework, retention/PII, telemetry, queue, and session auth."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from packaging.requirements import Requirement
from packaging.version import Version

from app.config import Settings
from app.database import Database
from app.migrations import all_migrations, run_migrations
from app.queue import SQLiteTaskQueue, create_task_queue
from app.retention import DEFAULT_RETENTION_DAYS, PII_FIELDS, RetentionService, redact_pii
from app.security import Authenticator
from app.session_auth import (
    OIDCAuthenticator,
    OIDCConfig,
    SessionPrincipal,
    create_session_cookie,
    generate_session_id,
    generate_state,
    renew_session_cookie,
    verify_session_cookie,
)
from app.telemetry import TelemetryMetrics, span


class RuntimeDependencyLockTests(unittest.TestCase):
    def test_direct_runtime_dependencies_are_pinned(self) -> None:
        root = Path(__file__).resolve().parents[1]
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        direct = [Requirement(value) for value in project["project"]["dependencies"]]
        locked = {
            line.split("==", maxsplit=1)[0].strip().lower().replace("_", "-"): line.split(
                "==", maxsplit=1
            )[1]
            .split(";", maxsplit=1)[0]
            .strip()
            for line in (root / "requirements.lock").read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#") and "==" in line
        }
        missing: set[str] = set()
        incompatible: set[str] = set()
        for requirement in direct:
            name = re.sub(r"[-_.]+", "-", requirement.name).lower()
            locked_version = locked.get(name)
            if locked_version is None:
                missing.add(name)
            elif Version(locked_version) not in requirement.specifier:
                incompatible.add(f"{name}=={locked_version} not in {requirement.specifier}")
        self.assertEqual(missing, set(), "direct runtime dependencies must be pinned")
        self.assertEqual(incompatible, set(), "locked versions must satisfy project constraints")

    def test_uvicorn_standard_extra_has_linux_uvloop_pin(self) -> None:
        root = Path(__file__).resolve().parents[1]
        lock_lines = (root / "requirements.lock").read_text(encoding="utf-8").splitlines()
        uvloop_lines = [line for line in lock_lines if line.startswith("uvloop==")]
        self.assertEqual(uvloop_lines, ['uvloop==0.22.1; platform_system != "Windows"'])


class MigrationFrameworkTests(unittest.TestCase):
    def test_migrations_are_registered_and_ordered(self) -> None:
        migrations = all_migrations()
        self.assertGreater(len(migrations), 0)
        versions = [m.version for m in migrations]
        self.assertEqual(versions, sorted(versions))
        self.assertEqual(len(versions), len(set(versions)))

    def test_fresh_database_runs_all_migrations(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = Path(f.name)
        try:
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            result = run_migrations(conn, all_migrations())
            self.assertGreater(len(result.applied), 0)
            applied = {
                r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()
            }
            self.assertEqual(applied, {m.version for m in all_migrations()})
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            self.assertIn("conversations", tables)
            self.assertIn("schema_migrations", tables)
            self.assertIn("retention_policies", tables)
            conn.close()
        finally:
            path.unlink(missing_ok=True)

    def test_legacy_database_is_auto_detected(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = Path(f.name)
        try:
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            conn.execute("CREATE TABLE tenants (id TEXT PRIMARY KEY, name TEXT, created_at TEXT)")
            conn.execute(
                "CREATE TABLE conversations (id TEXT, tenant_id TEXT, customer_name TEXT, "
                "channel TEXT, status TEXT, priority TEXT DEFAULT 'normal', "
                "first_response_at TEXT, created_at TEXT, updated_at TEXT)"
            )
            conn.commit()
            result = run_migrations(conn, all_migrations())
            self.assertGreater(len(result.skipped), 0)
            conn.close()
        finally:
            path.unlink(missing_ok=True)

    def test_legacy_messages_get_seq_backfilled(self) -> None:
        """A pre-``seq`` database gets the column backfilled in insertion order.

        The upgrade path runs migration 4 on a legacy schema (migrations 1-3 are
        skipped for it), so the ALTER + backfill + trigger must all work on a
        messages table that predates the column.  Two rows sharing a timestamp
        must keep their insertion order via the backfilled ``seq``.
        """
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = Path(f.name)
        try:
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE tenants (id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    created_at TEXT NOT NULL);
                CREATE TABLE conversations (
                    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                    customer_name TEXT NOT NULL, channel TEXT NOT NULL,
                    status TEXT NOT NULL, priority TEXT NOT NULL DEFAULT 'normal',
                    version INTEGER NOT NULL DEFAULT 1, preview TEXT,
                    message_count INTEGER NOT NULL DEFAULT 0,
                    labels_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    first_response_at TEXT
                );
                CREATE TABLE messages (
                    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL, turn_id TEXT, role TEXT NOT NULL,
                    author TEXT NOT NULL, content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
                );
                CREATE TABLE audit_events (
                    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                    conversation_id TEXT, request_id TEXT, actor TEXT NOT NULL,
                    event_type TEXT NOT NULL, payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            # Same-instant inserts; only rowid (insertion order) can break the tie.
            conn.execute(
                "INSERT INTO messages (id, tenant_id, conversation_id, role, author, "
                "content, created_at) VALUES ('m1','t','c','customer','C','first',"
                "'2026-01-01T00:00:00Z')"
            )
            conn.execute(
                "INSERT INTO messages (id, tenant_id, conversation_id, role, author, "
                "content, created_at) VALUES ('m2','t','c','customer','C','second',"
                "'2026-01-01T00:00:00Z')"
            )
            conn.commit()
            run_migrations(conn, all_migrations())
            rows = conn.execute("SELECT content, seq FROM messages ORDER BY seq").fetchall()
            self.assertEqual([r["content"] for r in rows], ["first", "second"])
            self.assertEqual([r["seq"] for r in rows], [1, 2])
            # The installed trigger keeps new rows monotonic.
            conn.execute(
                "INSERT INTO messages (id, tenant_id, conversation_id, role, author, "
                "content, created_at) VALUES ('m3','t','c','customer','C','third',"
                "'2026-01-01T00:00:01Z')"
            )
            conn.commit()
            third = conn.execute("SELECT seq FROM messages WHERE id = 'm3'").fetchone()
            self.assertEqual(third["seq"], 3)
            conn.close()
        finally:
            path.unlink(missing_ok=True)


class SecretsConfigTests(unittest.TestCase):
    """API keys loaded from a secrets file instead of the environment."""

    @staticmethod
    def _write_keys(path: Path, key: str, actor: str) -> None:
        path.write_text(
            json.dumps(
                {
                    key: {
                        "tenant_id": "demo",
                        "actor_id": actor,
                        "role": "admin",
                    }
                }
            ),
            encoding="utf-8",
        )

    def test_api_keys_file_is_used_for_authentication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            secrets = Path(tmp) / "api_keys.json"
            self._write_keys(secrets, "file-key-000000000", "file-user")
            settings = Settings(
                database_path=Path(tmp) / "s.db",
                auth_mode="api_key",
                api_keys_file=secrets,
            )
            settings.validate()
            auth = Authenticator(settings)
            principal = auth.authenticate("file-key-000000000", None)
            self.assertEqual(principal.actor_id, "file-user")
            self.assertEqual(set(auth.configured_tenants), {"demo"})

    def test_api_keys_file_takes_precedence_over_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            secrets = Path(tmp) / "api_keys.json"
            self._write_keys(secrets, "file-key-000000000", "file-user")
            settings = Settings(
                database_path=Path(tmp) / "s.db",
                auth_mode="api_key",
                api_keys_json=json.dumps(
                    {
                        "env-key-000000000": {
                            "tenant_id": "demo",
                            "actor_id": "env-user",
                            "role": "admin",
                        }
                    }
                ),
                api_keys_file=secrets,
            )
            auth = Authenticator(settings)
            self.assertEqual(auth.authenticate("file-key-000000000", None).actor_id, "file-user")
            with self.assertRaises(Exception):
                auth.authenticate("env-key-000000000", None)

    def test_missing_or_malformed_secrets_file_fails_fast(self) -> None:
        settings = Settings(auth_mode="api_key", api_keys_file=Path("no-such-file.json"))
        with self.assertRaises(ValueError):
            settings.effective_api_keys_json
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                Settings(auth_mode="api_key", api_keys_file=bad).effective_api_keys_json

    def test_production_guard_accepts_file_based_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            secrets = Path(tmp) / "api_keys.json"
            self._write_keys(secrets, "prod-key-00000000", "prod-user")
            settings = Settings(
                app_env="production",
                auth_mode="api_key",
                api_keys_file=secrets,
            )
            settings.validate()  # must not raise: keys come from the file


class RetentionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db = Database(Path(self._tmp.name))
        self.db.initialize()
        self.db.ensure_tenant("test-tenant")
        self.service = RetentionService(self.db)

    def tearDown(self) -> None:
        self.db.close()
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_set_and_get_retention_policy(self) -> None:
        self.service.set_retention_policy("test-tenant", "messages", 90, "admin")
        policies = self.service.get_retention_policies("test-tenant")
        self.assertEqual(len(policies), 1)
        self.assertEqual(policies[0]["retention_days"], 90)

    def test_effective_retention_uses_defaults(self) -> None:
        days = self.service.effective_retention_days("test-tenant", "messages")
        self.assertEqual(days, DEFAULT_RETENTION_DAYS["messages"])

    def test_enforce_retention_deletes_old_records(self) -> None:
        self.db.create_conversation("test-tenant", "Customer", None, "web", "admin", 120)
        with self.db.connect() as conn:
            conv_id = conn.execute(
                "SELECT id FROM conversations WHERE tenant_id = 'test-tenant' LIMIT 1"
            ).fetchone()[0]
        self.db.add_message("test-tenant", conv_id, "customer", "Customer", "old")
        # Manually backdate the message
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE messages SET created_at = '2020-01-01T00:00:00+00:00' "
                "WHERE conversation_id = ?",
                (conv_id,),
            )
        deleted = self.service.enforce_retention("test-tenant", "messages", retention_days=30)
        self.assertGreaterEqual(deleted, 1)

    def test_audit_retention_archives_before_deleting_and_keeps_chain_verifiable(self) -> None:
        self.db.audit("test-tenant", None, "admin", "retention.one", {"safe": True})
        self.db.audit("test-tenant", None, "admin", "retention.two", {"safe": True})

        deleted = self.service.enforce_retention("test-tenant", "audit_events", retention_days=-1)
        self.assertEqual(deleted, 2)
        with self.db.connect() as conn:
            hot_count = conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE tenant_id = ?",
                ("test-tenant",),
            ).fetchone()[0]
            archive = conn.execute(
                "SELECT * FROM audit_archives WHERE tenant_id = ?",
                ("test-tenant",),
            ).fetchone()
        self.assertEqual(hot_count, 0)
        self.assertIsNotNone(archive)
        assert archive is not None
        document = json.loads(archive["archive_json"])
        self.assertEqual(len(document["events"]), 2)
        self.assertEqual(
            hashlib.sha256(archive["archive_json"].encode("utf-8")).hexdigest(),
            archive["content_sha256"],
        )

        from app.audit_chain import verify_chain
        from scripts.verify_audit_chain import load_rows

        connection = sqlite3.connect(self.db.path)
        try:
            rows = load_rows(connection)
        finally:
            connection.close()
        self.assertEqual(verify_chain(rows), [])

    def test_audit_chain_continues_after_hot_table_is_fully_archived(self) -> None:
        from app.audit_chain import verify_chain
        from scripts.verify_audit_chain import load_rows

        self.db.audit("test-tenant", None, "admin", "retention.old", {})
        self.service.enforce_retention("test-tenant", "audit_events", retention_days=-1)
        self.db.audit("test-tenant", None, "admin", "retention.new", {})

        with self.db.connect() as connection:
            rows = load_rows(connection)
        self.assertEqual([row["event_type"] for row in rows], ["retention.old", "retention.new"])
        self.assertEqual([int(row["seq"]) for row in rows], [1, 2])
        self.assertEqual(rows[1]["prev_hash"], rows[0]["event_hash"])
        self.assertEqual(verify_chain(rows), [])

    def test_audit_archive_manifest_tampering_is_rejected(self) -> None:
        self.db.audit("test-tenant", None, "admin", "retention.tamper", {})
        self.service.enforce_retention("test-tenant", "audit_events", retention_days=-1)
        archive_id = self.db.list_audit_archives("test-tenant")[0]["id"]
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE audit_archives SET first_event_hash = ? WHERE id = ?",
                ("0" * 64, archive_id),
            )
        with self.assertRaisesRegex(ValueError, "first hash mismatch"):
            self.db.get_audit_archive("test-tenant", archive_id)

    def test_audit_archive_event_tampering_is_rejected_after_digest_rewrite(self) -> None:
        self.db.audit("test-tenant", None, "admin", "retention.tamper", {"safe": True})
        self.service.enforce_retention("test-tenant", "audit_events", retention_days=-1)
        archive_id = self.db.list_audit_archives("test-tenant")[0]["id"]
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT archive_json FROM audit_archives WHERE id = ?",
                (archive_id,),
            ).fetchone()
            assert row is not None
            document = json.loads(row["archive_json"])
            document["events"][0]["payload_json"] = json.dumps({"safe": False})
            archive_json = json.dumps(
                document,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            digest = hashlib.sha256(archive_json.encode("utf-8")).hexdigest()
            connection.execute(
                "UPDATE audit_archives SET archive_json = ?, content_sha256 = ? WHERE id = ?",
                (archive_json, digest, archive_id),
            )
        with self.assertRaisesRegex(ValueError, "event hash mismatch"):
            self.db.get_audit_archive("test-tenant", archive_id)

    def test_audit_archive_validation_failure_preserves_hot_rows(self) -> None:
        self.db.audit("test-tenant", None, "admin", "retention.validation", {})
        with (
            patch(
                "app.audit_chain.validate_audit_archive",
                side_effect=ValueError("forced validation failure"),
            ),
            self.assertRaisesRegex(ValueError, "forced validation failure"),
        ):
            self.service.enforce_retention("test-tenant", "audit_events", retention_days=-1)

        with self.db.connect() as connection:
            hot_count = connection.execute(
                "SELECT COUNT(*) FROM audit_events WHERE tenant_id = ?",
                ("test-tenant",),
            ).fetchone()[0]
            archive_count = connection.execute(
                "SELECT COUNT(*) FROM audit_archives WHERE tenant_id = ?",
                ("test-tenant",),
            ).fetchone()[0]
        self.assertEqual(hot_count, 1)
        self.assertEqual(archive_count, 0)

    def test_data_subject_deletion(self) -> None:
        self.db.create_conversation("test-tenant", "Customer A", "CUST-DSR-1", "web", "admin", 120)
        self.db.create_conversation("test-tenant", "Customer A", "CUST-DSR-1", "web", "admin", 120)
        self.db.create_conversation("test-tenant", "Customer B", "CUST-DSR-2", "web", "admin", 120)
        with self.db.connect() as conn:
            conv_ids = [
                row[0]
                for row in conn.execute(
                    "SELECT id FROM conversations WHERE tenant_id = 'test-tenant' AND customer_ref = 'CUST-DSR-1'"
                ).fetchall()
            ]
        self.db.add_message("test-tenant", conv_ids[0], "customer", "Customer A", "hi")
        self.db.audit(
            "test-tenant",
            conv_ids[0],
            "admin",
            "dsr.archive.test",
            {"customer_ref": "CUST-DSR-1"},
        )
        self.service.enforce_retention("test-tenant", "audit_events", retention_days=-1)
        counts = self.service.execute_data_subject_deletion("test-tenant", "CUST-DSR-1")
        self.assertEqual(counts["conversations"], 2)
        self.assertEqual(counts["messages"], 1)
        self.assertEqual(counts["audit_events"], 0)
        self.assertEqual(counts["audit_archives"], 0)
        # Audit evidence follows its independent retention policy and remains
        # verifiable after operational customer data has been erased.
        archive = self.db.list_audit_archives("test-tenant")
        self.assertEqual(len(archive), 1)
        from app.audit_chain import verify_chain
        from scripts.verify_audit_chain import load_rows

        with self.db.connect() as connection:
            rows = load_rows(connection)
        self.assertEqual(verify_chain(rows), [])
        # Customer B's conversation is untouched
        with self.db.connect() as conn:
            remaining = conn.execute(
                "SELECT COUNT(*) FROM conversations WHERE tenant_id = 'test-tenant' AND customer_ref = 'CUST-DSR-2'"
            ).fetchone()[0]
        self.assertEqual(remaining, 1)

    def test_data_subject_export(self) -> None:
        self.db.create_conversation("test-tenant", "Customer C", "CUST-EXP", "web", "admin", 120)
        with self.db.connect() as conn:
            conv_id = conn.execute(
                "SELECT id FROM conversations WHERE tenant_id = 'test-tenant' AND customer_ref = 'CUST-EXP'"
            ).fetchone()[0]
        self.db.add_message("test-tenant", conv_id, "customer", "Customer C", "data")
        export = self.service.execute_data_subject_export("test-tenant", "CUST-EXP")
        self.assertEqual(len(export["conversations"]), 1)
        self.assertEqual(len(export["messages"]), 1)

    def test_invalid_data_type_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.set_retention_policy("test-tenant", "bogus", 30, "admin")

    def test_invalid_retention_days_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.set_retention_policy("test-tenant", "messages", 0, "admin")
        with self.assertRaises(ValueError):
            self.service.set_retention_policy("test-tenant", "messages", 99999, "admin")


class PIIRedactionTests(unittest.TestCase):
    def test_redact_simple_dict(self) -> None:
        data: dict[str, Any] = {"customer_name": "张三", "content": "密码123", "id": "conv-1"}
        result = redact_pii(data)
        assert isinstance(result, dict)
        self.assertEqual(result["customer_name"], "[REDACTED]")
        self.assertEqual(result["content"], "[REDACTED]")
        self.assertEqual(result["id"], "conv-1")

    def test_redact_nested(self) -> None:
        data: dict[str, Any] = {"outer": {"customer_name": "李四", "safe": "ok"}, "author": "王五"}
        result = redact_pii(data)
        assert isinstance(result, dict)
        outer = result["outer"]
        assert isinstance(outer, dict)
        self.assertEqual(outer["customer_name"], "[REDACTED]")
        self.assertEqual(outer["safe"], "ok")
        self.assertEqual(result["author"], "[REDACTED]")

    def test_redact_list(self) -> None:
        data: list[dict[str, Any]] = [{"customer_name": "A"}, {"customer_name": "B"}]
        result = redact_pii(data)
        assert isinstance(result, list)
        self.assertEqual(len(result), 2)
        for item in result:
            assert isinstance(item, dict)
            self.assertEqual(item["customer_name"], "[REDACTED]")

    def test_pii_fields_covers_critical_fields(self) -> None:
        for field in ("customer_name", "customer_ref", "content", "author", "actor"):
            self.assertIn(field, PII_FIELDS)


class TelemetryTests(unittest.TestCase):
    def test_metrics_increment_and_observe(self) -> None:
        m = TelemetryMetrics()
        m.increment("requests", route="/api/test")
        m.increment("requests", route="/api/test")
        m.observe("latency_ms", 42.0, route="/api/test")
        snap = m.snapshot()
        self.assertEqual(snap["counters"]["requests{route=/api/test}"], 2)
        self.assertEqual(snap["histograms"]["latency_ms{route=/api/test}"]["count"], 1)
        self.assertEqual(snap["histograms"]["latency_ms{route=/api/test}"]["avg"], 42.0)

    def test_span_records_duration(self) -> None:
        with span("test_operation", attr1="value1") as s:
            time.sleep(0.05)
            s.add_event("midpoint")
        self.assertIsNotNone(s.end_time)
        self.assertGreater(s.duration_ms or 0, 0)
        self.assertEqual(s.attributes["attr1"], "value1")
        self.assertEqual(len(s.events), 1)
        self.assertEqual(s.events[0]["name"], "midpoint")

    def test_span_nesting(self) -> None:
        with span("parent") as p, span("child") as c:
            pass
        self.assertIsNone(p.parent)
        self.assertIs(c.parent, p)


class SessionAuthTests(unittest.TestCase):
    def test_create_and_verify_cookie(self) -> None:
        config = OIDCConfig(session_secret="test-secret-key-for-testing-only")
        principal = SessionPrincipal(
            tenant_id="tenant-a",
            actor_id="user-1",
            role="admin",
            session_id=generate_session_id(),
            expires_at=int(time.time()) + 3600,
        )
        cookie = create_session_cookie(principal, config)
        verified = verify_session_cookie(cookie, config)
        assert verified is not None
        self.assertEqual(verified.tenant_id, "tenant-a")
        self.assertEqual(verified.actor_id, "user-1")
        self.assertEqual(verified.role, "admin")

    def test_expired_cookie_rejected(self) -> None:
        config = OIDCConfig(session_secret="test-secret-key-for-testing-only")
        principal = SessionPrincipal(
            tenant_id="t",
            actor_id="u",
            role="admin",
            session_id="sid",
            expires_at=int(time.time()) - 1,
        )
        cookie = create_session_cookie(principal, config)
        self.assertIsNone(verify_session_cookie(cookie, config))

    def test_tampered_cookie_rejected(self) -> None:
        config = OIDCConfig(session_secret="test-secret-key-for-testing-only")
        principal = SessionPrincipal(
            tenant_id="t",
            actor_id="u",
            role="admin",
            session_id="sid",
            expires_at=int(time.time()) + 3600,
        )
        cookie = create_session_cookie(principal, config)
        tampered = cookie[:-1] + ("a" if cookie[-1] != "a" else "b")
        self.assertIsNone(verify_session_cookie(tampered, config))

    def test_wrong_secret_rejected(self) -> None:
        config1 = OIDCConfig(session_secret="secret-one")
        config2 = OIDCConfig(session_secret="secret-two")
        principal = SessionPrincipal(
            tenant_id="t",
            actor_id="u",
            role="admin",
            session_id="sid",
            expires_at=int(time.time()) + 3600,
        )
        cookie = create_session_cookie(principal, config1)
        self.assertIsNone(verify_session_cookie(cookie, config2))

    def test_state_generation_is_unique(self) -> None:
        states = {generate_state() for _ in range(100)}
        self.assertEqual(len(states), 100)

    def test_oidc_config_from_env(self) -> None:
        import os

        keys = ("OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET", "SESSION_SECRET", "OIDC_DISCOVERY_URL")
        old_vals = {k: os.environ.get(k) for k in keys}
        try:
            os.environ["OIDC_CLIENT_ID"] = "test-client"
            os.environ["OIDC_CLIENT_SECRET"] = "test-secret"
            os.environ["SESSION_SECRET"] = "session-secret"
            os.environ["OIDC_DISCOVERY_URL"] = "https://idp.example.com"
            config = OIDCConfig.from_env()
            self.assertTrue(config.is_configured)
        finally:
            for k, v in old_vals.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    @staticmethod
    def _make_id_token(claims: dict[str, Any]) -> str:
        header = (
            base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode())
            .decode()
            .rstrip("=")
        )
        payload = (
            base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode())
            .decode()
            .rstrip("=")
        )
        return f"{header}.{payload}.signature"

    def test_unsigned_id_token_rejected_by_flow(self) -> None:
        """M0 SEC-001: an alg=none token must not establish a session.

        The old decode-only path trusted unsigned claims; the verified flow
        rejects them before any claim is read.
        """
        from app.oidc_flow import RazorThinJwtError, _assert_rs256_algorithm

        claims = {
            "sub": "user-42",
            "preferred_username": "alice",
            "helix_tenant": "acme",
            "helix_role": "operator",
            "iss": "https://idp.example.com",
            "aud": "test-client",
            "exp": int(time.time()) + 600,
            "iat": int(time.time()),
        }
        token = self._make_id_token(claims)
        # Signature verification itself (alg=none header) is rejected.
        with self.assertRaises(RazorThinJwtError):
            _assert_rs256_algorithm({"alg": "none"})
        # The full RS256 verifier rejects a token with an alg=none header.
        from app.oidc_flow import verify_rs256_signature

        with self.assertRaises(RazorThinJwtError):
            verify_rs256_signature(token, modulus=2**2048 + 1, exponent=3)

    def test_renew_session_cookie_extends_expiry(self) -> None:
        config = OIDCConfig(
            session_secret="test-secret-key-for-testing-only", session_ttl_minutes=10
        )
        principal = SessionPrincipal(
            tenant_id="tenant-a",
            actor_id="user-1",
            role="admin",
            session_id="sid-1",
            expires_at=int(time.time()) + 300,
        )
        cookie = create_session_cookie(principal, config)
        renewed = renew_session_cookie(cookie, config)
        assert renewed is not None
        renewed_principal, renewed_cookie = renewed
        self.assertEqual(renewed_principal.session_id, "sid-1")
        self.assertEqual(renewed_principal.actor_id, "user-1")
        self.assertEqual(renewed_principal.tenant_id, "tenant-a")
        self.assertGreater(renewed_principal.expires_at, principal.expires_at)
        verified = verify_session_cookie(renewed_cookie, config)
        assert verified is not None
        self.assertEqual(verified.expires_at, renewed_principal.expires_at)

    def test_renew_expired_cookie_rejected(self) -> None:
        config = OIDCConfig(
            session_secret="test-secret-key-for-testing-only", session_ttl_minutes=10
        )
        principal = SessionPrincipal(
            tenant_id="t",
            actor_id="u",
            role="admin",
            session_id="sid",
            expires_at=int(time.time()) - 1,
        )
        cookie = create_session_cookie(principal, config)
        self.assertIsNone(renew_session_cookie(cookie, config))

    def test_renew_enforces_max_lifetime(self) -> None:
        config = OIDCConfig(
            session_secret="test-secret-key-for-testing-only",
            session_ttl_minutes=10,
            session_max_lifetime_minutes=5,
        )
        # Issued 10 minutes ago, still unexpired: beyond the 5-minute cap.
        old = SessionPrincipal(
            tenant_id="t",
            actor_id="u",
            role="admin",
            session_id="sid",
            expires_at=int(time.time()) - 300 + 600,
        )
        old_cookie = create_session_cookie(old, config, issued_at=int(time.time()) - 600)
        self.assertIsNone(renew_session_cookie(old_cookie, config))
        # A fresh session inside the cap renews fine.
        fresh = SessionPrincipal(
            tenant_id="t",
            actor_id="u",
            role="admin",
            session_id="sid-2",
            expires_at=int(time.time()) + 600,
        )
        fresh_cookie = create_session_cookie(fresh, config, issued_at=int(time.time()))
        self.assertIsNotNone(renew_session_cookie(fresh_cookie, config))


class QueueAbstractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db = Database(Path(self._tmp.name))
        self.db.initialize()
        self.db.ensure_tenant("test-tenant")

    def tearDown(self) -> None:
        self.db.close()
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_sqlite_queue_protocol(self) -> None:
        queue = SQLiteTaskQueue(self.db)
        self.db.create_conversation("test-tenant", "Customer", None, "web", "admin", 120)
        with self.db.connect() as conn:
            conv_id = conn.execute(
                "SELECT id FROM conversations WHERE tenant_id = 'test-tenant' LIMIT 1"
            ).fetchone()[0]
        job, _ = queue.enqueue("test-tenant", conv_id, "idem-q-1", "actor-1", "test", 3)
        self.assertTrue(job["id"].startswith("job_"))
        self.assertEqual(job["conversation_id"], conv_id)
        claimed = queue.dequeue("worker-1", 300)
        assert claimed is not None
        self.assertEqual(claimed["id"], job["id"])
        success = queue.complete(job["id"], "worker-1", {"result": "ok"}, 300)
        self.assertTrue(success)
        job_after = self.db.get_turn_job("test-tenant", job["id"])
        assert job_after is not None
        self.assertEqual(job_after["status"], "completed")

    def test_create_task_queue_defaults_to_sqlite(self) -> None:
        settings = Settings()
        queue = create_task_queue(self.db, settings)
        self.assertIsInstance(queue, SQLiteTaskQueue)

    def test_create_task_queue_redis_fallback(self) -> None:
        settings = Settings(queue_backend="redis")
        queue = create_task_queue(self.db, settings)
        # Redis may or may not be available in test env; either is acceptable
        from app.queue import RedisTaskQueue, SQLiteTaskQueue

        self.assertIsInstance(queue, (SQLiteTaskQueue, RedisTaskQueue))


class RepositoryProtocolTests(unittest.TestCase):
    """Verify the Database class satisfies the repository protocols."""

    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db = Database(Path(self._tmp.name))
        self.db.initialize()

    def tearDown(self) -> None:
        self.db.close()
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_database_satisfies_conversation_repository(self) -> None:
        from app.repositories import ConversationRepository

        self.assertIsInstance(self.db, ConversationRepository)

    def test_database_satisfies_message_repository(self) -> None:
        from app.repositories import MessageRepository

        self.assertIsInstance(self.db, MessageRepository)

    def test_database_satisfies_knowledge_repository(self) -> None:
        from app.repositories import KnowledgeRepository

        self.assertIsInstance(self.db, KnowledgeRepository)

    def test_database_satisfies_turn_job_repository(self) -> None:
        from app.repositories import TurnJobRepository

        self.assertIsInstance(self.db, TurnJobRepository)

    def test_database_satisfies_audit_repository(self) -> None:
        from app.repositories import AuditRepository

        self.assertIsInstance(self.db, AuditRepository)

    def test_database_satisfies_feedback_repository(self) -> None:
        from app.repositories import FeedbackRepository

        self.assertIsInstance(self.db, FeedbackRepository)

    def test_database_satisfies_tenant_repository(self) -> None:
        from app.repositories import TenantRepository

        self.assertIsInstance(self.db, TenantRepository)


class AuditChainTests(unittest.TestCase):
    """Phase 28.3: audit events form a tamper-evident hash chain."""

    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db = Database(Path(self._tmp.name))
        self.db.initialize()
        self.db.ensure_tenant("demo")

    def tearDown(self) -> None:
        self.db.close()
        Path(self._tmp.name).unlink(missing_ok=True)

    def _rows(self) -> list[dict]:
        from scripts.verify_audit_chain import load_rows

        with self.db.connect() as conn:
            return load_rows(conn)

    def test_chain_is_intact_after_writes(self) -> None:
        from app.audit_chain import verify_chain

        self.db.audit("demo", None, "admin", "a", {"n": 1})
        self.db.audit("demo", "conv-1", "admin", "b", {"n": 2})
        self.db.audit_many("demo", "admin", [("conv-1", "m1", {"x": 1})])
        rows = self._rows()
        self.assertGreaterEqual(len(rows), 3)
        self.assertEqual(verify_chain(rows), [])

    def test_tampered_payload_is_detected(self) -> None:
        from app.audit_chain import verify_chain

        self.db.audit("demo", None, "admin", "a", {"secret": "value"})
        self.db.audit("demo", None, "admin", "b", {"n": 2})
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE audit_events SET payload_json = ? WHERE event_type = 'a'",
                ('{"secret": "tampered"}',),
            )
            conn.commit()
        problems = verify_chain(self._rows())
        self.assertTrue(problems, "tampered audit row must be detected")

    def test_tampered_actor_is_detected(self) -> None:
        from app.audit_chain import verify_chain

        self.db.audit("demo", None, "admin", "a", {})
        self.db.audit("demo", None, "admin", "b", {})
        with self.db.connect() as conn:
            conn.execute("UPDATE audit_events SET actor = 'evil' WHERE event_type = 'b'")
            conn.commit()
        problems = verify_chain(self._rows())
        self.assertTrue(problems, "tampered actor must be detected")

    def test_chain_head_tracks_last_event(self) -> None:
        from app.audit_chain import chain_head

        self.db.audit("demo", None, "admin", "a", {})
        self.db.audit("demo", None, "admin", "b", {})
        rows = self._rows()
        head = chain_head(rows)
        self.assertIsNotNone(head)
        self.assertEqual(head, rows[-1]["event_hash"])

    def test_concurrent_audit_chain_stays_linear(self) -> None:
        """Regression: concurrent audit() calls must not fork the chain
        (both linking to the same prev_hash, making it permanently tampered)."""
        import threading

        from app.audit_chain import verify_chain

        def writer(index: int) -> None:
            for _ in range(10):
                self.db.audit("demo", None, f"actor-{index}", f"evt-{index}", {"n": index})

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(verify_chain(self._rows()), [])


class TelemetryConfigureTests(unittest.TestCase):
    def test_configure_tracing_no_crash_without_otel(self) -> None:
        from app.telemetry import configure_tracing

        configure_tracing()  # should not raise

    def test_span_with_attributes_and_events(self) -> None:
        with span("outer", service="helix") as outer:
            outer.set_attribute("custom", 42)
            with span("inner", depth=1) as inner:
                inner.add_event("step", {"phase": "processing"})
            self.assertIs(inner.parent, outer)
        self.assertIsNotNone(outer.duration_ms)


class SessionKeyRotationTests(unittest.TestCase):
    """Phase 28.2: session cookies support rolling signing-key rotation."""

    def _config(self, **overrides: Any) -> OIDCConfig:
        kwargs: dict[str, Any] = {
            "session_secret": "legacy-secret-for-testing-only",
            "session_ttl_minutes": 60,
        }
        kwargs.update(overrides)
        return OIDCConfig(**kwargs)

    def _principal(self) -> SessionPrincipal:
        return SessionPrincipal(
            tenant_id="t1",
            actor_id="u1",
            role="admin",
            session_id="s1",
            expires_at=int(time.time()) + 3600,
        )

    def test_cookie_signed_with_active_kid(self) -> None:
        config = self._config(
            session_secrets_json='{"2": "new-secret-2", "1": "legacy-secret-for-testing-only"}',
            session_active_kid="2",
        )
        cookie = create_session_cookie(self._principal(), config)
        # The payload must carry kid=2 and verify under the new key.
        decoded = verify_session_cookie(cookie, config)
        assert decoded is not None
        self.assertEqual(decoded.actor_id, "u1")

    def test_old_kid_still_verifies_during_rotation(self) -> None:
        # Cookie signed while key 1 was active.
        old_config = self._config(session_secret="legacy-secret-for-testing-only")
        cookie = create_session_cookie(self._principal(), old_config)
        # After rotation, the key map keeps kid 1 for verification.
        rotated = self._config(
            session_secrets_json='{"2": "new-secret-2", "1": "legacy-secret-for-testing-only"}',
            session_active_kid="2",
        )
        self.assertIsNotNone(verify_session_cookie(cookie, rotated))

    def test_rotated_out_kid_rejected(self) -> None:
        old_config = self._config(session_secret="old-secret-gone")
        cookie = create_session_cookie(self._principal(), old_config)
        # After the old key is fully removed from the map, the cookie fails.
        rotated = self._config(
            session_secrets_json='{"2": "new-secret-2"}',
            session_active_kid="2",
        )
        self.assertIsNone(verify_session_cookie(cookie, rotated))

    def test_active_kid_missing_from_map_raises(self) -> None:
        config = self._config(
            session_secrets_json='{"1": "legacy-secret-for-testing-only"}',
            session_active_kid="9",
        )
        with self.assertRaises(ValueError):
            create_session_cookie(self._principal(), config)

    def test_legacy_single_secret_uses_kid_1(self) -> None:
        config = self._config()
        cookie = create_session_cookie(self._principal(), config)
        self.assertIsNotNone(verify_session_cookie(cookie, config))

    def test_malformed_secrets_json_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._config(session_secrets_json="not-json").session_key_map()


class SessionAuthAdvancedTests(unittest.TestCase):
    def test_create_session_via_oidc_authenticator(self) -> None:
        config = OIDCConfig(
            session_secret="test-secret-key-for-testing-only",
            session_ttl_minutes=60,
        )
        auth = OIDCAuthenticator(config)
        principal, cookie = auth.create_session("tenant-1", "user-1", "admin")
        self.assertEqual(principal.tenant_id, "tenant-1")
        self.assertTrue(cookie)
        verified = verify_session_cookie(cookie, config)
        assert verified is not None
        self.assertEqual(verified.actor_id, "user-1")

    def test_oidc_config_not_configured(self) -> None:
        config = OIDCConfig()
        self.assertFalse(config.is_configured)


if __name__ == "__main__":
    unittest.main()
