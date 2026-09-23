# quill-agent 架构说明

> **三份文档的分工**
>
> - [`README.md`](./README.md)：给要用它的人 —— 怎么跑起来、界面怎么走、够用的原理。
> - [`README-developer.md`](./README-developer.md)：给要改它的人 —— 按「目录 / 模块」**纵向**
>   组织：这里有什么文件、每个模块提供什么接口、怎么用。
> - 本文：按「功能」与「技术机制」**横向**贯通 —— 一个能力怎样从界面一路贯穿到存储；
>   一个机制到底解决了什么问题、代价是什么、和谁耦合。
>
> 写法上，功能切面每节回答四件事：**是什么 → 怎么实现 → 和谁协作 → 关键取舍**；
> 技术切面每节回答三件事：**解决什么问题 → 原理 → 边界与代价**。

---

# 第一部分 · 功能切面

## 1. 一次对话轮次

**是什么**：用户发一句话，模型思考、调工具、给出回答，界面实时显示全过程。

**实现链路**（这条链是后面所有功能的骨架）：

```
浏览器 → POST /api/chat（multipart：文本 + 附件）
   server/routes/chat.py: chat()
     ├─ 建 Run（内含 Interaction 通道，绑到事件循环）
     ├─ _open(run) 登记进 _runs 表
     └─ 起 _pump 线程跑 stream_round(生成器)
              ↓ 逐事件 publish 进 asyncio.Queue
   _sse(run)  ← 事件循环里 await 队列，翻译成 SSE 文本
              ↓ text/event-stream
   浏览器 web/src/api/chat.ts: streamChat() 手写解析
              ↓ ChatEvent
   stores/session.ts: sendMessage() 更新界面状态
```

**为什么绕这么大一圈**（不让 `StreamingResponse` 直接迭代生成器）：

1. agent 循环是**同步生成器**。它在等用户确认时连 `yield` 都做不到 —— 问题根本发不出去。
   拆出队列后，`interaction.ask()` 能绕过被阻塞的生成器直接往队列塞事件。
2. 一轮运行此前「只活在生成器里」，外界没有句柄。有了 `_runs` 表，才能凭 `run_id`
   把答案（`/chat/answer`）或停止信号（`/chat/cancel`）送进那一轮。

**关联模块**：`agent.run_agent_stream`（业务核心）、`interaction`（旁路）、`store`/`history`（落盘）、
`preferences`（模型与模式）、前端 `session.ts`（状态）。

**关键取舍**：多了一层队列和两个线程，换来「工具能阻塞着提问」与「运行可被外部寻址」。
代价是所有跨线程操作都必须小心（见技术切面 §2 线程模型）。

---

## 2. 工具调用

**是什么**：模型请求执行某个工具，项目执行并把结果回灌给模型，循环往复直到它不再请求。

**实现链路**：

1. `registry.schemas(only=context.tools)` 把工具组名单转成 API 的 `tools` 参数下发。
2. 流式响应里 `delta.tool_calls` 按 `index` 累积成完整调用（分片到达，中途是残缺 JSON，
   必须等流结束才能执行）—— 这就是「**双轨解析**」：文本立刻外吐，工具调用只累积。
3. 一批调用按 `index` 排序**逐个执行**；每个执行**之前**先 `yield ToolStart`（只带名字），
   执行完 `yield ToolStep`（带结果与耗时）。
4. 结果 `_clip(result, MAX_TOOL_RESULT_CHARS=2000)` 后作为 `role="tool"` 消息回灌，
   进入下一轮请求。

**工具的样子**：`@registry.tool(description=..., category=..., parameters={...})` 装饰一个普通函数，
**工具名直接取函数名**（避免两处名字对不上）。目前 27 个，分布在 `tools/` 下七个模块
（文件 10 / 执行 5 / 联网 2 / 规划 1 / 子代理 1 / 技能 3 / 记忆 2 / 交互 2 / 对话 1）。

**关联模块**：`tools/base.py`（注册表）、各工具模块、`agent._execute`（确认拦截）、
`ModeContext.tools`（哪些工具可用）。

**关键取舍**：

- **工具结果回灌时截断**（2000 字符）：完整结果已经通过 `ToolStep` 交给界面了，
  回灌给模型的那份不需要全文 —— 否则读一个大文件当场把上下文撑大几倍。
- **注册表不设全局开关**：一个工具给不给模型，完全由模式引用哪个工具组决定。
  注册表只负责「有哪些」。

---

## 3. 人在环：确认、提问、计划审批

**是什么**：三种「模型必须等用户点头才能继续」的场景。

| 场景 | 谁来触发 | 机制 | 拿不到答案时 |
|---|---|---|---|
| **工具确认** | 模式配置的工具组 `confirm` 名单 | `agent._execute` 里拦 | 按**拒绝**处理 |
| **工具自己要求** | `run_command` 认出灾难性命令（`rm -rf /`、fork 炸弹…） | 工具内直接调 `interaction.confirm` | 按**拒绝**处理 |
| **主动提问** | 模型调 `ask_user` | `interaction.ask(kind="ask")` | 返回「问不到」 |
| **计划审批** | 模型调 `submit_plan` | `interaction.ask(kind="plan")` | 按**未批准**处理 |

