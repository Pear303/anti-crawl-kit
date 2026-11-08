# 真实站点冒烟记录 · 2026-11-08

- 环境：Windows / Python 3.13 / patchright / 系统 Chrome 156
- 命令：`ack-smoke --json smoke.json`
- 结果：**PASS=9  FAIL=0  SKIP=0**，退出码 0
- 原始数据：[`results.json`](results.json)

| 用例 | 类型 | 耗时 | 实际结果 |
|---|---|---|---|
| `books_static` | `http_static` | 1.4s | 20 条（期望 20） |
| `books_pagination` | `http_pagination` | 1.4s | 首页 20 + 第二页 20 |
| `web_scraping_products` | `http_pagination` | 1.9s | 首页 5 + 第二页 5 |
| `countries_big_list` | `big_list` | 1.3s | 250 条（期望 250） |
| `quotes_js_render` | `js_render` | 6.8s | HTTP 0 条 / 浏览器 10 条 |
| `quotes_slow_load` | `slow_load` | 14.3s | `render_wait_ms=10402`，最终 10 条 |
| `quotes_infinite_scroll` | `infinite_scroll` | 11.0s | 滚动序列 `[10, 20, 30, 30, 50, 50, 70]` |
| `saucedemo_login` | `login` | 1.8s | 登录后 6 件商品 |
| `quotes_negative_control` | `negative_control` | 0.6s | 错误选择器 0 条，检测层判 FAIL |

## 降级验证

```text
ack-smoke --no-browser            → PASS=5 FAIL=0 SKIP=4，退出码 0
ack-smoke --no-browser --strict   → 退出码 1（SKIP 视为失败）
```

## 说明

- 全部站点均为公开练习站（web scraping sandbox），允许抓取。
- 负向控制是核心：错误选择器必须被判 FAIL；若它也 PASS，说明检测层把 0 条当成功了。
- 慢加载用例真实等待了 10.4s，证明「慢 ≠ 失败」的语义生效。
- 无限滚动用例有轮数上限，不会无限等待「最后一页」。
