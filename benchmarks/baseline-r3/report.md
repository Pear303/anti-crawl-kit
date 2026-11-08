# 最小 benchmark：curl_cffi vs patchright

- 时间：2026-11-05 17:23:28
- 环境：Python 3.13.3 · Windows 11
- 依赖：curl_cffi 0.15.0 · patchright 1.62.2
- 浏览器启动耗时：1071 ms（不计入单站耗时）
- **统计口径：表中耗时 = 同站同工具 3 次运行的「中位数」**（不是均值，也不是最后一次）

| 站点 | 工具 | 成功 | 状态 | 耗时(ms) | 大小(KB) | 卡片数 | 风险/检测 |
|---|---|---|---|---|---|---|---|
| quotes | http | ✅ | 200 | 920 | 10.8 | 10 | ok |
| quotes | browser | ✅ | 200 | 623 | 10.7 | 10 | ok |
| books | http | ✅ | 200 | 1200 | 50.1 | 20 | ok |
| books | browser | ✅ | 200 | 1359 | 49.8 | 20 | ok |
| biquge | http | ✅ | 200 | 483 | 26.9 | 24 | ok |
| biquge | browser | ✅ | 200 | 831 | 27.5 | 24 | ok |

> 原始三次数据见 `results.json`。例如 books/browser 为 1843 / 1359 / 1284 ms，
> 排序后中位数是 **1359 ms**（不是最小值 1284，也不是均值 1495）。

## 基线断言

后续跑 `bench.py` 时用下面的命令做回归，成功率下降或中位耗时超过基线 20% 会退出码 1：

```bash
python tools/bench.py --repeat 3 --assert-baseline benchmarks/baseline-r3/results.json
```

CI 里可用 `--tolerance 0.3` 放宽网络波动。

## 结论

- **quotes**：两条路都通，浏览器快 1.5×（HTTP 920ms vs 浏览器 623ms）。单页成本接近，但浏览器每次任务还要付启动成本（本次 1071ms）→ 批量优先 HTTP。
- **books**：两条路都通，HTTP 快 1.1×（HTTP 1200ms vs 浏览器 1359ms）。单页成本接近，但浏览器每次任务还要付启动成本（本次 1071ms）→ 批量优先 HTTP。
- **biquge**：两条路都通，HTTP 快 1.7×（HTTP 483ms vs 浏览器 831ms）。单页成本接近，但浏览器每次任务还要付启动成本（本次 1071ms）→ 批量优先 HTTP。

> 说明：单次运行受网络波动影响；要下结论请用 `--repeat 3` 取中位数。
