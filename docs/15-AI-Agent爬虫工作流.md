# 15 · AI Agent 驱动的爬虫工作流

> 核心判断：**Agent 不做执行，做决策。**
>
> 让 LLM 去点按钮、填选择器、猜接口，是最贵、最不稳定、最难复现的做法。
> 正确分工是：**Agent 感知和决策 → 确定性工具执行 → 结构化结果回传 → 人兜底。**

---

## 0. 先纠正三个常见误解

| 误解 | 现实 |
|---|---|
| 「Agent 能自己浏览网页，所以不用写采集器」 | 探索可以，稳定采集不行。LLM 每次点的地方可能不同，无法保证 1000 条一致 |
| 「MCP 工具越多，Agent 越强」 | 工具 schema 占上下文。工具越多，Agent 越容易选错、越贵 |
| 「让 LLM 直接生成选择器/接口」 | LLM 会编。选择器必须来自 `dom_probe` 的真实输出，接口必须来自 `js_api_hunt` 的真实证据 |

**一句话：LLM 负责「下一步做什么」，工具负责「怎么做准」。**

---

## 1. 五层能力模型

```text
感知（Perceive）
  ↓  页面结构、接口、登录态、验证墙
决策（Decide）
  ↓  选哪条数据源、要不要停、要不要降级
执行（Act）
  ↓  确定性 CLI / Python 工具
验证（Verify）
  ↓  条数、字段完整率、退出码
自愈（Recover）
  ↓  改版 → 换选择器；风控 → 停；接口变 → 重新发现
```

### 1.1 感知层

Agent 需要知道的四件事：

| 问题 | 工具 | 输出 |
|---|---|---|
| 数据在 HTML 里吗？ | `tools/agent_probe.py` | `decision=html_has_data` |
| 接口在哪？ | `tools/js_api_hunt.py` | 候选接口 + 证据 |
| 选择器是什么？ | `tools/dom_probe.py` | 重复结构 + 样本 |
| 会不会被拦？ | `tools/detector_check.py` | 指纹检测结果 |

**感知层的输出必须是结构化的**，不能是「我看了下页面，感觉数据在接口里」这种自然语言。

### 1.2 决策层

决策树（Agent 按这个走，不要自由发挥）：

```text
目标字段在 HTML 里？
├─ 在 → 直接解析（L2/L4），结束
└─ 不在
   ├─ 有外链 JS → js_api_hunt
   │  ├─ 找到接口 + 无签名 → HTTP 直连
   │  ├─ 找到接口 + 有签名 → 浏览器拦截
   │  └─ 找不到 → 浏览器 Network / dom_probe
   └─ 无 JS → 浏览器 + XHR / DOM
```

**决策层允许 LLM 参与的地方：**

- 判断「这个接口像不像目标数据」（看字段名/上下文）；
- 判断「页面是改版还是风控」（看截图/文案）；
- 选择「先修哪一层」（成本排序）。

**不允许 LLM 参与的地方：**

- 编造接口地址；
- 编造选择器；
- 绕过验证墙；
- 决定「再试一次」。

### 1.3 执行层

执行必须是**确定性工具**：

```bash
python tools/agent_probe.py --url "URL" --field fansNum --json
python tools/js_api_hunt.py --url "URL" --filter popular --json
python tools/dom_probe.py --url "URL" --json
python examples/05_http_only.py --url "API" --out output/result.json
```

要求：

- 同样的输入，同样的输出；
- 失败有明确退出码；
- 结果可落盘、可 diff；
- 不依赖 LLM 在线。

### 1.4 验证层

Agent 不能只看「命令 exit 0」。必须验证：

```text
[ ] 条数 > 0，且和页面显示量级一致
[ ] 必填字段完整率 ≥ 95%
[ ] 没有「成功但 0 条」
[ ] 没有把验证墙当成功
[ ] 输出 JSON 能被下游解析
```

### 1.5 自愈层

自愈不是「自动重试」，是**定位 + 换策略**：

