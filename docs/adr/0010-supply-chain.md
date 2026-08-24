# ADR-010: 供应链与可复现发布 gate（SEC-003）

- 状态：已接受（Phase 41.2，1.4 / Secure Operations）
- 日期：2026-08-20

## 背景

ROADMAP 41.2（SEC-003）要求把“依赖能进代码库”和“发布物能重建”变成可审计、
可门禁的事实，而不是靠人记：

- 依赖漏洞必须带期限、带 owner、带证据地管理：`pip-audit` 只能报告存在，
  无法表达“我们明知某 CVE 但该路径不可达、到 X 修好”。到期必须自动红灯。
- 许可证必须有方向：`pip install` 静默引入新依赖时，必须强制一次有意的
  license 审查，而不是事后被发现。
- 发布物必须可重建并核对：`CI 构建出的镜像 digest`、SBOM digest、源码树
  哈希要能独立重算与比对，篡改或漂移立刻失败。
- 供应链摘要固定必须由受控流程完成（受控更新机器人 PR），不能靠工程师
  临时手写一个未经验证的 digest。

## 决策

新增 `supplychain/` 配置目录 + 五个独立 gate 脚本，全部挂进 CI
`supply-chain` job（`.github/workflows/ci.yml`）：

1. **secret 扫描**（`scripts/scan_secrets.py`）——扫描发布来源树中的已知凭证
   形态（私钥、Anthropic/OpenAI/AWS/GitHub/Slack/service-account/Fernet），
   忽略 build 产物、测试夹具（`tests/` 不出现在 wheel/Docker 中,见
   `pyproject.packages` 与 `.dockerignore`）与 npm 完整性哈希。
   匹配器刻意窄——只有命中*已知*凭证形状才算泄漏,避免 `X-API-Key` 头名、
   配置 key、测试样例误报。
2. **license 策略**（`scripts/license_gate.py` + `supplychain/license-policy.json`）——
   `requirements.lock` 每个包必须逐包登记;新包未登记即红灯。
   未完成法务确认的许可以 `LicenseRef-TBD` + `approved_until` 临时登记,
   到期自动红灯。
3. **漏洞例外审查**（`scripts/vuln_review.py` + `supplychain/vulnerability-exceptions.json`）——
   每条例外含 CVE、受影响组件、不可达证据、补偿控制、owner、到期日;
   open 例外到期自动红灯;`--audit pip-audit.json --require-coverage` 模式
   保证:任何被上报的 CVE 都必须登记为例外,否则红灯。
4. **workflow pin 一致性**（`scripts/check_workflows.py` + `supplychain/ci-pins.json`）——
   `.github/workflows` 中每个 `uses: owner/repo@ref` 必须在 pins 登记且 ref
   一致;本地 action 与 `docker://` 引用跳过。未固定到 commit SHA 的 action
   输出 warning,由受控更新机器人回填,禁止人工伪造 digest。
5. **发布 manifest**（`scripts/release_manifest.py`）——`--build` 计算
   `requirements.lock`/`pyproject.toml`/`Dockerfile`/整棵 `app/` 树/SBOM 的
   SHA-256 与基础镜像 pin,写 `artifacts/release-manifest.json`;
   `--verify` 从磁盘重算全部哈希并比对,任何漂移或篡改立刻失败
   （“从 tag 重建并核对 digest”）。`base_image_digest` 为 null 时按 warning
   处理——本机离线环境禁止伪造 digest,由真实 registry 受控回填。

## 结果

- 伪造 secret / 未登记依赖 / 被上报而未登记的 CVE / 过期例外 / 篡改源码树
  均使 gate 失败（见各脚本 tests:35 case,`tests/test_scan_secrets.py`、
  `tests/test_vuln_review.py`、`tests/test_license_gate.py`、
  `tests/test_release_manifest.py`、`tests/test_check_workflows.py`）。
- 五条 gate 彼此正交:secret 管“值不能进仓库”、license 管“依赖必须有许可”,
  vuln 管“已知漏洞必须带期限处理”、pin 管“CI 引用必须是受控登记”、
  manifest 管“最终发布物与源码树一致”。
- 尚未闭环（Gate B 待办）:真实 registry 的基础镜像 digest 回填、cosign 镜像
  签名与 provenance/attestation、staging admission 拒绝未签名镜像。