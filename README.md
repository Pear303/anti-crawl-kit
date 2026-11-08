# 浏览器自动化 + 反爬防风控 · 基础工作包

> **如果你是 agent：先读 [`AGENTS.md`](AGENTS.md)。** 里面是 30 秒决策、红线速查和任务模板。
>
> 一个可以直接复制到新项目里的「爬虫 / 浏览器自动化」起点。
> 代码来自一套已在生产运行的多平台内容采集系统（抖音 / 快手 / B站 / 小红书 /
> 知乎 / 头条 / CSDN / 简书 / 掘金 / 百家号 / 公众号等），经过真实风控对抗与
> 线上事故打磨；文档记录的是踩过的坑，不是理论。

---

## 1. 这个包解决什么问题

新项目要抓数据时，通常会在同样几个地方反复摔跤：

| 问题 | 本包的答案 |
|---|---|
| Playwright 一打开就被识别 | patchright + 精简 stealth + 真实系统 Chrome |
| 登录态每次都要重扫 | 持久化 `user-data-dir`，按平台隔离 profile |
| 验证码 / 登录等待把预算烧光 | 验证码、登录「双时钟」独立预算 |
| 用户关掉浏览器，程序空转 5 分钟 | `ensure_alive()` 关窗即取消 |
| 平台改版，选择器全废 | `tools/dom_probe.py` + 声明式 YAML 配置 |
| 不知道数据在 DOM 还是接口 | `ApiDiscoverer` + 响应拦截 + `__INITIAL_STATE__` |
| 接口 403 / 429 不知道怎么退 | `classify_http_result` → 自动判定是否降级浏览器 |
| HTTP 请求 TLS 指纹被识别 | `curl_cffi` 浏览器指纹伪装 |
| 采集频率太高被封 | 集中化导航限频 + 自适应退避 |
| 采到的正文混入反爬水印 | `clean_anti_crawl_watermark` |
| 页面加密 + 必须登录 | `08_logged_in_encrypted.py`：持久化 profile + 接口拦截 + 渲染稳定判定 |
| 加载更多时撞上验证码 | `ChallengeGate`：截图 + 通知 + 立即停，绝不重试 |
| HTML 里没数据，数字是空的 | `tools/js_api_hunt.py`：枚举全部 JS（含动态 chunk）找真实接口 |
| 页面是 SPA，HTML 只有一个 script | `js_api_hunt.py --browser always`：枚举页面实际加载的全部 JS |
| 不知道 HTTP 够不够、该不该开浏览器 | `tools/bench.py`：同站两路实测，比成功率与成本 |
| 不知道有没有漏掉某条路径 | `docs/23`：14 条路径全量审计 + 生产选型评分卡 |
| 任务「成功」但 0 条数据 | `RunReport` 强制 `actual_count=0 → empty`，CI 直接红 |
| 平台改版靠用户投诉发现 | `ack-health`：静态注册表校验 + 免登录动态探针 |
| profile 涨到几个 GB、登录窗卡死 | `prune_profile_cache()`：白名单 + 双阈值 + 24h 节流 |

---

## 2. 五分钟上手

