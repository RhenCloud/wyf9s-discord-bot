# 项目介绍

`wyf9s-discord-bot` 是一个多功能 Discord 机器人，基于 [discord.py](https://discordpy.readthedocs.io/) 构建，使用 YAML + [Pydantic v2](https://docs.pydantic.dev/) 进行配置验证，采用模块化 Cog 架构。

## 整体架构

```text
config.py            # Pydantic 配置模型 + 加载器
config.yaml          # 运行时配置 (被 gitignore)
config.example.yaml  # 带内联文档的示例配置 (配置字段的事实来源)
schedules.yaml       # 计划锁定数据 (自动生成)
perm.yaml            # 动态权限数据 (通过 /perm 指令管理)
main.py              # 机器人入口，Cog 加载，命令本地化翻译器注册
utils.py             # 共享工具 (权限判定、限速器、消息发送等)
perm.py              # 动态权限存储服务
i18n.py              # i18n 运行时 (t / lang_of) + Discord 命令本地化翻译器
lang_store.py        # 用户 / 服务器语言偏好持久化
lang/                # 语言文件
  zh.yaml            # 简体中文 (默认)
  en.yaml            # English
cogs/                # 命令模块 (discord.py Cog)
  admin.py           # 管理指令: /sync, /reload
  emoji.py           # 表情 / 贴纸指令: /e, /emoji info, /emoji update
  tools.py           # 工具 / 管理指令
  lock.py            # 频道锁定 / 解锁 + 计划锁定
  voice.py           # 语音频道指令: /vc join, /vc leave
  antispam.py        # 反垃圾消息处理
  manage.py          # 自动删除事件处理
  perm.py            # 动态权限指令: /perm add/rm/show
  announce.py        # 公告推送指令: /subscribe
  lang.py            # 多语言指令: /lang
modules/             # 共享服务 (非 Cog)
  audit.py           # 审计日志服务
  clear_message.py   # 批量清理消息服务
```

## 启动流程

`main.py` 的启动过程如下：

1. **初始化日志**：先配置 [Loguru](https://loguru.readthedocs.io/)，并将标准库 `logging`（含 discord.py 的日志）拦截转发到 Loguru。
2. **加载配置**：`Config()` 读取 `config.yaml`，用 Pydantic 模型校验。校验失败或文件缺失会直接退出。
3. **重配置日志**：根据配置的日志等级、文件路径、轮转/保留策略重新设置输出。
4. **创建客户端**：`commands.Bot`，启用 `message_content` intent，可选配置代理。将 `config`、`audit`、`rate_limiter`、`perm_store`、`lang_store` 等共享状态挂载到 bot 实例上。
5. **注册翻译器**：`tree.set_translator(I18nTranslator())`，用于斜杠命令 / 参数描述的本地化。
6. **加载 Cog**：通过 `load_extension()` 按需加载各 Cog 模块 (根据 `enabled` 开关)。
7. **登录**：`on_ready` 时同步斜杠指令、同步表情列表。

## 核心设计

### Cog 架构

所有命令模块使用 discord.py **Cog** 架构，支持热重载 (`/reload`)、生命周期钩子 (`cog_load` / `cog_unload`)、事件监听 (`@Cog.listener`)。共享状态 (emoji 缓存、限速器、计划锁定数据、动态权限等) 存储在 `bot` 实例上，重载 Cog 不会丢失：

```python
class EmojiCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.c = bot.config
        ...

    @app_commands.command(name="e", description="Send an emoji")
    async def e(self, interaction: discord.Interaction, name: str): ...

    # 子命令组
    emoji_group = app_commands.Group(name="emoji", description="...")

    @emoji_group.command(name="update")
    async def emoji_update(self, interaction: discord.Interaction): ...
```

### 热重载

`/reload [module]` 可在不重启 bot 的情况下热更新单个 Cog (有 15s 冷却防滥用)。不带参数则列出所有可用 Cog 及加载状态。语音连接 (VoiceClient)、限速状态、计划锁数据等存储在 bot 实例上，重载时不会丢失。

### 声明式权限控制

通过 `@u.requires(Permission.MOD)` 装饰器统一进行权限判定，支持 `config.yaml` + `perm.yaml` 双层权限。详见 [权限系统](/guide/permissions)。

### 多语言 (i18n)

所有面向用户的文本均通过 `lang/zh.yaml`（默认）与 `lang/en.yaml` 提供翻译，不在代码中硬编码：

- **运行时消息**：按 `用户 > 服务器 > 默认(zh)` 优先级解析语言（`i18n.lang_of` + `LangStore.resolve`），由 `/lang` 设置。
- **斜杠命令 / 参数描述**：用 `i18n.ls("ns.key")` 包装，注册英文为基准值，`I18nTranslator` 在 `tree.sync()` 时按 Discord 客户端 locale 提供本地化（命令名称保持不变）。

详见[多语言 (lang)](/modules/lang)。

### 双命令模式

| 模式 | 触发方式 | 配置开关 |
| --- | --- | --- |
| 斜杠命令 | `/random` | 各模块的 `slash: true` |
| 前缀命令 | `//vc leave`（前缀由 `command_prefix` 决定） | 各模块的 `prefix: true` |

::: tip 关于前缀命令的参数
斜杠命令通过 Discord 原生参数 UI 传参；前缀命令部分复杂指令（如 `clear-message`、`lock plan`）使用 `--key=value` 形式的 flag 传参（支持带引号的值）。
:::

## 技术栈

| 组件 | 说明 |
| --- | --- |
| **Python 3.13** | 运行时（`pyproject.toml` 要求 `>=3.10`） |
| **[uv](https://docs.astral.sh/uv/)** | 包管理器 |
| **[discord.py](https://discordpy.readthedocs.io/)** `[voice]` | Discord API 封装 (Cog 架构) |
| **[Pydantic v2](https://docs.pydantic.dev/)** | 配置模型验证 |
| **[PyYAML](https://pyyaml.org/)** | 配置文件解析 |
| **[Loguru](https://loguru.readthedocs.io/)** | 日志记录 |
| **[aiohttp](https://docs.aiohttp.org/)** | 异步 HTTP 请求 |
| **davey / PyNaCl** | 语音 / DAVE 加密支持 |

## 功能模块一览

| 模块 | 键名 | 类型 | 说明 |
| --- | --- | --- | --- |
| [工具 / 管理](/modules/tools) | `tools` | 指令 | 随机数 / UUID、删消息、批量清理、频道移动、文本转文件 |
| [表情](/modules/emoji) | `emoji` | 指令 | 远程表情包浏览 / 搜索 / 发送 |
| [频道锁定](/modules/lock) | `lock` | 指令 | 锁定 / 解锁频道，计划锁定 |
| [语音频道](/modules/voice) | `voicechannel` | 指令 | 加入 / 离开语音频道 |
| [管理指令](/modules/admin) | — | 指令 | 指令同步 `/sync`、热重载 `/reload` |
| [动态权限](/modules/perm) | `perm` | 指令 | `/perm add/rm/show` 权限规则管理 |
| [公告推送](/modules/announce) | `announce` | 指令 | `/subscribe` 关注公告频道 |
| [多语言](/modules/lang) | —（始终启用） | 指令 | `/lang` 切换用户 / 服务器语言 |
| [自动管理](/modules/manage) | `rmmsg` / `rmtodo` | 事件 | 自动删除消息 |
| [反垃圾](/modules/antispam) | `antispam` | 事件 | 频道级反垃圾规则 |
| [审计日志](/modules/audit) | `audit` | 服务 | 记录管理操作到指定频道 |

> 每个模块的详细指令、用法、所需权限见[模块总览](/modules/)。
