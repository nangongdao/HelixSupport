#!/usr/bin/env python3
"""Phase 43.2 contract (a): live PostgreSQL row-level tenant isolation drill.

Provisions a scratch database on the local PostgreSQL cluster with two roles
— ``helix_rls_migrator`` (schema owner) and ``helix_rls_app`` (least-
privilege, no BYPASSRLS) — installs the app schema plus the tenant-isolation
RLS policies as the owner, then connects as the *app* role to prove the
acceptance contract:

1. no tenant context  -> zero rows visible, inserts fail WITH CHECK;
2. wrong-tenant predicate under an established scope -> still zero rows
   (the GUC, not the WHERE clause, decides);
3. ``DELETE`` without any ``WHERE`` under tenant A's scope cannot touch
   tenant B's rows;
4. pool-reuse analogue: after commit the context is gone and the next
   transaction starts fail-closed;
5. every protected table reports ENABLE + policy (``missing_protection`` empty);
6. break-glass shape: cross-tenant system reads work only through the
   owner/exempt role (migrator), never through the app role.

Usage::

    python scripts/run_rls_drill.py                 # default local cluster
    python scripts/run_rls_drill.py --admin-url postgresql://...
    python scripts/run_rls_drill.py --keep          # keep the scratch db

Exit codes: 0 = all checks PASSED, 1 = failure. The JSON report lands in
``artifacts/rls-drill-report.json``.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ADMIN_URL_DEFAULT = "postgresql://postgres:helix@127.0.0.1:5433/postgres"
MIGRATOR_ROLE = "helix_rls_migrator"
APP_ROLE = "helix_rls_app"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _scratch_dsn(admin_url: str, role: str, password: str, db_name: str) -> str:
    """Build a DSN for the scratch database on the admin cluster's host/port.

    Strips any userinfo from the admin URL (e.g. ``postgres:helix@``) and
    substitutes the drill role, so the scratch DSN targets the same host/port
    as ``--admin-url`` without inheriting its credentials.
    """
    parts = urlsplit(admin_url)
    hostport = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, f"{role}:{password}@{hostport}", f"/{db_name}", "", ""))


class Drill:
    def __init__(self, admin_url: str, keep: bool) -> None:
        import psycopg2

        self._psycopg2 = psycopg2
        self.admin_url = admin_url
        self.keep = keep
        self.password = f"rls-{secrets.token_urlsafe(12)}"
        self.db_name = f"helix_rls_drill_{uuid.uuid4().hex[:8]}"
        self.steps: list[dict[str, Any]] = []
        self.migrator_dsn = ""
        self.app_dsn = ""

    @property
    def psycopg2(self) -> Any:
        return self._psycopg2

    # ------------------------------------------------------------- helpers

    def record(self, name: str, ok: bool, detail: str) -> None:
        self.steps.append({"step": name, "ok": ok, "detail": detail})
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name}: {detail}")

    def _connect(self, dsn: str, autocommit: bool = False) -> Any:
        import psycopg2

        connection = psycopg2.connect(dsn)
        connection.autocommit = autocommit
        return connection

    @contextmanager
    def _session(self, dsn: str, autocommit: bool = False) -> Iterator[Any]:
        """Raw session without psycopg2's ``with conn`` transaction semantics
        (entering ``with conn`` starts an explicit transaction, which breaks
        ``DROP DATABASE`` even under autocommit)."""
        connection = self._connect(dsn, autocommit=autocommit)
        try:
            yield connection
        finally:
            connection.close()

    # ------------------------------------------------------------- phases

    def provision_roles(self) -> None:
        with self._session(self.admin_url, autocommit=True) as conn, conn.cursor() as cur:
            for role in (MIGRATOR_ROLE, APP_ROLE):
                cur.execute(f"DROP ROLE IF EXISTS {role}")
            cur.execute(f"CREATE ROLE {MIGRATOR_ROLE} LOGIN PASSWORD '{self.password}'")
            cur.execute(f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{self.password}'")
            cur.execute(
                "SELECT rolname, rolbypassrls FROM pg_roles WHERE rolname = %s",
                (APP_ROLE,),
            )
            row = cur.fetchone()
            ok = row is not None and row[1] is False
            self.record("app-role-has-no-bypassrls", ok, f"rolbypassrls={row[1] if row else None}")

    def create_scratch_db(self) -> None:
        with self._session(self.admin_url, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (self.db_name,),
            )
            cur.execute(f"DROP DATABASE IF EXISTS {self.db_name}")
            cur.execute(f"CREATE DATABASE {self.db_name} OWNER {MIGRATOR_ROLE}")
        self.migrator_dsn = _scratch_dsn(self.admin_url, MIGRATOR_ROLE, self.password, self.db_name)
        self.app_dsn = _scratch_dsn(self.admin_url, APP_ROLE, self.password, self.db_name)

    def install_schema_and_rls(self) -> Any:
        from app.postgres_db import PostgresDatabase

        database = PostgresDatabase(self.migrator_dsn)
        database.initialize()
        database.install_row_level_security()
        return database

    def grant_app_privileges(self) -> None:
        statements = [
            f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}",
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}",
            f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}",
            f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {APP_ROLE}",
        ]
        with self._session(self.migrator_dsn) as conn, conn.cursor() as cur:
            for statement in statements:
                cur.execute(statement)
            conn.commit()

    def seed_two_tenants(self) -> None:
        now = _utc_now()
        conv_sql = (
            "INSERT INTO conversations (id, tenant_id, customer_name, channel, status,"
            " priority, labels_json, message_count, needs_response, version,"
            " created_at, updated_at)"
            " VALUES (%s, %s, %s, %s, %s, 'normal', '[]', 0, 0, 1, %s, %s)"
        )
        msg_sql = (
            "INSERT INTO messages (id, tenant_id, conversation_id, role, author,"
            " content, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)"
        )
        rows = [
            (
                "INSERT INTO tenants (id, name, created_at) VALUES (%s, %s, %s)",
                ("acme", "Acme", now),
            ),
            (
                "INSERT INTO tenants (id, name, created_at) VALUES (%s, %s, %s)",
                ("globex", "Globex", now),
            ),
            (
                conv_sql,
                ("conv-acme", "acme", "Acme Customer", "web_chat", "new", now, now),
            ),
            (
                conv_sql,
                ("conv-globex", "globex", "Globex Customer", "web_chat", "new", now, now),
            ),
            (
                msg_sql,
                (
                    "msg-acme",
                    "acme",
                    "conv-acme",
                    "customer",
                    "customer",
                    "acme secret payload",
                    now,
                ),
            ),
            (
                msg_sql,
                (
                    "msg-globex",
                    "globex",
                    "conv-globex",
                    "customer",
                    "customer",
                    "globex secret payload",
                    now,
                ),
            ),
        ]
        with self._session(self.migrator_dsn) as conn, conn.cursor() as cur:
            for sql, params in rows:
                cur.execute(sql, params)
            conn.commit()
        with self._session(self.migrator_dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM messages")
            total = cur.fetchone()[0]
        self.record("seed-two-tenants", total == 2, f"owner-visible message rows={total}")

    # ------------------------------------------------------------ app-role

    def _app_session(self):
        return self._connect(self.app_dsn)

    def check_no_context_fail_closed(self) -> None:
        msg_sql = (
            "INSERT INTO messages (id, tenant_id, conversation_id, role, author,"
            " content, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)"
        )
        with self._app_session() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM messages")
            count = cur.fetchone()[0]
            self.record(
                "no-context-select-sees-zero",
                count == 0,
                f"count={count} (current_setting unset -> NULL -> policy filters all)",
            )
        inserted = False
        try:
            with self._app_session() as conn, conn.cursor() as cur:
                cur.execute(
                    msg_sql,
                    (
                        "msg-evil",
                        "globex",
                        "conv-globex",
                        "customer",
                        "attacker",
                        "should not land",
                        _utc_now(),
                    ),
                )
                conn.commit()
                inserted = True
        except self.psycopg2.Error as exc:  # WITH CHECK violation surfaces as a PG error
            detail = type(exc).__name__
            if "violates row-level security policy" in str(exc):
                detail = "row-level security policy violation"
            self.record("no-context-insert-rejected", True, f"rejected: {detail}")
        if inserted:
            self.record("no-context-insert-rejected", False, "insert unexpectedly succeeded")

    def check_scope_isolates(self) -> None:
        with self._app_session() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", ("acme",))
                cur.execute("SELECT COUNT(*) FROM messages")
                own = cur.fetchone()[0]
                self.record("scoped-select-sees-own-only", own == 1, f"acme-visible rows={own}")
                cur.execute("SELECT COUNT(*) FROM messages WHERE tenant_id = 'globex'")
                forged = cur.fetchone()[0]
                self.record(
                    "explicit-other-tenant-predicate-yields-zero",
                    forged == 0,
                    f"rows={forged} (policy ignores the WHERE clause)",
                )
            conn.commit()
            # Pool-reuse analogue: same physical session, next transaction has
            # no context because set_config(..., true) died with the commit.
            with conn.cursor() as cur:
                cur.execute("SELECT current_setting('app.tenant_id', true)")
                guc = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM messages")
                count = cur.fetchone()[0]
                self.record(
                    "context-dies-with-transaction",
                    (guc is None or guc == "") and count == 0,
                    f"guc_after_commit={guc!r}, rows={count}",
                )

    def check_delete_without_where(self) -> None:
        with self._app_session() as conn, conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", ("acme",))
            cur.execute("DELETE FROM messages")
            deleted = cur.rowcount
            conn.commit()
        with self._session(self.migrator_dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT tenant_id FROM messages")
            survivors = sorted(row[0] for row in cur.fetchall())
        ok = deleted == 1 and survivors == ["globex"]
        self.record(
            "where-less-delete-confined-to-scope",
            ok,
            f"deleted={deleted}, survivor_tenants={survivors} (globex untouched)",
        )

    def check_update_cross_tenant_blocked(self) -> None:
        with self._app_session() as conn, conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", ("acme",))
            cur.execute("UPDATE messages SET content = 'tampered' WHERE tenant_id = 'globex'")
            touched = cur.rowcount
            conn.rollback()
        self.record("cross-tenant-update-touches-zero", touched == 0, f"updated={touched}")

    def check_verify_report(self, database: Any) -> None:
        from app.rls import missing_protection, verify_rls

        with database.connect() as connection:
            report = verify_rls(connection)
        problems = missing_protection(report)
        protected = sum(
            1
            for state in report["tables"].values()
            if state.get("rel_rls") and state.get("policy_ok")
        )
        self.record(
            "verify-all-tables-protected",
            not problems,
            f"{protected}/{len(report['tables'])} ENABLEd+policy; problems={problems[:3]}",
        )

    # ------------------------------------------------------------- cleanup

    def teardown(self) -> None:
        if self.keep:
            print(f"[KEEP] scratch database {self.db_name} left in place")
            return
        try:
            with self._session(self.admin_url, autocommit=True) as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (self.db_name,),
                )
                cur.execute(f"DROP DATABASE IF EXISTS {self.db_name}")
                for role in (APP_ROLE, MIGRATOR_ROLE):
                    cur.execute(f"DROP ROLE IF EXISTS {role}")
            print("[CLEAN] scratch database and drill roles removed")
        except Exception as exc:  # cleanup best-effort
            print(f"[WARN] cleanup incomplete: {exc}")

    # ---------------------------------------------------------------- main

    def run(self) -> bool:
        ok = True
        try:
            self.provision_roles()
            self.create_scratch_db()
            database = self.install_schema_and_rls()
            self.grant_app_privileges()
            self.seed_two_tenants()
            self.check_no_context_fail_closed()
            self.check_scope_isolates()
            self.check_delete_without_where()
            self.check_update_cross_tenant_blocked()
            self.check_verify_report(database)
        except Exception as exc:
            ok = False
            self.record("drill-error", False, f"{type(exc).__name__}: {exc}")
            raise
        finally:
            try:
                database.close()
            except UnboundLocalError:
                pass
            passed = [step for step in self.steps if step["ok"]]
            failed = [step for step in self.steps if not step["ok"]]
            ok = not failed
            report = {
                "drill": "rls",
                "roadmap": "ROADMAP_2_X §43.2 contract (a)",
                "database": self.db_name,
                "finished_at": _utc_now(),
                "steps": self.steps,
                "status": "PASSED" if ok else "FAILED",
            }
            out = Path(__file__).resolve().parent.parent / "artifacts" / "rls-drill-report.json"
            out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\nRLS drill {report['status']}: {len(passed)} passed / {len(failed)} failed")
            print(f"report: {out}")
            self.teardown()
        return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-url", default=ADMIN_URL_DEFAULT)
    parser.add_argument("--keep", action="store_true", help="keep the scratch database")
    args = parser.parse_args()
    drill = Drill(args.admin_url, args.keep)
    return 0 if drill.run() else 1


if __name__ == "__main__":
    sys.exit(main())
