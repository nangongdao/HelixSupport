"""D5: Tauri 桌面配置一致性门禁测试。

Tauri 静默忽略 ``plugins.*`` 下的未知字段（实测：注入 bogus 字段后
``cargo tauri info`` 零输出、exit 0），所以 1.x 残留字段与 updater 三件套
不自洽都只能靠这道门禁抓。本测试锁定它真的会红灯——门禁自己失效时
（如 LOCAL_IMPORT_RE 反向引用那次）没有测试就无人发现。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts.tauri_config_gate import review

# 一份自洽的 v2 配置：有 endpoints 就配齐 createUpdaterArtifacts + pubkey
VALID: dict[str, Any] = {
    "$schema": "https://schema.tauri.app/config/2",
    "bundle": {"active": True, "createUpdaterArtifacts": True},
    "plugins": {
        "updater": {
            "endpoints": ["https://releases.example.com/{{target}}.json"],
            "pubkey": "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6",
        }
    },
}


def _write(config: dict[str, Any], root: Path) -> Path:
    path = root / "tauri.conf.json"
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return path


class TauriConfigGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _review(
        self, mutate: Any = None, *, allow_empty_pubkey: bool = False
    ) -> tuple[list[str], list[str]]:
        config = json.loads(json.dumps(VALID))  # 深拷贝，避免测试间串味
        if mutate is not None:
            mutate(config)
        return review(_write(config, self.root), allow_empty_pubkey)

    def test_valid_config_passes(self) -> None:
        violations, warnings = self._review()
        self.assertEqual(violations, [])
        self.assertEqual(warnings, [])

    def test_legacy_active_field_is_rejected(self) -> None:
        """1.x 的 active 字段：v2 静默忽略，正是它潜伏整个 D5 的原因。"""

        def mutate(config: dict[str, Any]) -> None:
            config["plugins"]["updater"]["active"] = True

        violations, _ = self._review(mutate)
        self.assertEqual(len(violations), 1)
        self.assertIn("plugins.updater.active", violations[0])
        self.assertIn("1.x", violations[0])
        self.assertIn("createUpdaterArtifacts", violations[0])

    def test_legacy_dialog_field_is_rejected(self) -> None:
        def mutate(config: dict[str, Any]) -> None:
            config["plugins"]["updater"]["dialog"] = False

        violations, _ = self._review(mutate)
        self.assertTrue(any("plugins.updater.dialog" in v for v in violations))

    def test_missing_updater_artifacts_is_rejected(self) -> None:
        """有 endpoints 却无 createUpdaterArtifacts：构建不产出 .sig，无包可装。"""

        def mutate(config: dict[str, Any]) -> None:
            del config["bundle"]["createUpdaterArtifacts"]

        violations, _ = self._review(mutate)
        self.assertEqual(len(violations), 1)
        self.assertIn("createUpdaterArtifacts", violations[0])

    def test_v1_compatible_artifacts_accepted(self) -> None:
        """createUpdaterArtifacts 合法值除 true 外还有 "v1Compatible"。"""

        def mutate(config: dict[str, Any]) -> None:
            config["bundle"]["createUpdaterArtifacts"] = "v1Compatible"

        violations, _ = self._review(mutate)
        self.assertEqual(violations, [])

    def test_false_artifacts_is_rejected(self) -> None:
        def mutate(config: dict[str, Any]) -> None:
            config["bundle"]["createUpdaterArtifacts"] = False

        violations, _ = self._review(mutate)
        self.assertTrue(any("createUpdaterArtifacts" in v for v in violations))

    def test_empty_pubkey_is_rejected_by_default(self) -> None:
        def mutate(config: dict[str, Any]) -> None:
            config["plugins"]["updater"]["pubkey"] = ""

        violations, warnings = self._review(mutate)
        self.assertEqual(len(violations), 1)
        self.assertIn("pubkey", violations[0])
        self.assertEqual(warnings, [])

    def test_empty_pubkey_downgraded_by_explicit_waiver(self) -> None:
        """签名证书采购期的豁免必须在调用处可见，不靠空串蒙混。"""

        def mutate(config: dict[str, Any]) -> None:
            config["plugins"]["updater"]["pubkey"] = ""

        violations, warnings = self._review(mutate, allow_empty_pubkey=True)
        self.assertEqual(violations, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("--allow-empty-pubkey", warnings[0])

    def test_dangerous_transport_flag_is_rejected(self) -> None:
        def mutate(config: dict[str, Any]) -> None:
            updater = config["plugins"]["updater"]
            updater["dangerousInsecureTransportProtocol"] = True

        violations, _ = self._review(mutate)
        self.assertEqual(len(violations), 1)
        self.assertIn("dangerousInsecureTransportProtocol", violations[0])

    def test_dangerous_flag_set_false_is_allowed(self) -> None:
        """显式关掉不算违规，只有真开启才拦。"""

        def mutate(config: dict[str, Any]) -> None:
            config["plugins"]["updater"]["dangerousAcceptInvalidCerts"] = False

        violations, _ = self._review(mutate)
        self.assertEqual(violations, [])

    def test_no_updater_plugin_skips_checks(self) -> None:
        """未启用 updater 时不该凭空要求 createUpdaterArtifacts。"""

        def mutate(config: dict[str, Any]) -> None:
            config["plugins"] = {}
            del config["bundle"]["createUpdaterArtifacts"]

        violations, warnings = self._review(mutate)
        self.assertEqual(violations, [])
        self.assertEqual(warnings, [])

    def test_updater_without_endpoints_skips_artifact_checks(self) -> None:
        """只声明插件不给 endpoints，说明还没接线，不强求签名三件套。"""

        def mutate(config: dict[str, Any]) -> None:
            config["plugins"]["updater"] = {}
            del config["bundle"]["createUpdaterArtifacts"]

        violations, _ = self._review(mutate)
        self.assertEqual(violations, [])

    def test_multiple_violations_all_reported(self) -> None:
        """一次跑出全部问题，避免修一个才发现下一个。"""

        def mutate(config: dict[str, Any]) -> None:
            updater = config["plugins"]["updater"]
            updater["active"] = True
            updater["pubkey"] = ""
            del config["bundle"]["createUpdaterArtifacts"]

        violations, _ = self._review(mutate)
        self.assertEqual(len(violations), 3)

    def test_unreadable_config_is_a_violation(self) -> None:
        violations, _ = review(self.root / "does-not-exist.json")
        self.assertEqual(len(violations), 1)
        self.assertIn("无法读取", violations[0])

    def test_real_repo_config_has_no_legacy_fields(self) -> None:
        """真实仓库配置：允许 pubkey 待填，但不允许 1.x 残留字段回归。"""
        from scripts.tauri_config_gate import DEFAULT_CONFIG

        violations, _ = review(DEFAULT_CONFIG, allow_empty_pubkey=True)
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
