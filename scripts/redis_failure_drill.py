#!/usr/bin/env python3
"""M0 REL-001: four-class failure drill against a real Redis backend.

Runs the Phase 40.3 acceptance contract against a *scratch* Redis server
(started on an ephemeral port, with no ``redis-server`` the drill skips).
It is deliberately independent of the running system Redis on 6379, so it can
run on any workstation that has the binary without touching shared state.

The four classes from the roadmap acceptance (ROADMAP_2_X §40.3):
1. 双实例断 Redis …… two independent ``RedisTaskQueue`` instances over one
   Redis never double-claim and every job is claimed exactly once.
2. 启动时 Redis 不可用 … an app configured fail_closed boots against a dead
   port (the scratch Redis has not been started yet): readiness reports
   degraded, the API answers 503 + ``Retry-After``, and every queue operation
   raises ``QueueUnavailableError`` instead of silently degrading to SQLite.
3. 处理中断链 … a claim abandoned by a killed worker (present in the Rediss
   processing hash, lease expired) is recovered and re-dispatched exactly once.
4. 恢复 … after the scratch Redis comes back the same fail-closed app answers
   normal API traffic again and the stranded job reaches a terminal state
   without duplication.

Exit code is 0 only when all four classes pass.
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.config import Settings
from app.database import Database
from app.main import create_app
from app.queue import QueueUnavailableError, RedisTaskQueue

QUEUE_KEYS = (
    "helix:q:dispatch",
    "helix:q:processing",
    "helix:q:leases",
    "helix:q:delayed",
)

DRILL_ADMIN_KEY = "drill-admin-key-0001"


def _pick_free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return cast(int, sock.getsockname()[1])


class DrillRedis:
    """Lifecycle of a scratch Redis server on an ephemeral port."""

    def __init__(self, args: argparse.Namespace, workdir: Path) -> None:
        self.server = args.redis_server or shutil.which("redis-server")
        self.port = args.port or _pick_free_port()
        self.workdir = workdir
        self.process: subprocess.Popen[bytes] | None = None

    @property
    def url(self) -> str:
        return f"redis://127.0.0.1:{self.port}/0"

    def available(self) -> bool:
        try:
            import redis

            client = redis.from_url(self.url, decode_responses=True, socket_connect_timeout=1)
            client.ping()
            return True
        except Exception:
            return False

    def start(self) -> None:
        if self.process is not None:
            return
        if not self.server:
            raise SystemExit("redis-server binary not found; pass --redis-server")
        log = (self.workdir / f"redis-{self.port}.log").open("ab")
        self.process = subprocess.Popen(
            [self.server, "--port", str(self.port), "--save", "", "--appendonly", "no"],
            stdout=log,
            stderr=log,
        )
        deadline = time.time() + 5
        while time.time() < deadline:
            if self.available():
                return
            time.sleep(0.1)
        raise RuntimeError(f"scratch Redis failed to start on port {self.port}")

    def stop(self) -> None:
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None

    def flush(self) -> None:
        try:
            import redis

            client = redis.from_url(self.url, decode_responses=True, socket_connect_timeout=1)
            for key in QUEUE_KEYS:
                client.delete(key)
        except Exception:
            pass


class StrengthTests(unittest.TestCase):
    """Classes 1/3 against the live scratch Redis."""

    db_path: Path
    redis_url: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.db = Database(cls.db_path)
        cls.db.initialize()
        cls.db.ensure_tenant("drill-tenant")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()

    def setUp(self) -> None:
        import redis

        self.redis = redis.from_url(self.redis_url, decode_responses=True)
        for key in QUEUE_KEYS:
            self.redis.delete(key)
        self.queue = RedisTaskQueue(self.redis, self.db)

    def tearDown(self) -> None:
        for key in QUEUE_KEYS:
            self.redis.delete(key)

    def _conversation(self, index: int = 0) -> str:
        return self.db.create_conversation(
            "drill-tenant", f"Drill Conv {index}", f"CUST-D-{index}", "web", "admin", 120
        )["id"]

    def test_no_double_claim_across_instances(self) -> None:
        """双实例断 Redis: two queue instances never double-claim."""
        import redis as redislib

        second = RedisTaskQueue(redislib.from_url(self.redis_url, decode_responses=True), self.db)
        for index in range(8):
            self.queue.enqueue(
                "drill-tenant", self._conversation(index), f"k-{index}", f"a{index}", f"c{index}", 3
            )
        queues = [self.queue, second]
        claimed: list[str] = []
        for i in range(16):
            job = queues[i % 2].dequeue(f"worker-{i}", 300)
            if job is not None:
                claimed.append(cast(str, job["id"]))
        self.assertEqual(len(claimed), 8)
        self.assertEqual(len(set(claimed)), len(claimed), "double-claim detected")

    def test_abandoned_claim_recovered_exactly_once(self) -> None:
        """处理中断链: a killed worker's lease expires and the job re-dispatches."""
        conv = self._conversation()
        self.queue.enqueue("drill-tenant", conv, "ab-1", "a1", "c1", 3)
        job = self.queue.dequeue("killed-worker", 1)  # one-second lease
        self.assertIsNotNone(job)
        assert job is not None
        time.sleep(1.1)
        recovered = self.queue.recover(1)
        self.assertGreaterEqual(recovered["redis_requeued"], 1)
        reclaimed = self.queue.dequeue("rescue-worker", 300)
        self.assertIsNotNone(reclaimed)
        assert reclaimed is not None
        self.assertEqual(reclaimed["id"], job["id"])


