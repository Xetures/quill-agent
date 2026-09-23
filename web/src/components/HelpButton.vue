<script setup lang="ts">
import { QuestionFilled } from '@element-plus/icons-vue'
import { ref } from 'vue'

/**
 * 「使用说明」入口，跟在侧边栏品牌区的 LOGO 与应用名后面（见 Sidebar.vue）。
 *
 * 早先在顶栏右侧，后来挪进了品牌区：顶栏右侧留给「执行权限」那个锁 —— 它是整机级别的
 * 开关，得在任何页面都够得着，而说明是「第一次打开看一眼」的东西，放品牌区更合适。
 *
 * 弹窗里的内容刻意写得很短：它只解决「第一次打开该点哪儿」，完整的设计说明在
 * README 里，这里不重复一遍 —— 两处长文迟早会对不上。
 */
const open = ref(false)
</script>

<template>
  <el-button
    class="help"
    text
    :icon="QuestionFilled"
    title="使用说明"
    @click="open = true"
  />

  <!-- append-to-body 必须留着：弹窗得挂在 body 下。四块玻璃板都有 backdrop-filter，
       它会给板子建一个层叠上下文 —— 弹窗若渲染在板子里，`position: fixed` 就改成
       相对那块板子定位（弹窗跑到板子中间、遮罩也只盖住板子）。 -->
  <el-dialog v-model="open" title="使用说明" width="640px" append-to-body>
    <div class="doc">
      <section>
        <h4>一、先配一个模型</h4>
        <p>
          打开左侧「API设置」→「新建连接」：填接口地址（例如
          <code>https://api.deepseek.com</code>）、协议和 API Key。
        </p>
        <p>
          点「获取模型列表」把该端点的模型拉回来勾选（也可直接手敲），再填「上下文窗口」——
          任务页右上角的用量仪表盘按它算占比。最后点「测试连接」确认能通。
        </p>
        <p>
          接本地 Ollama 就简单些：协议选 <code>Ollama（本地）</code>，<strong>地址留空</strong>
          （自动用 <code>http://localhost:11434/v1</code>），Key 也不用填。
        </p>
      </section>

      <section>
        <h4>二、建一个模式</h4>
        <p>
          打开「模式」→「添加模式」。一个模式 = 提示词组 + 工具组 + 技能组 + 记忆开关 +
          偏好模型，是 Agent 的一套完整配置。
        </p>
        <p>
          三个组<strong>都可以不选</strong> —— 那就是纯问答。任务页必须选一个模式：没有模式
          就没有提示词、没有工具，那已经不是这个 Agent 了。出厂带了三个（全量 Agent /
          轻量 Agent / 仅问答），想先跑通可以直接用「轻量 Agent」。
        </p>
      </section>

      <section>
        <h4>三、开始对话</h4>
        <ul>
          <li>在「任务」页底部输入框里提问，<kbd>Enter</kbd> 发送、<kbd>Shift</kbd> + <kbd>Enter</kbd> 换行。</li>
          <li>
            输入框上方那一排是模式 / 模型 / 工作目录 / 附件 —— 它们只管
            <strong>当前这一轮</strong>；旁边的思考开关是个偏好，关掉之后下次还关着。
          </li>
          <li>回答生成中，发送按钮原地变成「停止」，点一下把这一轮中断。</li>
          <li>
            左侧「新建任务」开一个新会话；hover 会话右侧的按钮可以导出或归档，归档的在
            「偏好设置 → 归档」里翻。
          </li>
        </ul>
      </section>

      <section>
        <h4>四、窗口左右那两条</h4>
        <ul>
          <li>
            <strong>右侧：公共剪贴板</strong> —— 点边缘那个箭头拉出来。消息里复制过的内容会按
            时间攒在这里，跨页面、跨会话都不丢，可以从这儿再复制回去。
          </li>
          <li>
            <strong>左侧：公共备忘录</strong> —— 同样点箭头拉出，一块自己敲的便签（Markdown）。
            和剪贴板是一对，方向相反：一个是被动收集的复制记录，一个是主动写的。
          </li>
        </ul>
        <p>这两样只存在这台机器上（浏览器本地存储），不跟着会话走，也不上传。</p>
      </section>

      <section>
        <h4>五、几个概念</h4>
        <ul>
          <li>
            <strong>提示词</strong>：六类（身份 / 能力 / 工具策略 / 工作流程 / 输出规范 /
            约束），正文就是 <code>prompt/</code> 下的 <code>.md</code> 文件 —— 界面里能改，
            拿外部编辑器直接改文件也行，两边不会各存一份。
          </li>
          <li><strong>工具</strong>：模型能调用的动作（读文件、搜索、跑命令、看 git 等），给哪些由模式的工具组决定。</li>
          <li>
            <strong>技能</strong>：某类任务该怎么做。平时只在上下文里占一行，模型觉得需要时才读全文
            （<code>skills/&lt;名字&gt;/SKILL.md</code>）。
          </li>
          <li>
            <strong>MCP</strong>：工具的另一个来源 —— 外部 MCP 服务器，在「偏好设置 → MCP」
            里连上之后，它提供的工具会和内置工具一起摆到模型面前。
          </li>
          <li>
            <strong>执行权限（沙箱）</strong>：顶栏右侧那个锁 —— 限制命令<strong>够得着什么</strong>，
            由系统内核执行，越界的写入和联网直接失败。它管的是<strong>整台机器</strong>，
            和输入框上方那一排（都只管当前这一轮）不是一类东西；和「危险命令先问用户」
            那套审批也不是一回事（那个管<strong>要不要问</strong>）。
          </li>
          <li>
            <strong>记忆</strong>：跨会话保留的事实与偏好，由模型主动写入，可在「记忆」页查看和关闭。
            它和上面的<strong>备忘录</strong>不是一回事 —— 记忆是模型记的，备忘录是你自己写的。
          </li>
          <li>
            <strong>联网搜索</strong>：在「偏好设置 → 联网搜索」里挑服务商、填 Key。配好之后
            模型的 <code>web_search</code> / <code>web_fetch</code> 才用得上。
          </li>
          <li><strong>用量</strong>：token 消耗统计，按天看折线、按任务看表格（含已归档）。</li>
        </ul>
      </section>

      <section>
        <h4>六、一个限制</h4>
        <p>
          文件工具只能访问<strong>工作目录</strong>以内的位置 —— 它既是工作台，也是安全边界。
          工作目录在任务页切换，但<strong>只有空会话能改</strong>：聊到一半再换，历史里还留着旧目录下
          文件的内容，模型会拿它当新目录里同名文件的答案。
        </p>
      </section>
    </div>
  </el-dialog>
