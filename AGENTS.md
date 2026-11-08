# AGENTS.md — 拿到任务先读这里

> 这是一个「浏览器自动化 + 反爬防风控」的工作包。
> 你的目标不是写最多代码，而是**用最短路径拿到稳定数据**。

---

## 30 秒决策

```text
第 0 步：先判形态（classify_page_shape，或 agent_probe --quick）
├─ ssr        → 数据在 HTML，直接解析
├─ ssr_state  → 先读 __NEXT_DATA__ / __NUXT__ / __INITIAL_STATE__
├─ spa_shell  → 走下面的「不在 HTML」分支
├─ login_wall → 持久化 profile + 人工登录
└─ captcha / blocked → 停，不要重试

目标数据在 HTML 里吗？
├─ 在 → 直接解析，不要开浏览器
└─ 不在（空壳/数字是空的）
   ├─ 有外链 JS → tools/js_api_hunt.py 找接口
   │              ├─ 静态入口 ≤2 个 → --browser always 枚举全部 chunk
   │              ├─ 不需要签名 → requests/curl_cffi 直连
   │              └─ 需要签名   → 浏览器拦截
   └─ 没有 JS → 浏览器 + XHR 拦截 / DOM
```

**顺序永远是：JS 包 → HTTP 直连 → SSR 状态 → XHR 拦截 → DOM → 截图 OCR。**

**L3 不是终点。** 浏览器拦截跑通后，做一次签名剥离测试（`docs/22`）；能导出运行时参数就降回 L1。

**选生产路径前，先过 `docs/23` 的 14 条路径矩阵。** 没测过的路径写“未测”，不能写“不可用”。

不要一上来就 Playwright。

---

## 常用命令

```bash
# 0) 一站式探测（可选加速器；手写也行，但 HTTP 必须用 AntiRiskHttpClient）
python tools/agent_probe.py --url "URL" --field fansNum --quick

# 0.1) 保存发现基线（以后回归用）
python tools/agent_probe.py --url "URL" --field fansNum \
    --save-baseline output/discovery/demo.json

# 0.2) 脚本失效时，与基线对比出变更点
python tools/agent_probe.py --url "URL" --field fansNum \
    --baseline output/discovery/demo.json

# 1) 页面探测（框架/接口/选择器）
python tools/dom_probe.py --url "URL"

# 2) HTML 空壳 → 从 JS 包找接口（零浏览器）
python tools/js_api_hunt.py --url "URL" --cookie "k=v" --field fansNum

# 2.1) SPA / 动态 import：枚举页面实际加载的全部 JS
python tools/js_api_hunt.py --url "URL" --browser always --max-scripts 80 --field fansNum

# 3) 纯 HTTP 直连
python examples/05_http_only.py

# 4) 浏览器拦截 XHR
python examples/04_intercept_api.py

# 5) 登录 + 加密页
python examples/08_logged_in_encrypted.py \
    --url "URL" --profile ./profiles/demo \
    --login-js examples/platforms/login_check_generic.js

# 6) 加载更多 + 验证墙硬停
python examples/09_load_more_guard.py \
    --url "URL" --item-selector "section.note-item"

# 7) 反检测自检
python tools/detector_check.py

# 8) 最小评测：这个站 HTTP 够不够？
python tools/bench.py --repeat 3

# 5) SSR 状态提取（不用浏览器直接拿 JSON）
python examples/15_state_extract.py
python examples/15_state_extract.py --live
```

---

## 目录地图

