# quill

一个可本地运行、可二次开发的 Agent 应用。**业务层（`src/quill_agent/`）与 UI 层（`app/`）完全分离**，
业务层是纯 Python，不依赖任何界面框架，因此同一份逻辑既能被 Streamlit 调用，也能被命令行或脚本复用。

---

## 1. 运行环境要求与运行方式

### 环境要求

| 项 | 要求 |
| --- | --- |
| Python | **>= 3.10**（开发环境用 3.12，见 `.python-version`） |
| 包管理 | [uv](https://github.com/astral-sh/uv)（仓库内已含 `uv.lock`，安装可复现） |
| 操作系统 | macOS / Linux / Windows 均可。工作目录选择器会按平台自动适配 |

### 快速开始

```bash
# 1. 安装依赖（首次会自动创建 .venv 并下载对应 Python）
make dev

# 2. 复制环境变量模板（可选，不配也能跑）
cp .env.example .env

# 3. 启动
make run        # 浏览器访问 http://localhost:8501
```

### 常用命令

| 命令 | 说明 |
| --- | --- |
| `make install` | 只装运行时依赖 |
| `make dev` | 装运行时 + 开发依赖（pytest / ruff） |
| `make run` | 启动 Streamlit UI |
| `make cli` | 运行命令行入口 |
| `make test` | 运行单元测试 |
| `make lint` | 静态检查（ruff） |
| `make fmt` | 格式化代码 |
| `make clean` | 清理缓存与虚拟环境 |

### 首次使用

应用启动后需要先配置一个模型，否则任务页无法发起对话：

1. 打开左侧「**模型**」页 → 添加模型
   - 填接口地址（如 `https://api.deepseek.com`）、协议、API Key
   - 模型名可点「连通测试」自动拉取（走 `GET /models`），也可手动填
2. 回到「**任务**」页 → 在功能区选择模型 → 开始对话

> 配置示例（DeepSeek 官方）
> 地址 `https://api.deepseek.com`，协议 `OpenAI 协议`，模型名 `deepseek-chat` / `deepseek-reasoner`

---

## 2. 目录结构

```
quill-agent/
├── app/                          # UI 层（Streamlit）—— 只做渲染与交互
│   ├── app.py                    # 唯一入口：st.set_page_config + 声明页面列表
│   ├── main.py                   # 页面：任务（对话主界面）
│   ├── models.py                 # 页面：模型（连接与模型名管理）
│   ├── prompt.py                 # 页面：提示词（提示词库浏览 + 模式配置）
│   ├── tools.py                  # 页面：工具（筛选 + 开关）
│   ├── skills.py                 # 页面：技能（占位，尚未实现）
│   └── archived.py               # 页面：归档（归档会话的搜索 / 恢复 / 删除）
│
├── src/quill_agent/                 # 业务层 —— 纯 Python，禁止 import streamlit
│   ├── __init__.py               # 版本号
│   ├── config.py                 # 集中式配置（环境变量 / .env）
│   ├── models.py                 # 数据模型（Pydantic）：连接、模式、选择
│   ├── store.py                  # 配置类数据持久化（JSON）
│   ├── preferences.py            # 界面偏好（上次选的模型 / 模式 / 工作目录 / 会话草稿）
│   ├── history.py                # 会话历史（JSONL + 归档）
│   ├── prompts.py                # 提示词库：读写 prompt/ 目录
│   ├── core.py                   # 接口连通性测试（拉取模型列表）
│   ├── agent.py                  # ★ Agent 循环：组装上下文 → 请求 → 执行工具
│   ├── cli.py                    # 命令行入口
│   └── tools/                    # 工具层
│       ├── __init__.py           # 导出 registry（import 即触发工具注册）
│       ├── base.py               # ToolSpec / ToolRegistry：声明、路由、开关
│       ├── builtin.py            # 通用内置工具的落脚点（当前为空）
│       └── files.py              # 文件工具 + 路径边界校验
│
├── prompt/                       # 提示词正文（用户在文件系统里直接维护）
│   ├── 身份/  能力/  工具策略/  工作流程/  输出规范/  约束/
│
├── data/                         # 运行时数据（已 gitignore，不会提交）
│   ├── models.json               # 模型连接配置
│   ├── modes.json                # 提示词模式
│   ├── tools.json                # 工具开关状态
│   ├── preferences.json          # 界面偏好
│   └── conversations/            # 会话历史
│       ├── active/               #   活跃会话
│       └── archived/             #   归档会话
│
├── tests/                        # 单元测试
├── .streamlit/config.toml        # Streamlit 运行配置与主题
├── .env.example                  # 环境变量模板
├── pyproject.toml                # 项目元数据、依赖、ruff / pytest 配置
├── Makefile                      # 常用命令
└── uv.lock                       # 依赖锁定
```

### 分层约定

依赖方向**严格单向**：

```
app/  ──依赖──>  src/quill_agent/
```

- `src/quill_agent/` 内**不允许** import `streamlit`。这条约束是整个架构的支点：它保证业务逻辑能被 UI、CLI、
  测试、脚本任意复用，也让底层改动不会牵动界面。
- `app/` 内**不写业务逻辑**，只做「取输入 → 调底层 → 展示结果」。数据读写的代码全部在业务层，
  页面里出现 `open()` / `json.load()` 基本就是分层漏了。

---

## 3. Agent 设计逻辑

### 3.1 提示词

**核心概念：正文是文件，组合是配置。**

```
提示词正文  →  prompt/<类别>/<名称>.md      ← 用户直接新建 / 编辑 / 删除文件
模式（组合）→  data/modes.json              ← 界面上配置：每类挑一个
```

六类提示词（顺序即拼装顺序）：

| 类别 | 作用 |
| --- | --- |
| 身份 | 模型扮演什么角色 |
| 能力 | 具备哪些能力与知识边界 |
| 工具策略 | 何时使用工具、如何使用 |
| 工作流程 | 处理任务的标准步骤 |
| 输出规范 | 回答的格式与风格要求 |
| 约束 | 安全与合规红线 |

一个「**模式**」= 从六类里各挑一个（**都可以不挑**）拼成的一套系统提示词。
六类全不挑也是合法配置，等价于「不带任何系统提示词」的纯问答。

**几个设计决定：**

- **正文放文件而不是数据库**。提示词是用户要反复迭代的东西，用编辑器改 `.md` 比在网页表单里改舒服得多，
  也方便纳入版本控制。
- **拼装顺序固定按 `PROMPT_CATEGORIES`**，与用户在弹窗里的勾选顺序无关。顺序稳定 → 同一模式每次拼出的
  system prompt 完全一致 → 命中模型侧的前缀缓存（省钱、降延迟）。
- **引用的文件被删除不会报错**，只是该类别不参与拼装；界面上会标注「（文件缺失）」。
- **偏好模型**：模式可以绑定一个模型。在任务页切换到该模式时，模型选择会自动跟着换
  （未指定则保持当前模型不变）。实现见 `app/main.py` 的 `apply_preferred_model`。

**运行时上下文不放 system prompt**。当前时间、工作目录、附件名这些每轮都在变的信息，放在 user 消息开头。
如果塞进 system prompt，前缀缓存会永远失效。见 `agent.build_user_message()`。

### 3.2 工具

**定义在代码里，开关在配置里。** 两者分开，避免出现两份互相打架的真相。

```python
@registry.tool(
    description="一句话说清「什么时候该用我」—— 这是模型判断的唯一依据。",
    category="分类（只用于界面筛选，不会发给模型）",
    parameters={"type": "object", "properties": {...}, "required": [...]},
)
def your_tool(...) -> str:
    ...
```

- **`description` 是模型判断要不要调用的唯一依据**，写得越贴合真实场景，调用越准。
- 未启用的工具**不会被发给模型**（`registry.schemas()` 只返回已启用的），模型自然不会请求调用它。
- **执行契约**：工具内部任何异常都转成可读文本返回，不向上抛。模型看到错误说明后通常能自行修正，
  而抛异常会直接打断整个 Agent 循环。

**当前工具清单（7 个）：**

| 工具 | 说明 |
| --- | --- |
| `list_dir` | 列出目录内容：只列一层，带文件大小 |
| `read_file` | 读文件内容：带行号、自动截断，可用 `offset` 分页续读 |
| `write_file` | 写入 / 覆盖文件：自动创建父目录、原子写入 |
| `edit_file` | 局部替换：要求 `old_string` 在文件中唯一 |
| `search_content` | 按正则搜索**文件内容**，返回 `文件:行号: 内容` |
| `search_files` | 按通配符查找**文件名**，如 `*.py`、`**/*.json` |
| `delete_file` | 删除文件（不删目录） |

> `search_content` 是这批里最关键的一个：没有它，模型要确认「某个函数在哪定义」就只能挨个读文件，
> token 消耗是几十倍。它会自动跳过 `.venv` / `.git` / `__pycache__` 等目录，并按行截断、
> 限制命中数量 —— 这三条都是为了不把上下文撑爆。

**文件工具的安全边界：**

所有文件工具都跑在工作目录（`work_dir`）内，路径统一经 `PathGuard` 校验：

```python
resolved = (root / raw).resolve()           # 展开 .. 和符号链接
if not resolved.is_relative_to(root):
    raise ValueError("拒绝访问工作目录之外的位置")
```

`resolve()` 会跟随符号链接，所以「在工作目录里放一个软链接指向外部」这条绕过路径也拦得住
（只做字符串层面判断 `..` 是拦不住的）。

工作目录可在任务页的 📁 里切换，取值优先级：**界面选择 > 配置项 `WORK_DIR`**。
选过的目录若被删除，会自动回落到配置默认值，不会让工具直接罢工。

### 3.3 技能

**尚未实现。** `app/skills.py` 目前是空文件。

### 3.4 记忆

**尚未实现独立的记忆机制。**

当前只有「会话历史」（见第 5 节），它是完整的对话记录，**不是** Agent 的长期记忆：
模型每轮只会收到最近若干条消息，没有任何检索、摘要或跨会话引用能力。

### 3.5 用户能看到的文件

| 路径 | 谁维护 | 作用 |
| --- | --- | --- |
| `prompt/<类别>/*.md` | **用户** | 提示词正文。新建文件即新增一个可选提示词，文件名就是引用标识 |
| `data/models.json` | 界面 | 模型连接配置（地址、Key、模型名） |
| `data/modes.json` | 界面 | 提示词模式（各类选了哪个提示词 + 偏好模型） |
| `data/tools.json` | 界面 | 工具开关状态 |
| `data/preferences.json` | 界面 | 上次选的模型 / 模式 / 工作目录、各会话的输入草稿 |
| `data/conversations/**` | 界面 | 会话历史与归档 |
| `.env` | 用户 | 环境变量（可选）。参考 `.env.example` |

> ⚠️ **`api_key` 以明文存储在 `data/models.json` 中。** `data/` 已在 `.gitignore` 里，不会被提交，
> 但文件本身在你的磁盘上是明文。生产环境请改用环境变量或密钥管理服务。

---

## 4. 业务层对象与函数（二次开发指南）

业务层的对外接口都通过模块级导入暴露，没有额外的门面层 —— 直接 `from quill_agent.xxx import yyy` 即可。

### 4.1 配置 `quill_agent.config`

```python
from quill_agent.config import get_settings

settings = get_settings()   # 全局唯一实例（带缓存，避免重复解析 .env）
```

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `app_name` | `quill` | 应用名 |
| `app_debug` | `False` | 调试模式 |
| `work_dir` | `.` | **文件工具的工作目录，同时是安全边界** |
| `models_path` | `data/models.json` | 模型配置 |
| `modes_path` | `data/modes.json` | 提示词模式 |
| `tools_path` | `data/tools.json` | 工具开关 |
| `preferences_path` | `data/preferences.json` | 界面偏好 |
| `conversations_dir` | `data/conversations` | 会话目录 |
| `prompt_dir` | `prompt` | 提示词目录 |

所有字段都能用**同名大写环境变量**覆盖（如 `WORK_DIR`、`MODELS_PATH`）。测试时如需重新解析，
调用 `get_settings.cache_clear()`。

### 4.2 数据模型 `quill_agent.models`

| 对象 | 说明 |
| --- | --- |
| `ModelConfig` | 一条连接配置：`id / name / base_url / protocol / api_key / models[]` |
| `ModelChoice` | 一次具体选择：`config`（连接）+ `model`（模型名） |
| `PromptMode` | 提示词模式：`id / name / settings{} / preferred_model` |
| `Protocol` | 协议枚举（`OPENAI` / `ANTHROPIC`），`.label` 给界面用 |
| `model_choice_key(config_id, model)` | 生成模型选择的**稳定标识** `"{连接id}::{模型名}"` |
| `check_api_key(protocol, api_key)` | 按协议校验 Key 格式，通过返回 `None` |

> `model_choice_key` 这个「连接 id + 模型名」的标识很重要：它解决了**列表下标会漂移**的问题
> （删掉一个连接，原本的「第 3 个」可能变成「第 2 个」，用户的选择就悄悄换了对象）。
> 任务页的模型选择、模式的偏好模型都用这个口径，两边可以直接互相赋值。

### 4.3 持久化 `quill_agent.store`

三个结构相似的存储类，接口统一：

```python
store.list()                  # 读全部
store.get(id)                 # 按 id 取，找不到返回 None
store.add(...)                # 新增，返回新对象（id 由内部生成）
store.update(item)            # 按 id 覆盖更新
store.remove(id)              # 按 id 删除
```

| 类 | 存什么 | 文件 |
| --- | --- | --- |
| `ModelStore` | `list[ModelConfig]` | `data/models.json` |
| `PromptModeStore` | `list[PromptMode]` | `data/modes.json` |
| `ToolStateStore` | `dict[工具名, bool]`（接口是 `load()` / `set()`） | `data/tools.json` |

### 4.4 会话历史 `quill_agent.history`

```python
from quill_agent.history import ConversationStore

store = ConversationStore(settings.conversations_dir)
store.ensure_dirs()

conv_id = store.create()               # 新建空会话，返回 id
store.append(conv_id, {"role": "user", "content": "你好"})
messages = store.load(conv_id)         # -> list[dict]

store.list_active()                    # list[ConversationMeta]，按最近更新倒序
store.list_archived()                  # 同上，时间是「归档时间」

store.archive(conv_id)                 # -> bool：True 已归档，False 是空会话被直接删除
store.restore(conv_id)                 # 归档恢复
store.remove_archived(conv_id)         # 永久删除单条
store.remove_all_archived()            # 清空归档，返回删除数量
```

`ConversationMeta` 提供：`id` / `title`（首条用户消息截断）/ `updated_at` / `updated_text` / `archived_text`。

### 4.5 界面偏好 `quill_agent.preferences`

```python
from quill_agent.preferences import PreferenceStore, draft_key

prefs = PreferenceStore(settings.preferences_path)
prefs.get("model")            # 读不到时返回 ""
prefs.set("model", "c1::m-a")
prefs.remove("model")

draft_key("20260918-043332-0307")   # -> "prompt_draft::20260918-043332-0307"
```

这是个极简键值存储（值统一按字符串处理）。**文件损坏、类型不符、JSON 解析失败都退回默认值**，
绝不因为一个偏好文件坏掉就让页面起不来。

### 4.6 提示词库 `quill_agent.prompts`

```python
from quill_agent.prompts import PROMPT_CATEGORIES, PromptLibrary

library = PromptLibrary(settings.prompt_dir)
library.ensure_dirs()
library.list_names("身份")             # 某类别下的提示词名（不含扩展名）
library.read("身份", "Agent助手")       # 正文，不存在返回 None
library.exists("身份", "Agent助手")     # 引用是否还有效
library.collect(mode.settings)         # {类别: 正文}，失效条目自动跳过
```

### 4.7 接口连通性 `quill_agent.core`

```python
from quill_agent.core import test_connection, fetch_models

result = test_connection(base_url="https://api.deepseek.com", api_key="sk-...")
result.ok          # bool
result.message     # "连接成功" / "连接失败"
result.models      # 拉取到的模型名列表
result.detail      # 原始错误信息（排查用，界面不展示）
```

实现走 `GET /models`，**不消耗 token**。并非所有服务都实现了这个端点（部分中转站只有
`/chat/completions`）——失败时改用界面上的手动录入即可。当前仅支持 OpenAI 协议。

### 4.8 Agent 循环 `quill_agent.agent`

这是整个业务的入口，也是**二次开发最可能改的地方**。

```python
from quill_agent.agent import run_agent_stream, AgentResult, ToolStep

for event in run_agent_stream(
    prompt="帮我看看当前目录有什么",   # 本轮用户输入
    files=[],                          # 上传的文件列表
    mode=mode,                          # PromptMode | None
    choice=model_choice,                # ModelChoice | None
    history=[{"role": "user", "content": "..."}],
):
    if isinstance(event, str):
        ...        # 文本增量，直接追加渲染
    else:
        ...        # ToolStep：一次工具调用已完成（name / arguments / result）
```

**为什么是生成器**：一次流式响应里可能**同时**有文本增量和工具调用增量，两者处理方式完全不同 ——
文本可以立刻往外吐（用户看到逐字输出），工具调用只能累积（分片到达，中途是残缺 JSON），
必须等整条流结束才能执行。生成器让调用方按类型分派，UI 想怎么渲染都行。

其他可用的函数：

| 函数 | 说明 |
| --- | --- |
| `build_system_prompt(mode)` | 按固定顺序把模式选中的提示词拼成 system prompt |
| `build_user_message(prompt, files)` | 拼装运行时上下文（时间 / 工作目录 / 附件）+ 本轮问题 |

循环上限由 `MAX_ITERATIONS` 控制（默认 5），防止工具调用陷入死循环。

### 4.9 工具层 `quill_agent.tools`

```python
from quill_agent.tools import registry, ToolSpec

registry.all()                          # list[ToolSpec]，已合并持久化的开关状态
registry.search(keyword="", category="") # 名称模糊 + 分类精确
registry.categories()                    # 现有分类
registry.set_enabled("list_dir", False)  # 持久化开关
registry.schemas()                       # 转成 API 的 tools 参数（只含已启用的）
registry.execute("read_file", '{"path": "README.md"}')   # 执行，异常转文本
```

### 4.10 二次开发：三个常见场景

**① 新增一个工具**（最常见）

在 `src/quill_agent/tools/builtin.py` 里加一个函数 + 装饰器，工具页会自动出现它：

```python
@registry.tool(
    description="把文本转成大写。当用户要求转换文本大小写时使用。",
    category="文本",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string", "description": "要转换的文本"}},
        "required": ["text"],
    },
)
def to_upper(text: str) -> str:
    return text.upper()
```

要点：返回 `str`；异常自己转成可读文本（不要抛）；`description` 写清「什么时候用」。

**② 给数据模型加字段**

以「模式加偏好模型」为例，改动链条是固定的三步：

1. `src/quill_agent/models.py` —— 加字段，**给默认值**（保证旧数据能读进来）
2. `src/quill_agent/store.py` —— 如果 `add()` 是显式参数，补上它
3. `app/prompt.py` —— 弹窗里加控件，保存时写进去

**③ 换持久化方案**

只需替换 `src/quill_agent/store.py` 的实现（比如改成 SQLite 或远端 API）。
页面代码调用的是 `list / get / add / update / remove` 这套薄接口，不受影响 ——
这也是刻意把这些类写得这么薄的原因。

---

## 5. 持久层设计

### 5.1 数据文件一览

```
data/
├── models.json           # 模型连接配置
├── modes.json            # 提示词模式
├── tools.json            # 工具开关
├── preferences.json      # 界面偏好
└── conversations/
    ├── active/           # 活跃会话
    │   └── 20260918-034512-a1b2.jsonl
    └── archived/         # 归档会话
        └── 20260918-030000-c3d4.jsonl
```

### 5.2 各文件的数据结构

**`models.json`** — 数组，每个元素是一条连接

```json
[
  {
    "id": "5382674b1ed6462bbd5005d9b4dc5cef",
    "name": "deepseek官方",
    "base_url": "https://api.deepseek.com",
    "protocol": "openai",
    "api_key": "sk-...",
    "models": ["deepseek-v4-pro", "deepseek-flash"]
  }
]
```

**`modes.json`** — 数组，每个元素是一个模式

```json
[
  {
    "id": "286d7e6ed0d2450d9fade2f43e356b19",
    "name": "Agent 模式",
    "settings": { "身份": "Agent助手", "能力": "通用能力", "约束": "安全边界" },
    "preferred_model": "5382674b...::deepseek-flash"
  }
]
```

`settings` 只包含用户实际选了的类别；`preferred_model` 为空串表示不指定。

**`tools.json`** — 对象，键是工具名

```json
{ "list_dir": true, "search_content": true, "delete_file": false }
```

只存开关，不存工具定义 —— 定义由代码负责（避免两份真相互相打架）。

**`preferences.json`** — 扁平键值对象

```json
{
  "model": "5382674b...::deepseek-flash",
  "mode": "286d7e6ed0d2450d9fade2f43e356b19",
  "work_dir": "/Users/you/projects/demo",
  "prompt_draft::20260918-043332-0307": "写到一半的内容"
}
```

草稿键按会话隔离（`prompt_draft::<会话id>`），所以在会话 A 里写一半切到 B，两边互不影响。

**`conversations/*.jsonl`** — 一个会话一个文件，**一行一条消息**

```jsonl
{"role": "user", "content": "你好", "ts": "2026-09-18T04:33:32"}
{"role": "assistant", "content": "你好！有什么可以帮你的？", "ts": "2026-09-18T04:33:35", "steps": []}
```

**会话 id 就是文件名**（`时间戳-随机后缀`），所以按文件名排序即按时间排序，不需要额外的索引文件。

### 5.3 为什么用 JSONL 而不是 JSON 数组

对话是**只追加**的：新消息永远加在末尾。

- JSONL 追加一行是 `O(1)`；JSON 数组每次都要读出全部、追加、再整体写回 —— `O(n)` 且中间崩溃会毁掉整个文件
- 某一行的 JSON 写坏了只丢那一条消息，其余照常读出；数组格式则整个文件报废

### 5.4 归档机制

归档就是文件在两个目录之间移动：

- **归档**：`active/` → `archived/`，并显式更新文件的 `mtime`。
  （`Path.replace()` 只是改名，不会更新 `mtime`，不显式处理的话「归档时间」显示的会是最后一条消息的时间。）
- **恢复**：`archived/` → `active/`，同样不动 `mtime` —— 消息时间应该保持真实。
  代价是恢复后的会话按原时间排序，可能排在列表靠后。
- **空会话归档 = 直接删除**：一条消息都没有的会话没有归档价值，不产生空文件。
- **删除会话时同步清理它的草稿**，避免偏好文件里堆积孤儿键。

### 5.5 旧数据兼容

所有新增字段都带默认值，且用 `model_validator(mode="before")` 兜底老格式：

- `ModelConfig.models` 早期叫 `model`（单个字符串），现在会自动迁移成列表
- `ModelConfig.protocol` 缺失时默认按 OpenAI 处理
- `PromptMode.preferred_model` 缺失时默认空串

**读旧数据不会报错，也不会自动改写文件** —— 直到用户下次编辑该条记录才会写入新字段。

---

## 6. UI 层实现（简略）

### 6.1 结构

使用 Streamlit 的 `st.navigation` 多页模式：

- **`app/app.py` 是唯一入口**，声明页面列表；`st.set_page_config` 只能在这里调用一次
- 侧边栏的会话列表也在这里渲染 —— 它属于全局 UI，与当前在哪个页面无关
- 页面文件整体执行，各自的 `main()` 在导航切换时运行

### 6.2 页面职责

页面只做三件事：**取输入 → 调业务层 → 渲染结果**。

一个典型例子是任务页的提交流程：把参数交给 `run_agent_stream()`，然后按事件类型分派 ——
文本增量写进输出区预留的 `st.empty()` 占位符（实时渲染），工具调用写进 `st.status` 面板。

### 6.3 几个踩过的坑（改动 UI 前建议先读）

| 现象 | 原因与对策 |
| --- | --- |
| 切换页面后输入框内容消失 | **widget 的状态在切页时会被清理**，普通 `st.session_state` 键不会。需要跨页保留的状态要自己存到普通键里（或用 widget 的 `persist_state="page"`） |
| 弹窗里点按钮后弹窗直接消失 | `st.dialog` 只在其 `if` 分支里渲染，重跑后条件不成立就没了。要用 `session_state` 驱动渲染（见 `archived.py`）。**`st.popover` 没有这个问题**，它的展开状态由前端管理 |
| 改变控件后错误提示一闪而过 | 回调/按钮点击后紧跟 `st.rerun()` 会清掉提示。错误路径不要 rerun |
| 改 `src/quill_agent/` 后页面报 ImportError | Streamlit 热重载只重跑页面脚本，**不会重新 import 已加载的模块**。新增/删除被导入的符号、改 `.env`、动依赖，都需要重启进程 |

### 6.4 样式定制

任务页的高度布局依赖 CSS 覆盖，选择器基于 Streamlit 内部类名（`st-key-*`、`data-testid`），
属于**非公开接口**，升级 Streamlit 后可能失效。相关代码集中在 `app/main.py` 的 `PAGE_CSS` 里，
并附有实测结论（哪一层才是真正控制高度的元素），改动前建议先读那段注释。

---

## 许可

[MIT](LICENSE) © 2026 zhaoyifan

可自由使用、修改、分发（含商业用途），只需保留版权声明。详见 [LICENSE](LICENSE)。
