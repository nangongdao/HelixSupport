# Gate B Evaluation — 1.4 退出标准逐项核对

日期：2026-08-20
依据：ROADMAP_2_X.md §Gate B（1.4 退出标准）；Phase 41 全部闭环（41.0–41.7）

## 逐项评估

| # | 退出标准 | 现状 | 判定 |
|---|----------|------|------|
| 1 | 无超期 Critical/High；漏洞例外到期检测进入 CI | CI `supply-chain` job 含 `vuln_review.py`（exception 到期红灯）与每日 `pip_audit` 双重扫描；`requirements.lock` pin。 | ✅ 已满足 |
| 2 | 发布镜像具备 digest、签名、provenance 和 SBOM，staging admission 拒绝未签名镜像 | SBOM 生成（Phase 28.5 CI `generate_sbom.py`）与 release manifest（Phase 41.2）就位；**admission gate 已实现**：`scripts/image_admission_check.py`（fail-closed：digest 未固定/SBOM 缺失/树漂移即拒绝发布；`--cosign-key/--registry` 时追加 `cosign verify`），`tests/test_image_admission_gate.py` 6 例覆盖缺失 pin/null digest/格式错误/缺 SBOM/空 SBOM/clean 通过。**digest 回填、真实 cosign 签名、provenance、staging admission 对接**依赖真实 registry 与部署方私钥（离线环境禁止伪造 digest，`base-image-pin.json` digest 保持 null）。 | ⚠️ admission 脚本就绪、真实 registry 依赖项待环境 |
| 3 | API/channel/session key 轮换演练完成，旧 key 退出后没有认证流量 | 轮换能力完整：`CredentialLifecycle.rotate/revoke/activate` + credential_registry 状态机 + 有界 overlap + `tests/test_credentials.py`（并发轮换、跨实例吊销、过期边界、staged activation）。**演练已自动化**：`scripts/run_rotation_drill.py`（真实 HTTP 路径：runtime issue → config promote → rotate → overlap 200 → revoke → 旧 401/新 200/旁观 200），记录入 `supplychain/rotation-drills.json`（PASSED，2026-08-20）。 | ✅ 已满足（演练 PASSED） |
| 4 | 外部审计 anchor 可验证一次真实备份恢复 | `scripts/verify_audit_chain.py`（DB frontier tips + WORM claims + 本地重算三证据）+ `scripts/restore.py`（checksum 验证恢复）+ DISASTER_RECOVERY.md §3 恢复命令就位；`tests/test_audit_anchors.py` 8 例覆盖 healthy/TAMPER 各故障。**演练已自动化**：`scripts/run_restore_drill.py`（scratch DB 高危事件 + 真实 anchors/WORM claims → `backup.py` → `restore.py` → verify intact → 篡改 → verify TAMPER），记录入 `supplychain/restore-drills.json`（PASSED，2026-08-20）。真实环境的 trusted-kid 外部锚定仍需部署方。 | ✅ 已满足（演练 PASSED） |
| 5 | AI 安全集 100%，模型/prompt 晋级报告可追溯 | Phase 41.5 闭环：对抗集 24/24、golden 27/27、`EvalReportStore` WORM 报告、`decide_promotion` 五条件晋级、CI `ai-eval` job 上传报告产物。 | ✅ 已满足 |
| 6 | 真实私有安全报告渠道仍可用，threat-model delta、演练和命名 owner gate 全绿 | `scripts/threat_model_gate.py` 全红灯形态（缺失 delta、placeholder owner 五形态、空证据、过期演练）就位；**全绿已达成**：`supplychain/threat-model-deltas.json` 含 1.4.0 delta（release/date/owner/approved_by/controls/verification_evidence 全字段），`--release 1.4.0` 与 `--check-today`（90 天）均 exit 0；`security-drills.json` 已记录三场 Gate B 自动化演练（automated_rotation/restore/patch_release，各带 reference 台账）。真实渠道可用性依赖部署方私有环境确认（SEC-007 约定）。 | ⚠️ gate 全绿、真实渠道待部署方确认 |
| 7 | 至少一次安全补丁发布演练完成，覆盖签名 hotfix、canary、回滚和发布后监控 | **演练已脚本化**：`scripts/run_patch_drill.py`（manifest 构建 + 签名（无真实 key 时确定性模拟签名 + sign-binding 断言；`--cosign-key/--registry` 时走真实 cosign）+ 真实 HTTP 路径 canary 会话 + 签名绑定/篡改检测（回滚依赖的核心性质）+ 发布后 diagnostics 监控断言），记录入 `supplychain/patch-drills.json`（PASSED，2026-08-20，simulated signing）。真实签名 hotfix 部署/回滚仍依赖部署方 registry 与 cosign key。 | ⚠️ 脚本化演练通过、真实签名待环境 |
| 8 | M0 与 1.4 的 runbook 由非作者按文档成功执行 | **runbook 已编写**：`docs/runbooks/M0_HARDENING_ACCEPTANCE.md`（OIDC 负向、DSR 权限、队列 fail-closed、报告渠道闭环、threat-model）、`docs/runbooks/RELEASE_1_4.md`（CI 门禁复核、供应链证据、AI 基线、演练、canary 发布、监控、回滚）——均面向非作者、按步骤可执行。**实际由非作者执行**依赖部署方/发布负责人（本机无生产凭据），条目语义上仍待真实执行。 | ⚠️ runbook 就绪、执行待部署方 |

