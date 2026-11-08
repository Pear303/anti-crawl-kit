# 04 · 新平台接入 SOP

> 目标：从「拿到一个 URL」到「稳定采集 20 条」不超过半天。
> 每接一个平台，按顺序走完 9 步，不要跳步。

---

## 第 0 步：合规与边界

- 确认数据是公开可见的，不需要登录也能看到（或你有合法账号）。
- 看 `robots.txt` 与平台条款；不要碰个人隐私、付费内容、私信。
- 确认采集频率在合理范围（默认限频已偏保守）。

---

## 第 1 步：探测页面

```bash
python tools/dom_probe.py --url "https://目标站/列表页"
```

重点看四块输出：

1. **框架检测**：Vue3 / React / Next.js / Nuxt → 决定用哪种全局状态。
2. **`__INITIAL_STATE__` 顶层键**：有数据就直接读，不要解析 DOM。
3. **页面请求过的候选接口**：有 JSON 接口就优先拦截。
4. **重复 DOM 结构候选**：`10x div.note-item` 这类就是列表卡片。

如果需要登录才能看到内容：

```bash
python tools/dom_probe.py --url "https://目标站" --data-dir ./profiles/目标平台
```

---

## 第 1.5 步：HTML 没数据？先查 JS 包（别急着开浏览器）

如果第 1 步发现目标数字/列表在 HTML 里是空的，先跑：

```bash
python tools/js_api_hunt.py \
    --url "https://目标站/页面" \
    --cookie "关键cookie=xxx" \
    --field fansNum \
    --field subscribeNum
```

它会：

1. 抓 HTML，提取所有 `<script src>`；
2. 补 `Referer` + UA 下载 JS（绕过防盗链 403）；
3. 跟进 152 字节的「加载器」JS，找到真正的 chunk；
4. 搜 `baseURL` / `.get("/xxx")` / 字段名；
5. 拼出完整接口 URL，直接告诉你候选接口。

找到接口且不需要签名 → 用 `requests` / `curl_cffi` 直连，**零浏览器**。
完整方法见 [`09-JS包找接口.md`](09-JS包找接口.md)。

> 判断标准：先花 10 分钟查 JS，查不到再上浏览器。不要反过来。

---

## 第 2 步：判定数据源

按 `01-决策与方法论` 的优先级：

```
L0 JS 包找接口 →  tools/js_api_hunt.py
L1 HTTP 接口  →  examples/05_http_only.py
L2 SSR 状态   →  page.evaluate("() => window.__INITIAL_STATE__", isolated_context=False)
L3 XHR 拦截   →  examples/04_intercept_api.py
L4 DOM        →  examples/02_generic_scrape.py / 07_config_driven.py
```

**判定技巧：**

- 接口 URL 里没有 `sign` / `_signature` / `a_bogus` / `w_rid` / `X-s` → 先试 L1。
- 接口需要 cookie 但不需要签名 → `PageAdapter.api_request()`（带浏览器 cookie）。
- 接口需要签名 → L3 拦截页面自己发的请求，不要自己构造。
- 页面滚动后 DOM 才出现数据 → L3 或 L4。

---

## 第 3 步：登录与验证码

1. 找登录检测锚点：头像、用户名、「发布」按钮、特定 cookie。
2. 写 `login_js`（返回 true 表示已登录）。
3. 选验证码检测：内置平台名或自定义 `captcha_js`。
4. 用 `wait_for_captcha_and_login`，不要自己写 while 循环。

```python
outcome = wait_for_captcha_and_login(
    page=page,
    check_login=check_login,
    captcha_dom_js=get_platform_captcha_dom_js("zhihu"),
    login_timeout=200,
    label="zhihu",
)
if not outcome.resolved:
    # 区分 captcha_exhausted / login_exhausted
    ...
```

**常见错误：** 登录检测条件写得太严（例如要求某个 class 存在），
页面小改版就一直等。优先用「文案 + 多个选择器兜底」。

---

## 第 4 步：实现采集

### 4.1 配置驱动（首选）

```yaml
name: demo
start_url: https://example.com/list
# SSR 状态里已有列表时优先从这里取，取不到自动回退 DOM
state_path: props.pageProps.items
card_selector: div.card          # DOM 兜底
scroll: {times: 10, delay_min: 1.5, delay_max: 3.0, stop_if_no_new: 3}
fields:
  title: {selector: "h3", attr: text, state_path: "title"}
  url:   {selector: "a.cover", attr: href, state_path: "url"}
  likes: {selector: ".count", attr: text, transform: count, state_path: "likeCount"}
```

