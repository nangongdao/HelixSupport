"""Channel webhooks configuration validation branches.

覆盖 app/channel_webhooks.py 中的配置验证错误路径：
- line 75: account value 不是 dict
- line 82: tenant_id 缺失或无效
- line 86: channel 格式无效
- line 89-90: secret 长度不足
"""

from __future__ import annotations

import json
import unittest

from app.channel_webhooks import ChannelWebhookConfigError, InboundChannelRegistry


class ChannelWebhookValidationTests(unittest.TestCase):
    def test_account_value_not_dict_raises(self) -> None:
        # line 75: value 不是 dict
        config = json.dumps({"acc1": "not-a-dict"})
        with self.assertRaisesRegex(ChannelWebhookConfigError, "must map to an object"):
            InboundChannelRegistry(config)

    def test_missing_tenant_id_raises(self) -> None:
        # line 82: tenant_id 缺失
        config = json.dumps({"acc1": {"channel": "web", "secret": "x" * 32}})
        with self.assertRaisesRegex(ChannelWebhookConfigError, "requires a non-empty tenant_id"):
            InboundChannelRegistry(config)

    def test_empty_tenant_id_raises(self) -> None:
        # line 82: tenant_id 为空
        config = json.dumps({"acc1": {"tenant_id": "  ", "channel": "web", "secret": "x" * 32}})
        with self.assertRaisesRegex(ChannelWebhookConfigError, "requires a non-empty tenant_id"):
            InboundChannelRegistry(config)

    def test_invalid_channel_format_raises(self) -> None:
        # line 86: channel 格式无效
        config = json.dumps(
            {"acc1": {"tenant_id": "demo", "channel": "Invalid@Channel", "secret": "x" * 32}}
        )
        with self.assertRaisesRegex(ChannelWebhookConfigError, "requires a valid channel"):
            InboundChannelRegistry(config)

    def test_secret_too_short_raises(self) -> None:
        # line 89-90: secret 长度不足 32 字节
        config = json.dumps({"acc1": {"tenant_id": "demo", "channel": "web", "secret": "short"}})
        with self.assertRaisesRegex(ChannelWebhookConfigError, "at least 32 bytes"):
            InboundChannelRegistry(config)


if __name__ == "__main__":
    unittest.main()