```bash
# 1) 安装（建议虚拟环境）
pip install -e ".[all,dev]"      # 全部能力 + 测试
# 按需：
# pip install -e ".[browser]"     # 只要浏览器（patchright）
# pip install -e ".[http]"        # 只要 HTTP（curl_cffi）
# pip install -e ".[dev]"         # 纯逻辑 + 测试（不装 patchright）

# 0) 一站式探测（可选加速器，3~5 行出决策）
python tools/agent_probe.py --url "https://目标站/页面" --field 目标字段 --quick

# 2) 自检：能否启动系统 Chrome、webdriver 是否为 false
python examples/01_hello_browser.py

# 3) 声明式采集（公开练习站）
python examples/02_generic_scrape.py

# 4) 拦截页面 JSON 接口（B站热门，公开）
python examples/04_intercept_api.py

# 5) 纯 HTTP 直连（更快）
python examples/05_http_only.py

# 6) HTML 里没数据？从 JS 包找接口（零浏览器）
python tools/js_api_hunt.py \
    --url "https://目标站/页面" \
    --cookie "关键cookie=xxx" \
    --field fansNum

# 7) 登录后的加密页面（有头登录 → 拦截接口 → 渲染稳定后落盘）
python examples/08_logged_in_encrypted.py \
    --url "https://目标站/需要登录的页面" \
    --profile ./profiles/demo \
    --login-js examples/platforms/login_check_generic.js \
    --content-selector "article, .note-content" \
    --api-pattern "/api/"

# 8) 自动加载更多 + 验证墙硬停（命中即截图/通知/退出码 2）
python examples/09_load_more_guard.py \
    --url "https://目标站/列表页" \
    --profile ./profiles/demo \
    --item-selector "section.note-item" \
    --max-rounds 20

# 9) 多策略降级（SSR → XHR → DOM）
python examples/10_strategy_fallback.py --headless

# 10) 登录态三态（valid / expired / missing）
python examples/11_login_state_tristate.py

# 11) 数据源分层兜底 + 双跑校验
python examples/12_datasource_fallback.py

# 12) 内容质量校验（识别投毒章节）
python examples/13_content_quality.py

# 13) 自适应选择器（改版后重定位元素）
python examples/14_adaptive_selector.py

# 14) SSR 状态提取（不用浏览器直接拿 JSON）
python examples/15_state_extract.py
python examples/15_state_extract.py --live

# 15) 最小评测：这个站 HTTP 够不够？
python tools/bench.py --repeat 3

# 16) 平台路径全量审计（14 条路径逐条探测 + 评分卡 + 选型卡）
python tools/path_audit.py --config examples/platforms/bilibili_audit.yaml
# 只跑几条 / 强制开浏览器：
python tools/path_audit.py --config examples/platforms/bilibili_audit.yaml \
    --only anonymous_json,ssr_state --browser always

# 17) 平台健康体检（改版静默失效的例行检查，FAIL 退出码 1）
ack-health --init                                  # 生成配置模板
ack-health --config ack_health.yaml                # 静态体检（秒级）
ack-health --config ack_health.yaml --browser      # + 免登录动态探针
```

不想安装成包也能跑：所有示例和工具都会自动把 `src/` 加进 `sys.path`。
想当依赖用：

```bash
pip install -e .
```

最小代码：

```python
from anti_crawl_kit import BrowserSession

with BrowserSession(headless=False, data_dir="./profiles/demo") as page:
    page.goto("https://example.com")
    print(page.title())
    print(page.evaluate("() => navigator.webdriver", isolated_context=False))
```

---

## 3. 目录结构

