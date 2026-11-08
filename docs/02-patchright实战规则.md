# 02 · patchright 实战规则

## 1. 为什么是 patchright，不是 playwright

Playwright 在协议层有几处可被检测的泄漏：

- `Runtime.enable` 调用（CDP 检测）
- `Console.enable`
- 启动命令行参数里的自动化标记
- `navigator.webdriver`

patchright 是 Playwright 的补丁分支，在驱动层修掉了这些，API 与 Playwright 一致。
**不要再用 playwright-stealth 叠加**：它的 navigator/硬件伪造会与真实环境冲突，
反而制造新的检测信号。

---

## 2. 版本对齐（最重要的一条）

**patchright 必须与系统 Chrome 大版本同步。**

真实事故：patchright 1.61.2（面向 Chromium 150）驱动 Chrome 151 时，
CDP 首次导航的完成事件丢失，页面一直空白转圈，只能手动硬刷新。

规则：

1. 系统 Chrome 自动更新后，先跑 `tools/detector_check.py` 和一次真实导航。
2. 若 patchright 官方还没支持新版本，先实机验证「导航 + evaluate」是否正常，
   再决定是否放宽版本上限。
3. 升级 patchright 后，重新打包所有分发物（exe / 安装包 / sidecar），
   不要只升级开发环境。

当前工作包基线：patchright 1.62.x，已验证 Chrome 152 / 156 导航正常。

---

## 3. 启动参数：只保留兼容项

`CHROMIUM_ARGS` 里的参数是给**系统 Chrome/Edge** 用的，不是给 Playwright 内置
Chromium 用的。以下参数在 Windows 系统 Chrome 上会导致白屏，已移除：

- `--no-zygote`（Linux 专用）
- `--force-color-profile=srgb`
- `--disable-gpu-rasterization` / `--disable-gpu-composited-animations`
- `--force-device-scale-factor=1`（改用「最大化窗口 + 跟随系统 DPR」）

必须保留：

- `--disable-blink-features=AutomationControlled`
- `--disable-features=...`（合并成一条，Chrome 同 key 只取第一条）
- `--disk-cache-size=268435456`（限制 profile 缓存，防冷启动卡顿）

---

## 4. 有头 / 无头

| | 有头 | 无头 |
|---|---|---|
| 检测风险 | 低 | 高（HeadlessChrome UA、渲染差异） |
| 登录/验证码 | 必须 | 不可用 |
| 截图 | 受 DPI 影响 | 需固定 viewport + DSF=1 |
| 用途 | 采集主链路、发布、登录 | CI、自检、无登录公开页 |

有头模式：

```python
BrowserSession(headless=False)   # viewport=None + --start-maximized
```

无头模式：

```python
BrowserSession(headless=True, viewport={"width": 1920, "height": 1080})
```

无头 UA 会带 `HeadlessChrome`。如果目标站对无头敏感，用有头或
`--headless=new`（patchright 新版本支持时）。

---

## 5. isolated_context：patchright 特有陷阱

patchright 默认在**隔离世界**执行 `evaluate`。这意味着：

```python
# ❌ 读不到页面真实状态，返回 None / 空对象
page.evaluate("() => window.__INITIAL_STATE__")

# ✅ 读主世界
page.evaluate("() => window.__INITIAL_STATE__", isolated_context=False)
```

必须用 `isolated_context=False` 的场景：

- 读 `window.__INITIAL_STATE__` / `__NEXT_DATA__` / `__NUXT__`
- 读 `window.__vue_app__` / Pinia / Vuex
- 读页面 SDK 挂载的全局对象（如抖音 `window.byted_acrawler`）
- 调用页面自身的签名函数

纯 DOM 操作（`querySelector`、`innerText`）保持默认即可。

`PageAdapter.evaluate()` 已把这个参数暴露出来，新代码统一走适配器。

---

## 6. CDP 接管已开浏览器

适用场景：

- 复用用户已登录的 Chrome（不想再扫码）
- 调试已打开页面
- 外部程序管理浏览器生命周期

```python
with BrowserSession(cdp_endpoint="http://127.0.0.1:9222") as page:
    page.goto("https://example.com")
```

注意：

1. **CDP 模式下 `__exit__` 只断开连接，不关闭浏览器进程。**
   浏览器由启动方（用户或外部 launcher）管理。
2. CDP 接管后要主动做「首导航就绪检查」：`document.readyState` 达
   `interactive/complete`，否则 reload（最多 2 次，每次 7s）。
   这段自愈逻辑已内置在 `BrowserSession._ensure_initial_navigation_ready()`。
3. patchright 1.62.1 的 sync `Page.evaluate` **没有 timeout 参数**，
   不要照搬旧版 Playwright 代码传 `timeout=`。

启动带调试端口的 Chrome：

```bat
"C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --remote-debugging-port=9222 ^
  --user-data-dir="%USERPROFILE%\.ac-kit\cdp-profile"
```

---

## 7. 持久化 profile

```python
BrowserSession(data_dir="./profiles/xiaohongshu")
```

- 每个平台一个目录，登录态隔离。
- 目录里是完整 Chrome user-data-dir（Cookie、LocalStorage、IndexedDB）。
- **不要提交到 git**，不要上传服务端。
- 定期清理 `Cache` / `Code Cache`（`--disk-cache-size` 已限制总量；
  超过 400MB 可手工删缓存目录，但不要删 `Default/Cookies`）。

profile 缓存过大的真实后果：Chrome 冷启动要加载校验缓存，登录窗/采集卡十几秒。
实测单 profile 曾到 487MB、总缓存 3GB。

---

## 8. 关窗 watchdog

持久化 context 没有 `browser` 对象，无法监听 `disconnected` 事件。
`BrowserSession` 用 5s 轮询 `context.pages` 判断：

- `pages` 为空 → 浏览器被关
- `pages` 抛异常 → 浏览器崩溃

`check_browser_alive()` 返回 False 后，业务层应尽快退出。

但注意：watchdog 只是「粗粒度」感知。等待循环里必须用
`anti_crawl_kit.alive.ensure_alive()` 做「细粒度」探测（见 06 踩坑实录）。

---

## 9. 资源拦截：默认关闭

`block_resources=True` 会 abort 图片/字体/媒体。

**只在以下条件同时满足时开启：**

- 已登录
- 页面无验证码
- 不需要截图
- 不需要二维码

否则会出现「登录二维码一直不显示」「滑块图加载不出来」这类问题。
实测对加载速度提升也有限（`domcontentloaded` 通常在图片加载前就触发）。

---

## 10. 常见错误对照

| 报错 | 原因 | 处理 |
|---|---|---|
| `Executable doesn't exist` | 用了内置 chromium 但没装 | 用 `channel="chrome"`，或 `patchright install chromium` |
| `Target page, context or browser has been closed` | 用户关窗 / 浏览器崩溃 | `ensure_alive` 捕获，退出任务 |
| `Execution context was destroyed` | 页面正在导航 | **不要当关窗**，等导航完成 |
| `net::ERR_ABORTED` | 资源被拦截 / 导航被打断 | 检查 `block_resources` |
| 页面空白但 `goto` 成功 | CDP 首导航完成事件丢失 | 检查 readyState，reload |
| `driver` 进程残留 | `__enter__` 失败未 stop | 已内置 `_cleanup_after_failed_enter` |
| 页面缩放 7% | profile 持久化 zoom | `_clear_persistent_zoom` 已内置 |
