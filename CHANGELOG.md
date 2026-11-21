# 变更记录

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.13.3] - 2026-11-21

### 新增

- Windows CI（`windows-offline`）：路径分隔符、GBK 控制台、文件 mtime 精度
  这些平台差异现在会被真正跑到。
- 类型检查 job：mypy 先只报告不阻塞（当前基线约 140 个错误 / 22 个文件）。

### 变更

- 无头 UA 实测结论：Chrome 158 + patchright 1.62.2 / 1.64.4 下，
  `--headless=new` 与旧无头模式的 UA 完全相同，都带 `HeadlessChrome`。
  `docs/02` 改为「有头 / xvfb / 接受特征」三条路，不再建议靠启动参数规避；
  `ack-detect` 对这条未通过项会直接提示「属预期」。
- `dev` extras 增加 `mypy>=1.11`。

## [未发布]

### 计划

- 覆盖率基线统计；ruff 规则逐步扩到常规集合。

## [0.13.2] - 2026-11-21

### 新增

- `ack-dom` / `ack-detect` 新增 `--skip-version-check`：系统 Chrome 自动更新跑在
  patchright 前面时，探测工具不再只能等降级；默认仍是严格检查。

### 文档

- `docs/11` 补充「探测工具默认严格、配方和 UI 默认跳过」的说明。

## [0.13.1] - 2026-11-21

### 修复

- `ack probe|dom|detect --help` 崩溃：三个旧工具的 `main()` 不接受 argv，
  而 `ack` 统一入口会转发参数。现在统一改为 `main(argv=None)`。

### 测试

- 新增 15 个子命令 `--help` 的参数化测试。
- 新增 GBK 控制台回归测试，确保 fixture 里的 `¥` 不会让进程崩。

## [0.13.0] - 2026-11-21

### 新增

- `ack` 统一入口：`ack doctor` / `ack init recipe` / `ack recipe ...`，
  旧的 `ack-xxx` 全部保留。
- `ack doctor`：Python、可选依赖、Chrome/Edge、patchright 版本差、端口、
  `runs` 目录、临时目录、代理变量自检；每个问题带可直接粘贴的修复命令。
  只有阻塞问题返回 1，缺可选依赖只是 WARN。
- `ack init recipe`：生成可直接运行的 logic / http / browser 三种配方骨架，
  生成后立即解析验证；`ack init health` 等价于 `ack-health --init`。
- 离线 fixture：`examples/fixtures/` 三个静态页 + `examples/16_local_fixture.py`，
  演示页面形态判断、DOM 提取、分页、空壳页与验证墙识别，不联网、不启浏览器。
- `README_EN.md` 与 `docs/assets/demo.gif`（`ack-ui` 真实录屏）。
- `docs/32-三分钟上手.md`。

## [0.12.0] - 2026-11-20

### 新增

- `--baseline auto`：在同目录自动找上一次同类型报告，跳过 `latest.*`，
  只选更旧的文件；`--dir` 模式逐份对比并汇总退化。
- `ack-recipe` 命令行入口，与 `ack-ui` 共用 `runs.py` 的落盘规则。
- `ack-ui` 历史对比页：`GET /api/runs/history`，复用 `report_diff`。
- 每日监控模板 `examples/ci/daily-recipe.yml` 与 `docs/31`。

### 修复

- `ack-report --dir` 不再把 `latest.json` 当成一份独立报告；
  `ack-ui` 的份数也按 JSON 统计且不计 `latest`。

## [0.11.0] - 2026-11-18

### 新增

- 运行结果落盘：`runs/<recipe>/<timestamp>.json|html` + `latest.*`。
- `ack-report --baseline <JSON>` 对比两次报告，退化退出码 1。

### 变更

- PyPI 发布改为 `PUBLISH_PYPI` 开关，默认跳过。
