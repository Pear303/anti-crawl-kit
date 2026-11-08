# 17 · Agent 发现与脚本固化

> **Agent 是勘探员，不是矿工。**
>
> 它负责「找到可固化的路径」和「改版后重新定位」，
> 不留在生产链路。生产链路永远是固定脚本 + 配置文件。

---

## 0. 一句话流程

```text
发现（Agent） → 固化（脚本） → 回归（Agent 再次发现）
     ↑                                    │
     └──────────── 改版/失效时回到发现 ────┘
```

| 阶段 | 谁来做 | 产出 | 是否依赖 LLM |
|---|---|---|---|
| 发现 | Agent + 探测工具 | 决策 + 证据 + 脚本草稿 | 是（归纳） |
| 固化 | 人 + 代码 | YAML / Python / 常量 | 否 |
| 回归 | Agent + 基线 diff | 变更点 + 修复建议 | 是（判断） |

**生产环境不跑 LLM。** 这是整个方案的前提。

---

## 1. 发现阶段

### 1.1 输入

```text
目标 URL
目标字段（如 fansNum / 标题 / 作者 / 作品列表）
是否需要登录（可选）
```

### 1.2 一条命令

```bash
python tools/agent_probe.py \
    --url "https://目标站/页面" \
    --field fansNum \
    --save-baseline output/discovery/demo.json \
    --json
```

`--save-baseline` 会保存这次发现的结果，供以后回归对比。

### 1.3 输出

```json
{
  "ok": true,
  "decision": "js_api",
  "confidence": "high",
  "summary": "HTML 无目标字段，JS 包中找到 1 个候选接口",
  "evidence": {
    "field_in_html": false,
    "script_count": 3,
    "api_hosts": ["api.example.com"]
  },
  "candidates": [
    {
      "method": "GET",
      "url": "https://api.example.com/my/stats",
      "source": "https://example.com/static/js/main.js",
      "context": "...a.get(\"/my/stats\")...",
      "score": 1.0
    }
  ],
  "script_hint": {
    "type": "http_json",
    "code": "from anti_crawl_kit.http import AntiRiskHttpClient\n..."
  },
  "next_actions": ["带 cookie 请求候选接口验证 ..."]
}
```

### 1.4 四种决策，四条固化路径

| decision | 含义 | 固化方向 |
|---|---|---|
| `html_has_data` | 数据在 HTML 里 | YAML 配置 + DOM 选择器 |
| `js_api` | 找到接口 | Python 脚本 / HTTP 直连 |
| `browser_needed` | 接口在运行时 | 浏览器拦截脚本 |
| `captcha` / `login_required` / `blocked` | 被拦 | **先解决登录/风控，再发现** |

### 1.5 发现阶段的验收

```text
[ ] decision 不是 unknown
[ ] 至少有一条「实测证据」（状态码 / 条数 / 接口返回）
[ ] 脚本草稿能跑通（或明确知道缺什么）
[ ] 基线已保存（output/discovery/{name}.json）
```

**没有证据的发现不算发现。**

---

## 2. 固化阶段

### 2.1 产出物清单

一个平台固化后，应该留下五样东西：

```text
platforms/{name}.yaml              # DOM 配置（如果是 L4）
scripts/{name}.py                  # 自定义采集器（如果是 L1/L3）
constants.py 或 scripts/{name}.py  # 接口常量 + last_verified
output/discovery/{name}.json       # 发现基线（回归用）
docs/07 里的一条记录               # 平台速查
```

### 2.2 路径 A：数据在 HTML（YAML）

```yaml
name: demo
start_url: https://example.com/list
card_selector: "section.item"
fallback_selectors:
  - "div.item"
fields:
  title: {selector: "h3", attr: text}
  url:   {selector: "a.cover", attr: href}
  fans:  {selector: ".fans", attr: text, transform: count}
```

跑：

```bash
python examples/07_config_driven.py examples/platforms/demo.yaml
```

**固化要点：**

- 选择器必须来自 `tools/dom_probe.py` 的真实输出；
- 必须写 `fallback_selectors`；
- 字段解析用 `transform: count`，不要手写 if。

### 2.3 路径 B：找到接口（Python）

```python
"""demo 采集器 —— 固定脚本，不依赖 LLM。"""
from anti_crawl_kit.http import AntiRiskHttpClient

# last_verified: 2026-11-04
STATS_URL = "https://api.example.com/my/stats"

def fetch_stats(cookies: dict[str, str]) -> dict:
    client = AntiRiskHttpClient("demo")
    result = client.get_json(
        STATS_URL,
        headers={"Referer": "https://example.com/page"},
        cookies=cookies,
    )
    if result.status_code != 200:
        raise RuntimeError(f"stats failed: {result.status_code} {result.risk.reason}")
    return result.data["data"]
```

**固化要点：**

- 接口常量集中，带 `last_verified`；
- cookie 从 profile 读，不硬编码；
- 失败抛异常，不返回空 dict；
- 返回标准字段，不把原始 JSON 直接透传。

### 2.4 路径 C：接口在运行时（浏览器拦截）

```python
from anti_crawl_kit import BrowserSession

with BrowserSession(headless=False, data_dir="./profiles/demo") as page:
    captured = page.capture_responses("/api/")
    page.goto("https://example.com/page")
    page.wait_for_timeout(3000)
    page.stop_capture("/api/")
    print(len(captured), captured[0] if captured else None)
```

**固化要点：**

- 拦截规则写常量，不要散落；
- 拦截到的响应要做 schema 校验；
- 浏览器只负责「拿数据」，解析在 Python 侧做。