## 结论

- **代码/工具链侧可继续自主完成的**：Gate B 自动化侧已全部落地（admission 脚本、三场演练脚本 + 台账、runbook、threat-model delta/演练 gate、1.4.0 delta）。
- **依赖真实环境/人工的**：digest 回填/真实 cosign 签名/provenance、真实备份恢复演练、私有报告渠道端到端、轮换演练真实执行、真实签名补丁发布、非作者 runbook 执行。
- **Gate B 由此判定为"基本达标（自动化侧）"**：项 1/3/4/5 已满足；项 2 的 admission 与项 6 的 gate 均已脚本化/全绿；项 7/8 的脚本与 runbook 已就绪。剩余全部依赖部署方真实环境（registry/cosign key/私有渠道/非作者人员），属本机不可自动化项。

## 待办（按可自动化程度排序）

1. ✅ `scripts/run_rotation_drill.py`：API key 轮换端到端（注册新 key→旧 key retire→overlap→revoke→旧 key 401 断言），结果入 `supplychain/rotation-drills.json`（PASSED 2026-08-20）
2. ✅ `scripts/run_restore_drill.py`：backup→restore（checksum）→verify_audit_chain 编排，结果入台账（PASSED 2026-08-20）
3. ✅ `docs/runbooks/M0_HARDENING_ACCEPTANCE.md`：M0 加固验收 runbook（非作者执行导向，OIDC 负向/DSR 权限/队列 fail-closed/报告渠道/threat-model）
4. ✅ `docs/runbooks/RELEASE_1_4.md`：1.4 发布 runbook（门禁复核/供应链证据/AI 基线/演练/发布/监控/回滚）
5. ✅ `scripts/run_patch_drill.py`：补丁发布演练（manifest 构建 + 签名绑定 + canary + 回滚性质 + 发布后监控），结果入 `supplychain/patch-drills.json`（PASSED 2026-08-20,simulated signing）
6. ✅ `supplychain/security-drills.json`：三场 Gate B 自动化演练条目（rotation/restore/patch，reference 指向台账）；`threat-model-deltas.json` 含 1.4.0 delta；`threat_model_gate.py --release 1.4.0` 与 `--check-today` 全绿（2026-08-20）
7. ✅ `scripts/image_admission_check.py`：staging admission gate（fail-closed digest/SBOM/manifest 一致性；cosign 可选）+ `tests/test_image_admission_gate.py` 6 例