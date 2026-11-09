# Edge 通道验证记录 · 2026-11-08

## 环境

| 项 | 版本 |
|---|---|
| Microsoft Edge | 157.0.4341.42 |
| Google Chrome | 156.0.8078.80 |
| patchright 期望 Chromium | 151.0.7922.34（存在版本 gap，smoke 用 `skip_version_check=True`） |

## 1. CLI 冒烟

```bash
ack-smoke --channel msedge --only quotes_js_render,saucedemo_login
```

```text
汇总: PASS=2  FAIL=0  SKIP=0
[PASS] quotes_js_render   HTTP 0 条 / 浏览器 10 条
[PASS] saucedemo_login    登录后 6 件商品
[INFO] 使用系统浏览器: msedge
```

覆盖了：

- Edge 启动 + 导航；
- JS 渲染（等待 10 条 quote）；
- 表单填写 + 点击提交 + 登录后列表。

## 2. MCP 通道

```bash
ack-mcp --channel msedge
```

```text
list_capabilities -> browser_channel = msedge
smoke_test(quotes_js_render) -> job done, exit_code=0, PASS=1
```

## 3. 行为说明

- 不传 `--channel`：按 `chrome → msedge` 自动回退；
- 传 `--channel msedge`：**只**尝试 Edge，不会偷偷回退到 Chrome；
- `BrowserPool`、`BrowserSession`、`path_audit`、`ack-assess` 都支持 `msedge`；
- `--headed` 与 `--channel` 可组合。
