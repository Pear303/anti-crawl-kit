## 目标

<!-- 一句话：这个 PR 解决什么问题 -->

## 验收标准

- [ ] ...
- [ ] ...

## 风险等级

- [ ] 低（文档 / 测试 / 注释）
- [ ] 中（普通功能 / 依赖 / 工具脚本）
- [ ] 高（发版流程 / CI / 选择器契约 / 安全边界）

## 改动范围

- 主要文件：
- 是否触碰高风险路径：是 / 否
- 是否有行为变更：是 / 否

## 证据

> 证据包结构见 `.prctl/artifacts/<pr>/evidence.json`，用 `prctl verify` 校验。

- [ ] `ruff check .` 通过（含 examples / scripts）
- [ ] `mypy src/anti_crawl_kit` 通过（0 错误）
- [ ] `pytest -q` 通过（数量：____ passed / ____ skipped）
- [ ] 覆盖率不低于 70%（`pytest --cov --cov-fail-under=70`）
- [ ] 涉及 CLI 的改动已实际跑过命令，输出见评论
- [ ] 涉及浏览器路径的改动已标注 `not_tested` 或给出实测记录
- [ ] 验收标准逐条验证
- [ ] 安全自查完成（无 Cookie/Token 落盘、无新增外部请求）
- [ ] 回滚方案已写

## AI 审核

- Reviewer 结论：
- Security 结论：
- 未解决项：

## 回滚方案

- 库：`pip install anti-crawl-kit==<上一版本>`；tag 撤回用 `git push --delete origin vX.Y.Z`
- CLI：同上，用户侧无需迁移

## 人工审批（中 / 高风险）

- 审批人：
- 审批结论：
