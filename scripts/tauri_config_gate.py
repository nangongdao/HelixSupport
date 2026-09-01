"""Tauri 桌面配置一致性门禁（D5 / DESKTOP_TAURI_PLAN §9）。

Tauri 对 ``plugins.*`` 下的未知字段是**静默忽略**的：既不报错也不生效。
`cargo tauri info` 对注入的 ``bogusFieldThatDoesNotExist`` 零输出、exit 0，
`$schema` 也覆盖不到这一层。1.x 的 ``plugins.updater.active`` 因此在 v2 配置里
潜伏了整个 D5 阶段——看起来"自动更新已启用"，实际该字段从未被读取，
而 v2 真正需要的 ``bundle.createUpdaterArtifacts`` 缺失，构建根本不产出
更新产物与 ``.sig`` 签名文件。

本门禁守住三条不变量：

1. **无 1.x 残留字段**：``plugins.updater.active`` / ``plugins.updater.dialog``
   在 v2 已不存在（tauri-plugin-updater 2.10.1 的 ``Config`` 无此字段），
   留着只会制造"已配置"的假象。
2. **updater 三件套自洽**：一旦声明了 ``plugins.updater.endpoints``，就必须
   同时有 ``bundle.createUpdaterArtifacts``（否则无产物可更新）和非空
   ``pubkey``（否则 ``verify_signature`` 走 ``PublicKey::decode("")`` 必然
   报错，更新永远装不上）。空 pubkey 是 fail-closed，不是静默接受未签名包，
   但功能上等于自动更新整体不可用。
3. **dangerous_* 开关不得开启**：``dangerousInsecureTransportProtocol`` 等
   会放宽传输层校验，不允许提交进仓库。

``pubkey`` 待采购签名证书期间允许显式豁免（``--allow-empty-pubkey``），
但豁免必须在调用处可见，不能靠一个空字符串蒙混过关。

Usage:
    python scripts/tauri_config_gate.py [--config PATH] [--allow-empty-pubkey]

Exit 0 表示配置自洽；非零并逐行打印违规项。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_DESCRIPTION = (__doc__ or "tauri config gate").strip().splitlines()[0]

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "src-tauri" / "tauri.conf.json"

# 1.x 字段 -> v2 中的正确做法，供报错时直接给出修法
LEGACY_UPDATER_FIELDS = {
    "active": "v2 无此字段；是否产出更新产物由 bundle.createUpdaterArtifacts 决定",
    "dialog": "v2 已移除内置更新对话框；改由前端调用 check()/downloadAndInstall() 自绘",
}

DANGEROUS_UPDATER_FIELDS = (
    "dangerousInsecureTransportProtocol",
    "dangerousAcceptInvalidCerts",
    "dangerousAcceptInvalidHostnames",
)


def review(
    config_path: Path = DEFAULT_CONFIG, allow_empty_pubkey: bool = False
) -> tuple[list[str], list[str]]:
    """返回 ``(violations, warnings)``；violations 为空表示通过。"""
    try:
        config: dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"无法读取 {config_path}: {exc}"], []

    violations: list[str] = []
    warnings: list[str] = []

    updater = config.get("plugins", {}).get("updater")
    if not isinstance(updater, dict):
        return violations, warnings  # 未启用 updater，无需校验

    for field, guidance in LEGACY_UPDATER_FIELDS.items():
        if field in updater:
            violations.append(
                f"plugins.updater.{field} 是 Tauri 1.x 残留字段，v2 静默忽略它——{guidance}"
            )

    for field in DANGEROUS_UPDATER_FIELDS:
        if updater.get(field):
            violations.append(f"plugins.updater.{field} 放宽了更新传输层校验，不允许提交进仓库")

    if updater.get("endpoints"):
        artifacts = config.get("bundle", {}).get("createUpdaterArtifacts")
        if artifacts is not True and artifacts != "v1Compatible":
            violations.append(
                "声明了 plugins.updater.endpoints 但 bundle.createUpdaterArtifacts "
                f"为 {artifacts!r}（默认 false）——构建不会产出更新产物与 .sig 签名，"
                "自动更新无包可装"
            )

        if not updater.get("pubkey"):
            message = (
                "plugins.updater.pubkey 为空——verify_signature 会以 "
                'PublicKey::decode("") 报错，更新包永远装不上'
                "（安全上 fail-closed，功能上等于自动更新不可用）；"
                "用 `cargo tauri signer generate` 生成后填入"
            )
            if allow_empty_pubkey:
                warnings.append(f"{message}；当前由 --allow-empty-pubkey 显式豁免")
            else:
                violations.append(message)

    return violations, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=_DESCRIPTION)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--allow-empty-pubkey",
        action="store_true",
        help="签名证书采购期间显式豁免空 pubkey（降级为 warning）",
    )
    args = parser.parse_args(argv)

    violations, warnings = review(args.config, args.allow_empty_pubkey)
    for warning in warnings:
        print(f"tauri_config_gate: warning — {warning}", file=sys.stderr)
    if not violations:
        print("tauri_config_gate: Tauri 配置自洽，无 1.x 残留字段")
        return 0
    for violation in violations:
        print(f"tauri_config_gate: {violation}", file=sys.stderr)
    print(f"tauri_config_gate: {len(violations)} 项违规", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