```
browser-automation-kit/
├─ AGENTS.md                    # ★ agent 入口：30 秒决策 + 红线 + 任务模板
├─ src/anti_crawl_kit/          # 可复用核心库
│  ├─ session.py                # ★ 浏览器会话（系统 Chrome、持久化、CDP、watchdog）
│  ├─ page.py                   # ★ PageAdapter（导航重试、反爬守卫、响应拦截）
│  ├─ stealth.py                # 反检测 JS + 框架/状态探测 JS
│  ├─ behavior.py               # 贝塞尔鼠标、变速键入、滚动节奏
│  ├─ guard.py                  # 集中化反爬守卫（同域限频、风控页检测）
│  ├─ captcha.py                # 验证码检测（URL + 平台 DOM）
│  ├─ login.py                  # 登录轮询框架
│  ├─ waiting.py                # ★ 验证码/登录双时钟
│  ├─ alive.py                  # ★ 关窗即取消
│  ├─ http.py / risk.py         # HTTP 直连 + 风险分类
│  ├─ headers.py                # 平台 Headers / CDN Referer
│  ├─ js_bundle.py              # ★ 从 JS 包找真实接口（零浏览器）
│  ├─ api_discovery.py          # ★ 新平台接口发现
│  ├─ generic.py                # ★ 声明式列表采集器
│  ├─ challenge.py              # ★ 验证墙硬停闸门（截图 + 通知 + 抛异常）
│  ├─ notify.py                 # ★ 多通道通知（控制台/桌面/webhook）
│  ├─ loadmore.py               # ★ 自动加载更多（滚动/点按钮 + 无增长终止）
│  ├─ strategy.py               # 多策略降级执行
│  ├─ platform_spec.py          # ★ 平台能力描述 + 登录态三态 + 身份比对
│  ├─ datasource.py             # ★ 数据源分层兜底 + 双跑校验 + 回滚开关
│  ├─ content_quality.py        # ★ 投毒/低质内容检测（长度/重复/广告/乱码/熵）
│  ├─ empty_shell.py            # ★ 空壳页检测（正文太短/选择器 0 命中/骨架屏）
│  ├─ adaptive_selector.py      # ★ 改版后重定位元素（零依赖，参考 Scrapling）
│  ├─ page_shape.py             # ★ 页面形态分类（SSR/状态容器/SPA/登录墙/WAF）
│  ├─ state_extract.py          # ★ 从 __NEXT_DATA__/__NUXT__/__INITIAL_STATE__ 提 JSON
│  ├─ text.py                   # 计数归一 / 水印清洗 / 去重键
│  ├─ resources.py              # 资源拦截（有明确使用边界）
│  ├─ http_signed.py            # ★ 带签名接口规格（契约，不内置算法）
│  ├─ run_report.py             # ★ 可判定的运行报告（empty / 覆盖率 / 降级原因）
│  ├─ health.py                 # ★ 平台健康体检（注册表 + 入口 + 登录判据 + 探针）
│  ├─ profile_cache.py          # ★ profile 缓存治理（白名单 + 双阈值 + 节流）
│  ├─ console.py                # ★ GBK 控制台兜底 + 日志脱敏
│  └─ tools/                    # 探测与评测的库实现（随包安装）
├─ examples/                    # 15 个可运行示例
├─ tools/                       # 兼容入口（实现在 src/anti_crawl_kit/tools/）
│  ├─ agent_probe.py            # ★ Agent 一站式探测（决策 + JSON）
│  ├─ dom_probe.py              # ★ 新平台接入探测（选择器/接口/框架）
│  ├─ js_api_hunt.py            # ★ 从 JS 包找接口（全量 chunk / baseURL / 字段名）
│  ├─ bench.py                  # ★ curl_cffi vs patchright 最小评测
│  ├─ path_audit.py             # ★ 14 条路径全量审计（矩阵 + 评分卡 + 选型卡）
│  ├─ health.py                 # ★ 平台健康体检（ack-health）
│  └─ detector_check.py         # 反检测自检（sannysoft/rebrowser/creepjs）
├─ scripts/
│  └─ gen_requirements.py       # 从 pyproject 生成 requirements（单一来源）
├─ docs/                        # 方法论 + SOP + 排障 + 踩坑 + 平台速查
├─ benchmarks/                  # 评测输出（report.md + results.json）
└─ tests/                       # 离线测试 + 可选浏览器集成（ACK_BROWSER=1）
```

---

## 4. 采集方案决策树（先想清楚再写代码）

```
目标数据
│
├─ 第 0 步：判形态（classify_page_shape）
│   ├─ ssr        → 数据在 HTML，直接解析
│   ├─ ssr_state  → HTTP 正则提 JSON（比浏览器便宜一个量级）
│   ├─ spa_shell  → 继续往下走
│   ├─ login_wall → 持久化 profile + 人工登录
│   └─ captcha / blocked → 停，不要重试
│
├─ 先 requests 抓 HTML：目标数据在里面吗？
│   ├─ 在 → 直接解析（SSR / DOM），不要开浏览器
│   └─ 不在（空壳、数字是空的）↓
│
├─ 页面加载了 JS？→ tools/js_api_hunt.py（默认递归全部 chunk）
│   ├─ 找到接口 + 不需要签名 → HTTP 直连（curl_cffi）
│   └─ 找到接口 + 需要签名   → 浏览器 + response 拦截
│        └─ 别停在 L3：做签名剥离测试（docs/22），能导出参数就降回 L1
│
├─ 有公开 JSON 接口？
│   ├─ 是 → 先 HTTP 直连（curl_cffi）
│   │        ├─ 200 且结构稳定 → 结束（成本最低）
│   │        └─ 403/429/签名失败 → 降级浏览器
│   └─ 否 ↓
│
├─ 页面 SSR 注入了 __INITIAL_STATE__ / __NEXT_DATA__ / __NUXT__？
│   └─ 是 → HTTP 正则提取 JSON；提取不到再浏览器打开一次读全局变量
│
├─ 页面 XHR 里有 JSON？
│   └─ 是 → 浏览器 + response 拦截（比 DOM 稳）
│
└─ 只能解析 DOM
    ├─ 结构稳定 → 声明式 YAML（GenericCollector）
    └─ 结构复杂/需交互 → 自定义脚本（PageAdapter + behavior）
```