**这四种走的是同一条路**：`Interaction.ask()` 建一个问题（`Question`，带 `id`/`text`/`detail`/`options`），
publish 到队列推给前端，然后**在 runner 线程里阻塞**等 `threading.Event`；
用户点按钮后 `POST /chat/answer` 把答案写回，事件被唤醒。

**为什么这条通道必须独立于生成器**：agent 循环是同步生成器，被阻塞时连 `yield` 都做不到。
详情见技术切面 §3。

**两级确认是叠加的**：agent 层的拦截看的是「这个工具要不要盯」（模式配置），
工具自己的判断看的是「这次调用危不危险」。两者独立，都会生效。

**关联模块**：`interaction.py`、`agent._execute` / `_preview`、`tools/shell.py`（危险命令）、
`tools/builtin.py`（提问/计划）、`server/routes/chat.py`（`/chat/answer`）、前端确认卡片。

---

## 4. 任务清单（todo）

**是什么**：模型把「现在到哪一步了」摊开给用户看。

**实现**：`todo_write` 每次提交**完整清单**（不是增量）—— 幂等，避免坐标漂移。
`TodoBoard` 是这一轮运行里的可变清单板，和 `RunStats` 同属「生成器不能返回值，
所以由调用方建好传进去就地改」的那类对象。

提交顺序刻意是「**先落盘、再播报**」：`board.replace()` 决定落盘内容，
`_publish()` 决定界面实时进度。

**三处清除时机**（各有理由）：跑完（`done`）清、换会话清、开始下一轮清；
**出错或被取消时刻意不清** —— 那时用户最想知道的正是「它卡在哪一步」。

**关联模块**：`tools/todo.py`、`RunEnvironment.todos`、`agent`（事件流）、前端 `liveTodos` + `TodoList`。
子代理**不能**用 `todo_write`（`SUBAGENT_EXCLUDED`）—— 它的清单没地方显示，
留着只会白花 token 还可能和父级的进度打架。

---

## 5. 子代理

**是什么**：派一个独立代理去干一件事，只把结论带回来。

**为什么存在**：主代理上下文有限。让它自己翻遍代码库，那一堆中间过程会**永久占住上下文**，
之后的对话还得在这个被撑大的窗口里继续。子代理有自己的一份上下文，**用完就扔**。

**实现**：

- 延迟 import `quill_agent.agent`（模块级互导会拿到只加载一半的模块）。
- 从 `RunEnvironment` 取父级的 `ModeContext`，**照搬**提示词 / 技能 / 记忆开关 / 确认名单，
  只把 `SUBAGENT_EXCLUDED`（`spawn_agents`/`ask_user`/`submit_plan`/`todo_write`）剔掉。
- `history=[]` —— 干净上下文就是它存在的全部意义。
- `BRIEF` 交代处境：看不到发起方的对话、遇到不确定按最合理假设继续、结论必须自包含。
- 用量 `environment.stats.merge(nested_stats)` 并回父级账上（否则花了钱不出现在用量页）。
- **只允许一层**（`_depth` ContextVar）：递归派下去成本是乘的、排查是难的。

**事件透传**：子代理的中间过程不进父级消息，而是借同一条交互通道**单向播出去**，
前端用一块「子代理正在工作」的看板显示。播报策略：`ToolStart`（执行前）+ 每 30 秒一句心跳
（`_HEARTBEAT_SECONDS`）。

**关联模块**：`tools/subagent.py`、`agent.RunEnvironment`、`interaction`（播报）、前端 `subagentEvents`。

---

## 6. 技能

**是什么**：把「这类任务怎么做」存成文档，模型判断需要时才读取。

**存在形式**：`skills/<技能名>/SKILL.md`，frontmatter 里只有 `description`，其余是正文。

**按需加载的实现**（这是技能设计的核心）：

- 平时**只把「名字 + 一句话 description」**拼进一条独立 system 消息（`build_skill_catalog`，
  描述截断到 120 字符）。
- 模型判断任务匹配时，用 `read_skill` 把正文取回来。

**为什么正文不进 system 提示词**：技能正文动辄上千字，全量注入会拖累**每一轮**无关对话；
清单很短，用不到的技能一个 token 都不花。

**关联模块**：`skills.py`（`SkillLibrary`）、`agent.build_skill_catalog`、`tools/builtin.py`（read/create/delete）、
`resolve_mode`（给了技能就**强制**带上 `read_skill`，否则模型会去调一个不存在的工具）。

---

## 7. 记忆

**是什么**：跨会话保留「关于用户的既定事实」。

**实现**：一个 JSON 数组文件；每条 `MemoryItem` 有 `id`/`text`/`enabled`/`created_at`。
`enabled=False` 表示**关掉但不删**（这两件事不一样）。上限 50 条、单条 200 字，写入时归一化 + 去重。

**注入方式**：`build_memory_block()` 拼成「关于用户的已知信息…」清单，**单独占一条 system 消息**。

**和技能的机制高度相似**：都存成数据、都由模式决定给不给、都作为清单进上下文、用不到不花 token。
区别在分工：**技能管「这类任务怎么做」，记忆管「关于用户的既定事实」**。

**刻意不做自动抽取**：自动抽取要额外调一次模型、而且用户看不见它在记什么。
改由模型显式 `remember` 写入。

**关联模块**：`memory.py`、`agent.build_memory_block`、`tools/builtin.py`、`ModeContext.memory_enabled`。

---

## 8. 提示词

**是什么**：可组合的 system 提示词片段库。