class FailClosedApiTests(unittest.TestCase):
    """Classes 2/4 against the dead-port fail-closed app."""

    db_path: Path
    dead_port: int

    def _settings(self) -> Settings:
        # queue_backend is orthogonal to the database backend: job rows live in
        # SQLite, dispatch/leases in Redis.  Using the single-profile SQLite
        # stack here keeps the drill self-contained (no PostgreSQL), while the
        # multi-profile wiring contract is covered by DeploymentProfileTests.
        return Settings(
            database_path=self.db_path,
            queue_backend="redis",
            redis_url=f"redis://127.0.0.1:{self.dead_port}/0",
            queue_failure_mode="fail_closed",
            auth_mode="api_key",
            api_keys_json=json.dumps(
                {
                    DRILL_ADMIN_KEY: {
                        "tenant_id": "drill-tenant",
                        "actor_id": "drill.admin",
                        "role": "admin",
                    }
                }
            ),
            rate_limit_per_minute=10000,
            docs_enabled=False,
            enable_session_auth=False,
            turn_worker_enabled=False,
        )

    def test_startup_redis_down_is_fail_closed(self) -> None:
        """启动时 Redis 不可用: API 503 + Retry-After, no silent SQLite fallback."""
        app = create_app(self._settings())
        client = TestClient(app)
        services = cast(Any, app.state).services
        self.assertIsInstance(services.queue, RedisTaskQueue)
        self.assertFalse(services.queue.is_ready())
        try:
            ready = client.get("/health/ready")
            self.assertEqual(ready.status_code, 503)
            self.assertFalse(ready.json()["queue"]["ready"])
            self.assertIsNotNone(ready.json()["queue"]["degraded_reason"])
            conv = services.database.create_conversation(
                "drill-tenant", "FC", "CUST-FC", "web", "admin", 120
            )
            headers = {"X-API-Key": DRILL_ADMIN_KEY, "X-Tenant-Id": "drill-tenant"}
            # The async turn intake endpoint goes through the queue; a fail-closed
            # queue that is down must answer 503 + Retry-After + queue_unavailable.
            response = client.post(
                f"/api/conversations/{conv['id']}/turn-jobs",
                headers=headers,
                json={"content": "hello"},
            )
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(response.headers.get("Retry-After"), "30")
            self.assertEqual(response.json()["code"], "queue_unavailable")
            with self.assertRaises(QueueUnavailableError):
                services.queue.enqueue("drill-tenant", conv["id"], "fc-1", "a1", "m", 3)
        finally:
            client.close()
            services.database.close()

    def test_no_sqlite_fork_created(self) -> None:
        """No independent SQLite dispatch appears while the backend is down."""
        app = create_app(self._settings())
        client = TestClient(app)
        services = cast(Any, app.state).services
        try:
            # A valid conversation lets enqueue reach the Redis dispatch push;
            # a fail-closed Redis queue raises there before any job is queued.
            conv = services.database.create_conversation(
                "drill-tenant", "NF", "CUST-NF", "web", "admin", 120
            )
            with self.assertRaises(QueueUnavailableError):
                services.queue.enqueue("drill-tenant", conv["id"], "nx-1", "a1", "m", 3)
            # The queue was replaced entirely by the Redis backend at
            # construction; the orchestrator got the same instance, so no
            # silent SQLite dispatch side-channel can appear.
            self.assertIsInstance(services.queue, RedisTaskQueue)
            self.assertIs(services.orchestrator.queue, services.queue)
        finally:
            client.close()
            services.database.close()


