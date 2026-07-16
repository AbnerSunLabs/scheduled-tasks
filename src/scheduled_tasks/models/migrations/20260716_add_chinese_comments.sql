-- 全库表/列/视图中文注释（COMMENT）
-- 幂等：可重复执行；COMMENT ON 会覆盖既有注释。
-- 边界：仅元数据，不改表结构/RLS/数据；对象不存在时跳过（兼容未跑齐 migration 的环境）。

-- ---------------------------------------------------------------------------
-- 工具：表存在时执行 comment
-- ---------------------------------------------------------------------------
create or replace function public._tmp_comment_if_exists(
  p_kind text,  -- 'table' | 'column' | 'view'
  p_ident text, -- e.g. public.etf_daily 或 public.etf_daily.close
  p_comment text
)
returns void
language plpgsql
as $$
declare
  tbl text;
  col text;
  col_exists boolean;
begin
  if p_kind = 'column' then
    tbl := regexp_replace(p_ident, '\.[^.]+$', '');
    col := substring(p_ident from '[^.]+$');
  else
    tbl := p_ident;
  end if;

  if to_regclass(tbl) is null then
    return;
  end if;

  if p_kind = 'column' then
    select exists (
      select 1
      from pg_catalog.pg_attribute a
      join pg_catalog.pg_class c on c.oid = a.attrelid
      join pg_catalog.pg_namespace n on n.oid = c.relnamespace
      where n.nspname = split_part(tbl, '.', 1)
        and c.relname = split_part(tbl, '.', 2)
        and a.attname = col
        and a.attnum > 0
        and not a.attisdropped
    ) into col_exists;
    if not col_exists then
      return;
    end if;
  end if;

  if p_kind = 'table' then
    execute format('comment on table %s is %L', p_ident, p_comment);
  elsif p_kind = 'view' then
    execute format('comment on view %s is %L', p_ident, p_comment);
  elsif p_kind = 'column' then
    execute format('comment on column %s is %L', p_ident, p_comment);
  else
    raise exception 'unknown kind: %', p_kind;
  end if;
end;
$$;

-- ===========================================================================
-- indices
-- ===========================================================================
select public._tmp_comment_if_exists('table', 'public.indices', '指数元数据（暂不维护）');
select public._tmp_comment_if_exists('column', 'public.indices.code', '指数代码，格式 000300.SH / .SZ / .CSI');
select public._tmp_comment_if_exists('column', 'public.indices.name', '指数名称');
select public._tmp_comment_if_exists('column', 'public.indices.category', '分类标签');
select public._tmp_comment_if_exists('column', 'public.indices.display_order', '展示排序');
select public._tmp_comment_if_exists('column', 'public.indices.created_at', '创建时间');
select public._tmp_comment_if_exists('column', 'public.indices.updated_at', '更新时间');

-- ===========================================================================
-- index_daily_prices
-- ===========================================================================
select public._tmp_comment_if_exists('table', 'public.index_daily_prices', '指数日收盘价（暂不维护）');
select public._tmp_comment_if_exists('column', 'public.index_daily_prices.index_code', '指数代码，关联 indices.code');
select public._tmp_comment_if_exists('column', 'public.index_daily_prices.trade_date', '交易日');
select public._tmp_comment_if_exists('column', 'public.index_daily_prices.close', '收盘价');
select public._tmp_comment_if_exists('column', 'public.index_daily_prices.created_at', '创建时间');
select public._tmp_comment_if_exists('column', 'public.index_daily_prices.updated_at', '更新时间');