**存在形式**：`prompt/<8位hex id>.md`，**文件名是稳定 id**，名字与分类写在 frontmatter 里。
（标识用 id 而不是名字，是为了让改名不动引用。）

**六类与顺序**：`身份 → 能力 → 工具策略 → 工作流程 → 输出规范 → 约束`。

**顺序为什么重要**：它既是界面展示顺序，也是拼进 system prompt 的顺序，而且**必须固定** ——
同一套配置拼出来的前缀完全一致，**前缀缓存才有意义**。所以拼接顺序取决于「分类顺序 + 类别内名字」，
**与用户勾选顺序无关**。

**关联模块**：`prompts.py`、`agent.build_system_prompt`、`store.PromptGroupStore`（组合成组）、
`Mode.prompt_group_id`。

---

## 9. 模式（Mode）

**是什么**：Agent 的一套完整配置 —— 把四类资源打包成一个可切换的「工作方式」。

**字段**：`prompt_group_id` / `tool_group_id` / `skill_group_id`（空串 = 这类什么都不给）/
`memory_enabled` / `preferred_model`。

**核心机制是「引用组」而不是「内联成员」**：模式只记「引用哪个组」，
`resolve_mode(mode)` 负责把引用解析成这一轮实际要用的清单：

```
Mode.prompt_group_id → PromptGroupStore → prompts（id 列表）
Mode.tool_group_id   → ToolGroupStore   → tools + confirm（confirm 只保留在 tools 里的）
Mode.skill_group_id  → SkillGroupStore  → skills（非空则强制加 read_skill）
                     → ModeContext（frozen，供组装直接消费）
```

**为什么模式要按会话记忆**：模式是「**这个任务在用哪套配置聊**」，它属于任务。
原先存在全局偏好里，切任务不会跟着变。现在按会话记（`mode::<会话id>`），
并且**刻意不做全局回落** —— 否则「在 A 选模式、切到 B 还是 A」的问题会原样复现。

**关联模块**：`models.Mode`、`store.ModeStore`、`agent.resolve_mode`、`preferences`（按会话记）、前端模式选择器。

---

## 10. 联网搜索

**是什么**：两个工具，一个搜、一个读。

**分工**（照 Claude Code 的 WebSearch / WebFetch）：

- `web_search` 只给**标题 / URL / 摘要**（搜索接口自带，便宜），让模型挑候选。
- `web_fetch` 按需读正文，**并且必须带 `prompt`**（要它回答什么问题）。

**`prompt` 必填是设计支点**：一旦允许省略，模型就会习惯「先抓来再说」，上下文很快被撑爆。

**网页摘要**：用**当前运行的同一个模型**带问题压缩，输入上限 24000 字符；
失败则**降级返回原文节选**并标注「未能压缩」—— 摘要失败不该让「读网页」整体失败。

**关联模块**：`search.py`（后端抽象与网络）、`tools/web.py`（工具外壳与摘要）、
`environment.stats`（摘要用量并入本轮账）。

---

## 11. 附件与工作目录

**是什么**：用户上传的文件进了哪里、模型怎么看到它。

**工作目录**：优先取界面偏好里选过的目录（**每次都校验它还在不在**，被删就自动退回默认），
否则用 `settings.work_dir`。换目录限制在**空会话**才能做（业务层规则）。

**附件**：`save_attachments()` 存进 `<工作目录>/.attachments/`，文件名只取基名防路径注入。

**关键分工**：

| 类型 | 怎么给模型 | 为什么 |
|---|---|---|
| **图片** | 走请求体的 `content` 块（`data:image/...;base64`） | 工具契约是「返回一段文本」，图像没法变文本，只能随消息给视觉能力 |
| **文本** | 只给**落盘后的路径** | 模型已有 `read_file` / `search_content`，不必另造一套「附件工具」语义 |

**没有图片时 `image_content` 原样返回字符串** —— 保住请求体一字不差，不影响前缀缓存。

**关联模块**：`tools/files.py`、`agent.image_content` / `build_user_message`、`PathGuard`（路径边界）。

---

## 12. 沙箱

**是什么**：把命令的权限边界交给内核，而不是靠命令行黑名单。

**三档**：`off`（不隔离）/ `read-only`（全盘只读）/ `workspace-write`（只有工作目录可写）。
另有 `sandbox_network` 控制能否联网（默认断网）。

**机制**：按平台选后端，都不用 root。

- **Linux — bubblewrap**。参数序列的关键顺序是「先 `--ro-bind / /` 整个文件系统只读 →
  再用 `--tmpfs` 盖住 `.ssh` 等敏感目录 → 最后才 `--bind work_dir work_dir`」。
- **macOS — Seatbelt（`sandbox-exec`）**。一样是「先 `(deny default)`，再逐条开口子」。
  有一条规则语义必须记住：**后写的覆盖先写的** —— 「读盘放开、再关掉 `~/.ssh`」只能
  按这个顺序写，反过来等于没关。

**两个后端的语义差异是有意保留的**，都写在 `sandbox.py` 的注释里：bwrap 能给沙箱一个
干净空的 `/tmp`，Seatbelt 只能「允许 / 拒绝某个路径」，做不到「换一个」。所以 macOS 那边
是**放行** `$TMPDIR` 和 `/tmp` 而不是隔离它们 —— 不这么做 python 会直接报「没有可用的
临时目录」。**假装两边一样，比承认差异更糟**。

