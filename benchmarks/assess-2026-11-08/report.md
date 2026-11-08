# 技术验证评估记录 · 2026-11-08

- 环境：Windows / Python 3.13 / patchright / 系统 Chrome 156
- 命令：`ack-assess --config ...`
- 站点：`books.toscrape.com`（公开练习站，允许抓取）

## 1. 正常链路

[`books/assessment.md`](books/assessment.md)

```text
决策: CONDITIONAL_GO   评分: 85/100

[PASS] G0 形态: shape=ssr empty=False
[WARN] G1 路径: 已测 2 条，成功 2 条
[PASS] G2 数据: 20 条 / 去重后 20 / 重复率 0.0% / 新增 0
[PASS] G3 稳定: 3/3 成功，counts=[20, 20, 20]，blocked=0
[WARN] G4 工程: 未配置 health_config
```

缺口：

- 单次审计不能下生产可用结论；
- 增量未验证（`mode=full`）；
- 健康体检未配置。

## 2. G4 通过

[`books-health/assessment.md`](books-health/assessment.md)

```text
决策: CONDITIONAL_GO   评分: 80/100

[PASS] G0 / G2 / G3 / G4
[SKIP] G1（本次未配置 audit）
```

G4 条件：`ack-health` 无 FAIL + `mcp` 可导入。

## 3. 负向验证：错误选择器

[`books-bad/assessment.md`](books-bad/assessment.md)

```text
决策: NO_GO           退出码 1

[FAIL] G2 数据: 条目数 0 < 要求 20
[FAIL] G3 稳定: 0/2 成功，counts=[]
```

证明评估器不会把 0 条当成功。

## 4. 结论

- G0→G4 全链路可执行，输出 `assessment.json` + `assessment.md`。
- 正常链路是 `conditional_go`，不是因为工具坏了，而是因为单次审计/增量/health
  这三个缺口没有被假装成通过。
- 错误选择器会稳定落到 `no_go`，退出码 1。