**优先级：JS 包找接口 > HTTP 直连 > SSR 全局状态 > XHR 拦截 > DOM 解析。**
越靠左越快、越省资源、越不容易被行为检测；越靠右越稳、越能拿到登录态数据。

**不确定 HTTP 够不够？** 先跑 `python tools/bench.py --repeat 3`，
用同一站点的两路实测结果决定，不要凭感觉。

---

## 5. 十五条铁律（违反会浪费很多时间）

1. **浏览器默认不覆写 UA。** 固定 UA 与真实 Chrome 版本、`sec-ch-ua` 矛盾，是检测信号。
2. **patchright 必须与系统 Chrome 大版本同步。** Chrome 自动更新不可控；版本 gap 会导致 CDP 首导航空白转圈。
3. **登录/验证码阶段不要拦截图片。** 二维码、滑块图被 abort 后永远等不到。
4. **验证码不要尝试自动破解。** 检测到就提示用户手动完成，程序轮询等待。
5. **验证码和登录必须分开计时。** 共用一个预算必然出现「还没扫码就超时」。
6. **任何长等待循环都要检查浏览器存活。** 用户关窗后 `is_closed()` 可能假阴性，要用 evaluate 探针。
7. **不要用 `destroyed` 判断关窗。** 页面导航期间 evaluate 会抛 `Execution context was destroyed`，窗口还活着。
8. **profile 按平台隔离。** 一个目录混用多平台登录态，既容易串号也更容易被关联。
9. **接口失败要能降级。** HTTP 403/429 不是终点，切浏览器往往能过。
10. **平台改版是常态。** 选择器写进配置、探测脚本随代码走，不要把选择器散落在业务逻辑里。
11. **验证墙是止损信号，不是重试信号。** 命中就截图 + 通知 + 退出，重试只会加深风控评分。
12. **加密页面不要逆算法。** 让浏览器自己解密，优先拦截它发出的 XHR；逆出来的签名下周就过期。
13. **HTML 没数据先查 JS 包。** 数字是空壳、只有一个 `<script src>` 时，接口大概率写在 JS 里；先 `js_api_hunt.py`，别急着开浏览器。
14. **不要只扫一个 JS chunk。** 字段名、接口定义、错误码可能分散在 20+ 个动态 chunk 里；先看「发现 N 个 / 扫描 M 个」的覆盖率，M < N 时结论不可信。
15. **L3 不是终点。** 浏览器拦截跑通后仍要做签名剥离测试（docs/22）；能导出运行时参数就用 `AntiRiskHttpClient` 重放，不要长期为每个请求开浏览器。

---

## 6. 新平台接入五步法

```bash
# 第 1 步：探测页面（框架 / 接口 / 重复 DOM 结构 / 链接）
python tools/dom_probe.py --url "https://目标站/list"

# 第 1.5 步：HTML 里没数据？先查 JS 包（零浏览器）
python tools/js_api_hunt.py --url "https://目标站/page" --field 目标字段

# 第 2 步：判断数据源
#   - 输出里 [页面请求过的候选接口] 有 JSON → 走拦截（examples/04）
#   - 有 __INITIAL_STATE__ → 读全局状态
#   - 都没有 → 用 DOM 候选选择器

# 第 3 步：写配置（或自定义采集器）
cp examples/platforms/quotes.yaml examples/platforms/新平台.yaml

# 第 4 步：跑通 20 条
python examples/07_config_driven.py examples/platforms/新平台.yaml --out output/test.json

# 第 5 步：验收
#   - 字段完整率、重复率、翻页/滚动是否停得下来
#   - 连跑 3 次不触发验证码
#   - 关掉浏览器窗口，任务能在 10s 内退出
```