-- ===========================================================================
-- index_daily_valuations
-- ===========================================================================
select public._tmp_comment_if_exists('table', 'public.index_daily_valuations', '指数日估值 PE/PB（暂不维护）');
select public._tmp_comment_if_exists('column', 'public.index_daily_valuations.index_code', '指数代码，关联 indices.code');
select public._tmp_comment_if_exists('column', 'public.index_daily_valuations.trade_date', '交易日');
select public._tmp_comment_if_exists('column', 'public.index_daily_valuations.pe_ttm', '市盈率（TTM）');
select public._tmp_comment_if_exists('column', 'public.index_daily_valuations.pb', '市净率');
select public._tmp_comment_if_exists('column', 'public.index_daily_valuations.source', '估值数据来源');
select public._tmp_comment_if_exists('column', 'public.index_daily_valuations.created_at', '创建时间');
select public._tmp_comment_if_exists('column', 'public.index_daily_valuations.updated_at', '更新时间');

-- ===========================================================================
-- index_industry_weights
-- ===========================================================================
select public._tmp_comment_if_exists('table', 'public.index_industry_weights', '指数行业权重（申万分级，暂不维护）');
select public._tmp_comment_if_exists('column', 'public.index_industry_weights.index_code', '指数代码，关联 indices.code');
select public._tmp_comment_if_exists('column', 'public.index_industry_weights.as_of_date', '权重生效日');
select public._tmp_comment_if_exists('column', 'public.index_industry_weights.sw_level', '申万级别：sw1 / sw2 / sw3');
select public._tmp_comment_if_exists('column', 'public.index_industry_weights.industry_name', '行业名称');
select public._tmp_comment_if_exists('column', 'public.index_industry_weights.weight_pct', '权重占比（%）');
select public._tmp_comment_if_exists('column', 'public.index_industry_weights.created_at', '创建时间');
select public._tmp_comment_if_exists('column', 'public.index_industry_weights.updated_at', '更新时间');

-- ===========================================================================
-- sync_runs
-- ===========================================================================
select public._tmp_comment_if_exists('table', 'public.sync_runs', '同步任务执行记录');
select public._tmp_comment_if_exists('column', 'public.sync_runs.id', '自增主键');
select public._tmp_comment_if_exists('column', 'public.sync_runs.job_name', '任务名，如 sync_etf_kline_yfinance');
select public._tmp_comment_if_exists('column', 'public.sync_runs.status', '状态：running / success / partial / failed');
select public._tmp_comment_if_exists('column', 'public.sync_runs.started_at', '开始时间');
select public._tmp_comment_if_exists('column', 'public.sync_runs.finished_at', '结束时间');
select public._tmp_comment_if_exists('column', 'public.sync_runs.index_codes', '历史命名遗留：本 run 涉及的标的代码（不限于指数）');
select public._tmp_comment_if_exists('column', 'public.sync_runs.success_codes', '成功处理的标的代码');
select public._tmp_comment_if_exists('column', 'public.sync_runs.failure_count', '失败数量');
select public._tmp_comment_if_exists('column', 'public.sync_runs.success_count', '成功数量');
select public._tmp_comment_if_exists('column', 'public.sync_runs.error_summary', '失败详情（JSON 数组）');
select public._tmp_comment_if_exists('column', 'public.sync_runs.meta', '结构化运行上下文（mode、pool_size、adj 结果等）');
select public._tmp_comment_if_exists('column', 'public.sync_runs.created_at', '创建时间');

