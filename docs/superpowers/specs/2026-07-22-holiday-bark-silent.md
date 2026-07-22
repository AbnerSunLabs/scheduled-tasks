# 休市 Bark 静默

**日期**：2026-07-22  
**状态**：已确认（方案 A）

## 问题

法定假日工作日 `schedule`（定时）仍会跑闸门；`is_trading_day=false`（休市）时各 workflow 的 `notify-bark` 仍推「今日休市已跳过」。ETF 日 K 一天最多 3 档 cron，叠加估值 / 官网校验，推送连轰。

## 决策

**休市静默**：`is_trading_day=false` 不推 Bark；`unknown`（日历查询失败）仍带正文前缀警告；开市成功/失败文案不变。同步闸门逻辑不变。

## 范围

- `.github/workflows/sync-etf-kline.yml`
- `.github/workflows/sync-index-valuation.yml`
- `.github/workflows/sync-official-cross-check.yml`
- `doc/ashare-trading-day-gate.md`

## 非目标

- 不做按 `cal_date`（日历日）跨 run 去重
- 不改 job 内收盘定稿逻辑