| 症状 | 自愈动作 |
|---|---|
| 选择器 0 命中 | 跑 `dom_probe`，换兜底选择器 |
| 接口 404 | 跑 `js_api_hunt`，换域名/路径 |
| 接口 403 | 补 Referer/cookie；仍失败 → 浏览器拦截 |
| 验证墙 | **停**，通知，不重试 |
| 字段改名 | 看 `raw`，映射新字段 |
| 登录过期 | 三态校验 → 提示重登 |

---

## 2. 什么时候用 LLM，什么时候用工具

| 任务 | 用什么 | 原因 |
|---|---|---|
| 判断数据在 HTML 还是接口 | 工具 + LLM 读摘要 | 工具给证据，LLM 做归纳 |
| 生成选择器 | **工具** | LLM 会编 |
| 生成接口地址 | **工具** | LLM 会编 |
| 判断是否改版 | LLM + 截图/HTML | 需要语义理解 |
| 写采集器代码 | LLM | 适合 |
| 修一个具体报错 | LLM + 日志 | 适合 |
| 决定重试几次 | **规则** | 不能让 LLM 自由决定 |
| 过验证码 | **人** | 不让 LLM 碰 |
| 大批量翻页 | **工具** | 确定性循环 |
| 字段映射 | LLM 建议 + 人确认 | 半自动 |

**规律：**

- **「找事实」用工具**（接口、选择器、状态码）；
- **「做归纳」用 LLM**（这是改版还是风控、这个字段像不像目标）；
- **「做执行」用代码**（循环、重试、落盘）；
- **「做取舍」用人**（账号、合规、要不要硬刚）。

---

### 2.1 工具、库、脚本的边界

这是最容易被误解的地方：**不是所有东西都必须用工具。**

| 类型 | 例子 | 必要性 | 为什么 |
|---|---|---|---|
| **库** | `AntiRiskHttpClient`、`ChallengeGate`、`Notifier` | **必须** | 补的是手写容易漏、漏了会出事的东西：TLS 指纹、止损、通知 |
| **探测工具** | `agent_probe`、`js_api_hunt`、`dom_probe` | **可选** | 加速器。agent 手写能解决，工具只是省时间 |
| **固化脚本** | `scripts/*.py`、`platforms/*.yaml` | **必须** | 生产环境不能每次让 LLM 现场生成 |

**为什么探测工具是可选的：**

- 探索阶段目标不确定，工具的参数反而束缚；
- LLM 手写 10 行 `requests` 比读 `--help` 再解析 JSON 更快；
- 工具真正的价值在「一致性」和「回归」，不在「第一次探索」。

**什么时候工具明显更划算：**

| 场景 | 工具 | 手写 |
|---|---|---|
| 改版回归 | `--baseline` diff，直接定位变更点 | 很难复现「上次是什么样」 |
| 批量接新站 | `agent_probe --quick` 一次出决策 | 每次重新试探 |
| 团队协作 | 固定输出格式，可对比 | 每个人的探测方式不同 |
| 补漏 | 自动补 Referer、跟加载器、识别 API 域名 | 容易漏 |

**一条硬边界：**

> 手写 HTTP 必须用 `AntiRiskHttpClient`，不要裸 `requests`。
>
> 这是**库**，不是工具。不需要学 CLI，只需要 `import`。
> 裸 `requests` 的 TLS 指纹和 Chrome 不同，强风控站会直接 403。

---

## 3. 上下文与 token 预算

### 3.1 CLI 优先，MCP 按需

Playwright MCP 官方 README 的判断值得引用：

> CLI 对 coding agent 更 token 高效，因为它避免把大型工具 schema 和冗长的 accessibility tree 加载进上下文；
> MCP 更适合需要持久状态、丰富内省、迭代推理的探索性自动化。

所以启动包的设计是：

```text
默认：CLI + JSON（tools/*.py）
需要持续浏览/调试：再接 Playwright MCP / Chrome DevTools MCP
```

### 3.2 输出要「分级」

不要让工具一次吐 200KB HTML。分三级：

