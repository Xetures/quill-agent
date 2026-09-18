# quill

一个可本地运行、可二次开发的 Agent 应用。**业务层（`src/quill_agent/`）与界面完全分离**，
业务层是纯 Python、不依赖任何界面框架，所以同一份逻辑今天能被 Vue 前端调用，
明天也能被命令行或脚本复用：

```
web/ (Vue)  ──HTTP/SSE──┐
                        ├──>  src/quill_agent/  (业务层，纯 Python)
app/ (Streamlit) ───────┘
```

> **两个界面并存。** `web/` 是新的 Vue 前端，`app/` 是早期的 Streamlit 版本。
> 后者保留着当参照物（接口对不上时切过去比一比），功能对齐后可以删。
> 两套界面共用同一份会话记录，互不干扰。

---

## 1. 运行环境要求与运行方式

### 环境要求

| 项 | 要求 |
| --- | --- |
| Python | **>= 3.10**（开发环境用 3.12，见 `.python-version`） |
| 包管理 | [uv](https://github.com/astral-sh/uv)（仓库内已含 `uv.lock`，安装可复现） |
| Node | **>= 20**（只在跑 Vue 前端时需要；用 Streamlit 界面可以不管） |
| 操作系统 | macOS / Linux / Windows 均可。工作目录选择器会按平台自动适配 |

### 快速开始

```bash
# 1. 安装后端依赖（首次会自动创建 .venv 并下载对应 Python）
make dev

# 2. 复制环境变量模板（可选，不配也能跑）
cp .env.example .env
```

**方式一：Vue 前端（推荐）** —— 需要两个终端：

```bash
cd web && npm install      # 只需一次
make api                   # 终端 A：后端，监听 8000
make web                   # 终端 B：前端，浏览器打开 http://localhost:5173
```

前端开发服务器会把 `/api` 转发给后端，所以浏览器看到的是同源请求，不用配跨域。

**方式二：Streamlit 界面（旧）**

```bash
make run        # 浏览器打开 http://localhost:8501
```

### 常用命令

| 命令 | 说明 |
| --- | --- |
| `make install` | 只装运行时依赖 |
| `make dev` | 装运行时 + 开发依赖（pytest / ruff） |
| `make api` | 启动后端 API（FastAPI，8000） |
| `make web` | 启动前端开发服务器（Vue，5173） |
| `make run` | 启动 Streamlit UI（旧界面，8501） |
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
│   ├── skills.py                 # 页面：技能（列表 + 开关）
│   ├── memory.py                 # 页面：记忆（查看 / 开关 / 删除）
│   └── archived.py               # 页面：归档（归档会话的搜索 / 恢复 / 删除）
│
├── server/                       # 后端：把业务层暴露成 HTTP + SSE
│   ├── main.py                   # FastAPI 应用装配（CORS、路由挂载）
│   ├── stores.py                 # 各存储的构造入口（每次新建，不缓存）
│   ├── schemas.py                # 请求体模型
│   └── routes/                   # 按「前端一个页面 ≈ 一个模块」拆分
│
├── web/                          # 前端：Vue 3 + Vite + TypeScript
│   ├── vite.config.ts            # 含 /api 反向代理
│   └── src/
│       ├── api/                  # 接口封装（REST + SSE 解析）
│       ├── stores/               # 共享状态（会话、当前选择、草稿）
│       ├── components/           # Sidebar / MessageItem
│       └── views/                # 各页面，与路由一一对应
│
├── src/quill_agent/                 # 业务层 —— 纯 Python，禁止 import streamlit
│   ├── __init__.py               # 版本号
│   ├── config.py                 # 集中式配置（环境变量 / .env）
│   ├── models.py                 # 数据模型（Pydantic）：连接、模式、选择
│   ├── store.py                  # 配置类数据持久化（JSON）
│   ├── preferences.py            # 界面偏好（上次选的模型 / 模式 / 工作目录 / 会话草稿）
│   ├── history.py                # 会话历史（JSONL + 归档）
│   ├── prompts.py                # 提示词库：读写 prompt/ 目录
│   ├── skills.py                 # 技能库：读写 skills/ 目录（元信息 + 正文）
│   ├── memory.py                 # 记忆：跨会话保留的长期事实（JSON）
│   ├── core.py                   # 接口连通性测试（拉取模型列表）
│   ├── agent.py                  # ★ Agent 循环：组装上下文 → 请求 → 执行工具
│   ├── cli.py                    # 命令行入口
│   └── tools/                    # 工具层
│       ├── __init__.py           # 导出 registry（import 即触发工具注册）
│       ├── base.py               # ToolSpec / ToolRegistry：声明、路由、开关
│       ├── builtin.py            # 通用内置工具（read_skill / remember）
│       └── files.py              # 文件工具 + 路径边界校验
│
├── prompt/                       # 提示词正文（用户在文件系统里直接维护）
│   ├── 身份/  能力/  工具策略/  工作流程/  输出规范/  约束/
│
├── skills/                       # 技能（一个子目录一个技能）
│   ├── 代码审查/SKILL.md
│   └── 提交信息/SKILL.md
│
├── data/                         # 运行时数据（已 gitignore，不会提交）
│   ├── models.json               # 模型连接配置
│   ├── modes.json                # 提示词模式
│   ├── tools.json                # 工具开关状态
│   ├── skills.json               # 技能开关状态
│   ├── memory.json               # 长期记忆条目
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

**运行时上下文不放 system prompt**。当前时间、工作目录、附件路径这些每轮都在变的信息，
放在 user 消息开头。如果塞进 system prompt，前缀缓存会永远失效。见 `agent.build_user_message()`。

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

**当前工具清单（10 个）：**

| 工具 | 说明 |
| --- | --- |
| `list_dir` | 列出目录内容：只列一层，带文件大小 |
| `read_file` | 读文件内容：带行号、自动截断，可用 `offset` 分页续读 |
| `write_file` | 写入 / 覆盖文件：自动创建父目录、原子写入 |
| `edit_file` | 局部替换：要求 `old_string` 在文件中唯一 |
| `search_content` | 按正则搜索**文件内容**，返回 `文件:行号: 内容` |
| `search_files` | 按通配符查找**文件名**，如 `*.py`、`**/*.json` |
| `delete_file` | 删除文件（不删目录） |
| `read_skill` | 读取某个技能的完整说明（技能体系的按需加载入口） |
| `remember` | 写入一条长期记忆（跨会话保留的偏好与事实） |
| `forget` | 按原文忘掉一条记忆（要求一字不差，避免误删） |

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

**技能是一份「这类任务该怎么做」的说明：平时只占一行，用到时才全文加载。**

```
提示词（每轮全量注入）  →  技能（按需加载）  →  工具（按需执行）
   用户决定                  模型决定              模型决定
```

为什么不直接写成提示词：假设「代码审查」的流程说明有 800 字，塞进 system prompt 后，
**每一轮、每个不相关的任务**都要付这 800 字，模型还得在一堆无关指示里找相关的。
库里放 20 个这类流程就是 1.6 万字的常驻开销，前缀缓存也白搭。技能把这笔账拆成两段付：

| 阶段 | 花多少 | 谁决定 |
| --- | --- | --- |
| 平时 | 一行「名字 + 适用场景」（约 30 字） | 用户（技能开关） |
| 用到时 | 全文（可能上千字） | **模型**（判断任务匹配后调 `read_skill`） |

**目录约定**（和 `prompt/` 一个思路：正文是文件，用户在编辑器里维护）：

```
skills/
└── 代码审查/
    └── SKILL.md        ← 目录名就是技能名，入口固定叫 SKILL.md
```

`SKILL.md` = 文件头的元信息块 + 正文：

```markdown
---
description: 当用户要求审查代码、评估一段实现时使用。
---

（正文：这类任务的完整做法，可以写得很长）
```

- `description` 是**模型判断要不要加载的唯一依据**。写「处理代码」是没用的，要写清
  「什么时候用我」—— 和工具的 `description` 同理。
- 元信息块只支持单行 `键: 值`，所以没有引 YAML 解析依赖。没写 `description` 也能用，
  只是模型少了这条线索，技能页会把它标出来。
- **目录里有 SKILL.md 才算技能**，在 `skills/` 下放别的东西（草稿、附件）不会被误认。

**清单单独占一条 system 消息**，不拼进身份提示词。原因和「运行时上下文不放 system
prompt」是同一个：清单会随启用 / 停用技能变化，跟身份提示词拼在一起的话，停用一个技能
就把整段 system prompt 打乱，前面那段的缓存前缀跟着作废。

**开关在 `data/skills.json`**，和工具一样。停用的技能不出现在清单里；即使模型从别处
知道了名字，`read_skill` 也会拒绝 —— 否则开关形同虚设。

### 3.4 记忆

记忆不是一个东西，而是**三个不同的问题**。这个项目按性质分别处理：

| 类型 | 解决什么 | 实现 |
| --- | --- | --- |
| 工作记忆 | 当前对话装不下 | 会话历史 + 截断 / 还原（见第 5 节） |
| 程序性记忆 | 「这类任务怎么做」 | **技能**（见 3.3） |
| 长期记忆 | 跨会话的事实与偏好 | `data/memory.json` + `remember` 工具 |

**工作记忆**是一套「忘记」的机制。每轮只带最近 `MAX_HISTORY_RECORDS`（默认 20）条记录，
且上一轮的工具调用会被还原成 `assistant.tool_calls` + `tool` 消息一起发过去
（见 `agent.build_history_messages`）—— 模型因此记得自己查过什么，不会反复调用同一个工具。

截断刻意放在「展开工具调用之前」：先展开再截断会把 `assistant.tool_calls` 和对应的
`tool` 消息切开，那是 API 不接受的非法序列。单条工具结果回放时有 2000 字符上限，
避免读一个大文件就把历史撑爆（界面上展示的仍是完整结果）。

**长期记忆**解决的是另外两件事：窗口一过就忘、换个会话就从零开始。它的机制刻意做得跟
技能一样 ——

```
技能：模型按需加载「怎么做」    →  方法
记忆：常驻清单「关于用户的事实」 →  事实
```

都存成数据、都有开关、都作为独立的一条 system 消息进上下文。两个取舍值得说明：

- **写入是显式的**：模型必须主动调 `remember` 工具，而不是系统自动抽取。自动抽要额外调
  一次模型，而且用户看不见它到底记了什么、错在哪 —— 记忆一旦成了黑盒，错了就没法纠正，
  会一直污染后续对话。
- **遗忘也是显式的**：模型要调 `forget` 工具，而且 `text` 必须和记忆原文**一字不差**。
  不做模糊匹配，是因为模型看到的本来就是清单里的原文、照抄并不难；而按「包含」匹配会让
  「忘掉 A」顺手把「A 和 B」一起删掉 —— 删除不可逆，这里宁可让它多失败一次。工具描述里
  还写了条否定边界：只在用户明确要求时删，不能因为自己觉得某条过时就动手。
- **不做自动淘汰**：条数到上限（`MAX_MEMORIES`，默认 50）后**拒绝写入**，让用户去清理，
  而不是悄悄扔掉最旧的。记忆是用户的资产，丢哪条该由人决定。

记忆页把每条都摆出来：能看、能关（关掉不等于删除，留着以后还能用回来）、能删。

> ⚠️ **别把记忆当成授权。** 它只是提示。文件工具的权限始终由 `PathGuard` 兜底，
> 不会因为记忆里写了一句「以后不用确认」就放开。

### 3.5 用户能看到的文件

| 路径 | 谁维护 | 作用 |
| --- | --- | --- |
| `prompt/<类别>/*.md` | **用户** | 提示词正文。新建文件即新增一个可选提示词，文件名就是引用标识 |
| `skills/<技能名>/SKILL.md` | **用户** | 技能正文。新建目录即新增一个技能，目录名就是技能名 |
| `data/models.json` | 界面 | 模型连接配置（地址、Key、模型名） |
| `data/modes.json` | 界面 | 提示词模式（各类选了哪个提示词 + 偏好模型） |
| `data/tools.json` | 界面 | 工具开关状态 |
| `data/skills.json` | 界面 | 技能开关状态 |
| `data/memory.json` | 界面 / 模型 | 长期记忆条目（模型用 `remember` 写入，界面可管） |
| `<工作目录>/.attachments/` | 程序 | 上传附件的落脚处；同名覆盖，内容就是原文件 |
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
| `work_dir` | 启动时的当前目录 | **文件工具的工作目录，同时是安全边界** |
| `models_path` | `data/models.json` | 模型配置 |
| `modes_path` | `data/modes.json` | 提示词模式 |
| `tools_path` | `data/tools.json` | 工具开关 |
| `skills_dir` | `skills` | 技能目录（一个子目录一个技能） |
| `skills_state_path` | `data/skills.json` | 技能开关 |
| `memory_path` | `data/memory.json` | 长期记忆 |
| `preferences_path` | `data/preferences.json` | 界面偏好 |
| `conversations_dir` | `data/conversations` | 会话目录 |
| `prompt_dir` | `prompt` | 提示词目录 |

所有字段都能用**同名大写环境变量**覆盖（如 `WORK_DIR`、`MODELS_PATH`）。测试时如需重新解析，
调用 `get_settings.cache_clear()`。

> `work_dir` 的默认值取**进程启动时**的当前目录（`default_factory=Path.cwd`，实例化配置时
> 求值一次）。用 `Path(".")` 的话要到每次访问才解析，中途有别处 `chdir` 就会悄悄改变边界 ——
> 而这是个安全边界，不该有这种隐式行为。正式使用建议在 `.env` 里显式配 `WORK_DIR`。

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

四个结构相似的存储类，接口统一：

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
| `SkillStateStore` | `dict[技能名, bool]`（同上） | `data/skills.json` |

另外两个存储类不在这个文件里（它们各有自己的领域逻辑），但共用同一套读取方式：
`MemoryStore`（`memory.py`）、`PreferenceStore`（`preferences.py`）。

**读取统一走 `read_json()`**：文件不存在、内容为空、读取出错、JSON 损坏，一律退回
默认值，而不是把异常抛到界面上。这些文件用户会直接编辑 —— 为了一个手滑写坏的括号
让整个应用打不开，代价太大。

> 损坏的文件会被改名成 `xxx.corrupt` 留档。不能只是忽略：否则下一次保存就把用户
> 原来的数据永久盖掉了。

技能开关没记录过时算「启用」，这条规则由 `skill_enabled()` 统一给出 —— 不要在调用点
自己写 `True`，否则改默认值时必然漏掉某一处。

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
```

### 4.7 技能库 `quill_agent.skills`

```python
from quill_agent.skills import SkillLibrary, split_frontmatter

library = SkillLibrary(settings.skills_dir)
library.ensure_dir()
library.list_names()                  # 全部技能名（= 目录名），按名称排序
library.list_meta()                   # list[SkillMeta]：名字 + 适用场景
library.read("代码审查")               # 正文（不含元信息块），不存在返回 None
library.exists("代码审查")             # 目录和 SKILL.md 都在才算数
library.meta("代码审查")               # SkillMeta，不存在返回 None
```

`split_frontmatter(text)` 把 `SKILL.md` 拆成 `(元信息 dict, 正文)`。它只认单行
`键: 值`；没有元信息块（或有开头没结尾）时返回 `({}, 原文)` —— 宁可少解析，
也不要把正文误当成元信息吃掉。

拼清单的 `build_skill_catalog()` 不在这里，而在 `agent` 层 —— 它要读技能开关，
属于「组装上下文」的职责，和 `build_system_prompt()` 并列。

### 4.8 记忆 `quill_agent.memory`

```python
from quill_agent.memory import MemoryStore

store = MemoryStore(settings.memory_path)
store.list()                        # list[MemoryItem]，新的在前
store.enabled()                     # 只取参与注入的那些
store.add("偏好简洁回答")             # 新增；重复 / 超长 / 超上限会抛 ValueError
store.forget("偏好简洁回答")          # 按原文忘掉；一字不差才对得上，否则抛 ValueError
store.set_enabled(item_id, False)   # 关掉（不等于删除）
store.remove(item_id)               # 删除
store.clear()                       # 清空，返回删掉的条数
```

`MemoryItem` 有四个字段：`id` / `text` / `enabled` / `created_at`。`created_at` 不是装饰 ——
记忆会过时（「我在用 Python 3.10」，升级之后这条就是错的），时间是最基本的判断依据。

`add()` 抛出的 `ValueError` 文案是**直接给模型看的**：`remember` 工具会把它转成文本回给
模型，模型据此决定是换个说法重试、还是提醒用户去清理。

拼清单的 `build_memory_block()` 在 `agent` 层，和 `build_skill_catalog()` 并列。

### 4.9 接口连通性 `quill_agent.core`

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

### 4.10 Agent 循环 `quill_agent.agent`

这是整个业务的入口，也是**二次开发最可能改的地方**。

```python
from quill_agent.agent import Notice, ReasoningDelta, ToolStep, run_agent_stream

for event in run_agent_stream(
    prompt="帮我看看当前目录有什么",   # 本轮用户输入
    files=[],                          # 上传的文件列表
    mode=mode,                          # PromptMode | None
    choice=model_choice,                # ModelChoice | None
    history=[{"role": "user", "content": "..."}],
):
    if isinstance(event, Notice):
        ...        # 系统提示：没选模型 / 调用失败 / 模型空回答。不是模型输出
    elif isinstance(event, ReasoningDelta):
        ...        # 思维链增量（推理模型才有），累积起来展示
    elif isinstance(event, str):
        ...        # 文本增量，直接追加渲染
    else:
        ...        # ToolStep：一次工具调用已完成（name / arguments / result）
```

**为什么是生成器**：一次流式响应里可能**同时**有文本增量和工具调用增量，两者处理方式完全不同 ——
文本可以立刻往外吐（用户看到逐字输出），工具调用只能累积（分片到达，中途是残缺 JSON），
必须等整条流结束才能执行。生成器让调用方按类型分派，UI 想怎么渲染都行。

事件一共四类：**文本增量**（`str`）、**思维链增量**（`ReasoningDelta`）、
**工具调用记录**（`ToolStep`）、**系统提示**（`Notice`）。

`Notice` 和文本增量刻意分开，因为它**不是模型输出** —— 界面要单独标黄渲染，而且不能写进
会话历史：否则「请先选择模型」这类提示会被当成模型说过的话，下一轮又塞回上下文里污染对话。

模型一个字都没说时（推理模型把输出预算全花在思考上时会出现）也会产出一个 `Notice`，
否则界面上就是一个空气泡，用户不知道发生了什么。所有异常同样走 `Notice`，不向上抛。

`ReasoningDelta` 是推理模型的思考过程。DeepSeek 系在 `delta.reasoning_content` 里返回它，
少数中转站叫 `reasoning` —— 两者都用 `getattr` 兜底读取：它不是 OpenAI 的标准字段，
SDK 未必保留，取不到就静默跳过，不影响正文。思考过程按增量产出，界面累积后存进消息的
`reasoning` 字段供折叠回看，但**不进历史** —— 回传它既浪费上下文，也可能干扰后续推理。

> 一个细节：思考过程**不算**「模型回答了」。模型只思考不输出正文时，仍然会触发上面那条
> 空回答提示 —— 用户要的是答案，不是思考过程。

其他可用的函数：

| 函数 | 说明 |
| --- | --- |
| `build_system_prompt(mode)` | 按固定顺序把模式选中的提示词拼成 system prompt |
| `build_user_message(prompt, attachments)` | 拼装运行时上下文（时间 / 工作目录 / 附件路径）+ 本轮问题 |
| `build_history_messages(history)` | 把会话记录还原成 API 消息（含 `tool` 消息），并截断到最近 20 条记录 |

工具轮次上限由 `MAX_ITERATIONS` 控制（默认 5），防止工具调用陷入死循环。
注意它计的是**工具轮次**而不是请求次数：预算用尽后会再发一次**不带工具**的请求，
强制模型基于已有信息收尾 —— 整个循环因此最多请求 `MAX_ITERATIONS + 1` 次。
这样就不会出现「工具已经执行、副作用已经发生，结果却没机会被模型看到」的浪费。
发给模型的历史上限由 `MAX_HISTORY_RECORDS` 控制（默认 20 条记录）。

> `history` 参数收的是**会话记录**（界面口径，可能带 `steps` / `ts`），不是现成的 API 消息 ——
> 还原与截断都由 `build_history_messages` 负责，调用方不需要先清洗字段。

**用量与耗时**由一个 `RunStats` 对象带出来：

```python
stats = RunStats()
for event in run_agent_stream(..., stats=stats):
    ...
print(stats.total_tokens, stats.elapsed)
```

生成器没法「返回」值，而这两项都要等流结束才知道，所以只能由调用方创建、由
`run_agent_stream` 就地填充。`elapsed` 在任何结束路径上都会补上（内部用 `try/finally`
包了一层）。用量依赖接口支持 `stream_options`：少数网关不认这个参数，第一次失败后
会自动退回普通请求并**记住结果**，不会每轮都白试一次。

**附件先落盘，再把路径告诉模型。** 上传的文件保存在 `<工作目录>/.attachments/` 下，
user 消息里给出的是路径 —— 模型用现成的 `read_file` 就能读。落盘而不是把内容直接塞进
消息，是为了不破坏「文件工具只能访问工作目录」这条唯一的安全边界；二进制文件（PDF、
图片）也能先存下来，将来接上解析工具就能直接读。

### 4.11 工具层 `quill_agent.tools`

```python
from quill_agent.tools import registry, ToolSpec

registry.all()                          # list[ToolSpec]，已合并持久化的开关状态
registry.search(keyword="", category="") # 名称模糊 + 分类精确
registry.categories()                    # 现有分类
registry.set_enabled("list_dir", False)  # 持久化开关
registry.schemas()                       # 转成 API 的 tools 参数（只含已启用的）
registry.execute("read_file", '{"path": "README.md"}')   # 执行，异常转文本
```

### 4.12 二次开发：三个常见场景

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
├── skills.json           # 技能开关
├── memory.json           # 长期记忆条目
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

**`skills.json`** — 对象，键是技能名

```json
{ "代码审查": true, "提交信息": false }
```

同样只存开关。技能的「定义」（元信息 + 正文）全在 `skills/` 目录里，这里只记用户有没有
启用它。技能目录被删掉后这里会留下孤儿键 —— 无害，将来重建同名技能时开关还能接上。

**`memory.json`** — 数组，每个元素是一条记忆

```json
[
  {
    "id": "3912a8f0c4d94d1e8f7b6a5c4d3e2f10",
    "text": "偏好简洁回答，不要啰嗦的总结",
    "enabled": true,
    "created_at": "2026-09-18T14:30:00"
  }
]
```

这里**没有**单独的开关文件：记忆条目本来就是程序（`remember` 工具）写进来的，不是用户
手写的文件，所以 `enabled` 直接内联在条目里，改一条就是改一处。这一点和技能不同 ——
技能的定义在文件系统里，程序不该去改用户写的文件，开关才单独放一份。

`created_at` 用来判断记忆是否过时；界面按它显示记录时间。单条数据坏掉只跳过那一条，
其余照常读出。

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
{"role": "assistant", "content": "你好！有什么可以帮你的？", "ts": "2026-09-18T04:33:35", "steps": [], "stats": {"prompt_tokens": 812, "completion_tokens": 12, "total_tokens": 824, "elapsed": 2.4}}
{"role": "assistant", "content": "", "ts": "2026-09-18T04:34:02", "steps": [], "notices": ["模型没有返回任何内容。可以重试，或换一个模型 —— 推理模型有时会把输出预算全用在思考上。"]}
{"role": "user", "content": "读一下附件", "ts": "2026-09-18T04:35:10", "files": ["quill-attachment.txt"]}
```

**会话 id 就是文件名**（`时间戳-随机后缀`），所以按文件名排序即按时间排序，不需要额外的索引文件。

`steps` 里是这一轮的工具调用。界面用它做折叠展示；发给模型时由 `agent.build_history_messages`
还原成 `assistant.tool_calls` + `tool` 消息，而不是被丢掉。每条 step 另带一个 `elapsed`
（这次调用花了多久），回看时能看出是哪一步慢。

`stats` 是这一轮的用量与耗时，只用于界面展示（消息下方的 `⏱ 2.4s · 824 tokens`），
组历史时同样不带 —— 它在发给模型的消息里没有任何意义。

`notices` 是系统提示（没选模型、调用失败、模型空回答）。界面把它们标黄渲染；这类记录的
`content` 是空的，组历史时会被 `build_history_messages` 跳过 —— 提示不会污染上下文。

`reasoning` 是推理模型的思维链全文，只用于折叠回看，同样不进历史。

`files` 只出现在**带附件的用户消息**上，记的是文件名而不是内容 —— 附件已经落盘到工作目录的
`.attachments/` 下，模型拿到的是那里的路径（由 `build_user_message` 写进消息正文）。这个字段
纯粹是给界面回看的：不记的话，界面上完全看不出这条消息带过附件，**而模型明明看得见**，两边
对不上。它也不进历史（组历史只认 `role` / `content` / `steps`）。

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

## 6. 界面层：Streamlit（旧版）

> 下面是早期 Streamlit 界面的实现记录。里面不少坑（尤其是 6.3、6.4）恰好解释了
> **为什么后来要换 Vue**，所以原样留着。

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

### 6.5 输出区自动跟随

`st.container(height=...)` **不会**因为内容变长而自动滚动。而对话恰恰全是「新内容长在底部」，
所以不做处理的话，用户发完消息什么也看不到 —— 思考过程和回答都在下面看不见的地方。

方案是注入一段脚本（`app/main.py` 的 `AUTO_SCROLL_HTML`）。三个要点各对应一个踩过的坑：

| 坑 | 处理 |
| --- | --- |
| `st.markdown(unsafe_allow_html=True)` 会把 `<script>` 过滤掉 | 改用 `components.html`：它的 iframe 与原页面同源，能拿到父页面的 `document` |
| `components.html` 在重跑时被 React 复用，脚本**只在首次打开页面时执行过一次**，重跑后不会再对齐到底部 | 往 HTML 里塞一个时间戳注释，让每次重跑的内容都不同，iframe 才会真正重新加载 |
| 无脑跟随会打断「用户往上翻看历史」 | 只在距底部 40px 以内时跟随；用户上翻即停止，自己滚回底部后自动恢复 |

脚本里用的 `.st-key-task_output` 与 6.4 是同一类**非公开接口**，升级 Streamlit 后需要回归验证。

---

## 7. 界面层：Vue（新）

### 7.1 为什么换掉 Streamlit

不是审美问题，是性能：

| Streamlit 的做法 | 后果 |
| --- | --- |
| 每次交互**重跑整个脚本** | 打字时每个字符都要往返一次；页面一复杂就卡 |
| 状态存在服务端 `session_state` | 输入框、选择器全靠 workaround 兜（见 6.3 那张坑表） |
| 高度布局要覆盖内部 CSS / JS | 依赖非公开接口，Streamlit 一升级就坏 |

换成 Vue 之后前两条从根上消失：**状态在浏览器内存里，改一个字段只重渲染用到它的
组件**。打字时零网络请求，草稿直接存 `localStorage`（比写回服务端还快）。

### 7.2 结构

后端在 `server/`，按「前端一个页面 ≈ 一个路由模块」拆分 —— 界面怎么用，接口就怎么给，
而不是照抄业务层的模块划分。前端 `web/src/` 四个目录：`api`（接口）、`stores`（共享状态）、
`components`、`views`（与路由一一对应）。

**两套界面共用同一份数据**：会话文件、模型配置、技能、记忆都在 `data/` 和 `skills/`，
Streamlit 版和 Vue 版读写的完全是同一批东西，可以对照着用。

### 7.3 三个值得留意的实现点

**为什么用 SSE 而不是 WebSocket**：推送是方向单一的（服务端把过程吐给前端），前端没有
东西要实时回传。SSE 就是普通 HTTP 响应，浏览器原生支持、`curl` 就能调试 —— 为这点需求
上 WebSocket，多出来的复杂度不划算。

**SSE 解析必须处理「半截 JSON」**：网络分块不会正好切在事件边界上。所以要拿 buffer 攒着、
只解析完整的事件块（`api/chat.ts`），直接对每次 `read()` 的结果 parse，迟早会抛错。

**`markdown-it` 关掉了内联 HTML**（`html: false`）：模型输出、读到的文件内容都不可信，
允许内联 HTML 等于把 XSS 的口子开在聊天框里 —— 而 agent 恰恰会把外部内容一路带进消息流。

### 7.4 组件库：Element Plus（按需引入）

界面用 Element Plus，并且是**按需引入** —— 只有真正用到的组件和样式会进产物，
比全量引入小一个数量级。配置在 `vite.config.ts`，两个插件分工不同，缺一不可：

| 插件 | 负责 |
| --- | --- |
| `unplugin-auto-import` | 函数式 API（`ElMessage`、`ElMessageBox`） |
| `unplugin-vue-components` | 模板里的 `<el-xxx>` 标签 |

它们会在项目根生成 `auto-imports.d.ts` 和 `components.d.ts` 两个声明文件，
**这两个文件要一起提交** —— 否则在没跑过 dev server 的环境里，类型检查会因为
找不到 `el-button`、`ElMessage` 而报一堆错。

> 代价要心里有数：代码里看不到 `ElMessage` 的 import，读的时候得知道它从哪来。
> 这是为了产物体积换来的。

### 7.5 启动

```bash
make api    # 后端 8000
make web    # 前端 5173（Vite 把 /api 代理到 8000，所以浏览器看到的是同源请求）
```

生产部署时把前端 `npm run build` 出静态文件、由 FastAPI 一起托管即可，那时不需要代理。

### 7.6 输入区：两个设计点

**功能选择区贴在输入框上方**，而不是页面顶部。模式、模型、工作目录、附件是「组成这一轮
请求的东西」，和输入框放在一起，改完就发，不必在两个地方之间来回看。

**发送按钮内嵌在输入框右下角**。`el-input` 没有放按钮的插槽，所以是绝对定位 + 给
`textarea` 留一块底部 padding：

```css
.box :deep(.el-textarea__inner) {
  padding: 8px 12px 42px; /* 第 4 行的高度正好让按钮待着，不压住第三行文字 */
}
.send {
  position: absolute;
  right: 8px;
  bottom: 8px;
}
```

只写绝对定位会压住第三行文字 —— **多出来的那段 padding 才是关键**。

### 7.7 附件与工作目录

两者都是「后端本来就有、Vue 版漏接」的功能（业务层的 `save_attachments` / `set_work_dir`
一直都在，Streamlit 版也接了）。补的时候业务层一行没改。

**附件走 multipart，而不是拆成两个接口。** 先传文件拿路径、再带路径发消息也能做，但那样
「文件属于哪一轮」就得靠额外状态去维系；一轮请求带齐，语义最清楚。

后端为此加了一个适配壳（`server/routes/chat.py` 的 `Attachment`）：`save_attachments()`
是按鸭子类型写的（有 `name`、能 `read()` 就行），补个同样形状的壳就能复用 ——
**两个界面因此共用同一份附件落盘逻辑**。

> 壳里包的是 `UploadFile.file`（底层同步文件对象）而不是 `UploadFile.read()`：
> 后者是 async，而 chat 端点是同步 `def`（跑在线程池里），没有 await 可用。

**工作目录选择器刻意做成两步**：点目录只是「进去看看」，要真正切换得再点一下「用这个目录」。
它同时是文件工具的安全边界，误点一下就换掉边界的代价太大。

路径显示**直接折行，不用省略号**。从左省略得靠 `direction: rtl`，而 RTL 上下文里行首标点
算「行尾」—— 显示出来的路径会把开头的 `/` 挪到末尾，不再是能照着输入的正确路径。

**选目录的方式按环境自动切换**（业务层的 `system_dir_picker_command()` 一直在做这件事，
`GET /workdir` 把它探测的结果一并返回给界面）：

| 环境 | 点工作目录按钮的结果 |
| --- | --- |
| 本机跑（终端里有图形会话） | 直接拉**系统目录对话框** —— macOS 用 `osascript`、Windows 用 PowerShell、Linux 用 `zenity` |
| 云端部署 | **浏览器里的目录浏览**（服务器上弹的窗用户根本看不见）|

> 拉系统对话框是**阻塞调用**，后端会一直等到用户关掉窗口（`POST /workdir/pick`），
> 界面得为此显示等待状态。

**只有空会话能换工作目录**（前后端都拦）。工作目录是文件工具的安全边界，聊到一半再换，
历史里还留着旧目录下文件的内容，而新目录里的同名文件完全是另一个东西 —— 模型会拿着旧
内容当新文件的答案，这种错极难从对话里看出来。
后端为此刻意要界面把 `conversation_id` 传上来：服务是无状态的，它自己不知道当前在聊哪个会话。

### 7.8 一个 CSS 坑：grid item 的 `min-height`

任务页曾经整块撑破视口、输入框凭空消失，根因就一行：

```css
.main {
  min-height: 0; /* 少了它，主区高度会变成 1362px（视口只有 720px） */
}
```

`.main` 是 grid item，而 **flex / grid item 的 `min-height` 默认是 `auto` 而不是 `0`**。
`auto` 对它们意味着「不得小于内容的最小尺寸」，所以是**内容撑破容器**、而不是容器约束内容。
外层 `.shell` 又是 `overflow: hidden`，超出的部分被直接裁掉，连滚动条都没有。

同项目里 `.page` 从来没出过这个问题，因为它有 `overflow-y: auto` ——
**`overflow` 不为 `visible` 时，`min-height: auto` 会自动解析成 `0`**。

记住这条规则：*容器高度由外部决定、而内部需要滚动或裁剪时，必须显式写 `min-height: 0`
（纵向）或 `min-width: 0`（横向）。*

### 7.9 顶栏：用 Teleport 收拢页面标题

各页的标题和说明原本写在自己页面内部（`.page-head`），会跟着内容一起滚走。现在统一渲染在
顶栏里，位置固定。

**实现是 `<Teleport to="#page-head-slot">`。** 标题和提示天然属于页面 —— 只有页面自己知道
要说什么（记忆页的提示还带动态的「当前 3 / 5 条启用」）—— 但它们得渲染在滚动区之外。
Teleport 正是为这种「逻辑上属于 A、DOM 上要放进 B」的场景准备的。

顶栏内的样式写在 `App.vue` 的**非 scoped** `<style>` 里：Teleport 过去的节点，其 `data-v`
属性仍属于各自的页面组件，scoped 规则选不中它们。这顺带定下了分工 —— **顶栏长什么样由顶栏
决定**，页面只管按 `<h1>` 和 `.hint` 两个约定写内容，不必六个页面各写一遍边距和字号。

两个配套细节：

- 顶栏和侧边栏品牌区共用 `--topbar-height`（60px）。两者在视觉上是**同一条横带**，
  各写一个数迟早会错位 —— 差 4px 都看得出来。
- 标题搬走之后，工具页/归档页那行筛选控件要把 `margin-left: auto` 去掉，否则会被推到
  最右侧、左边留一大片空白。

> 顶栏的目标元素不能用 `v-if` 包住：Teleport 在子组件挂载时就要求目标已存在。

### 7.10 侧边栏：「新建任务」是个动作，不是页面

原来导航里有一项「任务」，点它只是切回任务页。但真实用起来不会这样操作：想继续聊就直接点
侧边栏的会话，想开新的才会想到「新建」——「切回任务页」这个动作本身是多余的。

所以：

- 导航里的「任务」换成了顶部的**「新建任务」主色按钮**（按下去 = 起一个新会话 + 跳回任务页）
- 会话列表标题行上的 `＋` **删掉** —— 它和上面那个按钮是同一个动作，两个入口只会让人犹豫该点哪个

按钮做成一整块主色，是为了让它和下面那排平级的导航项**看起来不一样**：它承载的是这个应用
最常用的动作，不该长成一个普通的页面入口。

> 顺带修了个小毛病：连点几下「新建任务」不会再堆出一串空会话 —— 当前已经是空会话时直接复用
> （空会话标题都一样，清理起来很烦）。

### 7.11 品牌素材与主题

**素材来源与放置**：设计稿在 `quill-source/`（图标 SVG / icns / 配色图 / 导出脚本）。
它**不参与构建**，用到的东西复制一份到 `web/` 里：

| 素材 | 去处 | 用在哪 |
| --- | --- | --- |
| `quill-icon-02c.svg`（小尺寸简化版） | `web/src/assets/quill-icon.svg` | 侧边栏 LOGO |
| 同上 | `web/public/icon.svg` | favicon（矢量，各尺寸都清晰）|
| `quill-icon-02a.svg`（1024 详细版） | `web/src/assets/quill-icon-large.svg` | 设置页「关于」区块 |
| `png/Quill_256.png` / `Quill_32.png` | `web/public/` | apple-touch-icon / 老浏览器兜底 |

> 两个版本的区别是**细节量**：02c 去掉了羽毛上的细枝，缩小到 16px 还看得清轮廓；
> 02a 保留全部细节，只在放得大的地方用。

**配色**取自 `color.jpg` 与图标 SVG：深海军蓝 `#1B3A5C`、中蓝 `#3A6588`、
赭金 `#C8933F`、米白 `#F5F0E6`、浅米 `#E0CCAF`、灰蓝 `#535762`。
两套主题都建立在它们之上，整体走「纸感」——浅色像米白纸张，深色像靛蓝夜色。
色值集中在 `web/src/style.css` 顶部的 `:root` 里，**组件里不写死任何颜色**。

**主题实现**：往 `<html>` 挂 `class="dark"`，颜色全靠 CSS 变量切换。组件里因此
没有一处「如果是深色就换个色」的判断 —— 加第三套主题只需要补一组变量。

三个值得留意的点：

- **`--on-accent`**：压在主色上的文字色。浅色模式主色是深蓝、配米白字；深色模式
  主色换成亮蓝，文字反而要用深色 —— 两个值必须分开定义，写死 `#fff` 会在其中一套里糊掉。
- **深色模式要换主色**：深海军蓝在深底上对比度不足，按钮会「糊」进背景，所以深色下
  主色改用亮蓝 `#5B9BD5`，金色也提亮一档。
- **防白闪**：主题必须在样式表生效**之前**就挂到 `<html>` 上，这段逻辑内联在
  `index.html` 里 —— 外链模块要等下载解析完才执行，那时深色用户已经看过一闪的白屏了。

**Element Plus 的变量被接到了我们的语义色上**（`style.css` 里那段以
`:root, html.dark` 开头的规则）。表格、输入框、下拉、开关这些不用逐个写样式就能
融入主题。同时引入了 EP 官方的 `dark/css-vars.css` —— 必须在 `style.css` **之前**，
后者会用同一批选择器覆盖它。

主题选择存在 `localStorage` 的 `quill:theme`，三档：浅色 / 深色 / 跟随系统（默认）。
选「跟随系统」时会监听 `prefers-color-scheme` 的变化实时切换。

---

## 许可

[MIT](LICENSE) © 2026 zhaoyifan

可自由使用、修改、分发（含商业用途），只需保留版权声明。详见 [LICENSE](LICENSE)。