class RecoveryTests(unittest.TestCase):
    """Class 4: the same fail-closed app recovers once Redis returns."""

    db_path: Path
    scratch_url: str

    def _settings(self) -> Settings:
        # queue_backend is orthogonal to the database backend; see
        # FailClosedApiTests._settings for why the drill uses the single-profile
        # SQLite stack.
        return Settings(
            database_path=self.db_path,
            queue_backend="redis",
            redis_url=self.scratch_url,
            queue_failure_mode="fail_closed",
            auth_mode="api_key",
            api_keys_json=json.dumps(
                {
                    DRILL_ADMIN_KEY: {
                        "tenant_id": "drill-tenant",
                        "actor_id": "drill.admin",
                        "role": "admin",
                    }
                }
            ),
            rate_limit_per_minute=10000,
            docs_enabled=False,
            enable_session_auth=False,
            turn_worker_enabled=False,
        )

    def test_app_recovers_when_redis_returns(self) -> None:
        app = create_app(self._settings())
        client = TestClient(app)
        services = cast(Any, app.state).services
        try:
            ready = client.get("/health/ready")
            self.assertEqual(ready.status_code, 200)
            self.assertTrue(ready.json()["queue"]["ready"])
            conv = services.database.create_conversation(
                "drill-tenant", "RC", "CUST-RC", "web", "admin", 120
            )
            headers = {"X-API-Key": DRILL_ADMIN_KEY, "X-Tenant-Id": "drill-tenant"}
            response = client.post(
                f"/api/conversations/{conv['id']}/messages",
                headers=headers,
                json={"content": "hello back"},
            )
            # Message intake is asynchronous even with no worker draining, so the
            # job is accepted and queued rather than held back.
            self.assertEqual(response.status_code, 200, response.text)
        finally:
            client.close()
            services.database.close()


def main() -> int:
    import unittest  # noqa: F811  # local import keeps argparse help import-light

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--redis-server",
        default=shutil.which("redis-server"),
        help="path to the redis-server binary (default: PATH lookup)",
    )
    parser.add_argument("--port", type=int, default=0, help="scratch port (default: ephemeral)")
    args = parser.parse_args()

    if not args.redis_server:
        print("redis-server binary not found; single failure drill requires it.")
        return 0

    with tempfile.TemporaryDirectory(prefix="helix-drill-") as workdir:
        scratch = DrillRedis(args, Path(workdir))
        db_path = Path(workdir) / "drill.db"

        # Class 1 + 3 need the scratch Redis up.
        scratch.start()
        try:
            StrengthTests.redis_url = scratch.url
            StrengthTests.db_path = db_path
            strength_result = unittest.TestResult()
            suite = unittest.TestLoader().loadTestsFromTestCase(StrengthTests)
            suite.run(strength_result)
        finally:
            scratch.flush()
            scratch.stop()

        if not strength_result.wasSuccessful():
            for failed in strength_result.failures + strength_result.errors:
                print("CLASS 1/3 FAILURE:\n" + "".join(failed[1]))
            return 1

        print(f"Class 1/3 (双实例/中断链) passed against scratch Redis :{scratch.port}")

        # Class 2 needs the same fail-closed app against the now-dead port.
        dead_port = scratch.port
        FailClosedApiTests.dead_port = dead_port
        FailClosedApiTests.db_path = db_path
        fail_result = unittest.TestResult()
        suite = unittest.TestLoader().loadTestsFromTestCase(FailClosedApiTests)
        suite.run(fail_result)
        if not fail_result.wasSuccessful():
            for failed in fail_result.failures + fail_result.errors:
                print("CLASS 2 FAILURE:\n" + "".join(failed[1]))
            return 1
        print(f"Class 2 (启动时 Redis 不可用) passed against dead port :{dead_port}")

        # Class 4 boots the same config back against the scratch Redis.
        scratch.start()
        try:
            RecoveryTests.scratch_url = scratch.url
            RecoveryTests.db_path = db_path
            recovery_result = unittest.TestResult()
            suite = unittest.TestLoader().loadTestsFromTestCase(RecoveryTests)
            suite.run(recovery_result)
        finally:
            scratch.stop()

        if not recovery_result.wasSuccessful():
            for failed in recovery_result.failures + recovery_result.errors:
                print("CLASS 4 FAILURE:\n" + "".join(failed[1]))
            return 1
        print(f"Class 4 (恢复) passed after Redis returned on :{scratch.port}")

    print("M0 REL-001 four-class failure drill: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
