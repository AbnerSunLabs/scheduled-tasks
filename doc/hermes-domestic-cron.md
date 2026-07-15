# Hermes 国内机 cron（BaoStock / AKShare 补数）

> 代码就绪说明：本文件描述国内机调度约定。安装 cron、跑 live 回填均需**单独授权**，
> 不得因功能分支合并而视为已上线。
>
> Spike 现状（2026-07-15）：海外机硬门禁未过（见 `doc/spike-baostock-akshare.md`）。
> **须在国内 Hermes 复跑 Spike 通过后**，再启用生产 cron / 五年回填。

## 与 GitHub Actions 分工

| 环境 | Job | 职责 |
| ---- | --- | ---- |
| GitHub Actions（海外） | `sync_etf_kline_baostock`（实为 yfinance） | 主写 `etf_daily` OHLC/volume/复权/`price_source`/`updated_at`；`amount = coalesce(incoming, existing)` |
| GitHub Actions（海外） | `sync_fx_rates_frankfurter` | 主写 `fx_rates` |
| Hermes `no_agent`（国内） | `sync_etf_enrich_akshare` | **UPDATE-only** 补 `amount` / `amount_source` / `amount_updated_at` |
| Hermes `no_agent`（国内） | `sync_trade_calendar_baostock` | upsert `trade_calendar(market='CN')` |

补数 job 与 yfinance 时间重叠可接受；最终业务字段态由双 writer 语义保证（价格权威 = yfinance，成交额权威 = AKShare）。

## 依赖安装（国内机）

```bash
cd /opt/scheduled-tasks
python3.11 -m venv .venv
/opt/scheduled-tasks/.venv/bin/pip install -U pip
/opt/scheduled-tasks/.venv/bin/pip install -e '.[domestic]'
/opt/scheduled-tasks/.venv/bin/python -c "import akshare, baostock; print(akshare.__version__)"
```

钉死版本：`akshare==1.18.64`、`baostock==0.9.3`（见 `pyproject.toml` optional `domestic`）。升级须重跑 Spike。

## 环境变量

| 变量 | 说明 |
| ---- | ---- |
| `DATABASE_URL` | Supabase Postgres 连接串（job owner / service_role 写权限） |
| `BARK_KEY` / Telegram 等 | 失败通知（可选；stdout 失败需接入现有告警） |

勿把密钥写入仓库或 cron 明文日志。

## 前置 DDL（Supabase，须授权后执行）

部署顺序：

1. `20260715_rename_etf_pool_snapshots_to_etf_pool.sql`（若 live 仍为旧表名）
2. `20260715_etf_daily_amount_enrichment_and_trade_calendar.sql`

验证：

```sql
select to_regclass('public.etf_pool'), to_regclass('public.trade_calendar');
select column_name from information_schema.columns
 where table_schema='public' and table_name='etf_daily'
   and column_name in ('amount_source','amount_updated_at');
```

## Cron 样例（`no_agent`，绝对路径 + venv + `-m`）

### 成交额增量（工作日 19:30 CST）

```bash
cd /opt/scheduled-tasks && /opt/scheduled-tasks/.venv/bin/python -m scheduled_tasks.jobs.sync_etf_enrich_akshare --mode=incremental
```

限标的调试：

```bash
cd /opt/scheduled-tasks && /opt/scheduled-tasks/.venv/bin/python -m scheduled_tasks.jobs.sync_etf_enrich_akshare --mode=incremental --codes=510300
```

### 近 5 年回填（仅获 live 写入授权后）

```bash
cd /opt/scheduled-tasks && /opt/scheduled-tasks/.venv/bin/python -m scheduled_tasks.jobs.sync_etf_enrich_akshare --mode=full
```

### 交易日历（每年 1 次 + 手动）

```bash
cd /opt/scheduled-tasks && /opt/scheduled-tasks/.venv/bin/python -m scheduled_tasks.jobs.sync_trade_calendar_baostock --start=2020-01-01 --end=2026-12-31
```

## 失败通知

- Job 非 0 退出或 `status=failed`：把 stdout / `artifacts/*_summary.json` 推送到 Bark/Telegram。
- `unmatched` 写入 `sync_runs.meta`，待 yfinance 建主行情后再补，不 INSERT 残缺行。

## 验收 SQL（逐标的最近 250 行）

```sql
with ranked as (
  select etf_code, amount,
         row_number() over (partition by etf_code order by trade_date desc) as rn
  from public.etf_daily
),
latest as (select * from ranked where rn <= 250)
select etf_code,
       count(*) as rows_in_denom,
       count(amount) as amount_nonnull,
       count(amount)::float / count(*) as fill_rate
from latest
group by etf_code
order by fill_rate asc, etf_code;
```

主行情已有 ≥250 行者要求 `fill_rate >= 0.95`；不足 250 行者进 `insufficient_history` 单列，不得用默认分母 250 伪报。