```text
src/anti_crawl_kit/
├─ session.py       浏览器会话（系统 Chrome、持久化 profile、CDP）
├─ page.py          PageAdapter（导航重试、限频、响应拦截）
├─ stealth.py       反检测注入 + 框架/状态探测 JS
├─ behavior.py      贝塞尔鼠标、变速键入、滚动节奏
├─ guard.py         同域限频 + 风控页检测
├─ captcha.py       验证码检测（URL + 平台 DOM）
├─ waiting.py       验证码/登录双时钟 + 渲染稳定等待
├─ challenge.py     验证墙硬停（截图 + 通知 + 抛异常）
├─ notify.py        多通道通知（控制台/桌面/webhook）
├─ loadmore.py      自动加载更多（滚动/点按钮 + 无增长终止）
├─ alive.py         关窗即取消
├─ http.py/risk.py  HTTP 直连 + 风险分类
├─ js_bundle.py     从 JS 包找真实接口
├─ api_discovery.py 浏览器内接口发现
├─ generic.py       声明式列表采集器
├─ strategy.py      多策略降级执行
├─ platform_spec.py 平台能力描述 + 登录态三态 + 身份比对
├─ datasource.py    数据源分层兜底 + 双跑校验 + 回滚开关
├─ content_quality.py 投毒/低质内容检测
├─ empty_shell.py   空壳页检测
├─ adaptive_selector.py 改版后重定位元素
├─ page_shape.py    页面形态分类
├─ state_extract.py SSR 状态 JSON 提取
├─ text.py          计数归一 / 水印清洗 / 去重键
└─ resources.py     资源拦截

tools/
├─ dom_probe.py      新平台接入探测
├─ js_api_hunt.py    从 JS 包找接口
├─ bench.py          curl_cffi vs patchright 最小评测
└─ detector_check.py 反检测自检

docs/                方法论 + SOP + 红线 + 任务模板
examples/            15 个可运行示例
benchmarks/          评测输出（report.md + results.json）
tests/               无浏览器冒烟测试
```

---

## 红线速查（完整见 `docs/11`）

1. **patchright 与 Chrome 大版本必须同步。**
2. **不要覆写 UA。** 系统 Chrome 默认值最真实。
3. **登录/验证码阶段不要拦图片。**
4. **不要只信 `page.is_closed()`。** 用 `evaluate` 探针确认存活。
5. **`destroyed` 不是关窗。** 导航期间会稳定抛。
6. **验证墙是止损信号，不是重试信号。** 命中就停。
7. **批量阶段不要等人。** 登录阶段才等。
8. **不要用 `0` 表示缺失。** 用 `None`。
9. **同一份数据至少留两条路。**
10. **选择器必须有兜底。**
11. **长循环必须可取消。**
12. **不要吞验证墙/关窗异常。**
13. **HTML 没数据先查 JS 包，别急着开浏览器。**
14. **不要只扫一个 JS chunk。** 先看「发现 N 个 / 扫描 M 个」，M < N 时结论不可信。
15. **L3 不是终点。** 浏览器跑通后做签名剥离测试，能降回 L1 就降回 L1。

---

## 文档阅读顺序

| 场景 | 读哪篇 |
|---|---|
| 第一次用这个包 | `README.md` → 本文 |
| 决定技术路线 | `docs/01-决策与方法论.md` |
| 写浏览器代码 | `docs/02-patchright实战规则.md` |
| 被风控/验证码烦 | `docs/03-防风控策略清单.md` |
| 接新平台 | `docs/04-新平台接入SOP.md` |
| 出问题 | `docs/05-排障手册.md` |
| 想知道为什么 | `docs/06-踩坑实录.md` |
| 查平台细节 | `docs/07-平台速查表.md` |
| 登录/加密/验证墙 | `docs/08-登录加密页与验证墙硬停.md` |
| HTML 空壳 | `docs/09-JS包找接口.md` |
| 设计采集器 | `docs/10-采集器工程架构.md` |
| 上线前检查 | `docs/11-工程红线与验收清单.md` |
| 拿到具体任务 | `docs/12-Agent任务模板.md` |
| 接平台 / 判断登录态 | `docs/13-平台适配器与登录态.md` |
| 换引擎 / 评估新工具 | `docs/14-开源生态与选型.md` |
| Agent 驱动采集 | `docs/15-AI-Agent爬虫工作流.md` |
| 接 camoufox / MCP / pydoll | `docs/16-引擎与工具接入.md` |
| 发现数据源 / 固化脚本 / 改版回归 | `docs/17-Agent发现与脚本固化.md` |
| 判断某个模块要不要用 | `docs/18-脚本价值审计.md` |
| API 和 HTML 选哪个 / 怎么兜底 | `docs/19-数据源选型与兜底.md` |
| 想用数据决定 HTTP 还是浏览器 | `docs/20-最小评测.md` |
| 接新站 / 判断页面形态 | `docs/21-站点形态实测.md` |
| 浏览器能跑但 HTTP 不行 / ID 字段对不上 | `docs/22-登录态与签名剥离.md` |
| 要测所有路径 / 选生产最优 | `docs/23-平台路径全量审计.md` |
| 任务「成功」但 0 条 / 改版静默失效 | `docs/24-健康体检与静默失效.md` |