-- ===========================================================================
-- etf_pool
-- ===========================================================================
select public._tmp_comment_if_exists('table', 'public.etf_pool', 'ETF 当前池主数据（每标的一行，非按日历史快照）');
select public._tmp_comment_if_exists('column', 'public.etf_pool.etf_code', 'ETF 代码（6 位数字，无交易所后缀）');
select public._tmp_comment_if_exists('column', 'public.etf_pool.etf_name', 'ETF 名称');
select public._tmp_comment_if_exists('column', 'public.etf_pool.category', '分类');
select public._tmp_comment_if_exists('column', 'public.etf_pool.direction', '方向标签');
select public._tmp_comment_if_exists('column', 'public.etf_pool.source', '数据来源');
select public._tmp_comment_if_exists('column', 'public.etf_pool.tracking_index_code', '跟踪指数代码');
select public._tmp_comment_if_exists('column', 'public.etf_pool.tracking_index_name', '跟踪指数名称');
select public._tmp_comment_if_exists('column', 'public.etf_pool.aum_yi', '规模（亿元）');
select public._tmp_comment_if_exists('column', 'public.etf_pool.avg_daily_turnover_yi', '日均成交额（亿元）');
select public._tmp_comment_if_exists('column', 'public.etf_pool.premium_discount', '折溢价');
select public._tmp_comment_if_exists('column', 'public.etf_pool.expense_ratio', '管理费率');
select public._tmp_comment_if_exists('column', 'public.etf_pool.snapshot_date', '池信息最近刷新日（非快照版本键；可跨行不一致）');
select public._tmp_comment_if_exists('column', 'public.etf_pool.updated_at', '行更新时间');

-- ===========================================================================
-- etf_daily
-- ===========================================================================
select public._tmp_comment_if_exists('table', 'public.etf_daily', 'ETF 日行情（不复权 OHLCV + 前/后复权 + 来源）');
select public._tmp_comment_if_exists('column', 'public.etf_daily.etf_code', 'ETF 代码');
select public._tmp_comment_if_exists('column', 'public.etf_daily.trade_date', '交易日');
select public._tmp_comment_if_exists('column', 'public.etf_daily.open', '开盘价（不复权）');
select public._tmp_comment_if_exists('column', 'public.etf_daily.high', '最高价（不复权）');
select public._tmp_comment_if_exists('column', 'public.etf_daily.low', '最低价（不复权）');
select public._tmp_comment_if_exists('column', 'public.etf_daily.close', '收盘价（不复权）');
select public._tmp_comment_if_exists('column', 'public.etf_daily.volume', '成交量（手）');
select public._tmp_comment_if_exists('column', 'public.etf_daily.amount', '成交额（元）；国内补数主源 AKShare');
select public._tmp_comment_if_exists('column', 'public.etf_daily.nav', '净值（非本 job 字段，upsert 不覆盖）');
select public._tmp_comment_if_exists('column', 'public.etf_daily.premium_rate', '溢价率（非本 job 字段，upsert 不覆盖）');
select public._tmp_comment_if_exists('column', 'public.etf_daily.fund_size', '基金规模（非本 job 字段，upsert 不覆盖）');
select public._tmp_comment_if_exists('column', 'public.etf_daily.listing_days', '上市天数（非本 job 字段，upsert 不覆盖）');
select public._tmp_comment_if_exists('column', 'public.etf_daily.updated_at', '价格侧（OHLCV/复权/price_source）最近写入时间；enrichment 禁止刷新本列');
select public._tmp_comment_if_exists('column', 'public.etf_daily.bid_price', '买一价（非本 job 字段，upsert 不覆盖）');
select public._tmp_comment_if_exists('column', 'public.etf_daily.ask_price', '卖一价（非本 job 字段，upsert 不覆盖）');
select public._tmp_comment_if_exists('column', 'public.etf_daily.open_qfq', '开盘价（前复权）');
select public._tmp_comment_if_exists('column', 'public.etf_daily.high_qfq', '最高价（前复权）');
select public._tmp_comment_if_exists('column', 'public.etf_daily.low_qfq', '最低价（前复权）');
select public._tmp_comment_if_exists('column', 'public.etf_daily.close_qfq', '收盘价（前复权）');
select public._tmp_comment_if_exists('column', 'public.etf_daily.open_hfq', '开盘价（后复权）');
select public._tmp_comment_if_exists('column', 'public.etf_daily.high_hfq', '最高价（后复权）');
select public._tmp_comment_if_exists('column', 'public.etf_daily.low_hfq', '最低价（后复权）');
select public._tmp_comment_if_exists('column', 'public.etf_daily.close_hfq', '收盘价（后复权）');
select public._tmp_comment_if_exists('column', 'public.etf_daily.price_source', '不复权 OHLCV 写入来源；adj_check 不更新本列');
select public._tmp_comment_if_exists('column', 'public.etf_daily.amount_source', '成交额写入来源（如 akshare）；与 price_source 独立');
select public._tmp_comment_if_exists('column', 'public.etf_daily.amount_updated_at', '成交额最近补数时间；不等于价格新鲜度（价格看 updated_at）');

