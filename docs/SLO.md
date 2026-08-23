# SLO & Error Budget (Phase 30.1)

目标对象:核心客服路径(客户消息 → 自动回答/转人工 → 流式回答)。

## SLO 定义

| 指标 | SLO | 错误预算(30 天) | 来源指标 |
|------|-----|----------------|---------|
| 可用性 | 99.9%(月) | 43.2 分钟 | `http_requests_total` 5xx / 总请求 |
| turn P95 延迟 | ≤ 4s(当月 P95) | 5% 样本超预算 | `http_request_duration_ms` histogram(POST /messages) |
| 队列滞留时长 | 99% turn 入队 → 完成 ≤ 30s | 1% 超预算 | `turn_jobs.oldest_queued_age_seconds` |
| SSE 投递延迟 | 首 token P95 ≤ 1.5s | 5% 超预算 | `turn_job_stream` 指标 |

## 错误预算计算

```
错误预算 = 30 天秒数 × (1 - SLO)
  可用性 99.9% → 2,592,000 × 0.001 = 2,592s ≈ 43.2 min
```

消费规则:当某指标本月消耗 ≥80% 错误预算时触发告警(见 `ops/prometheus/alerts.yml`),
进入"冻结新实验性变更"窗口,只允许修复类 PR。

## 指标导出

服务在进程内 `TelemetryMetrics` 记录(见 `app/telemetry.py`),`GET /api/system/metrics`
聚合 HTTP 延迟直方图与 turn 计数;`ops/prometheus/alerts.yml` 可直接对接
Prometheus 抓取该端点(经 `metrics:read` 或运维旁路)。

## 告警规则摘要

| 规则 | 表达式 | 严重级 |
|------|--------|--------|
| 高 5xx 率 | 5xx 占比 > 2%(5 分钟) | critical |
| 队列积压 | `oldest_queued_age_seconds > 30`(5 分钟) | warning |
| P95 超时 | `http_request_duration_ms` p95 > 4000ms(5 分钟) | warning |
| 预算耗尽 | 错误预算消耗 ≥ 80% | warning |

详细规则文件:`ops/prometheus/alerts.yml`;Grafana 看板:`ops/grafana/dashboard.json`。
