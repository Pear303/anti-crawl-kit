# 26 · Agent 接入：recipe + MCP

> 对应 `v0.5.0`。目标：**常见路径固化得极快，意外情况仍然有 agent 的判断空间。**

---

## 1. 两层设计，不要混在一起

```
MCP（协议层）
  └─ 把能力暴露成 tools / resources / prompts
recipe（编排层）
  └─ 确定性步骤 + agent 检查点 + 失败转检查点
anti_crawl_kit（能力层）
  └─ http / page / smoke / health / path_audit / ...
```

- **能力层**：真正干活，可单测、可 CLI、可 CI。
- **recipe 层**：把「先干什么、再干什么、什么时候问 agent」写成配置。
- **MCP 层**：把能力暴露给 agent，不复制业务逻辑。

三层都调用同一份实现，所以 CLI 的测试和真实站点冒烟结果对 MCP 同样有效。

---

## 2. 为什么不是「全自动」也不是「全 agent」

| 方案 | 问题 |
|---|---|
| 纯脚本 | 站点改版 / 没见过结构就卡死，必须改代码 |
| 纯 agent | 每次现场试错，慢、不可复现、容易漏步骤 |
| **recipe + agent 检查点** | 常见路径确定执行；只有需要判断的地方交给 agent |

recipe 的关键字段：

```yaml
- id: pick_selector
  kind: agent          # 检查点
  ask:
    prompt: "HTTP 是空壳，请给出浏览器侧卡片选择器"
    schema:
      type: object
      properties: {selector: {type: string}}
      required: [selector]
    context_keys: [steps.classify.kind, steps.empty.is_empty]
    suggestions: [div.quote]
```

执行到这里会返回：

```json
{
  "status": "needs_agent",
  "handoff": {
    "step_id": "pick_selector",
    "next_index": 4,
    "prompt": "HTTP 是空壳，请给出浏览器侧卡片选择器",
    "schema": {"type": "object", "properties": {"selector": {"type": "string"}}},
    "context": {"steps.classify.kind": "unknown", "steps.empty.is_empty": true},
    "suggestions": ["div.quote"]
  },
  "recipe": {"...": "配方快照"}
}
```

agent 回答后：

```json
{"tool": "resume_recipe", "arguments": {
  "run": {"...": "上一次的完整返回"},
  "value": {"selector": "div.quote"},
  "with_browser": true
}}
```

`run` 里带配方快照，所以 **跨进程 resume** 不需要重新读 YAML。

### 失败也能转检查点

```yaml
- id: fetch
  uses: http_get
  on_error: agent      # 失败不直接中断，交给 agent 决定
  ask: {prompt: "请求失败，是否需要换路径/换选择器？"}
```

`on_error` 三档：`stop`（默认）/ `continue` / `agent`。

---

## 3. recipe 规范

### 3.1 顶层

```yaml
id: product_list
title: 商品列表
version: "1"
allow_hosts: [books.toscrape.com]   # 域名白名单（可选）
inputs:
  url: {type: string, required: true}
steps: [...]
outputs:
  count: "${steps.count.count}"
```

### 3.2 模板变量

| 写法 | 含义 |
|---|---|
| `${inputs.url}` | 输入参数 |
| `${steps.fetch.text}` | 某一步的输出 |
| `${steps.count.count}` | 步骤输出里的字段 |
| `${vars.x}` | `set_var` 写入的变量 |
| `${agent.pick.selector}` | agent 返回值 |

### 3.3 条件执行

```yaml
- id: parse
  uses: count_html
  when: steps.fetch.status == 200 and steps.empty.is_empty == false
  with: {...}
```

`when` 只允许比较 / 布尔 / 常量 / 容器 / 点号取值；禁止函数调用和私有属性（`__class__` 等）。

### 3.4 内置步骤

| 步骤 | 作用 |
|---|---|
| `http_get` / `http_json` | GET HTTP 请求 |
| `http_request` | GET/POST + headers + `cookies_env`（推荐） |
| `browser_goto` | 浏览器导航 + 导航指标 |
| `browser_count` / `browser_wait_count` | 浏览器内选择器计数 / 轮询等待 |
| `count_html` | 原始 HTML 选择器计数 |
| `classify_html` / `empty_shell` | 页面形态 / 空壳检测 |
| `extract_state` | SSR 状态容器提取 |
| `assert` | `eq/ne/gt/gte/lt/lte/in/contains` |
| `assert_non_empty` | 列表/分页/总数非空断言（防静默 0 条） |
| `set_var` / `log` / `sleep` | 变量 / 日志 / 等待 |