**两个后端都带探针**：`bwrap` 在 PATH 里不等于跑得起来（AppArmor 会挡 user namespace），
`sandbox-exec` 躺着也不等于这套 profile 语法在当前系统版本上还认（它被 Apple 长期标为
deprecated）。**装了 ≠ 能用**，所以探一次、把失败翻译成人话。

**没有后端时会怎样**：**拒绝执行，绝不裸跑**。配置了沙箱却悄悄降级成无沙箱，
比根本没配更危险。Windows 上 `startup_note()` 会明说这一点。

**所以默认档位是按平台给的**（`config._default_sandbox_mode`）：Linux / macOS 默认
`workspace-write`（两个后端都真能拦），Windows 默认 `off`。原因是一句话 ——
**默认值对所有人生效，而"配了沙箱却没后端"在这里等于拒绝执行**；全局翻会让没配过沙箱的
Windows 用户每条命令都失败。

**`off` 模式的定位**：默认值，保持既有行为；此时 `run_command` 里那套危险命令识别
（`CATASTROPHIC_PATTERNS`）**只是防手滑，不是安全边界**。

**关联模块**：`sandbox.py`、`tools/shell.py`（唯一消费者）、`config.Settings.sandbox_mode`、
`server/main.py`（启动时说清状态）。

---

## 13. 上下文管理

**是什么**：让长对话既跑得下去、又不把钱烧光。三件事：**截断、摘要、预算**。

**三条线**：

| 机制 | 触发 | 作用 |
|---|---|---|
| **截断** | 组装历史时 | 按 token 预算（`HISTORY_BUDGET_RATIO=0.5`）或条数（`MAX_HISTORY_RECORDS=20`）取舍 |
| **摘要** | 预测会超预算 | 把早期历史压成一段（`SUMMARY_MAX_CHARS=1200`），保留最近 8 条原文 |
| **预算闸门** | 每轮请求前后 | 轮次上限（兜底）+ token 上限（主闸门）+ 八成预警 |

**摘要的几个关键决定**：

- **用同一把尺子**：`_split_for_summary` 切分时用的就是截断的标准 —— 「截断留不住的就是该压的」。
- **保留最近 8 条原文**：摘要负责很久以前，**原文负责正在进行** —— 模型下一步要动的就是那个文件，
  而摘要里的路径未必还准。
- **失败不抛异常**：压缩是锦上添花，失败照旧按预算截断。
- **原文一条都没删**：截断只影响「发给模型多少」，`recall_history` 仍能检索全文。

**关联模块**：`agent`（`_compact_if_needed` / `build_history_messages` / 预算检查）、
`SummaryMade`（落盘 + 界面卡片）、`RunEnvironment.history`（全量，给检索）。

---

## 14. 会话管理

**是什么**：任务的创建、切换、归档、导出、删除。

**存储**：`data/conversations/active/` 与 `archived/`，**一个会话一个 JSONL 文件**，
文件名即会话 id（`20260921-153509-d238`），按名排序即按时间排序。

**归档而不是删除**：空会话直接删（不留垃圾），非空则移动到 `archived/` 并**显式打归档时刻**
（`os.utime` —— `replace` 只改名不改 mtime）。

**导出**：会话是产出物，得拿得走 —— 支持 Markdown / JSON。

**关联模块**：`history.ConversationStore`、`server/routes/conversations.py`、
`preferences`（归档/删除时顺手清草稿键与模式键）。

---

## 15. 模型接入

**是什么**：把不同服务商的模型接进同一个对话循环。

**两条协议**：`openai`（OpenAI 兼容，含 DeepSeek 等）与 `ollama`。两者都走 OpenAI SDK，
差别在地址解析与能力探测。

**`model_choice_key` 的格式是 `"{config_id}::{model}"`**：用稳定标识而不是列表下标 ——
增删连接/模型时下标会漂移，这个 key 永远指向同一对象。任务页的模型选择和模式的
`preferred_model` 共用同一口径，可以直接互相赋值。

**上下文窗口从哪来**：优先问服务端的 `/models`；问不出来就查本地快照
（`data/model_catalog.json`，从 models.dev 拉的**规格快照**，刻意不内置写死表 ——
规格变得快，过期的表比没有更糟）。

**关联模块**：`models.py`、`core.py`（连通测试）、`model_catalog.py`、`agent._open_stream`（发请求）、
`server/routes/models.py`。

---

## 16. 用量统计

**是什么**：花了多少 token、花在哪。

**数据来源就是会话文件本身** —— 扫 active + archived 里的 `stats` 和 `model` 字段，**不另立账本**。
代价是清空归档会一起删掉这些记录。

**两个口径别混用**（这是最容易搞错的地方）：

| 字段 | 口径 | 谁读 |
|---|---|---|
| `context_tokens` | **最后一次请求**的输入量 = 窗口实际占用 | 任务页仪表盘 |
| `prompt_tokens` | 一轮里**累加**的账单（每轮都要重发同一份上下文） | 用量页 |
| `cached_tokens / context_tokens` | 缓存命中率（分子分母必须来自同一次请求） | 仪表盘 |

**关联模块**：`usage.py`、`agent.RunStats`、`server/routes/usage.py`、前端用量页（手写 SVG）。

---

# 第二部分 · 技术切面

## 2.1 事件流：从 agent 到浏览器

**解决什么问题**：agent 是同步的、面向过程的；界面需要实时、可中断、可寻址。