### 2.5 固化后的目录建议

```text
my-collector/
├─ platforms/
│  └─ demo.yaml
├─ scripts/
│  └─ demo_stats.py
├─ discovery/
│  └─ demo.json          # 从 output/discovery 复制过来
├─ output/
└─ README.md             # 记录「怎么跑、怎么修」
```

---

## 3. 回归阶段

### 3.1 什么时候触发

| 症状 | 可能原因 |
|---|---|
| 条数突然 0 | 改版 / 接口变 / 验证墙 |
| 字段完整率骤降 | 字段改名 / 选择器部分失效 |
| 接口 404 | 路径变了 |
| 接口 403 | 加签 / 风控 |
| 页面空白 | JS 加载失败 / CDN 变 |

### 3.2 一条命令做 diff

```bash
python tools/agent_probe.py \
    --url "https://目标站/页面" \
    --field fansNum \
    --baseline output/discovery/demo.json \
    --json
```

输出会多一个 `changes`：

```json
{
  "changes": [
    {
      "type": "candidates_changed",
      "added": ["https://api.example.com/my/stats_v2"],
      "removed": ["https://api.example.com/my/stats"],
      "hint": "接口路径变了：用 added 里的新地址替换脚本常量"
    }
  ]
}
```

### 3.3 变更类型 → 动作

| change type | 含义 | 动作 |
|---|---|---|
| `decision_changed` | 数据源层级变了 | 先确认是改版还是风控，再决定换哪条路 |
| `candidates_changed` | 接口路径变了 | 用 `added` 替换脚本常量，加 `last_verified` |
| `api_host_changed` | 接口域名变了 | 检查 baseURL / `--api-host` |
| `field_location_changed` | 数据从接口挪到 DOM（或反过来） | 换采集方式，不要硬修旧路径 |

### 3.4 回归的验收

```text
[ ] changes 为空（无变更），或
[ ] changes 已解释（每条都知道为什么、改哪里）
[ ] 修复只改一层（选择器 / 接口 / 解析）
[ ] 单页 20 条跑通
[ ] 连续 3 次不触发验证码
[ ] 基线已更新（--save-baseline 覆盖）
```

### 3.5 不要做的事

- 不要因为一次 diff 就重写采集器；
- 不要同时改接口和选择器；
- 不要把「验证墙」当成「改版」去修选择器；
- 不要在没有基线的情况下「凭感觉」改。

---

## 4. 完整案例（脱敏）

### 4.1 发现

```bash
python tools/agent_probe.py \
    --url "https://example.com/my/index" \
    --field fansNum \
    --cookie "SESSION=xxx" \
    --save-baseline output/discovery/example.json \
    --json
```

结果：

```json
{
  "decision": "js_api",
  "candidates": [
    {
      "method": "GET",
      "url": "https://example.com/my/stats",
      "context": "...get(\"/stats\")..."
    }
  ],
  "script_hint": {"type": "http_json"}
}
```

### 4.2 验证接口

```bash
python -c "
from anti_crawl_kit.http import AntiRiskHttpClient
c = AntiRiskHttpClient('example')
r = c.get_json('https://example.com/my/stats', cookies={'SESSION': 'xxx'})
print(r.status_code, r.data)
"
```

返回 200 + `{dynamicNum, subscribeNum, fansNum}`。

### 4.3 固化

```python
# scripts/example_stats.py
STATS_URL = "https://example.com/my/stats"  # last_verified: 2026-11-04
```

### 4.4 回归

三个月后脚本返回 404：

```bash
python tools/agent_probe.py \
    --url "https://example.com/my/index" \
    --field fansNum \
    --baseline output/discovery/example.json \
    --json
```

输出：

```json
{
  "changes": [{
    "type": "candidates_changed",
    "added": ["https://example.com/my/stats_v2"],
    "removed": ["https://example.com/my/stats"]
  }]
}
```

改一行常量，回归通过。**整个过程 10 分钟，不需要重写脚本。**

---

## 5. 什么时候不需要 Agent

| 情况 | 做法 |
|---|---|
| 脚本稳定运行 | 不需要 agent，定时跑脚本 |
| 只是网络抖动 | 重试，不需要重新发现 |
| 验证墙 | 人工处理，不需要 agent |
| 字段没变、条数正常 | 不需要任何探测 |

**Agent 只在「第一次接入」和「确认失效」时出现。** 平时它不应该在链路里。

---

## 6. 和现有文档的关系

```text
09 JS 包找接口      → 发现阶段的静态分析
15 Agent 工作流     → 为什么 agent 只做决策
17 本文             → 发现 → 固化 → 回归 的完整闭环
10 采集器工程架构    → 固化后的脚本怎么组织
11 工程红线         → 固化后的验收
12 Agent 任务模板    → 具体任务怎么走
```

---

## 7. 相关文件

- 一站式探测：[`tools/agent_probe.py`](../tools/agent_probe.py)
- JS 包分析：[`tools/js_api_hunt.py`](../tools/js_api_hunt.py)
- DOM 探测：[`tools/dom_probe.py`](../tools/dom_probe.py)
- 声明式采集：[`examples/07_config_driven.py`](../examples/07_config_driven.py)
- 任务模板：[`12-Agent任务模板.md`](12-Agent任务模板.md)
- 工程架构：[`10-采集器工程架构.md`](10-采集器工程架构.md)

---

**最后更新**：2026-11
**维护规则**：每固化一个平台，必须保存发现基线；每次回归修复，必须更新基线并记录变更原因。
