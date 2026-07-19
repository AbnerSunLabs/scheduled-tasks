# 官网双源校验设计（ETF + 指数）

- 日期：2026-07-17
- 状态：已落地 MVP（见 `doc/official-cross-check.md`）
- 范围：仅 ETF 日 K 与指数相关数据；不含汇率、账本、成交额

## 背景

主数据源已稳定：

| 数据 | 主写 |
|------|------|
| ETF 日 K（`etf_daily`） | yfinance；红色火箭仅缺日 INSERT + 已有行比对 |
| 指数估值（`index_valuation`） | 红色火箭 |
| 指数估值（`index_valuation`） | 红色火箭 |
| 行业权重（`index_industry_weights`） | 红色火箭 |

需要增加 **官网优先的双源校验**：默认不改正库，发现差异告警；人工确认后可用官网覆盖。

## 目标

1. 用**官网**对主库做交叉校验（不是用红火箭当「官网」）。
2. 默认 **只比对、不写库**；显式 `--apply-official` 才用官网 UPDATE mismatch 行。
3. 首期 **allowlist** 跑通后再扩池。

## 非目标（首期）

- 不用 AKShare / BaoStock / 红色火箭充当官方权威源。
- 不改 yfinance / 红色火箭主写语义与补缺职责。
- 不新建官网影子表（强审计需求出现后再议）。
- 不校验已删除的成交额字段。
- 首期不做行业权重双源（申万 vs 中证行业口径易误报）。
- 首期不强制校验复权列（口径差异大）。

## 决策摘要

| 项 | 选择 |
|----|------|
| 架构 | **独立校验 job**（与主写解耦） |
| 官网组合 | ETF → 交易所；指数 → 中证等指数公司；ETF 必要时基金公司兜底 |
| 冲突处理 | 默认只告警；`--apply-official` 才覆盖 |
| 缺日 | 官网有、库无 → 记 `missing_in_db`，**默认不 INSERT**（补缺仍归主写） |
| 首期标的 | `512170`（医疗ETF华宝）+ `399989.SZ`（中证医疗） |

## 角色与数据源

| 角色 | ETF | 指数 |
|------|-----|------|
| 主写 | yfinance（全池）；红火箭补缺 | 红火箭 |
| 校验源（优先） | 上交所（`512170` 在沪） | 中证指数（CSI） |
| 校验源（兜底） | 基金公司产品页（可选二期） | 深交所披露页（可选二期） |

红色火箭继续主写/补缺；**不**定义为官网权威。

## 校验字段（首期 MVP）

### ETF（`etf_daily`，按 `trade_date`）

| 字段 | 首期 |
|------|------|
| `close`（不复权） | 必校 |
| `open` / `high` / `low` | 官网有则校 |
| `*_qfq` / `*_hfq` | 暂缓 |
| volume | 可选；官网单位需 Spike 确认 |

### 指数

| 对象 | 字段 | 首期 |
|------|------|------|
| `index_valuation` | `current_pe_ttm` | 可选校（日线表已删除，不再校 close） |
| `index_valuation` | `current_pe_ttm` | 中证有则校 |
| `index_valuation` | `pe_ttm_avg_5y` / `10y` | 暂缓（官网未必有同口径序列） |
| `index_industry_weights` | — | 二期 |

## 行为语义

```
拉官网窗口（incremental 默认近 N 日；full：ETF begin=-10000 / 指数自 2004-01-01）
  → 官网空响应或总 validated=0 → failed（不得 success）
  → 与库中已有 (code, trade_date) 对齐
    → 一致：validated++
    → 不一致：mismatch 样本写入 summary / sync_runs.meta；可 Bark
    → 库缺官网有：missing_in_db（不 INSERT）
    → 库有官网无：missing_in_official（观测，不删库）

默认：禁止 UPDATE / INSERT
--apply-official：仅对 mismatch 行用官网字段 UPDATE；仍不 INSERT 缺日
```

- 数值容差：默认 `epsilon=0.001`（CLI 可覆盖），复用现有 `values_mismatch` 语义（两侧皆空算一致；一侧空一侧非空算不一致）。
- 退出码：存在 mismatch、源失败、或 `validated=0` 时非零，便于调度告警。

## 工程落点（名实相符）

| 类型 | 建议路径 / 名称 |
|------|-----------------|
| Job | `scheduled_tasks.jobs.sync_official_cross_check` |
| `JOB_NAME` | `sync_official_cross_check` |
| Client | `etf/sse_client.py`（上交所）、`etf/csindex_client.py`（中证） |
| Artifact | `artifacts/sync_official_cross_check_summary.json` |
| 文档 | `doc/official-cross-check.md`（实现后）；本设计文档同步保留 |

CLI 草案：

```bash
python -m scheduled_tasks.jobs.sync_official_cross_check
python -m scheduled_tasks.jobs.sync_official_cross_check --mode=incremental
python -m scheduled_tasks.jobs.sync_official_cross_check --mode=full
python -m scheduled_tasks.jobs.sync_official_cross_check --apply-official
python -m scheduled_tasks.jobs.sync_official_cross_check --etf-code=512170 --index-code=399989.SZ
python -m scheduled_tasks.jobs.sync_official_cross_check --epsilon=0.001
```

Allowlist 默认与红火箭一致，可用 CLI 覆盖。

## 实施分期

1. **Spike（只读、不写库）**  
   - 摸清上交所 `512170`、中证 `399989` 的可机读接口/字段/单位/历史深度/限流。  
   - 产出短笔记（可放 `doc/spike-official-sse-csindex.md`）。
2. **MVP**  
   - 独立 job：allowlist 日频 `close`（+ 可选 OHLC）与指数 `close`、当日 PE 比对；summary + `sync_runs`；告警。
3. **`--apply-official`**  
   - 仅 mismatch UPDATE；单测覆盖「无 flag 绝不写库」。
4. **扩容**  
   - 全 `etf_pool` / 跟踪指数；行业权重；复权字段（若 Spike 证明口径可对齐）。

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| 交易所/中证页面改版、无稳定 API | Spike 先证伪；client 隔离；失败记 `source_unavailable` 不误报为行情 mismatch |
| 不复权 vs 前复权混比 | 文档与代码明确只比不复权 close；Spike 抽样核对 |
| `--apply-official` 误用 | 默认关闭；日志与 meta 标明 `applied_official=true`；可要求 `--yes` |
| 与红火箭校验重复 | 红火箭仍是主写侧自检；本 job 专责「官网 vs 库」 |

## 验收标准（MVP）

- [x] allowlist 上可本地跑通比对，产出 summary JSON。
- [x] 无 `--apply-official` 时，对库的写操作次数为 0（单测 `apply_official=False` 不调 UPDATE）。
- [x] 人为制造 close 偏差时可出现在 `mismatches` 样本中（单测）。
- [x] README / `doc/official-cross-check.md` / `doc/supabase-schema.md` 数据流有对应说明。
- [x] 小白文档：仅复用 `sync_runs` + 现有表，无需更新。

## 已确认偏好

1. 官网类型：组合（ETF 交易所 + 指数中证）。
2. 冲突策略：先告警，人工后 `--apply-official`。
3. 首期范围：allowlist（`512170` + `399989.SZ`）。