**原理**：把「一次运行」抽象成一条**类型化事件流**。每一层做一件事：

| 层 | 输入 → 输出 | 关键点 |
|---|---|---|
| `agent` | 模型响应 → 事件对象 | `str` / `ReasoningDelta` / `ToolStart` / `ToolStep` / `Notice` / `Usage` / `Round` / `SummaryMade` |
| `stream_round` | 事件对象 → `(name, payload)` | **不拼 SSE 文本** —— 因为队列还要接纳 interaction 塞进来的事件 |
| `_pump` | `(name, payload)` → 队列 | 跑在 runner 线程 |
| `_sse` | 队列 → SSE 文本 | `_event()` 统一序列化 |
| 前端 | SSE 文本 → `ChatEvent` | 手写解析（`EventSource` 只能 GET，发消息要带附件体） |

**`data` 必须走 JSON**：SSE 规定 data 里不能有裸换行，而思考过程和工具结果都带换行。
`json.dumps(..., ensure_ascii=False)` 把换行转义掉、同时保留中文可读。

**边界与代价**：多一层队列 = 多一处可能积压或失序的地方。所以 `("start", ...)` 刻意
从 `_pump` 里发（保证排在最前），而不是在端点里发（那会变成两条线程写同一队列，顺序无保证）。

---

## 2.2 线程模型：三线程协作

**解决什么问题**：同步的 agent 循环 + async 的 HTTP 服务 + 用户要在中途插话，
三者必须共存。

**三个线程各干什么**：

| 线程 | 代码 | 职责 |
|---|---|---|
| **runner** | `_pump` | 跑 agent 循环（**运行的主线程**），工具在这里 `ask()` 并阻塞 |
| **事件循环** | `_sse` | `await queue.get()` 取事件、翻译成 SSE |
| **请求线程** | `POST /chat/answer`、`/chat/cancel` | 把答案写回 / 立取消标记 |

**协作的关键**：所有跨线程通信都通过 `Interaction`，它内部用
`threading.Lock`（保护状态）+ `threading.Event`（跨线程唤醒）。

**为什么队列是 `asyncio.Queue` 而不是 `queue.Queue`**：

- 写由**事件循环代劳**（`call_soon_threadsafe(queue.put_nowait, ...)`），
  因为 `asyncio.Queue` 不是线程安全的。
- 读方是 async 生成器，`await queue.get()` **不占线程**；普通队列的 `get` 会占住一个线程。
- 队列**无上界**：有界的话 `put_nowait` 会在 runner 线程里阻塞 —— 那是死锁。

**边界与代价**：这套设计只在「一个进程内、单用户」的量级下成立。
运行表 `_runs` 用一把全局锁保护，注释里明说「并发量是个位数，不值得上更细的粒度」。

---

## 2.3 交互通道：阻塞问答怎么做

**解决什么问题**：工具需要**停下来问用户**，但 agent 循环是同步生成器 —— 它被阻塞时连
`yield` 都做不到，问题根本没有通道发出去。

**原理**：把「发问题」和「等答案」拆到两条路上。

```
生成器 ──yield──┐
                ├──> queue ──> SSE ──> 浏览器
ask() ──publish─┘      （生成器被阻塞时，这一路照样通）
   │
   └── waiter.wait(timeout)   ← 阻塞在这里等答案
              ↑
       POST /chat/answer → channel.answer() → Event.set()
```

**几个刻意的设计**：

- **`ask()` 返回 `None` 表示「问不到」**（超时 / 没通道），必须与「用户回答了空字符串」区分开。
  前端也是这样：`/chat/answer` 返回 `false` 是**正常竞态**（点「允许」时那轮刚好超时结束），
  调用方不该弹错误提示。
- **答案按问题 id 匹配**：`answer()` 只在 `_pending == question_id` 时接受，
  防止迟到的答案落到下一轮提问上。
- **无通道时按「拒绝」处理**：CLI、单元测试里没有交互通道，这时**不能默许它动手** ——
  那正好把确认机制要防的事放了进来。
- **用 ContextVar 而不是给工具加参数**：工具签名是 `fn(**args) -> str`，参数来自模型 JSON，
  往这个契约里塞运行上下文意味着要改注册表和全部工具。ContextVar 只在 runner 线程设一次，
  谁用谁取。
- **用 ContextVar 而不是 `threading.local`**：将来把 runner 换成 async task，一行不用改。

**边界与代价**：`DEFAULT_TIMEOUT = 600` 秒 —— 用户关掉页面走人时，这一轮不能把 runner 线程
永久占住。但 600 秒内它是**真的阻塞着**的，这也是为什么必须有 SSE 心跳（见 §2.4）。

**这条通道的软肋是「不可见」**：阻塞期间没有任何新事件，界面上看不出「它在等谁」。
实测里一次 `spawn_agents` 的确认卡片躺在浮层里没被注意到，白等了十分钟，最后按超时判成
「拒绝」。所以界面侧做了两件事：`asking` 阶段那行提示高亮、标签页标题改成
「⚠ 等待你的确认」；时间轴上那行「跑了多久」也在持续走字 —— 一个人等了十分钟，
和刚等十秒，看起来必须不一样。

---

## 2.4 静默与心跳：一条长连接怎么算「还活着」

**解决什么问题**：「没有事件」和「连接死了」在客户端看起来一模一样。

**三方各有一条静默判断，阈值互相咬合**：