-- ===========================================================================
-- etf_valuation_snapshots
-- ===========================================================================
select public._tmp_comment_if_exists('table', 'public.etf_valuation_snapshots', '跟踪指数估值快照（本仓库 job 不写）');
select public._tmp_comment_if_exists('column', 'public.etf_valuation_snapshots.tracking_index_code', '跟踪指数代码');
select public._tmp_comment_if_exists('column', 'public.etf_valuation_snapshots.trade_date', '估值数据日期');
select public._tmp_comment_if_exists('column', 'public.etf_valuation_snapshots.current_pe_ttm', '当前 PE（TTM）');
select public._tmp_comment_if_exists('column', 'public.etf_valuation_snapshots.pe_ttm_avg_5y', '近 5 年 PE 均值');
select public._tmp_comment_if_exists('column', 'public.etf_valuation_snapshots.pe_ttm_avg_10y', '近 10 年 PE 均值');
select public._tmp_comment_if_exists('column', 'public.etf_valuation_snapshots.updated_at', '更新时间');

-- ===========================================================================
-- fx_rates
-- ===========================================================================
select public._tmp_comment_if_exists('table', 'public.fx_rates', '日频汇率（USD/CNY/HKD 三角；Frankfurter/ECB）');
select public._tmp_comment_if_exists('column', 'public.fx_rates.rate_date', '汇率日期（领域字段 FxRate.date 映射本列）');
select public._tmp_comment_if_exists('column', 'public.fx_rates.from_currency', '源币种：CNY / HKD / USD');
select public._tmp_comment_if_exists('column', 'public.fx_rates.to_currency', '目标币种：CNY / HKD / USD');
select public._tmp_comment_if_exists('column', 'public.fx_rates.rate', '汇率（1 from = rate to）');
select public._tmp_comment_if_exists('column', 'public.fx_rates.source', '数据来源，默认 frankfurter');
select public._tmp_comment_if_exists('column', 'public.fx_rates.created_at', '创建时间');
select public._tmp_comment_if_exists('column', 'public.fx_rates.updated_at', '更新时间');

-- ===========================================================================
-- trade_calendar
-- ===========================================================================
select public._tmp_comment_if_exists('table', 'public.trade_calendar', '市场级交易日历；本期仅写入 market=CN（沪深一致，不做 SSE/SZSE 分表）');
select public._tmp_comment_if_exists('column', 'public.trade_calendar.market', '市场代码，本期仅 CN');
select public._tmp_comment_if_exists('column', 'public.trade_calendar.cal_date', '日历日');
select public._tmp_comment_if_exists('column', 'public.trade_calendar.is_open', '是否开市');
select public._tmp_comment_if_exists('column', 'public.trade_calendar.updated_at', '行更新时间');

-- ===========================================================================
-- 驾驶舱账本 12 表
-- ===========================================================================
select public._tmp_comment_if_exists('table', 'public.portfolio_settings', '组合设置（每用户一行）');
select public._tmp_comment_if_exists('column', 'public.portfolio_settings.id', '主键');
select public._tmp_comment_if_exists('column', 'public.portfolio_settings.user_id', '所属用户（auth.users）');
select public._tmp_comment_if_exists('column', 'public.portfolio_settings.base_currency', '基准货币：CNY / HKD / USD');
select public._tmp_comment_if_exists('column', 'public.portfolio_settings.benchmark_id', '基准标的标识');
select public._tmp_comment_if_exists('column', 'public.portfolio_settings.relative_drift_threshold', '相对漂移阈值（触发再平衡）');
select public._tmp_comment_if_exists('column', 'public.portfolio_settings.absolute_drift_threshold', '绝对漂移阈值（触发再平衡）');
select public._tmp_comment_if_exists('column', 'public.portfolio_settings.review_cadence_days', '复盘周期（天）');
select public._tmp_comment_if_exists('column', 'public.portfolio_settings.created_at', '创建时间');
select public._tmp_comment_if_exists('column', 'public.portfolio_settings.updated_at', '更新时间');

