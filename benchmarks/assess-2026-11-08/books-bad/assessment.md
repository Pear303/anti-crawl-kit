# 技术验证评估 · books-bad

- URL: https://books.toscrape.com/
- 时间: 2026-11-08 17:00:43 -> 2026-11-08 17:00:52
- 决策: **no_go**
- 评分: **15/100**

## 关卡

| Gate | 名称 | 状态 | 摘要 |
|---|---|---|---|
| G0 | 形态 | WARN | shape=ssr empty=True states=[] |
| G1 | 路径 | SKIP | 未配置 path_audit |
| G2 | 数据 | FAIL | 0 条 / 去重后 0 / 重复率 0.0% / 新增 0 |
| G3 | 稳定 | FAIL | 0/2 成功，counts=[]，blocked=0 |
| G4 | 工程 | WARN | 未配置 health_config |

## 缺口

- 页面形态 ssr，需要 JS/浏览器路径或接口发现
- 路径审计未配置：补 audit YAML（14 条路径）
- 条目数 0 < 要求 20
- 增量未验证：当前 mode=full（需要游标字段/两次真实增量样本）
- 本次抓取为空
- 成功率 0.0% < 要求 100.0%
- 健康体检未配置：补 ack_health.yaml（注册表/入口/cookie/选择器）

## 下一步

- 先 hunt_api 找接口；没有接口再走 XHR/DOM
- 补 audit YAML，跑 ack-path / path_audit
- 提供游标字段或两次增量样本，把 mode 改为 incremental
- 补 ack_health.yaml，跑 ack-health
