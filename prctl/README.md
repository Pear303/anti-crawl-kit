# prctl：把 PR 规范变成可执行门禁

`prctl` 是本手册的可执行部分。它不负责写代码，也不负责调用模型；它负责回答一个问题：

> 这个 PR 的产物，是否真的满足了流程要求？

Agent 不能自己宣布"我完成了"。它必须产出 `evidence.json`、`review.json`、`security.json`、`risk.json`、`release.json`；`prctl` 逐项校验，任何一项不合格，`gate` 就非零退出。

## 它约束什么

| 规则 | prctl 怎么强制 |
|------|----------------|
| 没有运行证据不得审核 | `evidence` schema 要求命令、验收结果；UI 变更必须有截图，无 UI 变更必须写 `no_ui_reason` |
| 只审目标 / 兼容 / 影响 | `review` schema + `policy.yml` 的 `categories` |
| 每条意见必须有处置 | `disposition` 枚举；非 `fixed` 必须有 `disposition_note` |
| 审核轮次 ≤ 3 | 轮次必须连续；超过 `max_rounds` 直接拒绝 |
| 第 2 轮起只看增量 | `round > 1` 时 `context` 必须是 `delta` |
| 不重复提已提过的问题 | 跨轮 finding `id` 去重，重复直接拒绝 |
| 安全问题未清零不得合并 | 有 open 的 critical / high / medium finding 就失败 |
| 高风险 2 人审批 | `risk.json` 给出 required approvals；`gate --approvals` 不足就失败 |
| 上线必须有开关 / 回滚 | 中高风险必须有 `release.json`；service/cli 要 `flag`，library 要 `version`，且都包含 rollout、rollback、monitoring |

## 安装

```bash
# 在目标仓库根目录，把本目录的 prctl/ 复制进去，然后：
python -m pip install -e ./prctl
```

要求 Python 3.10+，依赖 `jsonschema` 和 `PyYAML`。

## 快速开始

```bash
# 1. 初始化 .prctl/policy.yml、.prctl/schemas/、.prctl/artifacts/
prctl init

# 2. 根据改动文件判定风险（高风险 = 2 人审批）
prctl risk --pr 123 --files billing/calculator.py README.md

# 3. 校验证据包
prctl verify --pr 123 --evidence evidence.json

# 4. 校验 AI 审核报告（第 1 轮）
prctl review --pr 123 --report review-1.json

# 5. 校验安全报告
prctl security --pr 123 --report security.json

# 6. 记录审批人（CI 里可以直接用 --approvals 覆盖）
prctl approve --pr 123 --by alice
prctl approve --pr 123 --by bob

# 7. 校验发布计划
prctl release --pr 123 --plan release.json

# 8. 总门禁：所有产物齐全才返回 0
prctl gate --pr 123

# 9. 查看当前状态
prctl status --pr 123
```

`prctl gate` 的输出：

```text
CHECK        STATUS   DETAIL
evidence     PASS     evidence pack is complete
review       PASS     round 1, 1 finding(s)
security     PASS     no blocking security findings
risk         PASS     high risk, 2/2 approval(s)
release      PASS     rollout and rollback plan are present

OVERALL: PASS  STATE: RELEASED
```

## 产物目录

```
.prctl/
├── policy.yml                  # 唯一事实来源
├── schemas/                    # JSON Schema
└── artifacts/
    └── 123/
        ├── evidence.json
        ├── review-1.json
        ├── review-2.json       # 第 2 轮起 context=delta
        ├── security.json
        ├── risk.json
        ├── approvals.json
        └── release.json
```

`prctl` 是**无状态**的：它不维护一个隐藏的状态文件，而是从产物本身计算状态。产物在，状态就在；产物缺失，状态就退回。这样 CI 和本地看到的是同一套事实。

## 状态机

```text
DRAFT
  └─ evidence 通过 ─────────────► VERIFIED
       └─ review 通过 ──────────► REVIEWED
            └─ security 通过 ───► SECURITY_CLEAR
                 └─ 审批足够 ───► RISK_APPROVED
                      └─ 发布计划 ─► RELEASED

任何一步失败 ────────────────────► BLOCKED
```

`prctl gate` 通过，状态才会前进；任何一项失败，状态就是 `BLOCKED`。

## policy.yml

所有规则集中在一个文件里，不再散落在提示词中：

```yaml
review:
  max_rounds: 3
  categories: [goal, compatibility, impact]
  blocking_severities: [P0]

evidence:
  require_screenshots: true
  require_rollback: true

risk:
  approvals: {low: 0, medium: 1, high: 2}
  rules:
    high: ["billing/**", "auth/**", "migrations/**"]
    medium: ["src/**"]
    low: ["docs/**", "*.md"]

release:
  required_for_levels: [medium, high]
  require_flag: true
  require_rollback: true
```