| 级别 | 内容 | 大小 |
|---|---|---|
| L1 摘要 | 决策、候选数、前 3 条 | < 2KB |
| L2 证据 | 上下文片段、状态码、字段命中 | < 20KB |
| L3 原文 | HTML/JS/截图 | 落盘，按需读 |

**Agent 默认只读 L1；需要时再读 L2；L3 只在排障时读。**

### 3.3 统一 JSON 协议

所有工具输出同一个外壳：

```json
{
  "ok": true,
  "tool": "agent_probe",
  "target": "https://example.com/page",
  "decision": "js_api",
  "confidence": "high",
  "summary": "HTML 无目标字段，JS 包中找到 3 个候选接口",
  "evidence": {
    "html_has_field": false,
    "script_count": 7,
    "api_hosts": ["api.example.com"]
  },
  "candidates": [
    {"method": "GET", "url": "https://api.example.com/x/list", "score": 0.9}
  ],
  "next_actions": [
    "带 cookie 请求候选接口，确认返回 JSON",
    "若 403，改用浏览器拦截"
  ],
  "artifacts": ["output/probe.json"]
}
```

好处：

- Agent 只需要读 `decision` / `summary` / `next_actions`；
- 需要细节时读 `candidates` / `evidence`；
- 不需要解析自然语言。

---

## 4. 标准工作循环

```text
observe → decide → act → verify → record
   ↑                                      │
   └──────────── 失败时回到 observe ──────┘
```

### 4.1 observe

```bash
python tools/agent_probe.py --url "URL" --field 目标字段 --json
```

Agent 读 `decision` 和 `summary`。

### 4.2 decide

按第 1.2 节决策树选一条路。**只选一条**，不要并行试三条。

### 4.3 act

```bash
# 例：找到了接口
python examples/05_http_only.py --url "API" --out output/result.json
```

### 4.4 verify

```bash
python -c "
import json
d = json.load(open('output/result.json'))
print('items', len(d.get('items', [])))
"
```

检查条数、字段、是否为空。

### 4.5 record

把「结论 + 证据 + 下一步」写进任务记录，不要只留聊天记录。

---

## 5. 六种失败模式

### 5.1 LLM 幻觉选择器

**表现：** Agent 写了一个 `div.note-item`，实际页面是 `section.note-item`，跑出来 0 条。

**防法：** 选择器必须来自 `dom_probe` 的输出，不允许手写。

### 5.2 过度自主

**表现：** Agent 发现 403，自动换代理、改 UA、重试 10 次。

**防法：** 403 是风控信号，按规则停；不要让 Agent 自由发挥。

### 5.3 上下文爆炸

**表现：** Agent 把整个 HTML 读进上下文，后续推理全在噪声里。

**防法：** 三级输出；HTML 落盘；只读摘要。

### 5.4 把验证墙当重试

**表现：** 检测到验证码，Agent 觉得「刷新一下可能就好了」。

**防法：** `ChallengeGate` 硬停，工具层直接抛异常，Agent 没有机会重试。

### 5.5 成功但 0 条

**表现：** 命令 exit 0，JSON 里 `items: []`，Agent 汇报「完成」。

**防法：** 验证层强制检查条数；0 条视为失败。

### 5.6 改错层

**表现：** 接口变了，Agent 去改 DOM 选择器。

**防法：** 先判断「哪一层变了」，只改那一层。

---

## 6. 和主流框架的关系

### 6.1 browser-use（120k ★，MIT）

**定位：** 全自主 AI browser agent。给一个目标，LLM 自己决定点哪里。

**适合：**

- 探索未知网站；
- 一次性任务；
- 「帮我看看这个后台怎么操作」。

**不适合：**

- 稳定采集 1000 条；
- 需要可复现、可审计；
- 成本敏感（每步都调 LLM）。

**和工作包的关系：** 互补。用 browser-use 做探索，用工作包做稳定采集。

---

### 6.2 Stagehand（25.9k ★，MIT）

**定位：** `act / extract / observe` 三个原语。AI 负责定位，代码负责执行。

**为什么值得借鉴：**

