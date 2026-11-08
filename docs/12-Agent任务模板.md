# 12 · Agent 任务模板

> 拿到任务先对号入座，按模板走，不要从零想。
> 每个模板都包含：输入 → 决策 → 步骤 → 产出 → 验收。

---

## 模板 A：接一个新平台

### 输入

- 目标 URL（列表页 / 详情页 / 后台）
- 是否需要登录
- 目标字段（标题、作者、点赞、粉丝数……）
- 期望规模（20 条验证 / 1000 条批量）

### 最小路径（可选）

```bash
python tools/agent_probe.py --url "URL" --field 目标字段 --quick
```

输出 3~5 行：`decision` / `summary` / `top` / `next`。

如果 agent 手写更快，可以跳过工具，但必须遵守一条：

> **HTTP 请求用 `AntiRiskHttpClient`，不要裸 `requests`。**

### 决策（先做这一步，不要直接写代码）

```text
1. requests 抓 HTML，目标字段在里面吗？
   ├─ 在 → 解析 HTML（L2/L4），不要开浏览器
   └─ 不在 ↓

2. 页面有几个 <script src>？有外链 JS 吗？
   ├─ 有 → tools/js_api_hunt.py 找接口（L0）
   │        ├─ 找到接口 + 不需要签名 → requests 直连（L1）
   │        ├─ 找到接口 + 需要签名   → 浏览器拦截（L3）
   │        └─ 找不到               → 下一步
   └─ 没有 ↓

3. 需要登录吗？
   ├─ 是 → 持久化 profile + 人工登录一次 + 双时钟等待
   └─ 否 ↓

4. 页面有 __INITIAL_STATE__ / __NEXT_DATA__ / RENDER_DATA？
   ├─ 有 → 读全局状态（L2）
   └─ 没有 → XHR 拦截（L3）或 DOM（L4）
```

### 步骤

```bash
# 1) 抓 HTML，确认数据在不在（用库，不要裸 requests）
python -c "
from anti_crawl_kit.http import AntiRiskHttpClient
r = AntiRiskHttpClient('demo').get_text('URL')
print(r.status_code, r.text[:2000])
"

# 2) HTML 空壳 → 查 JS 包
python tools/js_api_hunt.py \
    --url "URL" \
    --cookie "关键cookie=xxx" \
    --field 目标字段

# 3) 有接口 → 直接验证
python -c "
from anti_crawl_kit.http import AntiRiskHttpClient
c = AntiRiskHttpClient('demo')
r = c.get_json('接口URL', params={...}, headers={'Referer': '页面URL'})
print(r.status_code, r.data)
"

# 4) 需要登录 → 打开有头浏览器
python tools/dom_probe.py --url "URL" --data-dir ./profiles/demo

# 5) 写采集器（复制骨架，不要从零写）
cp examples/07_config_driven.py examples/my_platform.py
```

### 产出

```text
[ ] 一份可运行脚本
[ ] 一份 output/*.json（至少 20 条）
[ ] 一条平台速查记录（入口/数据源/登录/验证码/坑）
[ ] 一份验收结果（对照 11-工程红线 第 5 节）
```

### 验收

- 条数 = 页面显示条数（±10%）
- 必填字段完整率 ≥ 95%
- 连续跑 3 次不触发验证码
- 关窗 10s 内退出

### 常见坑

| 症状 | 原因 | 处理 |
|---|---|---|
| HTML 里没数字 | 前端 JS 拉的 | `js_api_hunt.py` |
| 接口 404 | 域名不对（`www` vs `api`） | `--api-host` 重拼 |
| 接口 403 | 缺 Referer / cookie | 补 Referer，带登录 cookie |
| 接口 200 但空 | 字段改名 / 要登录 | 看上下文，确认 cookie |
| DOM 0 条 | 选择器错 / 内容在 iframe | `dom_probe.py`，查 iframe |

### 反例（真实会话，2026-11）

某次 4 个任务的会话（biquge / 抖音 / 简书 / 百度）：

```text
工具调用 108 次：shell 61、write 30、read 8、edit 7、grep 2
探测工具执行：0 次（agent_probe / js_api_hunt / dom_probe 都没跑）
一次性 probe 脚本：20+（probe_baidu1~11 等）
cache read：1030 万 tokens
```

结果是对的，但：

- 全部用裸 `requests`，强风控站会 403；
- 没有基线，下次改版要重新试探；
- 临时脚本散落在 `C:\Windows\Temp\opencode`，没有清理。