select public._tmp_comment_if_exists('table', 'public.etf_instruments', '用户自定义 ETF 标的库');
select public._tmp_comment_if_exists('column', 'public.etf_instruments.id', '主键');
select public._tmp_comment_if_exists('column', 'public.etf_instruments.user_id', '所属用户');
select public._tmp_comment_if_exists('column', 'public.etf_instruments.symbol', '交易代码');
select public._tmp_comment_if_exists('column', 'public.etf_instruments.name', '名称');
select public._tmp_comment_if_exists('column', 'public.etf_instruments.market', '市场：CN / HK / US');
select public._tmp_comment_if_exists('column', 'public.etf_instruments.currency', '计价货币：CNY / HKD / USD');
select public._tmp_comment_if_exists('column', 'public.etf_instruments.asset_class', '资产类别');
select public._tmp_comment_if_exists('column', 'public.etf_instruments.tracking_index', '跟踪指数');
select public._tmp_comment_if_exists('column', 'public.etf_instruments.benchmark_id', '基准标识');
select public._tmp_comment_if_exists('column', 'public.etf_instruments.expense_ratio', '管理费率');
select public._tmp_comment_if_exists('column', 'public.etf_instruments.distribution_policy', '分红政策');
select public._tmp_comment_if_exists('column', 'public.etf_instruments.liquidity_tag', '流动性标签');
select public._tmp_comment_if_exists('column', 'public.etf_instruments.valuation_tag', '估值标签');
select public._tmp_comment_if_exists('column', 'public.etf_instruments.default_allocation_role', '默认仓位角色：core / satellite / cash / watch');
select public._tmp_comment_if_exists('column', 'public.etf_instruments.grid_eligible', '是否可用于网格策略');
select public._tmp_comment_if_exists('column', 'public.etf_instruments.created_at', '创建时间');
select public._tmp_comment_if_exists('column', 'public.etf_instruments.updated_at', '更新时间');

select public._tmp_comment_if_exists('table', 'public.target_allocations', '目标仓位配置');
select public._tmp_comment_if_exists('column', 'public.target_allocations.id', '主键');
select public._tmp_comment_if_exists('column', 'public.target_allocations.user_id', '所属用户');
select public._tmp_comment_if_exists('column', 'public.target_allocations.instrument_id', '标的标识（软引用，非强制 FK）');
select public._tmp_comment_if_exists('column', 'public.target_allocations.target_weight', '目标权重（0~1）');
select public._tmp_comment_if_exists('column', 'public.target_allocations.allocation_role', '仓位角色：core / satellite / cash / watch');
select public._tmp_comment_if_exists('column', 'public.target_allocations.updated_at', '更新时间');
select public._tmp_comment_if_exists('column', 'public.target_allocations.created_at', '创建时间');