修改 `policy.yml` 即可调整门禁，不需要改代码。`prctl init` 会复制一份默认策略；按项目改路径和审批数即可。

### release_type

`release.json` 支持三种发布类型：

- `service` / `cli`：必须有 `flag`，rollout 按百分比灰度。
- `library`：没有运行时开关；必须有 `version`，rollout 按包仓库阶段（如 testpypi → pypi），回滚是 yank + revert。

三种类型都必须有 `rollout`、`rollback`、`monitoring`。示例见 `examples/release.example.json` 和 `examples/release.library.example.json`。

## JSON Schema

五个 schema 在 `.prctl/schemas/`：

- `evidence.schema.json`：证据包。
- `review.schema.json`：审核报告。
- `security.schema.json`：安全报告。
- `risk.schema.json`：风险分级。
- `release.schema.json`：发布计划。

Agent 的输出必须是合法 JSON；缺字段、枚举值不对、类型不对，都会被 `prctl` 拒绝。这比"在 PR 里写一段话"强，因为它不依赖 Agent 的自觉。

## GitHub Actions

`templates/github/workflows/` 下有三个工作流：

| 工作流 | 作用 |
|--------|------|
| `prctl-gate.yml` | 在 PR 上运行 `prctl gate`；在 merge group 上直接通过（PR 级已 gate） |
| `prctl-risk.yml` | 运行 `prctl risk`，自动打 `risk:low/medium/high` 标签 |
| `prctl-release.yml` | 部署前再跑一次 gate，作为发布前最后一道门禁 |

分支保护的 required checks 建议：

- PR：`ci-fast`、`prctl-gate / gate`（GitHub UI 里显示为 workflow / job）
- 合并队列：`ci-fast`、`merge-queue-full`、`prctl-gate / gate`

注意：`prctl-gate.yml` 同时监听 `merge_group`，在合并队列里走"已 gate"分支直接通过，避免合并队列等待一个只在 PR 上运行的工作流。它还会在 CI 里从 PR diff 重新计算风险，不信任提交的 `risk.json`；即使 Agent 把高风险改动标成低风险，也会被 CI 纠正。

## 和 Agent 怎么配合

`prctl` 不调用模型。Agent 的产物由 `04-Agent提示词模板.md` 里的角色生成：

1. **Builder Agent** 写代码、跑测试、产出 `evidence.json`。
2. **Verifier Agent**（全新上下文）验证验收标准，补全证据。
3. **Reviewer Agent**（全新上下文）产出 `review-1.json`；修复后第 2 轮产出 `review-2.json`（`context=delta`）。
4. **Security Agent**（全新上下文）产出 `security.json`。
5. 人工审批在 GitHub 上完成；CI 用 `gh pr view` 统计 approvals，传给 `prctl gate --approvals`。
6. `prctl gate` 作为 required check，不通过就合不进去。

## 设计原则

- **产物优先于自述**：Agent 说"我测过了"不算，`evidence.json` 里有命令、输出、截图才算。
- **规则优先于提示词**：轮次、去重、处置、审批数，能写成代码就不写在提示词里。
- **无状态优先于隐藏状态**：状态从产物计算，CI 和本地看到同一套事实。
- **渐进增强**：先跑通 evidence + review + gate；安全、发布、评测可以后加。

## 局限

- 它不运行 Agent，也不验证截图的真实内容；它只验证产物结构和关键约束。截图的真实性最终由人和复核流程保证。
- 它不直接查 GitHub approvals；CI 用 `gh` 查询后通过 `--approvals` 传入。
- 它不替代 branch protection、CODEOWNERS、Merge Queue；这些仍然要在 GitHub 上配置。
- 它是骨架，不是平台。先跑起来，再按你的项目扩展 schema 和命令。

## 测试

```bash
cd prctl
python -m pytest -q
```

当前覆盖：风险分级、证据校验（含 `ui_change` / `no_ui_reason`）、审核轮次 / 增量 / 去重 / 处置 / 同轮幂等、安全清零、发布计划（service / library / cli）、gate happy path 与失败路径、CLI 端到端。共 33 个测试。

## 扩展方向

- `prctl review --from-agent`：直接调用 Reviewer Agent，产出并校验报告。
- 截图文件存在性检查：schema 只校验路径，进一步可以校验文件是否存在、是否为空。
- GitHub API 集成：`prctl gate --from-github` 自动读取 approvals 和 PR 元信息。
- SARIF 输出：把 security findings 转成 GitHub Code Scanning 格式。
- 评测集：把历史事故和漏检 PR 作为 `prctl eval` 的输入，量化审核效果。
