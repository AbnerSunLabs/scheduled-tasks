# Spike 报告：AKShare / BaoStock 作为 ETF 成交额（amount）补充数据源

> **已废弃（2026-07-17）**：`etf_daily` 不再维护成交额；`sync_etf_enrich_akshare` 与 BaoStock 成交额兜底已删除。本文仅保留历史 Spike 记录。BaoStock / AKShare / `trade_calendar` 均已停用；见 [hermes-domestic-cron.md](./hermes-domestic-cron.md)。

对应计划：`stock-charts/docs/superpowers/plans/2026-07-15-domestic-baostock-akshare-enrichment.md` §4。
脚本：`scripts/spike_baostock_akshare.py`；机器可读全量结果：`doc/spike-baostock-akshare.raw.json`。

## 0. 结论（先说结果）

**§4.4 硬门禁：未通过（FAILED）。整体判定：BLOCKED。**

| 门槛 | 结果     | 说明                                                             |
| ---- | -------- | ---------------------------------------------------------------- |
| 单位 | **PASS** | 抽样校验通过率 100%（远超 ≥95% 要求），0 条异常                  |
| 覆盖 | **FAIL** | 8 只测试标的中 2 只（本次为 `512480`、`511010`）未达 ≥250 交易日 |
| 深度 | **PASS** | `510300`、`159915` 近 5 年样本 amount 非空率 100%                |
| 稳定 | **FAIL** | AKShare 窗口级重试后失败率 17.9%（要求 ≤10%）                    |
| 格式 | **PASS** | 三种代码格式（6 位 / `sh./sz.` / `.SS/.SZ`）均可正确互转         |

**根因不是 AKShare/BaoStock 本身不可用，而是本次 Spike 运行环境（非国内 Hermes 机器）到东方财富
（`push2his.eastmoney.com`，AKShare `fund_etf_hist_em` 的底层依赖）的网络链路不稳定**：约 35–50%
的单次 HTTP 请求会被 `RemoteDisconnected`（连接被对端直接 RST），指数退避重试后仍有约 18–21% 的
窗口（跨 8 只标的、28 个请求窗口，先后两次完整运行分别为 21.4% 与 17.9%）最终失败。覆盖门禁失败
的标的**在两次运行中并不是同一批**（第一次是 `159928`/`159920`，第二次是 `512480`/`511010`）——
说明是概率性网络抖动导致某个窗口耗尽 4 次重试后失败，随机落在哪只标的身上，不是特定标的/参数
本身不可获取。已对单个失败标的单独复测 8 次（间隔 2s）：前 3 次失败、后 5 次连续成功，进一步证实
这是连接级抖动而非该标的或该请求不可用。**以下 §2–§4 数据取自最终提交的 raw JSON 对应的那次运行。**

**依据任务前置要求：门禁未过，Task 1–5（enrichment migration、AKShare client + job、五年回填等）不得
进行，只能降级沿用 `etf_pool.avg_daily_turnover_yi`。** 脚本、摘要报告、raw JSON 均已提交，供国内
Hermes 机器复现——预期在国内网络下 AKShare 请求失败率会显著下降，需重跑本脚本确认门禁结果。

## 1. 运行环境与网络缓解

- 运行机器：`/Users/abnersun/Downloads/code/scheduled-tasks` 所在 Mac（非国内 Hermes 机器，用于代码
  就绪与真实网络行为记录；**门禁最终结论以国内机复跑为准**）。
- Python 3.13.9；`pip install -e '.[domestic]'` 安装 `akshare==1.18.64`、`baostock==0.9.3` 成功。
- **必需网络缓解（已在脚本内自动生效，非伪造通过）**：`push2his.eastmoney.com` 对
  `requests` 库默认 User-Agent（`python-requests/x.y`）直接返回连接重置（`RemoteDisconnected`），
  与系统代理无关——已分别验证：
  - 禁用系统代理（`HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY=*`）后问题依旧；
  - `curl`（默认 UA 为 `curl/x.y`）对同一 URL + 参数直接返回 200；
  - Python `requests` 显式传入 `User-Agent: curl/8.7.1` 后同样返回 200。
    脚本对进程内 `requests.Session.request` 做了 monkeypatch，补一个常见浏览器 UA
    （详见 `patch_requests_user_agent()`），这是社区已知的 AKShare/东财反爬缓解手段，
    不修改任何业务参数。**即便加了此缓解，仍观测到 ~35–50% 的单次请求随机失败**（见 §3），
    说明还叠加了本机到大陆站点的网络链路抖动，而不仅是 UA 拦截问题。

## 2. 覆盖与深度（§4.1）

真实池内代码来源：2026-07-15 通过只读 SQL 查询 Supabase `public.etf_pool_snapshots`
（**当时** live rename 迁移未执行，`etf_pool` 尚不存在；本 Spike 只借该表拿真实池内 25 只标的与分类，
不涉及任何写操作，与生产表名切换无关）。

> **现状（2026-07-16）**：live 已更名为 `etf_pool`，旧名 `etf_pool_snapshots` 不存在。下文保留 Spike 当日写法，勿当作当前表名。