---

## 任务模板速查

| 任务 | 模板 |
|---|---|
| 第一次接平台 | A |
| 之前能跑，现在 0 条 | B |
| 老弹验证码 | C |
| 要自动发内容 | D |
| 要采几千条 | E |
| 先评估能不能采 | F |

详见 `docs/12-Agent任务模板.md`。

---

## 工作方式

### 按阶段选工具，不要一刀切

| 阶段 | 允许 | 必须 |
|---|---|---|
| **探索**（第一次碰这个站） | agent 手写探测，或跑 `agent_probe` | HTTP 用 `AntiRiskHttpClient` |
| **固化**（要重复跑） | 生成脚本 / YAML | 接口和选择器带 `last_verified` |
| **回归**（改版后修复） | 手写或工具 | 先跑 baseline diff，再决定改哪层 |

**探测工具（`agent_probe` / `js_api_hunt` / `dom_probe`）是可选加速器，不是强制入口。**
agent 手写能解决就手写，但不要裸 `requests`。

### 六条硬规则

1. **HTTP 必须用 `AntiRiskHttpClient`。** 裸 `requests` 的 TLS 指纹和 Chrome 不同，强风控站会直接 403。
2. **先跑通 20 条，再放大。** 不要一上来就 1000 条。
3. **每次只改一层。** 同时改选择器和接口，出问题无法归因。
4. **留下证据。** 截图、HTML、meta、日志，出事能复盘。
5. **验证墙前认输。** 停 + 通知 + 等人，比硬刚省时间。
6. **任务成功 ≠ 拿到数据。** `RunReport` 必须带 `actual_count`；`actual_count=0` 一律 `empty`，CI 必须红。新增平台必须跑 `ack-health`。

### 什么必须用，什么可选

| 类型 | 例子 | 必要性 |
|---|---|---|
| **库** | `AntiRiskHttpClient`、`ChallengeGate`、`Notifier` | **必须**：补的是手写容易漏、漏了会出事的东西 |
| **探测工具** | `agent_probe`、`js_api_hunt`、`dom_probe` | **可选**：加速器，agent 手写也行 |
| **固化脚本** | `scripts/*.py`、`platforms/*.yaml`、`path_audit` 配置 | **必须**：生产环境不能每次让 LLM 现场生成 |

---

## 环境

- Python 3.10+
- 系统 Chrome（或用 `channel="msedge"`）
- 安装：`pip install -e ".[all,dev]"`（全部能力 + 测试）
  - 纯逻辑：`pip install -e ".[dev]"`（不装 patchright / curl_cffi）
  - 按需：`pip install -e ".[browser]"` / `".[http]"` / `".[config]"`
- `requirements.txt` 由 `scripts/gen_requirements.py` 生成，勿手改
- 临时文件用 `C:\Windows\Temp\opencode`
- 安装后 CLI：`ack-probe` / `ack-hunt` / `ack-dom` / `ack-detect` / `ack-bench` / `ack-audit` / `ack-health`

```bash
python -m pytest tests -q             # 离线测试，应全绿
$env:ACK_BROWSER="1"; python -m pytest tests -m browser -q   # 浏览器集成（手动）
python examples/01_hello_browser.py   # 自检
```

---

**这个包不包含任何平台的账号、Cookie、密钥或私有签名。**
只用于采集公开可见数据；遵守目标站条款与当地法律。
