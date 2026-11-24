# 贡献指南

这个仓库用「AI 跑流程、人做决策」的方式维护：写代码的 Agent 不审自己的代码，
合并前必须有运行证据，`prctl gate` 通过才放行。

## 本地检查（提 PR 前必跑）

```bash
python -m ruff check .
cd prctl && python -m ruff check . && cd ..
python -m pytest -q
python -m pytest -q --cov --cov-fail-under=68     # 覆盖率门槛
```

当前基线：`667 passed / 2 skipped`，本地总覆盖率 72%（CI 门槛 68%，为 Linux
缺少 Windows 专有分支留余量）。

`mypy` 目前只报告不阻塞（143 个历史错误），新增代码不要继续增加。

## 证据包

证据不是 PR 里的一段自述，而是结构化 JSON：

```bash
pip install -e ./prctl      # 只需一次
prctl init                  # 生成 .prctl/policy.yml 和 schemas
prctl verify --pr <编号> --evidence evidence.json
```

`evidence.json` 至少包含：

- `commands`：实际跑过的命令与关键输出（含数字）；
- `acceptance_criteria`：每条验收标准的 PASS / FAIL / BLOCKED；
- `run_evidence`：启动命令 + 日志；没有 UI 的改动要写 `no_ui_reason`；
- `impact`、`security`、`rollback`。

模板见 `prctl/examples/evidence.example.json`。

## 审核

| 角色 | 上下文 | 产出 |
|---|---|---|
| Verifier | 全新 | 实际运行结果，补全证据 |
| Reviewer | 全新，不看编写过程 | `review-1.json`（目标 / 兼容 / 影响） |
| Security | 全新 | `security.json`（密钥 / 注入 / 越权 / 依赖） |

规则：

- 最多 3 轮；第 2 轮起 `context` 必须是 `delta`，只审增量；
- 同类问题一次说全，不重复提已提过且未变更的问题；
- 每条意见必须有处置：`fixed` / `explained` / `deferred` / `wontfix`；
- P0 必须 `fixed`；安全问题未清零不得合并。

## 风险分级与审批

`.prctl/policy.yml` 是唯一事实来源：

| 路径 | 等级 | 审批 |
|---|---|---|
| `.github/workflows/**`、`prctl/**`、`scripts/**` | high | 2 |
| `src/**`、`examples/**`、`pyproject.toml`、`requirements.txt` | medium | 1 |
| `docs/**`、`tests/**`、`*.md` | low | 0 |

个人项目里「2 次审批」= 2 次全新上下文的独立复核，不是两个不同的人。

```bash
prctl approve --pr <编号> --by <复核者>
prctl release --pr <编号> --plan release.json
prctl gate --pr <编号>
```

## 发布

1. 改 `src/anti_crawl_kit/__init__.py` 的 `__version__` 与 `CHANGELOG.md`；
2. 打 tag（`git tag -a vX.Y.Z`）并推送，release 工作流会构建 wheel + sdist；
3. 回滚：`pip install anti-crawl-kit==<上一版本>`；必要时删除远端 tag。

## 红线

- 不把真实 Cookie / Token / 手机号写进代码、测试、报告或日志；
- 不内置任何平台的签名算法，只定义接口约定；
- 选择器必须来自 `dom_probe` 实测，不允许手写；
- 验证墙是停止信号：截图 + 通知 + 硬停，不重试、不破解。
