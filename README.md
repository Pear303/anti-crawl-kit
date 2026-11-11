# anti-crawl-kit

浏览器自动化和反爬方向的工具包。接新平台时可以直接从这里起步，少踩一遍
登录态、验证码、改版、静默空结果的坑。

代码来自我自己几个平台采集项目的实践，业务部分（模型、数据库、服务端协议）
已经去掉了。Python 3.10+，浏览器层用 patchright，HTTP 层用 curl_cffi。
给 agent 用的话，先看 [AGENTS.md](AGENTS.md)。

## 安装

```bash
pip install -e ".[all,dev]"
```

也可以分开装：`.[browser]`、`.[http]`、`.[config]`、`.[mcp]`。
只跑逻辑和测试用 `.[dev]`，不需要 patchright。

不装成包也能跑，examples 和 tools 会自动把 `src/` 加进 `sys.path`。

## 先跑两个例子

```bash
python examples/01_hello_browser.py   # 启动系统 Chrome，看 webdriver 是不是 false
python examples/02_generic_scrape.py  # 用 YAML 配置抓公开练习站
python examples/05_http_only.py       # 纯 HTTP，不开浏览器
```

当成库用：

```python
from anti_crawl_kit import BrowserSession

with BrowserSession(headless=False, data_dir="./profiles/demo") as page:
    page.goto("https://example.com")
    print(page.title())
    print(page.evaluate("() => navigator.webdriver", isolated_context=False))
```

## 里面有什么

浏览器层用 patchright 配系统 Chrome，profile 持久化并按平台分开，登录一次
可以复用。长循环会检查窗口是否还在，用户关掉浏览器后任务能退出。同域请求
有统一限频，验证码和登录分开计时。碰到验证墙就截图、发通知、停下等人处理，
不重试。

数据不一定在 DOM 里。工具会先判页面形态，再从 JS bundle 里翻接口、拦 XHR
响应，或者从 `__INITIAL_STATE__` 这类全局变量里提 JSON。DOM 采集放在最后，
配置写成 YAML；改版后选择器失效，可以用自适应匹配重新定位。

HTTP 侧走 curl_cffi，403/429 先分类再决定要不要开浏览器。签名接口只定义
契约，不内置任何平台的算法。

工程侧主要是防静默失效：`RunReport` 把「请求成功但 0 条」判成 empty，
`ack-health` 做例行体检，`ack-smoke` 在发版前跑真实站点，profile 缓存有
白名单和清理阈值。`recipe` 用来编排确定性步骤，中间可以停下来交给 agent
判断；`ack-mcp` 把这些能力包成 MCP 工具。

结果不只在终端里看：`ack-report` 把审计、体检、冒烟、评估的 JSON 转成
单文件 HTML（样式和脚本都内嵌，双击就能开）；`ack-ui` 起一个只监听本机
的控制台，选配方、填参数、跑、看实时日志，agent 检查点也能在页面上继续。

立项阶段可以用 `ack-assess` 走 G0~G4，输出 Go/No-Go 和缺口清单。

## 命令行

```bash
# 一站式探测：给 URL 和目标字段，直接出结论
ack-probe --url "https://目标站/页面" --field fansNum --quick

# 页面结构 / 接口 / 框架
ack-dom --url "https://目标站/list"

# HTML 里没数据时，从 JS 包找接口（零浏览器）
ack-hunt --url "https://目标站/page" --field fansNum

# HTTP 够不够，两路实测
ack-bench --repeat 3

# 14 条采集路径逐条审计
ack-audit --config examples/platforms/bilibili_audit.yaml

# 平台健康体检
ack-health --init
ack-health --config ack_health.yaml
ack-health --config ack_health.yaml --browser

# 真实站点冒烟，FAIL 退出码 1
ack-smoke --json smoke.json
ack-smoke --channel msedge

# MCP server
ack-mcp
ack-mcp --transport http --port 8765

# 技术验证评估
ack-assess --config examples/assessments/books.yaml

# JSON 报告转单文件 HTML（--dir 批量转并生成索引）
ack-report --input runs/health.json
ack-report --dir runs

# 本地 Web 控制台：选配方、填参数、跑、看日志
ack-ui
ack-ui --headed --channel msedge
```

`ack-smoke`、`ack-assess`、`ack-mcp` 都支持 `--channel msedge` 走 Edge。

## 采集路径的优先级

JS 包找接口 > HTTP 直连 > SSR 全局状态 > XHR 拦截 > DOM。

越靠前越快、越省资源，也越不容易撞行为检测；越靠后越稳，能拿到登录态数据。
不确定 HTTP 够不够就先跑 `ack-bench`，别凭感觉。

浏览器拦截跑通不算结束。签名参数能导出来重放的话，还是应该降回 HTTP，
不然每个请求都要开一次浏览器。

## 几个容易踩的点

- 不要覆写 UA。固定的 UA 和真实 Chrome 版本、`sec-ch-ua` 对不上，本身就是特征。
- 登录和验证码阶段不要拦图片，二维码和滑块图被 abort 后就永远等不到了。
- 验证墙出现是止损信号，不是重试信号。
- 判断窗口关没关不要用 `destroyed`，导航期间 evaluate 会抛异常，得用探针。

其余的在 [docs/11-工程红线与验收清单.md](docs/11-工程红线与验收清单.md)。

## 文档

`docs/` 下按场景分：

- 入门：01 决策与方法论、02 patchright 实战规则、04 新平台接入 SOP
- 找数据：09 JS 包找接口、19 数据源选型、22 签名剥离、23 路径审计
- 稳定性：03 防风控、05 排障、06 踩坑、24 健康体检、25 冒烟
- Agent：12 任务模板、15 工作流、17 发现与固化、26 MCP 接入、27 技术验证
- 可视化：29 报告与控制台
- 案例：21 站点形态实测、28 掘金真实采集

`examples/` 有 15 个能直接跑的脚本，`tools/` 是命令行入口，
实现在 `src/anti_crawl_kit/tools/`。

## 边界

只用来采公开数据，或者你有权限的数据。不要拿它破解验证码、绕访问控制、
采个人隐私。频率控制得保守一点，是为了账号安全，不是为了压着上限跑。

完整说明见 [DISCLAIMER.md](DISCLAIMER.md)。

## License

MIT，见 [LICENSE](LICENSE)。第三方依赖和参考的算法思路见 [NOTICE](NOTICE)。
