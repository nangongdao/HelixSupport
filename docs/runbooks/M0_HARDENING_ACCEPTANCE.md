# Runbook: M0 安全加固验收(非作者执行)

> 目标读者:未参与 M0 实现(Phase 40/SEC-001/002、REL-001、SEC-007、SEC-008)的部署/安全工程师。
> 执行前提:staging 环境(独立数据库),无生产访问。本 runbook 只使用合入版本的脚本与
> 受控凭据,不要求阅读源码。时长约 90 分钟。

## 0. 前置条件

- [ ] staging 主机的 `requirements.lock`、`docs/`、`scripts/`、`supplychain/` 与发布 tag 一致。
- [ ] 可获得 staging 管理员 API key(由环境变量 `HELIX_ADMIN_KEY` 传入,不得写入文档或仓库)。
- [ ] `APP_VERSION` 与 release tag 一致:
  `python -c "from app.main import APP_VERSION; print(APP_VERSION)"`

## 1. 部署配置核查

1. 确认生产 profile 设定:
   ```bash
   # multi 必须 PostgreSQL + Redis;
   # 变量示例由部署方 preset,以下为验证不变量:
   python - <<'PY'
   from app.config import Settings
   s = Settings()
   assert s.deployment_profile in ("local", "single", "multi")
   assert s.queue_failure_mode in ("fallback", "fail_closed")
   if s.deployment_profile == "multi":
       assert s.database_url.startswith("postgres"), "multi 禁止 SQLite"
       assert s.redis_url, "multi 必须配置 Redis"
       assert s.queue_failure_mode == "fail_closed", "multi 必须 fail_closed"
   print("profile ok:", s.deployment_profile, s.queue_failure_mode)
   PY
   ```
2. 确认 `ENABLE_SESSION_AUTH` 只对已配置真实 OIDC issuer 的 profile 开启;
   未验证 IdP 的环境保持 `false`(SEC-001 回退)。
3. 确认 `docs/SECURITY_MODEL.md` 与 SECURITY.md 报告地址无占位符:
   ```bash
   python scripts/threat_model_gate.py   # exit 0;非法 JSON exit 2 视为失败
   ```

## 2. OIDC 负向测试(仅当 ENABLE_SESSION_AUTH=true)

用测试 IdP 按 `tests/` 中 OIDC 用例验证四条负向路径:

1. state 缺失 / 错配 / 过期 / 重放 → 登录拒绝(4xx,统一外部错误)。
2. ID token `alg=none`、错误签名、错误 issuer/audience、过期、未知 kid → 拒绝。
3. unknown tenant / unknown member / 非法 role → 拒绝,不得回退 admin。
4. 同一浏览器并发两次登录 → 仅一次成功,另一 transaction 原子消费后拒绝。

命令式健康检查(替代浏览器自动化或人工确认):
```bash
python - <<'PY'
import os, json, urllib.request, urllib.error
base = os.environ["HELIX_BASE_URL"].rstrip("/")
req = urllib.request.Request(base + "/auth/login", data=b"x", method="POST")
try:
    urllib.request.urlopen(req)
except urllib.error.HTTPError as e:
    print("login without valid state ->", e.code)  # 期望 4xx
PY
```

## 3. DSR 权限(SEC-002)

1. 用 operator(key 无 `privacy:*`)调用:
   - `POST /api/privacy/dsr-requests` → 403;
   - `POST /api/privacy/dsr-requests/{id}/approve` → 403;
   - `POST /api/privacy/dsr-requests/{id}/execute` → 403。
2. 用 admin 创建 DSR → 审批(非申请人)→ 执行;同一 admin 既申请又审批 → 拒绝。
3. 执行重复提交同一 request id → 幂等,返回先前结果。
4. 导出对象 ≤24h 过期、一次性 URL ≤15min 失效;API 只接受待审批 id(任意
   `customer_ref` 直接执行 → 4xx)。

## 4. 队列 fail-closed(REL-001)

仅在 staging multi profile 执行:

1. 双实例部署中断 Redis → readiness=503、worker 不启动、`GET /async/intake` 返回
   503 + `Retry-After`;诊断包显示实际 backend=PostgreSQL、degraded reason。
2. 恢复 Redis → reconciliation 重放已入 DB 的 job,turn/chunk 不重复(对比去重日志)。
3. 启动时 Redis 不可用 → readiness=503,不创建 SQLite 本地分叉队列。

## 5. 安全报告闭环演练(SEC-007)

1. 部署方确认报告渠道非 placeholder(真实邮箱/工单)。
2. 发送一封测试报告 → 确认接收 → 分级(Critical/High/…)→ 关闭。
3. 在 `supplychain/security-drills.json` 记录该次演练(drill_type=`report_intake`,
   满足 `scripts/threat_model_gate.py --check-today` 的字段要求)。
4. 运行 `python scripts/threat_model_gate.py --check-today` → exit 0。

## 6. 完成判定

- [ ] 上述 1-5 全部通过,失败项记录于企业 issue 并注明补偿控制。
- [ ] `docs/runbooks/` 无 placeholder 值(本 runbook 中 `HELIX_BASE_URL` 等是占位,使用前替换)。
- [ ] 任何发现写入 `supplychain/threat-model-deltas.json`(delta 需 release/date/owner/
  approved_by/controls/verification_evidence 全字段)。