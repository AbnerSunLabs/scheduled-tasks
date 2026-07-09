# ETF 表重命名兼容性审查规则

针对 `scheduled-tasks` 中 `etf_grid_pool_snapshots` / `etf_grid_daily` / `etf_grid_valuation_snapshots` 去 `grid` 重命名方案，用户已确认此前没有外部消费者使用这些表。

后续 review 该方案时，不要把“外部兼容性验收”作为问题或阻塞项；只需关注迁移幂等性、数据连续性、读写代码是否统一使用新表名。