</template>

<style scoped>
.help {
  flex-shrink: 0;
  color: var(--text-soft);
  /* 抬一层：这一块紧挨着侧栏板，万一后面加了绝对定位的东西也不会盖住它。
   * 点不开等于「这页没有入口」，属于最容易被忽略的那类故障 */
  position: relative;
  z-index: 1;
}

/* 内容偏长，矮窗口下要能滚 —— 这件事现在由全局规则统一管（见 style.css 的
 * `.el-dialog__body`），这里不再叠一层：两层 max-height 会套出两条滚动条 */
.doc {
  padding-right: 6px;
}

.doc section + section {
  margin-top: 16px;
}

.doc h4 {
  margin: 0 0 6px;
  font-size: 13px;
  font-weight: 600;
}

.doc p {
  margin: 0 0 6px;
  color: var(--text-soft);
  font-size: 13px;
}

.doc ul {
  margin: 0;
  padding-left: 18px;
  color: var(--text-soft);
  font-size: 13px;
}

.doc li + li {
  margin-top: 4px;
}

.doc code {
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--bg-soft);
  color: var(--gold);
  font-family: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, monospace;
  font-size: 12px;
}

.doc kbd {
  padding: 1px 5px;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--bg-soft);
  font-family: inherit;
  font-size: 11px;
}

.doc strong {
  color: var(--text);
}
</style>