### 3.5 自定义步骤（扩展点）

```python
from anti_crawl_kit import register_step

@register_step("my_platform_parse", description="平台专用解析")
def my_platform_parse(params, runtime):
    html = params["html"]
    return {"items": [...]}
```

然后 recipe 里直接 `uses: my_platform_parse`。**自定义步骤必须在 `load_recipe` 之前注册**，否则校验会报「未注册步骤」。

### 3.6 静默空结果断言

很多平台的参数错误不会报错：返回 `err_no=0` + `data=[]`。
掘金评论接口就是典型：`limit` 写成 `count` 时返回 0 条，看起来像「没有评论」。

recipe 必须显式断言非空：

```yaml
- id: comments
  uses: http_request
  with:
    method: POST
    url: "https://api.juejin.cn/interact_api/v1/comment/list?aid=2608&uuid=${inputs.uuid}&spider=0"
    json: {item_id: "${inputs.article_id}", item_type: 2, limit: 20, sort: 1}
    cookies_env: JUEJIN_COOKIE

- id: assert_comments
  uses: assert_non_empty
  with:
    value: "${steps.comments.data}"
    path: data
    min: 1
    label: "掘金评论"
```

`assert_non_empty` 支持 `list` / `dict` / `int`，`path` 用点号取值；
失败信息会提示检查 `count vs limit`、cookie/签名和分页游标。

示例：`examples/recipes/juejin_comment_check.yaml`。

实测：正确 `limit` → recipe `done`（第一页 20 条）；把 `limit` 改成 `count`
→ recipe `failed`，`data=[]`，错误信息直接提示「检查 count vs limit」。

---

## 4. MCP 工具

| 工具 | 类型 | 说明 |
|---|---|---|
| `probe_url` | 同步 | 页面形态 + 空壳 + 建议；`deep=True` 会扫 JS |
| `classify_page` | 同步 | 只做形态/空壳判定 |
| `extract_state` | 同步 | SSR JSON 提取（摘要 + 预览） |
| `hunt_api` | 同步（慢） | 全量 JS 找接口 |
| `health_check` | 同步 | `ack-health` 的 MCP 版 |
| `prune_profile_cache` | 同步 | 缓存治理 |
| `run_recipe` | 同步 | 执行配方；可能需要 agent |
| `resume_recipe` | 同步 | 从检查点继续 |
| `list_recipes` | 同步 | 列出配方 |
| `smoke_test` | **异步** | 返回 `job_id` |
| `audit_paths` | **异步** | 返回 `job_id` |
| `assess_project` | **异步** | G0~G4 评估，返回 `job_id` |
| `get_job` | 同步 | 查作业状态 |
| `get_job_result` | 同步 | 取完整结果 |
| `list_capabilities` | 同步 | 工具/步骤/依赖/白名单 |

### 4.1 作业模型

```text
smoke_test(...) -> {"job_id": "..."}
get_job(job_id) -> {"status": "queued|running|done|failed"}
get_job_result(job_id) -> {"result": {...}, "error": ""}
```

作业存在内存里，server 重启后丢失；结果要落盘请用 `--json` 或自行保存。

### 4.2 资源（agent 主动读）

| URI | 内容 |
|---|---|
| `ack://guide/decisions` | 路径优先级、验证墙、签名剥离、未测口径 |
| `ack://guide/health` | 静默失效、RunReport 四字段、profile 缓存 |
| `ack://guide/smoke` | 冒烟矩阵、负向控制、慢加载 |
| `ack://platform/template` | `PlatformCapability` 模板 |

### 4.3 提示词（工作流）

| Prompt | 场景 |
|---|---|
| `new_platform_onboarding` | 接新平台 |
| `regression_after_redesign` | 改版回归 |
| `diagnose_empty_result` | 任务成功但 0 条 |

---

## 5. 启动

