# 掘金接口实测笔记 · 2026-11-09

> 本文件只记录接口契约与坑，不包含任何 cookie、token、账号 ID 或手机号/邮箱。
> 匿名化结果见 [`report.md`](report.md)。

## 1. 可用接口

| 用途 | 方法 | 路径 | 关键参数 |
|---|---|---|---|
| 首页推荐流 | POST | `/recommend_api/v1/article/recommend_all_feed` | `id_type=2`、`client_type=2608`、`sort_type=200`、`cursor`、`limit=20` |
| 用户资料 | GET | `/user_api/v1/user/get?user_id=<id>` | 返回 `follower_count`、`post_article_count`、`got_digg_count` 等 |
| 当前登录用户 | GET | `/user_api/v1/user/get`（不传 `user_id`） | 带 cookie 时返回当前登录账号；可用于校验 cookie 身份 |
| 作者文章列表 | POST | `/content_api/v1/article/query_list` | `user_id`、`cursor`、`sort_type=2`；返回 `has_more`/`cursor` |
| 文章详情 | POST | `/content_api/v1/article/detail` | `article_id` + **`client_type=2608`** |
| 评论列表 | POST | `/interact_api/v1/comment/list?aid=2608&uuid=<uuid>&spider=0` | `item_id`、`item_type=2`、`cursor`、**`limit=20`**、`sort=1`、`client_type=2608` |

## 2. 关键坑

### 2.1 评论参数是 `limit`，不是 `count`

用 `count=20` 时接口返回 `err_no=0, data=[], count=0`，看起来「文章没有评论」，
实际是参数被忽略。改成 `limit=20` 后正常返回 20 条。

这个坑最危险的地方是**静默**：不报错、不 4xx，只是空列表。

### 2.2 文章详情必须带 `client_type`

```json
{"article_id": "<article_id>", "client_type": 2608}
```

不带 `client_type` 返回 `err_no=2 参数错误`；GET 路由直接 404。

### 2.3 GET 用户接口必须按 JSON 解析

`AntiRiskHttpClient.get_text()` 不会解析 JSON（`data=None`），
用户资料必须用 `get_json()`，否则会误判「拿不到数据」。

### 2.3.1 cookie 身份校验

带 cookie 调 `GET /user_api/v1/user/get`（**不传 user_id**）会返回当前登录用户。
本次返回的账号与目标 URL 一致，身份校验通过。

这条可以用来做账号监控的前置检查：cookie 是否过期、是否串号。

### 2.4 签名（`a_bogus`）不是所有接口都强制

浏览器里文章详情、v1 评论请求都带了 `a_bogus` + `verifyFp/fp`。
实测 v1 评论在参数正确（`limit`）时可以纯 HTTP，不需要 `a_bogus`；
如果后续接口收紧，走浏览器 XHR 拦截（L3）或回放签名 URL。

**结论：拿到接口 ≠ 能纯 HTTP，必须做参数/签名剥离测试。**

### 2.5 首页 feed 的 cursor

`recommend_all_feed` 的 `cursor` 是分页游标（实测首屏返回 `1`）。
`limit=20` 一次拿 20 条；用 `limit=10` + 同一 cursor 重复请求会拿到重复数据，
需要按 `article_id` 去重。

### 2.6 评论数的口径

文章详情里的 `comment_count` 包含主评论 + 回复。
`comment/list` 返回的是主评论（`reply_infos` 里嵌套回复）。
本次选中文章声明 114 条，抓到 51 条主评论，`has_more=False`，两者口径一致。

## 3. 数据边界

- 非选中作者：作品列表只抓前 2 页（20 篇），完整数看 `post_article_count`。
- 选中作者：抓全部 5 篇（`has_more=False`），阅读 14,595 / 点赞 151 / 评论 117 / 收藏 76。
- 评论：选中文章声明 114 条，抓到 51 条主评论，`has_more=False`；回复嵌套在 `reply_infos`。
- 我的账号：0 粉丝、0 文章，本次作为监控基线。
- 已排除 `phone`、`email`、`wallet_total_bill`、`user_priv_info` 等私密字段。

## 4. 复现方式

```bash
# 不要提交 cookie；只在当前进程设置
export JUEJIN_COOKIE='...'
export JUEJIN_UA='Mozilla/5.0 ... Edg/157.0.0.0'
python your_collector.py
```

采集脚本应使用 `AntiRiskHttpClient`，并把 cookie 从环境变量读入，
不要写进仓库、日志或报告。
