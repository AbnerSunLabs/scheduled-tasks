# A 股交易日闸门（GHA）

工作日 `schedule`（定时触发）在跑同步前，用公开 HTTP 判定上海时区「今天」是否为 A 股交易日；休市则跳过同步，避免法定节假日空跑。

## 行为

| 触发                          | 行为                       |
| ----------------------------- | -------------------------- |
| `schedule` + 工作日增量类任务 | 查日历；休市跳过；开市照跑 |
| `workflow_dispatch`（手动）   | 不查日历，一律跑           |
| 周日 `adj_check`（复权巡检）  | 不查日历                   |

| 日历结果              | 同步                  | Bark（推送）                      |
| --------------------- | --------------------- | --------------------------------- |
| 开市                  | 跑                    | 原有成功/失败文案                 |
| 休市                  | 跳过（job 仍绿）      | 「今日休市已跳过」                |
| 查询失败（`unknown`） | **仍跑**（fail-open） | 正文前缀警告「交易日历查询失败…」 |

## 数据源

1. **主源** `holiday-cn`：`NateScarlet/holiday-cn` 年 JSON（国务院放假）+ **周末一律休市**（调休周六不算交易日）
2. **备源** `tencent`：腾讯财经 `sh000001`（上证综指）日 K 是否含该日

**已知权衡**：主源失败时，若备源日 K 尚未覆盖到「今天」（节假日常见：最近一根早于查询日），备源会判定 inconclusive → 按 fail-open **照跑**。此时无法靠备源拦住休市空跑，只靠 Bark 警告。

不落库、不恢复已删除的 `trade_calendar`（交易日历表）。

## 入口

```bash
# 判定今天（上海时区）
python -m scheduled_tasks.market.ashare_trading_day

# 指定日
python -m scheduled_tasks.market.ashare_trading_day --date=2026-10-01

# 写入 GITHUB_OUTPUT（Actions composite 使用）
python -m scheduled_tasks.market.ashare_trading_day --github-output
```

代码：`src/scheduled_tasks/market/ashare_trading_day.py`  
复用步骤：`.github/actions/check-ashare-trading-day/`

已接入：

- `sync-etf-kline.yml`（ETF 日 K；仅 `incremental`）
- `sync-index-valuation.yml`（指数估值）
- `sync-official-cross-check.yml`（官网双源校验）

## 非目标

- 不改 job 内 15:05 收盘定稿逻辑
- 不维护全年交易日历表