select public._tmp_comment_if_exists('table', 'public.positions', '持仓快照');
select public._tmp_comment_if_exists('column', 'public.positions.id', '主键');
select public._tmp_comment_if_exists('column', 'public.positions.user_id', '所属用户');
select public._tmp_comment_if_exists('column', 'public.positions.instrument_id', '标的标识');
select public._tmp_comment_if_exists('column', 'public.positions.as_of_date', '持仓日期');
select public._tmp_comment_if_exists('column', 'public.positions.shares', '持股数量');
select public._tmp_comment_if_exists('column', 'public.positions.average_cost', '平均成本');
select public._tmp_comment_if_exists('column', 'public.positions.current_price', '现价（原币）');
select public._tmp_comment_if_exists('column', 'public.positions.market_value', '市值（原币）');
select public._tmp_comment_if_exists('column', 'public.positions.currency', '持仓货币');
select public._tmp_comment_if_exists('column', 'public.positions.fx_rate_to_base', '兑基准货币汇率');
select public._tmp_comment_if_exists('column', 'public.positions.market_value_base', '市值（基准货币）');
select public._tmp_comment_if_exists('column', 'public.positions.created_at', '创建时间');
select public._tmp_comment_if_exists('column', 'public.positions.updated_at', '更新时间');

select public._tmp_comment_if_exists('table', 'public.cash_accounts', '现金账户余额');
select public._tmp_comment_if_exists('column', 'public.cash_accounts.id', '主键');
select public._tmp_comment_if_exists('column', 'public.cash_accounts.user_id', '所属用户');
select public._tmp_comment_if_exists('column', 'public.cash_accounts.currency', '货币');
select public._tmp_comment_if_exists('column', 'public.cash_accounts.as_of_date', '余额日期');
select public._tmp_comment_if_exists('column', 'public.cash_accounts.balance', '余额（原币）');
select public._tmp_comment_if_exists('column', 'public.cash_accounts.fx_rate_to_base', '兑基准货币汇率');
select public._tmp_comment_if_exists('column', 'public.cash_accounts.balance_base', '余额（基准货币）');
select public._tmp_comment_if_exists('column', 'public.cash_accounts.created_at', '创建时间');
select public._tmp_comment_if_exists('column', 'public.cash_accounts.updated_at', '更新时间');

select public._tmp_comment_if_exists('table', 'public.rebalance_plans', '再平衡计划');
select public._tmp_comment_if_exists('column', 'public.rebalance_plans.id', '主键');
select public._tmp_comment_if_exists('column', 'public.rebalance_plans.user_id', '所属用户');
select public._tmp_comment_if_exists('column', 'public.rebalance_plans.status', '状态：draft / active / completed / cancelled');
select public._tmp_comment_if_exists('column', 'public.rebalance_plans.reason', '计划说明');
select public._tmp_comment_if_exists('column', 'public.rebalance_plans.trigger_reason', '触发原因：absolute_drift / relative_drift / calendar_review / cash_deployment');
select public._tmp_comment_if_exists('column', 'public.rebalance_plans.target_weights', '目标权重（JSON）');
select public._tmp_comment_if_exists('column', 'public.rebalance_plans.planned_trades', '计划交易列表（JSON）');
select public._tmp_comment_if_exists('column', 'public.rebalance_plans.created_at', '创建时间');
select public._tmp_comment_if_exists('column', 'public.rebalance_plans.updated_at', '更新时间');

select public._tmp_comment_if_exists('table', 'public.grid_plans', '网格交易计划');
select public._tmp_comment_if_exists('column', 'public.grid_plans.id', '主键');
select public._tmp_comment_if_exists('column', 'public.grid_plans.user_id', '所属用户');
select public._tmp_comment_if_exists('column', 'public.grid_plans.instrument_id', '标的标识');
select public._tmp_comment_if_exists('column', 'public.grid_plans.status', '状态：draft / active / paused / closed');
select public._tmp_comment_if_exists('column', 'public.grid_plans.params', '网格参数（JSON）');
select public._tmp_comment_if_exists('column', 'public.grid_plans.legs', '网格腿明细（JSON）');
select public._tmp_comment_if_exists('column', 'public.grid_plans.aggregated_rows', '聚合行（JSON）');
select public._tmp_comment_if_exists('column', 'public.grid_plans.total_budget', '总预算');
select public._tmp_comment_if_exists('column', 'public.grid_plans.remaining_budget', '剩余预算');
select public._tmp_comment_if_exists('column', 'public.grid_plans.created_at', '创建时间');
select public._tmp_comment_if_exists('column', 'public.grid_plans.updated_at', '更新时间');

