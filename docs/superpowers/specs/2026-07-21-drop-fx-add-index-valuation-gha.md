# 2026-07-21 下线汇率 + 指数估值 GHA

## 目标

1. 整条下线 Frankfurter 汇率同步：GHA、job、测试，并 **drop `fx_rates` 表**。
2. 新增指数估值定时同步：写 `index_valuation` + `index_daily_metrics`（PE/PB），通知仅 Bark。
3. Phase 1 标的：`510300` / `000300.SH`。

## 非目标

- 不改 `stock-charts` 前端（源码尚未读 `fx_rates`）。
- 暂不扩全池指数。
- 不处理 GitHub 账号级邮件通知（需用户在 Settings 关闭）。

## 数据流

```text
红色火箭 /fundex-quote/index/valuation
  → PE/PB 历史 + 当日快照
  → upsert index_daily_metrics (pe_ttm, pb, valuation_source=hongsehuojian)
  → upsert index_valuation (current + 5y/10y avg)
  → sync_runs + Bark
```

## 调度

- Workflow：`同步指数估值到 Supabase`
- Cron：`15 11 * * 1-5`（北京约 19:15）
- 默认 mode：`valuation-only`
- 默认代码：`--etf-code=510300 --index-code=000300.SH`

## 破坏性变更

- `fx_rates` 删除后，驾驶舱跨币种折算需另起数据源。
