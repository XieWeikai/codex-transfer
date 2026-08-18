<div align="center">

# Codex Relay

**在不同 Provider 之间移动或 Fork 本地 Codex Session，同时保留完整追溯能力。**

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-2f855a.svg)](LICENSE)
[![Local only](https://img.shields.io/badge/network-loopback%20only-4c956c)](#安全模型)
[![Runtime](https://img.shields.io/badge/runtime-stdlib%20only-2f855a)](#开发)

[English](README.md) | 简体中文 · [文档](docs/README.md) · [安全策略](SECURITY_ZH.md) · [贡献指南](CONTRIBUTING_ZH.md)

</div>

Codex Relay 是一个本地 Web 工作台，用于查看 Codex Session，并安全地改变其 Provider 归属。每次写操作都会先执行预检和完整备份，写入可检测篡改的审计链，并在恢复前通过操作后哈希检查状态是否已经分叉。

> [!CAUTION]
> Provider 标识不是凭据，Session 历史也不具备普遍可移植性。加密推理、模型专属状态、工具以及混合 Provider 来源都可能无法随迁移保留。重要 Session 应优先使用 **Fork**。

## 为什么需要 Codex Relay？

移动 Codex Session 并不是简单地重命名文件。当前 Codex 会同时使用 rollout JSONL 元数据与 SQLite thread 索引来发现 Session。只修改其中一处，可能导致状态不一致或 Session 消失。Codex Relay 将其设计为可恢复的操作流程，而不是一次文本替换。

| 能力 | 说明 |
|---|---|
| Provider 工作区 | 按 Provider、Project、状态、关键词和更新时间浏览紧凑的 Session 卡片。 |
| 安全 Fork | 调用 Codex 官方 app-server `thread/fork` 接口，在目标 Provider 下创建持久化的新 thread。 |
| 可审计移动 | 仅在预检和明确确认后更新原 rollout 与 SQLite 索引。 |
| 上下文风险确认 | 在操作发生时展示凭据、加密内容、写入锁、来源追溯和恢复风险。 |
| 备份代际 | 每次写操作都保存 rollout、SQLite 一致性快照、Manifest 和 SHA-256。 |
| 分叉感知恢复 | 后续聊天可能被覆盖时，拒绝自动恢复或删除 Fork。 |
| 大工作区性能 | 首屏只加载有界摘要，长标题按需读取，选择时不重建整个网格。 |

## 工作原理

```text
Codex home
  ├─ rollout JSONL ─┐
  └─ state SQLite ──┴─> 预检 ─> 快照 ─> Fork / 移动 ─> 验证
                                   │                    │
                                   └── 审计 Manifest <──┘
```

- **Fork** 保留来源 Session，并把新 thread 的创建交给 Codex app-server。
- **移动** 同时修改 rollout 中的 `session_meta.payload.model_provider` 和 SQLite 中的 `threads.model_provider`。
- **恢复** 本身也是一次新的审计操作。只有当前哈希仍与记录的操作后状态一致时才会执行。

Codex Relay 不会复制或保存 API Key、OAuth 状态、Provider 定义、Base URL、模型别名或其他凭据。

## 快速开始

### 环境要求

- Python 3.11 或更高版本
- 执行 Fork 时，`PATH` 中需要有 Codex CLI
- 本地 Codex home 使用当前支持的 `state_5.sqlite` thread schema

### 安装

```bash
git clone https://github.com/XieWeikai/codex-session-manager.git
cd codex-session-manager
./install.sh
codex-relay
```

安装器会在 `~/.local/share/codex-relay` 下创建隔离环境，并在 `~/.local/bin` 中提供 `codex-relay` 和 `csm`。遇到同名普通文件时会拒绝覆盖。

启动后打开 [http://127.0.0.1:8765](http://127.0.0.1:8765)。

### 从源码运行

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/codex-relay
```

指定自定义位置与端口：

```bash
codex-relay \
  --codex-home /path/to/.codex \
  --data-dir /path/to/private/backups \
  --codex-bin /path/to/codex \
  --port 8765
```

## 推荐操作流程

1. 停止准备操作的 Codex task。为了获得最清晰的快照边界，建议完全退出 Codex。
2. 从同一个来源 Provider 中选择 Session；大型工作区可使用 Project 和状态筛选。
3. 默认选择 **Fork**；只有必须改变原 thread 分桶时才使用**移动**。
4. 阅读全部预检结果，并按要求输入 `FORK` 或 `MIGRATE`。
5. 在目标 Provider 下恢复 Session，验证凭据、模型映射、工具和对话连续性。
6. 在完成验证之前保留备份。

操作重要 Session 前，请阅读[操作手册](docs/OPERATIONS.md)和[安全与恢复](docs/SAFETY.md)。

## 安全模型

Codex Relay 是一个同用户、本地运行的管理工具：

- HTTP 服务只接受 `127.0.0.1` 或 `localhost`。
- 写请求必须携带嵌入本地页面的每进程随机令牌。
- rollout 路径必须解析到指定 Codex home 内部。
- 备份只保存在本地，并在平台支持时使用仅当前用户可访问的权限。
- 工作区清单不会向浏览器返回 Session 消息正文。

备份属于敏感数据，其中可能包含提示词、源代码、命令、路径和工具输出。审计链可以发现修改，但它不是数字签名，也无法防御已经控制当前用户账户的攻击者。

请根据 [SECURITY_ZH.md](SECURITY_ZH.md) 私下报告漏洞，不要在公开 Issue 中提交密钥或 Session 内容。

## 已知限制

- Codex 本地存储不是稳定的公开 API；程序会拒绝修改未知 schema。
- Provider 迁移改变的是路由与发现分桶，不会转换后端协议。
- 不透明的 `encrypted_content` 在其他后端可能无法继续使用。
- 历史 rollout 没有可靠记录每一轮对应的 Provider，无法自动重建混合 Provider 来源。
- JSONL 与多个 SQLite 数据库之间不存在统一事务。备份和回滚可以降低风险，但无法消除突然断电的影响。
- 操作后状态一旦分叉，恢复会被主动阻止，以免覆盖新的聊天记录。

## 文档

| 文档 | 内容 |
|---|---|
| [文档索引](docs/README.md) | 仓库文档与应用内文档入口。 |
| [设计](docs/DESIGN.md) | 架构、存储边界、不变量和源码研究结果。 |
| [安全与恢复](docs/SAFETY.md) | 凭据、加密推理、来源追溯、状态分叉和备份隐私。 |
| [操作手册](docs/OPERATIONS.md) | 安装、Fork、移动、恢复和自定义路径。 |

工作台中的**文档**按钮也可以直接查看核心概念。

## 开发

运行时没有第三方 Python 依赖。完整测试命令：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
node --check src/codex_session_manager/static/app.js
```

欢迎提交能保持备份、审计和恢复不变量的贡献。开始之前请阅读[贡献指南](CONTRIBUTING_ZH.md)和[行为准则](CODE_OF_CONDUCT_ZH.md)。

## 许可证

Codex Relay 使用 [MIT License](LICENSE)。[LICENSE_ZH.md](LICENSE_ZH.md) 是便于阅读的中文翻译；发生歧义时以英文原文为准。

---

<div align="center">

为谨慎的本地操作而构建。本项目与 OpenAI 无隶属或背书关系。

</div>