select public._tmp_comment_if_exists('table', 'public.decision_logs', '投资决策日志');
select public._tmp_comment_if_exists('column', 'public.decision_logs.id', '主键');
select public._tmp_comment_if_exists('column', 'public.decision_logs.user_id', '所属用户');
select public._tmp_comment_if_exists('column', 'public.decision_logs.title', '标题');
select public._tmp_comment_if_exists('column', 'public.decision_logs.hypothesis', '假设/论点');
select public._tmp_comment_if_exists('column', 'public.decision_logs.validation_condition', '验证成立条件');
select public._tmp_comment_if_exists('column', 'public.decision_logs.invalid_condition', '证伪条件');
select public._tmp_comment_if_exists('column', 'public.decision_logs.review_date', '计划复盘日');
select public._tmp_comment_if_exists('column', 'public.decision_logs.status', '状态：open / validated / invalidated / archived');
select public._tmp_comment_if_exists('column', 'public.decision_logs.linked_instrument_id', '关联标的');
select public._tmp_comment_if_exists('column', 'public.decision_logs.linked_trade_id', '关联成交记录');
select public._tmp_comment_if_exists('column', 'public.decision_logs.linked_rebalance_plan_id', '关联再平衡计划');
select public._tmp_comment_if_exists('column', 'public.decision_logs.linked_grid_plan_id', '关联网格计划');
select public._tmp_comment_if_exists('column', 'public.decision_logs.created_at', '创建时间');
select public._tmp_comment_if_exists('column', 'public.decision_logs.updated_at', '更新时间');

select public._tmp_comment_if_exists('table', 'public.trade_records', '成交记录');
select public._tmp_comment_if_exists('column', 'public.trade_records.id', '主键');
select public._tmp_comment_if_exists('column', 'public.trade_records.user_id', '所属用户');
select public._tmp_comment_if_exists('column', 'public.trade_records.instrument_id', '标的标识');
select public._tmp_comment_if_exists('column', 'public.trade_records.trade_date', '成交日');
select public._tmp_comment_if_exists('column', 'public.trade_records.settlement_date', '交割日');
select public._tmp_comment_if_exists('column', 'public.trade_records.side', '方向：buy / sell');
select public._tmp_comment_if_exists('column', 'public.trade_records.price', '成交价');
select public._tmp_comment_if_exists('column', 'public.trade_records.quantity', '成交数量');
select public._tmp_comment_if_exists('column', 'public.trade_records.fee', '手续费');
select public._tmp_comment_if_exists('column', 'public.trade_records.tax', '税费');
select public._tmp_comment_if_exists('column', 'public.trade_records.currency', '成交货币');
select public._tmp_comment_if_exists('column', 'public.trade_records.fx_rate_to_base', '兑基准货币汇率');
select public._tmp_comment_if_exists('column', 'public.trade_records.execution_intent', '执行意图：rebalance / grid / manual');
select public._tmp_comment_if_exists('column', 'public.trade_records.rebalance_plan_id', '关联再平衡计划');
select public._tmp_comment_if_exists('column', 'public.trade_records.grid_plan_id', '关联网格计划');
select public._tmp_comment_if_exists('column', 'public.trade_records.decision_log_id', '关联决策日志');
select public._tmp_comment_if_exists('column', 'public.trade_records.broker_ref', '券商成交编号');
select public._tmp_comment_if_exists('column', 'public.trade_records.import_row_index', '导入行号');
select public._tmp_comment_if_exists('column', 'public.trade_records.import_hash', '导入去重哈希');
select public._tmp_comment_if_exists('column', 'public.trade_records.note', '备注');
select public._tmp_comment_if_exists('column', 'public.trade_records.created_at', '创建时间');
select public._tmp_comment_if_exists('column', 'public.trade_records.updated_at', '更新时间');