`state_path` 是可选的：

- 配了 → 页面命中 `__NEXT_DATA__` / `__NUXT__` / `__INITIAL_STATE__` 时，
  直接用 `pick_path` 按路径取值，不滚动、不解析 DOM；
- 没配 / 取不到 → 走原来的 DOM 路径（含自适应兜底）。

字段的 `state_path` 为空时，用字段名本身作为状态 key。

跑：

```bash
python examples/07_config_driven.py examples/platforms/demo.yaml
```

### 4.2 自定义采集器（复杂场景）

继承/组合 `BrowserSession` + `PageAdapter`，按阶段拆函数：

```
run()
├─ open_list()          # 导航 + 等就绪
├─ ensure_login()       # 登录/验证码
├─ collect_pages()      # 滚动/翻页循环（内部检查取消 + 存活）
├─ extract_list()       # 列表字段
└─ enrich_detail()      # 可选：详情页补充
```

### 4.3 停止条件（必须写）

- 连续 N 次滚动无新增（推荐 2-3 次）
- 达到 `max_items`
- 到达最后一页（下一页按钮 disabled / 页码不再变化）
- 全局超时（建议 10-30 分钟）
- 用户取消
- 浏览器被关闭

没有停止条件的采集器 = 定时炸弹。

---

## 第 5 步：限频

- 导航：`AntiCrawlGuard` 默认同域 1-4s，无需额外处理。
- 滚动：1.5-3.5s（列表页），详情页 3-6s。
- 接口：`AdaptiveRateLimiter`（base 4s，每 10 页 +0.5s，失败指数退避）。
- 下载：单文件串行，不要并发 10 个视频。

**经验值：**

| 场景 | 间隔 |
|---|---|
| 公开榜单/搜索 | 1-3s |
| 登录态列表 | 2-4s |
| 详情页 | 3-6s |
| 评论/弹幕分页 | 2-5s |
| 发布/写操作 | 10-30s，且串行 |

---

## 第 6 步：字段标准化

统一输出结构（建议）：

```json
{
  "platform": "xiaohongshu",
  "external_id": "note_id",
  "url": "https://...",
  "title": "...",
  "author": {"id": "...", "name": "..."},
  "stats": {"view": 123, "like": 45, "comment": 6, "collect": 7, "share": 2},
  "published_at": "2026-09-01T12:00:00+08:00",
  "collected_at": "2026-11-04T10:00:00+08:00",
  "raw": {}
}
```

规则：

- 计数用 `int | None`，未知用 `null` 不用 `0`。
- 时间统一 ISO8601 + 时区；相对时间（「2月前」）尽量进详情页换算。
- 保留 `raw` 原始字段，便于后续补字段，不用重新采集。

---

## 第 7 步：验收清单

- [ ] 采集条数 = 页面显示条数（±10%）
- [ ] 必填字段完整率 ≥ 95%
- [ ] 重复率 = 0（同一页跑两次）
- [ ] 连续跑 3 次不触发验证码
- [ ] 滚动能正常停止（不无限加载）
- [ ] 中途关窗，任务 10s 内退出且错误信息准确
- [ ] 断网/超时有重试，不卡死
- [ ] 选择器失效时有兜底或明确报错（不是静默返回 0 条）
- [ ] 日志能定位到「哪一步、哪个选择器、多少条」

---

## 第 8 步：上线监控

- 每次任务记录：开始/结束时间、条数、失败原因、是否命中验证码。
- 连续 3 次条数为 0 → 告警（大概率改版）。
- 字段完整率骤降 → 告警（选择器部分失效）。
- 验证码命中率上升 → 降频或换 IP。
- 保留最近 3 次页面 HTML 快照（脱敏），便于改版后对比。

---

## 第 9 步：平台能力表（维护在项目里）

| 平台 | 列表 | 搜索 | 详情 | 登录 | 验证码 | 备注 |
|---|---|---|---|---|---|---|
| demo | ✅ | ✅ | ⬜ | 否 | 无 | 配置驱动 |

新增平台时同步更新这张表，避免「以为支持其实没接」。