### 2.1 正式池内代表（宽基/行业/跨境 × 沪深各 1 只）

| 代码   | 名称        | 分类 | 交易所 |
| ------ | ----------- | ---- | ------ |
| 510300 | 沪深 300ETF | 宽基 | SSE    |
| 159915 | 创业板 ETF  | 宽基 | SZSE   |
| 512480 | 半导体 ETF  | 行业 | SSE    |
| 159928 | 消费 ETF    | 行业 | SZSE   |
| 513100 | 纳指 ETF    | 跨境 | SSE    |
| 159920 | 恒生 ETF    | 跨境 | SZSE   |

### 2.2 池外能力探针（**不在正式池，仅验证源能力**）

| 代码   | 名称     | 说明                         |
| ------ | -------- | ---------------------------- |
| 511010 | 国债 ETF | 债券类探针，**未进入正式池** |
| 518880 | 黄金 ETF | 商品类探针，**未进入正式池** |

### 2.3 覆盖结果（AKShare，窗口 ≤366 天，本次拉取范围约近 420 天）

| 代码           | 实际交易日数            | amount 非空率 | ≥250 日                                      | 非空率 ≥90% |
| -------------- | ----------------------- | ------------- | -------------------------------------------- | ----------- |
| 510300         | 989（含 5 年深度样本）  | 100%          | ✅                                           | ✅          |
| 159915         | 1214（含 5 年深度样本） | 100%          | ✅                                           | ✅          |
| 512480         | 38                      | 100%          | ❌（主窗口重试后仍失败，仅剩尾部小窗口）     | ✅          |
| 159928         | 281                     | 100%          | ✅                                           | ✅          |
| 513100         | 281                     | 100%          | ✅                                           | ✅          |
| 159920         | 281                     | 100%          | ✅                                           | ✅          |
| 511010（池外） | 38                      | 100%          | ❌（单列，不计入正式池门禁；同样是窗口失败） | ✅          |
| 518880（池外） | 281                     | 100%          | ✅（单列，不计入正式池门禁）                 | ✅          |

**成交额非空率在所有已成功拉到的数据中都是 100%**——数据质量本身没有问题，覆盖门禁失败纯粹是
`512480`、`511010`（池外）各丢了一个网络窗口（见 §0，两次运行失败标的不同，证实是随机抖动）。

### 2.4 深度抽样（近 5 年，`510300`、`159915`）

- `510300`：5 年窗口分段拉取，合计 989 行，amount 非空率 100%。
- `159915`：5 年窗口分段拉取，合计 1214 行，amount 非空率 100%。
- **深度门禁 PASS**：两只标的在已成功拉取的数据上 amount 非空率均为 100%（≥90% 要求）。

### 2.5 代码格式映射（§格式门禁）

对全部 8 只标的验证 `6位码 ↔ sh./sz.前缀 ↔ .SS/.SZ后缀` 三向互转，全部 `round_trip_ok=true`
（详见 raw JSON `code_format_mapping_checks`）。示例：`510300 ↔ sh.510300 ↔ 510300.SS`。

## 3. 稳定性与性能（§4.3）

### 3.1 AKShare（主源）

- 总窗口数 28（8 只覆盖标的 × 1~2 窗口 + 2 只深度标的 × 6 窗口）
- 平均单次请求延迟 253.9ms，P95 364.7ms（不含 sleep/backoff 等待时间）
- 52 次总尝试中，**28 个窗口里 11 个窗口需要重试**（即首次请求失败但重试后成功）
- **5 个窗口在 4 次尝试（首次 + 3 次重试，2s/4s/8s ± 20% jitter）后仍失败**，
  **窗口级失败率 17.9%（门禁要求 ≤10%，FAIL）**
- 失败错误类型单一：`ConnectionError: RemoteDisconnected('Remote end closed connection without response')`，
  未观测到限流响应码（如 403/429）或空 DataFrame——是连接级 RST，不是业务级拒绝
- 复测验证：对失败标的（`159920`）单独追加 8 次请求（间隔 2s），前 3 次失败、后 5 次全部成功，
  证实是概率性抖动而非该标的/该请求参数本身不可用；先后两次完整运行中覆盖门禁失败的标的也不同
  （见 §0），进一步排除标的级问题

### 3.2 BaoStock（单列统计，不稀释主源分母）

- `query_history_k_data_plus`：登录成功，全部 16 个窗口请求 **0 次失败**（100% 成功率），
  平均延迟 109.4ms、P95 261.4ms——TCP 协议本身在本机网络下反而比 AKShare 的 HTTPS 更稳定
- **但历史覆盖起点很晚**：对 8 只标的的实测显示，`sh.510300` 等 ETF 的 K 线数据从约
  **2026-01-05** 才开始有记录，此前（包括 2023–2025 年全部测试日期）稳定返回
  `error_code='0'`（成功）但 0 行数据——即 BaoStock 服务端认为查询成功，只是该区间无数据，
  **不是网络问题、也不是代码错误**（已用 `query_all_stock` 交叉确认：2024-01-15 该日
  8 只 ETF 代码均不在证券列表中；2026-07-14 该日 8 只全部在列，与 K 线起点时间吻合）