| 位置 | 阈值 | 撞上会怎样 |
|---|---|---|
| 前端 `IDLE_TIMEOUT_MS` | 360 秒 | 判定连接已死 → `reader.cancel()` → 服务端那一轮被取消 |
| 服务端 `SSE_HEARTBEAT_SECONDS` | 60 秒 | 补一行 SSE 注释（`: keep-alive`），让字节流不断 |
| 服务端 `DEFAULT_TIMEOUT` | 600 秒 | 没人回答确认题 → 按拒绝处理 |
| 服务端 `REQUEST_TIMEOUT` | 120 秒 | 单次模型请求超时 |

**为什么要有心跳**：`queue.get()` 没事件时会一直挂着 —— 那是长连接的**常态**
（等模型吐第一个字、等工具跑完、子代理在干活）。但对客户端来说，
「一直没有数据」和「连接死了」分不清。前端因此设了静默保护，用来防「跑模型的机器睡了一觉，
界面再也回不来」；可它分不清「睡着了」和「在苦干」。

**心跳发的是注释行**：SSE 协议规定 `:` 开头的行客户端应当忽略 —— 前端确实忽略了
（`parseBlock` 认不出 `event:` / `data:` 就跳过），但那一行字节照样算「收到了数据」。

**这个机制是在实测中被逼出来的**：一次 `spawn_agents` 跑了整 360 秒没往外播任何东西，
正好撞上前端阈值 —— 整条流被当成死连接掐掉，那一轮白跑。
**子代理干得越认真，越容易被自己人误杀。**

**边界与代价**：心跳只解决「静默」，不解决「真的断了」。
真断了仍然由前端 360 秒那条兜底 —— 那才是它的本职。

---

## 2.5 工具调用漏进正文

**解决什么问题**：模型偶尔不把调用放进 `tool_calls` 字段，而是当正文吐出来。
此时 `delta.tool_calls` 是空的，循环会把这一轮**误判成「模型答完了」**——
工具没执行、审批没弹出，用户只看到一段乱码，还以为是自己卡住了。

**三层防护**：

| 层 | 机制 | 认什么 |
|---|---|---|
| **流式阶段** | `TOOL_CALL_LEAK_MARKERS` + `_leak_index` | 带标签的调用块（DeepSeek 的 `｜｜DSML｜｜`、`<tool_call>`、`</invoke>`…） |
| **流结束后** | `_looks_like_tool_call`（`forged`） | **裸 JSON**（`[{"name":…,"arguments":…}]`），且只在**工具菜单非空**时才算 |
| **收尾轮** | `FINAL_ROUND_INSTRUCTION` | 从源头预防（见下） |

**流式阶段的两个细节**：

- **`_HOLD_CHARS` 压尾巴**：标记可能被切在相邻两个分片之间（`</inv` + `oke>`），
  所以每个分片末尾保留几个字符不外吐，代价只是晚几毫秒显示。
- **丢弃而不是显示**：标记之前的人话照常留下，**后面的调用块一律丢掉** ——
  留着只会污染正文和历史。

**归因比检测更重要**：收尾轮漏调用时，原来的提示语是「可以换一个函数调用更稳定的模型」。
可那个场景的根子在**我们自己没告诉模型「工具已经撤走」** —— 甩锅给模型，用户会去换模型，
换了照样漏。所以现在分两条：真·模型退化 vs 收尾轮漏调用。

**边界与代价**：这是**路线 A（原生 function calling）的必付成本**。
选文本协议的客户端（Cline 的 `replace_in_file`、OpenHands 的 CodeAct）不存在这个问题 ——
调用本来就在正文里。反过来，它们的解析复杂度和误触发风险也不存在。

---

## 2.6 预算与收尾：两道闸门

**解决什么问题**：一轮对话可能停不下来（模型一直要工具），也可能烧钱太狠。

**两道闸门的分工**（对齐 Claude Code 的 `maxTurns` / `maxBudgetUsd`）：

| 闸门 | 配置项 | 定位 |
|---|---|---|
| **token 预算** | 偏好 `max_run_tokens` | **主闸门** |
| **工具轮次** | 偏好 `max_iterations`（默认 30） | **兜底** |

**为什么预算当主闸门**：次数管不住成本 —— 两次请求的开销能差两个数量级
（读一个 3 行文件 vs 装一个包）。按次数卡，卡住的是正常的长任务，真跑飞了反而未必拦得住。

**软着陆 vs 硬停**（这是关键的设计判断）：

- **八成预警**（`BUDGET_WARN_RATIO = 0.8`）：发请求**之前**检查，命中就往上下文补一条
  「请开始收尾」，且**只发一次**。硬停很亏 —— 那一刻工具刚跑完、结果也回来了，
  却没有下一次请求去消化它。
- **轮次用尽**：撤掉 `tools` 字段，**并且明确告知模型**（`FINAL_ROUND_INSTRUCTION`）。
  只撤字段等于「不说」—— 模型的历史里全是 `tool_calls`，system 提示词也还列着工具名单，
  它会继续按调用格式输出。
- **token 超限**：直接停，**不再发收尾请求**（和轮次用尽故意相反：那边是别浪费已执行的工具结果，
  这边是别再花钱）。

**检查点的位置都是刻意的**：预警在请求前、token 截断在一批工具执行前、轮次收尾在
`tool_budget <= 0` 那次请求时。