**教训：探索阶段手写没问题，但 HTTP 要用库、结果要固化、临时文件要清理。**

---

## 模板 B：采集器「成功但 0 条」

### 输入

- 任务状态：`success`
- 实际条数：0
- 最近一次正常时间

### 决策

```text
0 条但成功 = 异常被吞了。按顺序查：

1. 页面还正常吗？（手动打开）
   ├─ 正常 → 改版/选择器失效
   └─ 弹验证 → 风控，先停

2. 接口还通吗？（抓包 / curl）
   ├─ 通但字段空 → 字段改名 / 要登录
   └─ 403/429 → 风控

3. 选择器还命中吗？（dom_probe）
   ├─ 0 命中 → 改版
   └─ 命中但文本空 → 懒加载 / 骨架屏

4. 日志里有没有 ChallengeDetected / BrowserClosedError？
   ├─ 有 → 被 except Exception 吞了，修异常捕获
   └─ 没有 → 提取函数返回空，修解析
```

### 步骤

```bash
# 1) 看最近日志
#    搜索关键词：'0 条'、'空'、'提取失败'、'ChallengeDetected'

# 2) 重跑单页，开 debug
python examples/02_generic_scrape.py --url "URL" --max-items 5

# 3) DOM 探测
python tools/dom_probe.py --url "URL"

# 4) 接口探测
python tools/js_api_hunt.py --url "URL" --filter 关键词

# 5) 对比历史快照（如果有）
diff output/snapshot_before.html output/snapshot_now.html
```

### 产出

```text
[ ] 根因一句话（改版 / 风控 / 异常被吞 / 字段改名）
[ ] 修复的最小改动（只改一层）
[ ] 回归结果（单页 + 连续 3 次）
[ ] 一条踩坑记录
```

### 禁止

- 不要「重写整个采集器」；
- 不要同时改选择器和接口；
- 不要用 `except Exception: pass` 把问题盖住。

---

## 模板 C：验证墙频繁

### 输入

- 验证码命中率（次/天）
- 触发场景（登录 / 列表 / 详情 / 发布）
- 账号数量、IP 情况

### 决策

```text
1. 是首次登录还是批量阶段？
   ├─ 登录 → 双时钟等人，正常
   └─ 批量 → 硬停，不重试

2. 同一账号一天几次？
   ├─ ≤ 2 次 → 偶发，降速即可
   └─ > 2 次 → 账号/IP 已被标记，换出口或停

3. 多账号是否共用 IP？
   ├─ 是 → 这是主因，先做出口隔离
   └─ 否 → 查行为节奏

4. 是否固定间隔/固定滚动？
   ├─ 是 → 加随机抖动
   └─ 否 → 查是否被降级到无头/内置浏览器
```

### 步骤

```python
# 1) 接入硬停闸门
from anti_crawl_kit import ChallengeGate, Notifier

gate = ChallengeGate(
    platform="demo",
    notifier=Notifier(webhook="企业微信/飞书 webhook"),
    snapshot_dir="output/challenge",
)
gate.check_or_stop(page)
```

```bash
# 2) 配置通知
set AC_KIT_NOTIFY_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
set AC_KIT_NOTIFY_DESKTOP=1
```

```text
3) 调度器层
   ├─ 退出码 2 → 告警，不自动重跑
   ├─ 冷却 30~60 分钟
   └─ 连续两次 → 换时间段 / 换 IP
```

### 产出

```text
[ ] 每次命中都有截图 + HTML + meta
[ ] 通知能到人（控制台/桌面/webhook 至少一条）
[ ] 调度器不会自动重跑
[ ] 命中率有记录，能看趋势
```

---

## 模板 D：发布 / 填稿

### 输入

- 平台
- 内容（标题/正文/图片/视频）
- 账号
- 是否需要人工确认

### 决策

```text
1. 平台有官方 API 吗？
   ├─ 有 → 优先 API（B站 biliup / 公众号草稿箱）
   └─ 没有 → 浏览器填稿

2. 浏览器填稿，停在「发布」前还是自动发布？
   ├─ 人工确认 → 填完停住，等用户点发布
   └─ 自动发布 → 需要更强的幂等和防重复

3. 登录态有效吗？
   ├─ 有效 → 继续
   └─ 失效 → 人工介入，不要静默失败
```

### 步骤

```text
1. 打开创作者中心（有头，放行图片）
2. 检测登录 → 失效则等人工
3. 填标题 / 正文 / 上传素材
4. 检测验证码 → 有则等人工
5. 停在发布前（或点发布）
6. 校验结果：URL / 作品 ID / 状态
```