```bash
pip install "anti-crawl-kit[mcp,all]"

ack-mcp                                    # stdio（本地 agent；默认 chrome → msedge）
ack-mcp --transport http --port 8765       # Streamable HTTP
ack-mcp --channel msedge                   # 强制 Edge
ack-mcp --allow-hosts books.toscrape.com,quotes.toscrape.com
ack-mcp --recipes-dir ./recipes
```

环境变量 `ACK_MCP_ALLOWED_HOSTS` 等价于 `--allow-hosts`；为空表示不限制（**仅建议本地开发**）。

### 5.1 浏览器会话池（`v0.6.0`）

MCP 连续调用不再每次启动 Chrome。`BrowserPool` 的策略：

- **同一线程复用 browser**，每次 `lease()` 新建 `BrowserContext + Page`；
- context 隔离 cookie / localStorage / IndexedDB，租约结束关闭；
- `max_sessions` 限制并发浏览器进程数；
- `idle_ttl` 到期或 browser 断连时自动重建；
- `--channel chrome|msedge` 强制通道；不传时按 `chrome → msedge` 回退；
- **线程亲和性**：patchright sync API 不能跨线程使用，所以 browser 按线程持有，
  不跨线程共享（这是驱动限制，不是设计取舍）。

实测（本机，Windows / Chrome 156）：

```text
冷启动 BrowserSession       1290 ~ 1455 ms
池化后 lease（非首次）        159 ~ 183 ms
首次 lease（含启动）          964 ms

MCP 连续两次 with_browser：
  sessions_created=1, sessions_reused=1, leases=2
```

用法：

```python
from anti_crawl_kit.browser_pool import BrowserPool

pool = BrowserPool(max_sessions=2)
with pool.lease() as page:
    page.goto("https://books.toscrape.com/")
# 第二次：browser 复用，只新建 context
with pool.lease() as page:
    ...
pool.shutdown()
```

边界：需要登录态的持久化 profile 不适用本池，仍用 `BrowserSession(data_dir=...)`。

---

## 6. 安全边界

| 边界 | 做法 |
|---|---|
| 目标域 | `allow_hosts` 白名单；未配置时不限制，生产必须配 |
| 只读 | 不暴露发布、登录提交、删除数据 |
| 脱敏 | 所有工具输出过 `redact_mapping`（cookie/token/password/signature） |
| profile | `prune_profile_cache` 只删白名单缓存，不动 Cookies/Local Storage |
| 并发 | 作业线程池默认 2；浏览器会话按需创建 |
| 作业上限 | 50 条，超出丢弃最旧的已完成作业 |
| 配方 | YAML 不能执行任意 Python；自定义逻辑必须显式 `register_step` |

**注意**：`run_recipe` 的返回值会脱敏后交给 agent。如果配方把 secret 写进 `inputs`/`context`，resume 时拿到的是 `<redacted>`。正确做法是用 `cookie_env`，不要内联 cookie。

---

## 7. 实测记录（2026-11-08）

用官方 `mcp` client 通过 stdio 验证：

```text
TOOLS: 15 个
PROMPTS: 3 个
RESOURCES: 4 个
probe_url(books.toscrape.com)  -> decision=parse_html, status=200
run_recipe(product_list.yaml)  -> status=done, count=20
smoke_test(books_static)       -> job done, exit_code=0, PASS=1
```

agent 检查点（真实浏览器）：

```text
run_recipe(js_render_with_agent.yaml)
  -> needs_agent, pending=pick_selector
resume_recipe({"selector": "div.quote"}, with_browser=True)
  -> done, browser_count=10
```

---

## 8. 限制与后续

- 作业队列在内存，重启丢失；需要持久化可接 Redis/SQLite。
- 浏览器池按线程持有 browser，无法跨线程共享（patchright sync 限制）；
  需要登录态的场景仍用 `BrowserSession(data_dir=...)`。
- `hunt_api` 是同步慢调用（5~60s）；大批量建议走 job。
- recipe 的 `when` 不支持函数调用，复杂逻辑请封装成 `register_step`。
- MCP 的 HTTP 传输目前没有内置鉴权；远程部署请放在反向代理 + token 后面。

---

**相关文档**：[24-健康体检与静默失效](24-健康体检与静默失效.md) ·
[25-真实站点冒烟](25-真实站点冒烟.md) ·
[12-Agent任务模板](12-Agent任务模板.md) ·
[15-AI-Agent爬虫工作流](15-AI-Agent爬虫工作流.md)
