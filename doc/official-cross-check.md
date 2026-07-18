# 官网双源校验（ETF + 指数）

设计见 [official-cross-check-design.md](./official-cross-check-design.md)；Spike 见 [spike-official-sse-csindex.md](./spike-official-sse-csindex.md)。

## 目标

用**官网**校验库内主写：

| 标的 | 官网 | 主库表 |
|------|------|--------|
| ETF `5xxxxx`（上交所） | 上交所 yunhq 日 K | `etf_daily` OHLC |
| ETF `15xxxx`（深交所） | **暂无**官网 client（`--from-pool` 会跳过） | — |
| 指数（可选） | 中证 `index-perf` | 仅 `etf_valuation.current_pe_ttm`（日线表已删除） |

## 行为

- **默认**：只比对，**不 UPDATE / 不 INSERT**
- **`--apply-official --yes`**：仅对 mismatch 行用官网字段 UPDATE；缺日仍不 INSERT
- **`--from-pool`**：校验 `etf_pool` 内全部沪市 ETF；自动 `--skip-index`（避免单指数绑死全池）
- **`--mode=full`**：ETF 拉 `begin=-10000`（覆盖老标的全历史）；指数从 `2004-01-01` 拉到 `--end`
- 官网返回空数组、或总 `validated=0` → `status=failed`（不再静默 success）
- 容差：价格 `--epsilon`（默认 `0.001`）；指数 close 先四舍五入到 2 位再比；PE `--pe-epsilon`（默认 `3.0`，红火箭 vs 中证常见差约 2 点）
- 退出码：存在 mismatch、源失败或 `status=failed` → 非 0

## 已知口径差异

- 中证 `peg`（映射为 PE）与红色火箭 PE 可能差约 2 点；默认容差 `3.0` 滤噪声，收紧用 `--pe-epsilon=0.5`。
- 库内指数 close 可能保留更高精度；比对按官网两位小数对齐。

## 运行

```bash
python3 -m scheduled_tasks.jobs.sync_official_cross_check
python3 -m scheduled_tasks.jobs.sync_official_cross_check --mode=full
# 全池沪市 ETF（跳过深市 + 默认不跑指数）
python3 -m scheduled_tasks.jobs.sync_official_cross_check --from-pool
python3 -m scheduled_tasks.jobs.sync_official_cross_check --pe-epsilon=0.5  # 更严 PE
# 人工确认后纠偏：
python3 -m scheduled_tasks.jobs.sync_official_cross_check --apply-official --yes
```

摘要：`artifacts/sync_official_cross_check_summary.json`  
`sync_runs.job_name=sync_official_cross_check`

## GitHub Actions

Workflow：`.github/workflows/sync-official-cross-check.yml`

- 定时：工作日北京约 20:00（ETF 日 K 之后），默认 `--from-pool` **只比对**
- 手动：`workflow_dispatch` 可改 mode / from_pool / etf_code
- **不会**自动 `--apply-official`；纠偏需本地显式确认
- 退出码：仅 **价差 mismatch** 或整次失败 → 非 0；个别源瞬时断连但已有有效比对 → 0（summary 仍可能 `partial`）
- `sync_runs.success_codes`：仅含源拉取/校验完成的标的；SSE/中证失败的代码不计入（`success_count` 同口径）
- 结果经 Bark（若配置 `BARK_KEY`）推送；summary 上传 artifact
