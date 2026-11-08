# 技术验证评估 · books

- URL: https://books.toscrape.com/
- 时间: 2026-11-08 16:59:40 -> 2026-11-08 17:00:16
- 决策: **conditional_go**
- 评分: **85/100**

## 关卡

| Gate | 名称 | 状态 | 摘要 |
|---|---|---|---|
| G0 | 形态 | PASS | shape=ssr empty=False states=[] |
| G1 | 路径 | WARN | 已测 2 条，成功 2 条 |
| G2 | 数据 | PASS | 20 条 / 去重后 20 / 重复率 0.0% / 新增 0 |
| G3 | 稳定 | PASS | 3/3 成功，counts=[20, 20, 20]，blocked=0 |
| G4 | 工程 | WARN | 未配置 health_config |

## 缺口

- 单次审计不能下生产可用结论；需要双跑 + 不同时段复测
- 增量未验证：当前 mode=full（需要游标字段/两次真实增量样本）
- 健康体检未配置：补 ack_health.yaml（注册表/入口/cookie/选择器）

## 下一步

- 提供游标字段或两次增量样本，把 mode 改为 incremental
- 补 ack_health.yaml，跑 ack-health