**边界与代价**：`MAX_ITERATIONS` 那个数字原本是 10，被「探路型」任务打脸 ——
光摸清项目结构、读文档、确认工具链就能吃掉十轮，正事还没开始（实测：一个 PPT 任务
十轮全花在探路上）。现在放到 30 并降级为兜底。

---

## 2.7 持久化：三种文件、两把锁

**解决什么问题**：多个请求（前端每个键各发一个请求，后端把同步路由跑在线程池里）
真的会并发；而配置写坏一次，用户丢的是数据。

**三类文件、三种策略**：

| 文件 | 格式 | 为什么 |
|---|---|---|
| 配置类（models / modes / 各种组 / 记忆 / 偏好） | **JSON 单对象/数组** | 整体读写，改动不频繁 |
| 会话 | **JSONL** | 对话是**只追加**的：追加一行 O(1)，JSON 数组每次得全量重写；且某行写坏只丢一条消息 |
| 模型规格快照 | JSON 摊平表 | 外部拉的，可重拉 |

**两把锁**（`locking.py`）：

- **`file_lock`（跨进程）**：`fcntl.flock` / `msvcrt.locking`。
  拦的是「后端线程池里的两个请求」「用户另跑一个脚本」「再起一个实例」三种情况。
  **使用约定是「读也必须进锁」** —— 先在锁外读基线再进锁写回，等于没锁。
- **`atomic_write_text`（原子替换）**：先写同目录临时文件，再 `os.replace` 原子替换。
  防的是「读到了半个文件」——`write_text` 先截断再写，另一进程此刻读会拿到空文件。

**容错策略是「宁可退回默认，也不让页面起不来」**：文件缺失 / JSON 损坏 / 类型不符
一律返回默认值；JSON 损坏时把坏文件改名成 `.corrupt`（避免下次保存把用户数据彻底盖掉）。
会话文件则更细：**单行坏了只跳过那一行**。

**服务层为什么要单独一层**：`server/stores.py` 是一组**工厂函数**，每次调用现场构造
（**不缓存**）—— 换来「永远读到最新数据」，改完配置无需重启。
业务层的 `store.py` 管「怎么存」，服务层的 `stores.py` 管「存哪份、要不要初始化」。

---

## 2.8 前端状态：运行跟着会话走

**解决什么问题**：用户切走会话去看别的，回来时那一轮还在不在跑？读数还在不在？

**核心原则**：**运行跟着会话走，不跟着界面走**。切走只是「暂时看不到它」，不是「把它停掉」。

**实现**：`runs` 是一张 `Map<会话id, ActiveRun>`（**非响应式** —— 里面装 `AbortController`
这类东西，不该被 Vue 代理）。界面读的是从它同步出来的几个响应式字段。

| 字段 | 含义 | 清除时机 |
|---|---|---|
| `busy` | 正在跑一轮 | 结束且没被别人接管 |
| `runningIds` | 哪些会话在跑（侧边栏转圈） | 每次 `runs` 增删后同步 |
| `liveUsage` / `liveCached` | 实时上下文 / 缓存读数 | **跑完不清**（出错后正是想看的时候），换会话必须清 |
| `liveTodos` | 运行中的清单 | 跑完 / 换会话 / 下一轮开始；**出错或取消时不清** |
| `subagentEvents` | 子代理实时动静 | `spawn_agents` 那步到达就清 |
| `pendingQuestion` | 待答的确认/提问 | 流结束时清（否则用户以为还能点） |

**`RunPhase` 状态机**（回答「是不是卡了」）：

```
connecting → waiting → thinking / generating → tool{name} → waiting → … → null
                                  ↓
                                asking
```

**两个容易踩的点**（代码里都留了注释）：

- **同类阶段不重复写**：`generating → generating` 的赋值会白白触发重渲染（`phase` 是新对象，
  Vue 认不出它们相等）。`tool` 是例外 —— 连着两个工具时名字要换。
- **同一会话不并发**：`if (runs.has(conversationId)) return` **必须在消息上屏之前拦** ——
  否则会留下一条永远没有回复的提问。界面 `busy` 时输入框已禁用，这个守卫是兜底
  （脚本 / 自动化路径也能走到）。`finally` 里也只删属于自己那份记录，避免误删后一轮的。

---

## 2.9 降级链：失败时退到哪

**原则**：**锦上添花的能力，失败了不能拖垮主流程**。

| 能力 | 降级路径 |
|---|---|
| 流式用量统计 | `include_usage` 不被认 → **按 base_url 记住**，之后退回普通请求（用量是锦上添花） |
| 关思考 | `reasoning_effort` 不被认 → 退回普通请求（关不掉思考是小事，让整轮失败是大事） |
| 历史压缩 | 摘要生成失败 → 退回纯截断（压缩是锦上添花） |
| 网页摘要 | 压缩失败 → 返回原文节选并标注「未能压缩」 |
| 模型规格 | `/models` 问不到窗口 → 查本地快照 → 仍无则显示「—」 |
| 文件锁 | 两个平台后端都不可用 → **降级为不加锁**（不因缺锁让应用不可用） |
| 工具执行 | 未知工具 / 参数错误 / 执行异常 → **一律转成文本返回，不向上抛** |
| 沙箱 | **不降级** —— 配了沙箱却没有后端就**拒绝执行** |