select public._tmp_comment_if_exists('table', 'public.cash_flows', '现金流记录');
select public._tmp_comment_if_exists('column', 'public.cash_flows.id', '主键');
select public._tmp_comment_if_exists('column', 'public.cash_flows.user_id', '所属用户');
select public._tmp_comment_if_exists('column', 'public.cash_flows.flow_date', '发生日');
select public._tmp_comment_if_exists('column', 'public.cash_flows.type', '类型：deposit / withdrawal / dividend / fee / tax / interest / fx_exchange');
select public._tmp_comment_if_exists('column', 'public.cash_flows.amount', '金额（原币）');
select public._tmp_comment_if_exists('column', 'public.cash_flows.currency', '货币');
select public._tmp_comment_if_exists('column', 'public.cash_flows.fx_rate_to_base', '兑基准货币汇率');
select public._tmp_comment_if_exists('column', 'public.cash_flows.amount_base', '金额（基准货币）');
select public._tmp_comment_if_exists('column', 'public.cash_flows.instrument_id', '关联标的（如分红）');
select public._tmp_comment_if_exists('column', 'public.cash_flows.counter_currency', '兑换对手货币（fx_exchange）');
select public._tmp_comment_if_exists('column', 'public.cash_flows.counter_amount', '兑换对手金额（fx_exchange）');
select public._tmp_comment_if_exists('column', 'public.cash_flows.note', '备注');
select public._tmp_comment_if_exists('column', 'public.cash_flows.created_at', '创建时间');
select public._tmp_comment_if_exists('column', 'public.cash_flows.updated_at', '更新时间');

select public._tmp_comment_if_exists('table', 'public.review_entries', '复盘报告');
select public._tmp_comment_if_exists('column', 'public.review_entries.id', '主键');
select public._tmp_comment_if_exists('column', 'public.review_entries.user_id', '所属用户');
select public._tmp_comment_if_exists('column', 'public.review_entries.period_start', '复盘区间起');
select public._tmp_comment_if_exists('column', 'public.review_entries.period_end', '复盘区间止');
select public._tmp_comment_if_exists('column', 'public.review_entries.report_markdown', '报告正文（Markdown）');
select public._tmp_comment_if_exists('column', 'public.review_entries.created_at', '创建时间');
select public._tmp_comment_if_exists('column', 'public.review_entries.updated_at', '更新时间');

select public._tmp_comment_if_exists('table', 'public.portfolio_snapshots', '组合资产快照');
select public._tmp_comment_if_exists('column', 'public.portfolio_snapshots.id', '主键');
select public._tmp_comment_if_exists('column', 'public.portfolio_snapshots.user_id', '所属用户');
select public._tmp_comment_if_exists('column', 'public.portfolio_snapshots.as_of_date', '快照日期');
select public._tmp_comment_if_exists('column', 'public.portfolio_snapshots.total_market_value_base', '持仓总市值（基准货币）');
select public._tmp_comment_if_exists('column', 'public.portfolio_snapshots.cash_value_base', '现金总值（基准货币）');
select public._tmp_comment_if_exists('column', 'public.portfolio_snapshots.total_assets_base', '总资产（基准货币）');
select public._tmp_comment_if_exists('column', 'public.portfolio_snapshots.created_at', '创建时间');
select public._tmp_comment_if_exists('column', 'public.portfolio_snapshots.updated_at', '更新时间');

-- ===========================================================================
-- 视图（指数侧暂不维护）
-- ===========================================================================
select public._tmp_comment_if_exists('view', 'public.index_latest_snapshot', '指数最新快照视图（含回撤、PE/PB 分位；暂不维护）');
select public._tmp_comment_if_exists('view', 'public.index_detail_snapshot', '指数各维度最新日期视图（暂不维护）');

-- 清理临时函数
drop function if exists public._tmp_comment_if_exists(text, text, text);