详细 SOP 见 [`docs/04-新平台接入SOP.md`](docs/04-新平台接入SOP.md)。

---

## 7. 文档索引

| 文档 | 什么时候读 |
|---|---|
| [01-决策与方法论](docs/01-决策与方法论.md) | 新项目开始，决定技术路线 |
| [02-patchright实战规则](docs/02-patchright实战规则.md) | 写浏览器代码前必读 |
| [03-防风控策略清单](docs/03-防风控策略清单.md) | 被风控/封号/验证码频繁时 |
| [04-新平台接入SOP](docs/04-新平台接入SOP.md) | 每接一个新平台 |
| [05-排障手册](docs/05-排障手册.md) | 出问题先查这里 |
| [06-踩坑实录](docs/06-踩坑实录.md) | 想知道「为什么这么设计」 |
| [07-平台速查表](docs/07-平台速查表.md) | 接国内内容平台前速查 |
| [08-登录加密页与验证墙硬停](docs/08-登录加密页与验证墙硬停.md) | 需要登录/加密页面，或加载中撞验证码 |
| [09-JS包找接口](docs/09-JS包找接口.md) | HTML 里没数据、数字是空壳、想零浏览器直连 |
| [10-采集器工程架构](docs/10-采集器工程架构.md) | 设计一个三个月后还能跑的采集器 |
| [11-工程红线与验收清单](docs/11-工程红线与验收清单.md) | 上线前、改版后、事故复盘 |
| [12-Agent任务模板](docs/12-Agent任务模板.md) | 拿到具体任务，按模板走 |
| [13-平台适配器与登录态](docs/13-平台适配器与登录态.md) | 接新平台、判断登录态、防串号 |
| [14-开源生态与选型](docs/14-开源生态与选型.md) | 想换引擎/框架，或评估新工具 |
| [15-AI-Agent爬虫工作流](docs/15-AI-Agent爬虫工作流.md) | 用 agent 驱动采集、设计 agent 产品 |
| [16-引擎与工具接入](docs/16-引擎与工具接入.md) | 接 camoufox / pydoll / MCP / Scrapy |
| [17-Agent发现与脚本固化](docs/17-Agent发现与脚本固化.md) | Agent 发现 → 固化脚本 → 改版回归 |
| [18-脚本价值审计](docs/18-脚本价值审计.md) | 哪些脚本真有用、哪些是冗余 |
| [19-数据源选型与兜底](docs/19-数据源选型与兜底.md) | API vs HTML、双跑校验、回滚开关 |
| [20-最小评测](docs/20-最小评测.md) | 用实测数据决定 HTTP 还是浏览器 |
| [21-站点形态实测](docs/21-站点形态实测.md) | 43 站实测：SSR / 状态容器 / SPA / 登录墙 / WAF |
| [22-登录态与签名剥离](docs/22-登录态与签名剥离.md) | 浏览器能跑但 HTTP 不行、ID/字段/错误码对不上 |
| [23-平台路径全量审计](docs/23-平台路径全量审计.md) | 要测所有路径、比较方案、选生产最优 |
| [24-健康体检与静默失效](docs/24-健康体检与静默失效.md) | 任务「成功」但 0 条、改版静默失效、profile 膨胀 |

---

## 8. 边界与合规

- 本包只提供**技术基础设施**，不包含任何平台的账号、Cookie、密钥或私有接口签名。
- 仅用于采集**公开可见**的数据；遵守目标站 `robots.txt`、服务条款与当地法律。
- 不要用于绕过付费墙、批量注册、刷量、骚扰或侵犯隐私。
- 采集频率请保持在合理范围；本包的限频默认值偏保守，是为了保护你的账号，不是为了突破限制。

---

## 9. 来源与维护

- 代码提炼自生产项目 `packages/多个真实平台采集项目`（2026-07 ~ 2026-09 实战版本），
  已去除业务模型、数据库、服务端协议等耦合，只保留通用能力。
- 版本基线：patchright 1.62.x / Chrome 152+；Python 3.10+。
- 平台选择器会过期。任何「平台速查表」里的选择器都只作结构参考，
  上线前必须用 `tools/dom_probe.py` 重新确认。
