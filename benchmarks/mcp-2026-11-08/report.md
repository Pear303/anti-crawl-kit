# MCP + recipe 验证记录 · 2026-11-08

- 环境：Windows / Python 3.13 / `mcp` 官方 SDK / patchright / 系统 Chrome 156
- 方式：官方 `mcp` client 通过 **stdio** 启动 `ack-mcp`，真实调用
- 结果：**全部通过**

## 1. Server 能力清单

```text
TOOLS: 14 个
  audit_paths, classify_page, extract_state, get_job, get_job_result,
  health_check, hunt_api, list_capabilities, list_recipes, probe_url,
  prune_profile_cache, resume_recipe, run_recipe, smoke_test

PROMPTS: 3 个
  diagnose_empty_result, new_platform_onboarding, regression_after_redesign

RESOURCES: 4 个
  ack://guide/decisions, ack://guide/health,
  ack://guide/smoke, ack://platform/template
```

## 2. 真实工具调用

| 调用 | 结果 |
|---|---|
| `probe_url(books.toscrape.com, selector=article.product_pod)` | `decision=parse_html`, `status=200` |
| `run_recipe(examples/recipes/product_list.yaml)` | `status=done`, `count=20` |
| `smoke_test(only=books_static, no_browser=true)` | job `done`, `exit_code=0`, `PASS=1` |

## 3. agent 检查点 + 浏览器 resume

配方：`examples/recipes/js_render_with_agent.yaml`

```text
run_recipe(...)
  -> status=needs_agent
  -> pending_step=pick_selector
  -> handoff_context={
       "steps.classify.kind": "unknown",
       "steps.empty.is_empty": true,
       "steps.empty.matched": 1
     }

resume_recipe({"selector": "div.quote"}, with_browser=True)
  -> status=done
  -> browser_count=10
```

## 5. 浏览器会话池

MCP 连续调用复用同一个 browser，每次租约新建隔离 context。

| 指标 | 冷启动 `BrowserSession` | 池化 `lease()`（非首次） |
|---|---|---|
| 耗时 | 1290~1455 ms | 159~183 ms |
| browser 启动 | 每次 | 仅首次 |
| cookie 隔离 | 新进程 | 新 context |

MCP 连续两次 `with_browser=True`：

```text
sessions_created=1, sessions_reused=1, leases=2, contexts_created=2
```

边界：按线程持有 browser（patchright sync API 线程亲和）；需要登录态 profile
仍用 `BrowserSession(data_dir=...)`。

---

## 6. 结论

- MCP 层能正确暴露工具、资源、提示词，调用结果可 JSON 解析。
- recipe 的确定性步骤（HTTP → 分类 → 空壳 → 计数 → 断言）真实跑通。
- agent 检查点能跨进程返回 `needs_agent`，带配方快照，resume 后继续执行。
- 异步 job 模型可用：`smoke_test` → `get_job` → `get_job_result`。