### 关键约束

| 约束 | 说明 |
|---|---|
| **幂等** | 同一内容重复提交不能发两次；用「平台 + 内容哈希」做键 |
| **人工介入协议** | 返回结构化结果，不要抛裸异常：`{success: False, requires_manual_intervention: True, stage: "captcha"}` |
| **等待期间检测存活** | 用户关窗立即中止，不要空等 5 分钟 |
| **填稿拟人** | 逐字键入，30~100ms 随机；不要 `fill()` 直接设值 |
| **发布前校验** | 标题非空、正文非空、图片上传完成、没有「草稿」标记 |
| **结果校验** | 发布后读作品列表/URL，确认真的发出去了 |

### 产出

```text
[ ] 发布结果（成功/失败/待人工）
[ ] 作品 URL 或 ID
[ ] 失败时的截图 + 当前 URL + 阶段
[ ] 重复提交测试通过
```

---

## 模板 E：批量采集 + 断点续跑

### 输入

- 目标规模（1000 条 / 100 个关键词 / 50 个博主）
- 是否可中断
- 去重键

### 步骤

```text
1. 先跑 20 条，验证字段和节奏
2. 设计断点：
   ├─ 游标（页码/时间戳/ID）
   ├─ 已采集 ID 集合（去重）
   └─ 失败队列（重试）
3. 限频：
   ├─ 公开接口 1~3s
   ├─ 登录态 2~4s
   ├─ 详情页 3~6s
   └─ 发布 10~30s，串行
4. 每 N 条落盘一次（不要全部攒内存）
5. 退出码：
   ├─ 0 正常
   ├─ 2 验证墙（不重跑）
   ├─ 3 登录超时
   └─ 130 用户取消
```

### 断点文件

```json
{
  "task_id": "xhs_search_20261104",
  "platform": "xiaohongshu",
  "cursor": {"page": 8, "last_id": "abc123"},
  "seen_ids": ["abc123", "def456"],
  "failed": [{"id": "ghi789", "reason": "timeout", "retries": 2}],
  "updated_at": "2026-11-04T15:30:00"
}
```

### 续跑规则

- 用 `seen_ids` 去重，不要从头再采；
- `failed` 单独重试，不要影响主流程；
- 续跑前先确认账号正常（手动打开目标页，不弹验证）；
- 验证墙退出后，至少冷却 30 分钟再续。

### 产出

```text
[ ] 断点文件（可恢复）
[ ] 去重键（platform:id 优先）
[ ] 限频配置
[ ] 退出码语义
[ ] 一次「中断 → 续跑」实测
```

---

## 模板 F：写一份探测报告

### 什么时候要

- 接新平台前，先给结论再写代码；
- 采集器失效，需要判断是改版还是风控；
- 给非技术同事解释「为什么这个平台难采」。

### 报告结构

```markdown
# {平台} 采集可行性报告

## 结论
（一句话：能采 / 需要登录 / 需要浏览器 / 不建议）

## 数据源
| 层级 | 是否可用 | 证据 |
|---|---|---|
| L0 JS 包 | ✅/❌ | 找到的接口 |
| L1 HTTP  | ✅/❌ | 状态码 + 返回样例 |
| L2 SSR   | ✅/❌ | 全局变量名 |
| L3 XHR   | ✅/❌ | 拦截到的接口 |
| L4 DOM   | ✅/❌ | 选择器 + 条数 |

## 登录与风控
- 是否需要登录：
- 验证码类型：
- 触发频率：
- 账号要求：

## 推荐方案
（选哪条路，为什么；预计成本）

## 风险
（改版风险、风控风险、法律/合规风险）

## 验证记录
（命令 + 输出摘要 + 时间）
```

### 产出

```text
[ ] 一页纸结论
[ ] 至少一条「实测证据」（状态码/条数/截图）
[ ] 明确的推荐方案
[ ] 风险提示
```

---

## 模板选择速查

| 你的任务 | 用哪个模板 |
|---|---|
| 第一次接这个平台 | A |
| 之前能跑，现在 0 条 | B |
| 老是弹验证码 | C |
| 要自动发内容 | D |
| 要采几千条 | E |
| 要先评估能不能采 | F |
| 要修某个具体报错 | 先看 `05-排障手册` |

---

**最后更新**：2026-11
**维护规则**：每做完一类任务，把「实际踩到的坑」补回对应模板的「常见坑」。