- 它的分层和工作包一致：**AI 做定位，代码做动作**；
- `extract` 用 schema 约束输出，避免 LLM 自由发挥；
- 比全自主 agent 稳定，比纯选择器灵活。

**工作包的对应：**

| Stagehand | 工作包 |
|---|---|
| `observe` | `dom_probe` / `agent_probe` |
| `act` | `human_scroll` / `LoadMoreDriver` |
| `extract` | `GenericCollector` + YAML 字段映射 |

---

### 6.3 Playwright MCP（39.1k ★，Apache-2.0）

**定位：** 微软官方 MCP server，用 accessibility tree 而不是截图。

**什么时候接：**

- Agent 需要持续浏览、调试、迭代；
- 需要看页面结构（accessibility tree）；
- 需要和 coding agent（Claude Code / Codex / Cursor）集成。

**什么时候不接：**

- 只是批量采集（CLI 更快更省）；
- 上下文预算紧张。

---

### 6.4 Chrome DevTools MCP（54.7k ★，Apache-2.0）

**定位：** 把 Chrome DevTools 暴露给 coding agent：网络、性能、console、DOM。

**最适合：**

- JS 逆向调试；
- 看真实请求（Network）；
- 性能分析。

**和工作包 `js_bundle` 的分工：**

- `js_bundle`：静态读 JS 包，快；
- DevTools MCP：动态看请求/断点，慢但能看到运行时。

---

### 6.5 Crawl4AI（86.9k ★，Apache-2.0）

**定位：** LLM 友好爬虫，输出干净 Markdown / 结构化 JSON。

**适合：**

- RAG 数据准备；
- 批量把网页转 Markdown；
- 不需要复杂登录/交互。

**不适合：**

- 需要精细控制浏览器行为；
- 强风控站点。

---

### 6.6 Firecrawl（198.5k ★，AGPL-3.0）

**定位：** 托管 + 开源的 web data API for AI。

**注意：** AGPL-3.0。自托管商用要确认许可证。

---

### 6.7 Skyvern（23.3k ★，AGPL-3.0）

**定位：** 视觉 + LLM 的浏览器工作流自动化。

**注意：** AGPL-3.0；偏「工作流自动化」，不是「稳定采集」。

---

## 7. 启动包怎么用

### 7.1 你是 coding agent

```text
1. 读 AGENTS.md（30 秒决策）
2. 跑 tools/agent_probe.py --json
3. 按 decision 选一条路
4. 跑对应工具/示例
5. 按 docs/11 验收
6. 失败按 docs/12 模板 B 排查
```

### 7.2 你是要搭「agent 产品」的人

```text
感知层：agent_probe + dom_probe + js_api_hunt
决策层：决策树（规则）+ LLM 做归纳
执行层：本包的 CLI / Python 库
验证层：条数/字段/退出码
兜底层：ChallengeGate + Notifier + 人
```

### 7.3 你要接 MCP

```text
需要持续浏览 → Playwright MCP
需要看网络/调试 → Chrome DevTools MCP
需要 JS 逆向 → js-reverse-mcp
需要稳定采集 → 不要用 MCP，用本包 CLI
```

---

## 8. 一句话总结

> **Agent 的价值在「判断下一步」，不在「代替工具执行」。**
>
> 把确定性的事交给代码，把不确定的事交给 LLM，把有风险的事交给人。

---

## 9. 相关文件

- Agent 入口：[`AGENTS.md`](../AGENTS.md)
- 任务模板：[`12-Agent任务模板.md`](12-Agent任务模板.md)
- 工程架构：[`10-采集器工程架构.md`](10-采集器工程架构.md)
- 工程红线：[`11-工程红线与验收清单.md`](11-工程红线与验收清单.md)
- 开源生态：[`14-开源生态与选型.md`](14-开源生态与选型.md)
- 一站式探测：[`tools/agent_probe.py`](../tools/agent_probe.py)

---

**最后更新**：2026-11
**维护规则**：每引入一个新的 agent 工具，先问「它替代的是决策还是执行」；替代执行的要保证确定性，替代决策的要保留人工兜底。
