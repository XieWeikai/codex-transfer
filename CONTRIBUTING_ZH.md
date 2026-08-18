# 参与贡献 Codex Relay

[English](CONTRIBUTING.md) | 简体中文

感谢你帮助改进 Codex Relay。本项目会修改敏感的本地状态，因此正确性、可恢复性和清晰的改动范围比改动规模更重要。

参与项目即表示你同意遵守[行为准则](CODE_OF_CONDUCT_ZH.md)。安全漏洞必须按照 [SECURITY_ZH.md](SECURITY_ZH.md) 私下报告，不要创建公开 Issue。

## 贡献方式

- 报告可复现的 Bug，但不要附带真实 Session 数据。
- 提议范围明确的功能或存储兼容性改进。
- 改进测试、文档、无障碍体验或性能。
- 研究 Codex 存储或 app-server 的变化，并记录准确的源码版本。
- 审查 Pull Request，验证恢复行为。

涉及行为变化或新存储格式时，请先创建 Issue，再投入大规模实现。请说明用户问题、受影响的 Codex 版本、预期安全属性以及考虑过的替代方案。

## 开发环境

要求：

- Python 3.11 或更高版本
- Node.js，仅用于 JavaScript 语法检查
- 手动测试 Fork 集成时需要 Codex CLI

```bash
git clone https://github.com/XieWeikai/codex-session-manager.git
cd codex-session-manager
python3 -m venv .venv
.venv/bin/pip install -e .
```

尽量使用一次性的 Codex home 运行应用：

```bash
.venv/bin/codex-relay \
  --codex-home /path/to/disposable/.codex \
  --data-dir /path/to/disposable/backups \
  --port 8765
```

不要把真实 rollout 或凭据用作测试 fixture。合成的 JSONL 和 SQLite fixture 应位于测试套件创建的临时目录中。

## 架构规则

修改写操作之前，请阅读 [docs/DESIGN.md](docs/DESIGN.md)。主要职责边界如下：

- `CodexRepository` 负责 Codex 文件、数据库发现、schema 检查和原子存储操作。
- `MigrationEngine` 负责预检、加锁、备份、执行、验证和回滚流程。
- `CodexAppServer` 负责官方 `thread/fork` 协议边界。
- `AuditStore` 负责 Manifest、备份代际、哈希和审计链。
- `server.py` 与 `static/` 把上述接口适配成本地 Web 应用。

不稳定的 Codex 存储细节应保留在 repository adapter 内。HTTP handler 和浏览器代码不得自行协调文件与 SQLite 修改。

每个新的写入路径都必须明确：

1. 前置条件与 writer lock 行为。
2. 完整的操作前备份集合。
3. 原子性边界与回滚行为。
4. 操作后验证。
5. 必须阻止恢复或撤销的准确条件。
6. 审计 Manifest 记录什么，以及如何避免暴露凭据。

## 测试与检查

提交 Pull Request 前运行：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
node --check src/codex_session_manager/static/app.js
git diff --check
```

为改变的行为添加聚焦测试。存储改动应根据实际情况覆盖成功、异常或无效输入、回滚、状态分叉和未知 schema 拒绝。

界面改动需要手动验证：

- Session 选择和拖拽均有键盘与点按替代方式。
- 明暗主题。
- 窄屏与宽屏布局。
- 写操作前的上下文风险确认。
- 大型工作区不会在 DOM 中重复完整 Session 内容。

## Pull Request 流程

1. 从 `main` 创建聚焦分支，例如 `feat/project-filter` 或 `fix/restore-divergence`。
2. 每个 Pull Request 只处理一个逻辑改动。
3. 使用 Conventional Commits 风格：`feat:`、`fix:`、`docs:`、`test:`、`perf:` 或 `refactor:`。
4. 说明用户影响、安全影响、存储假设和验证过程。
5. 存在对应 Issue 时添加关联。
6. 改变社区文档的共同含义时，同时更新英文和中文版本。

不要在行为改动中混入全局格式化或无关重构。维护者可能要求缩小 Pull Request 后再审查实现细节。

## AI 辅助贡献

项目允许使用 AI 辅助，但贡献者仍须对每一行负责。你必须理解改动、亲自完成验证、删除虚构的主张，并避免把私人提示词或 Session 数据放入生成内容。未经人工审阅的大型生成补丁可能会被关闭，因为它把验证成本转移给了维护者。

## 许可

提交贡献即表示你同意该贡献可以依据仓库的 [MIT License](LICENSE) 分发，并确认自己有权提交相关内容。