- **结论**：BaoStock 的 ETF 日 K 覆盖在本次 Spike 环境下仅约 6 个月，**无法承担近 5 年深度
  或历史回填职责**，只能作为最近数据的辅助交叉核对源——这与计划中"BaoStock 单列统计、
  不得稀释主源分母"的定位一致，只是覆盖窗口比预期更短，需在国内机复测确认是否为账号/
  版本限制还是服务端真实历史深度

> **生产实现备注（与上文结论一致）**：`sync_etf_enrich_akshare` 在东财 job 级熔断后会
> **机会性**回退 BaoStock 拉成交额，但 **空结果不计窗口成功**（`baostock_fallback=empty_result`），
> 因此不会把「服务端无历史」误标成已补齐。五年回填仍以 AKShare 为主、`fill_rate` 验收为准。

### 3.3 BaoStock `query_trade_dates`（交易日历）

- 请求 2026 年全年日历成功，字段 `['calendar_date', 'is_trading_day']`，与计划假设一致
- 可映射为 `trade_calendar.cal_date = calendar_date`，`is_open = is_trading_day`，`market='CN'`（固定）

## 4. 数据质量（§4.2）

### 4.1 成交额单位

抽样校验 `amount ≈ close × volume(手) × 100`，容忍相对误差 15%（因 amount 是当日 VWAP× 量，
非收盘价 × 量，天然存在偏差，15% 为经验容忍阈值，已记录于 raw JSON `amount_consistency_checks.*.tolerance`）：

- 全部 8 只标的、合计约 2600+ 条可校验记录，**通过率 100%，0 条异常**
- 结论：**amount 单位确认为元**，符合计划假设

### 4.2 AKShare vs BaoStock 同标的 amount 交叉对比

本次运行 **8/8 只标的**均在 BaoStock 覆盖窗口（约 2026-01 起）与 AKShare 数据有重叠日期，
抽样最近 5 个交易日（2026-07-08~07-14）对比，全部标的 `relative_diff` 均为 **0.0（完全一致）**，
详见 raw JSON `cross_source_amount_comparison`。

**AKShare 与 BaoStock 在有重叠数据的交易日上 amount 完全一致**——两者最终都取自交易所原始行情，
是强正向信号：只要能拿到数据，两源口径一致，可互相验证，§4.2 该项要求（抽样 ≥5 日）已满足。

## 5. 硬门禁判定详情（§4.4，对应脚本 `gate_evaluation`）

```
unit:       PASS  (min pass_rate = 1.0, 要求 >= 0.95)
coverage:   FAIL  (512480: 38日<250；511010池外: 38日<250；其余6只全部达标)
depth:      PASS  (510300: 989日100%非空；159915: 1214日100%非空)
stability:  FAIL  (AKShare 窗口失败率 17.9% > 10%；BaoStock 单列 0% 失败，未计入分母)
format:     PASS  (8/8 标的三向格式互转正确)

overall_gate_passed: false
```

完整明细（每个窗口的每次尝试、延迟、错误类型）见 `doc/spike-baostock-akshare.raw.json`。

## 6. 建议与后续动作

1. **不进入 Task 1–5**：按计划 §4.4，硬门禁未过，五年回填与 enrichment job 暂缓，流动性排序
   继续使用 `etf_pool.avg_daily_turnover_yi`。
2. **国内 Hermes 机器复现**：`git pull` 本分支后执行：
   ```bash
   pip install -e '.[domestic]'
   python scripts/spike_baostock_akshare.py --out-json doc/spike-baostock-akshare.raw.json
   ```
   预期国内网络下 AKShare 到东财的连接失败率会显著低于本次的 17.9%（本次失败模式是连接级
   RST，与地域网络质量强相关）；若失败率降到 ≤10% 且覆盖门禁涉及标的补齐 ≥250 日，则可判
   定门禁 PASS，继续 Task 1–5。
3. **BaoStock 覆盖起点需在国内机复核**：确认 2026-01 前的 ETF K 线是否为服务端真实无数据、
   还是本账号/版本的限制（可尝试 `bs.set_API_key` 或社区文档核实 ETF 数据开放策略）。
   即使国内机复测起点仍晚，也不影响整体判定——BaoStock 本就只是辅助单列统计。
4. **UA 缓解措施需固化进生产 client**：Task 3 的 AKShare client 实现必须包含本 Spike 验证的
   `requests.Session` 默认 UA 补丁（或等价方案），否则即使在国内网络下也可能遇到相同的
   东财 WAF 拦截。

## 7. 交付物清单

- `pyproject.toml`：新增 `[project.optional-dependencies].domestic`（`akshare==1.18.64`、
  `baostock==0.9.3`），未进入主依赖，避免拖累海外 GitHub Actions runner 安装
- `scripts/spike_baostock_akshare.py`：可复现、版本化 Spike 脚本
- `doc/spike-baostock-akshare.md`：本报告
- `doc/spike-baostock-akshare.raw.json`：机器可读全量原始结果（请求级明细、门禁判定）

未对 live Supabase 做任何写操作或 DDL；池内代码仅用于只读查询确认真实分类，未落库。
