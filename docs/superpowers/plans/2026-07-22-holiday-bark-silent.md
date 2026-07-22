# 休市 Bark 静默 Implementation Plan

> **For agentic workers:** 本变更极小，可直接按任务顺序改；不必再拆 subagent。

**Goal:** 法定假日 `schedule` 跳过同步时不再推「今日休市已跳过」Bark；仅保留 `unknown` 告警。

**Architecture:** 三个 workflow 的 `notify-bark` 在 `IS_TRADING_DAY=false` 时 `exit 0` 并打日志，不再 POST Bark。闸门 action / job 逻辑不动。

**Tech Stack:** GitHub Actions bash、现有 Bark HTTP push

## Global Constraints

- 中文回复 / 文档标识带中文释义
- 同步更新 `doc/ashare-trading-day-gate.md`
- 小白文档：无新 DB 概念，无需更新

---

### Task 1: 三 workflow 休市静默

**Files:**

- Modify: `.github/workflows/sync-etf-kline.yml`
- Modify: `.github/workflows/sync-index-valuation.yml`
- Modify: `.github/workflows/sync-official-cross-check.yml`
- Modify: `doc/ashare-trading-day-gate.md`

- [x] 将「今日休市已跳过」Bark 分支改为日志 + `exit 0`
- [x] 保留 `unknown` 前缀与开市推送
- [x] 更新闸门文档 Bark 表
- [x] 自检：三处无「今日休市已跳过」标题