**注意最后一条是反的**：沙箱和「锦上添花」相反 —— **安全相关的能力，宁可拒绝也不能静默降级**。
「配置了沙箱却悄悄裸跑」比「根本没配沙箱」更危险。

**降级要留下痕迹**：降级不是静默的。用量探测失败会记进缓存（下次不再白试一遍），
摘要失败会 `yield Notice` 告诉用户，沙箱没后端会在启动时明说。

---

## 2.10 安全边界：三道锁

**是什么**：三条不同强度的边界，各管一段。

| 边界 | 机制 | 强度 |
|---|---|---|
| **工作目录** | `PathGuard.resolve()` → 必须 `is_relative_to(root)` | 防误操作 |
| **命令白名单 / 黑名单** | `CATASTROPHIC_PATTERNS` + 确认弹窗 | **防手滑，不是安全边界**（注释里明说） |
| **沙箱** | Linux 用 bubblewrap 命名空间，macOS 用 Seatbelt 策略 | **真边界**（交给内核） |

**几个容易忽略的细节**：

- **路径解析必须 `resolve()` 后判断**：`..` 要展开、符号链接要跟随，
  否则 `link → /etc` 能绕过检查。读文件时用 **resolve 之后**的路径（防 TOCTOU）。
- **名字校验是同一个问题的另一面**：提示词 / 技能是「名字即文件名」的约定，
  名字来自界面输入框，不拦则 `../..` 能写出目录之外（`naming.safe_name`）。
- **正则也要防**：`search_content` 用启发式拦住灾难性回溯的模式。
- **无通道 = 拒绝**：没有交互通道时（CLI / 测试），确认类操作一律按拒绝处理 ——
  这是「先问再动」的底线，不能因为问不到就默许。

---

# 附录

## A. 关键常量速查

| 常量 | 值 | 位置 | 含义 |
|---|---|---|---|
| `MAX_ITERATIONS` | 30 | agent | 工具轮次兜底（可配） |
| `BUDGET_WARN_RATIO` | 0.8 | agent | token 预算预警比例 |
| `REQUEST_TIMEOUT` | 120s | agent | 单次模型请求超时 |
| `MAX_TOOL_RESULT_CHARS` | 2000 | agent | 工具结果回灌给模型的截断 |
| `MAX_HISTORY_RECORDS` | 20 | agent | 窗口未知时的历史条数兜底 |
| `HISTORY_BUDGET_RATIO` | 0.5 | agent | 历史最多占窗口的比例 |
| `SUMMARY_KEEP_RECENT` | 8 | agent | 压缩后保留的最近原文条数 |
| `SUMMARY_MAX_CHARS` | 1200 | agent | 摘要正文上限 |
| `MAX_SKILL_DESCRIPTION_CHARS` | 120 | agent | 技能清单里描述的截断 |
| `SSE_HEARTBEAT_SECONDS` | 60s | chat.py | SSE 心跳间隔 |
| `DEFAULT_TIMEOUT` | 600s | interaction | 等待用户回答的上限 |
| `IDLE_TIMEOUT_MS` | 360s | chat.ts | 前端静默保护阈值 |
| `MAX_TIMEOUT` | 300s | shell | 单个命令的最长执行时间 |
| `MAX_OUTPUT_CHARS` | 20000 | shell | 命令输出上限 |
| `MAX_MEMORIES` | 50 | memory | 记忆条数上限 |
| `MAX_TODOS` | 12 | todo | 清单条数上限 |

## B. 模块依赖方向

```
web/(Vue)  ──HTTP/SSE──▶  server/(FastAPI)  ──▶  src/quill_agent/(业务层)
                                                    │
                          ┌─────────────────────────┼─────────────────────────┐
                          ▼                         ▼                         ▼
                    agent(循环)              store/history(持久化)      tools(工具)
                          │                         │                         │
                          └──── interaction / config / locking ────────────────┘
```

**单向依赖**：`web → server → src`。业务层**不知道 HTTP 的存在**（它只产事件流），
服务层**不含业务规则**（规则在业务层，比如「只有空会话能换工作目录」）。

**循环依赖的唯一处理方式**：`tools/*` 需要 `agent`（取 `RunEnvironment` / `run_agent_stream`），
而 `agent` 需要 `tools.registry`。解法是**工具函数内延迟 import** ——
模块级互导会拿到一个只加载了一半的模块。

## C. 一句话索引

| 你想知道… | 看哪 |
|---|---|
| 一轮对话从哪开始、在哪结束 | `agent._run_stream` 的 `while True` |
| 事件怎么变成 SSE 字节 | `chat._event` |
| 工具为什么能停下来问用户 | `interaction.Interaction.ask` |
| 前端为什么不会永久卡在「等待」 | `chat.ts` 的 `IDLE_TIMEOUT_MS` + `SSE_HEARTBEAT_SECONDS` |
| 配置写坏了会怎样 | `store.read_json` |
| 两个请求同时改配置会怎样 | `locking.file_lock` |
| 模型把调用写进正文会怎样 | `agent` 的 `leaked` / `forged` / `FINAL_ROUND_INSTRUCTION` |
| 上下文快满了会怎样 | `agent._compact_if_needed` |
| 切走会话再回来会怎样 | `session.attachRun` |
| 这一轮跑了多久、跑到第几圈 | `agent.Round` + `ChatView.runMetaText` |
| 为什么确认卡片要闹出动静 | `ChatView` 的 `asking` 高亮 + 标签页标题 |
