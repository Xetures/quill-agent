<script setup lang="ts">
import { QuestionFilled } from '@element-plus/icons-vue'
import { ref } from 'vue'

/**
 * 顶栏右侧的「使用说明」入口。
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

  <el-dialog v-model="open" title="使用说明" width="640px">
    <div class="doc">
      <section>
        <h4>一、先配一个模型</h4>
        <p>
          打开左侧「API 设置」→「新建连接」：填接口地址（例如
          <code>https://api.deepseek.com</code>）、协议和 API Key。
        </p>
        <p>
          点「获取模型列表」把该端点的模型拉回来勾选（也可直接手敲），再填「上下文窗口」——
          任务页右上角的用量仪表盘按它算占比。最后点「测试连接」确认能通。
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
          就没有提示词、没有工具，那已经不是这个 Agent 了。
        </p>
      </section>

      <section>
        <h4>三、开始对话</h4>
        <ul>
          <li>在「任务」页底部输入框里提问，<kbd>Enter</kbd> 发送、<kbd>Shift</kbd> + <kbd>Enter</kbd> 换行。</li>
          <li>输入框上方那一排可以换模式 / 模型 / 工作目录，也可以带附件。</li>
          <li>左侧「新建任务」开一个新会话；点会话切换；hover 会话右侧的按钮可以归档。</li>
        </ul>
      </section>

      <section>
        <h4>四、几个概念</h4>
        <ul>
          <li>
            <strong>提示词</strong>：六类（身份 / 能力 / 工具策略 / 工作流程 / 输出规范 /
            约束），正文就是 <code>prompt/</code> 下的 <code>.md</code> 文件，直接改文件即可。
          </li>
          <li><strong>工具</strong>：模型能调用的动作（读文件、搜索、记东西等），给哪些由模式的工具组决定。</li>
          <li>
            <strong>技能</strong>：某类任务该怎么做。平时只在上下文里占一行，模型觉得需要时才读全文
            （<code>skills/&lt;名字&gt;/SKILL.md</code>）。
          </li>
          <li>
            <strong>执行权限（沙箱）</strong>：顶栏右侧那个锁 —— 限制命令<strong>够得着什么</strong>，
            由系统内核执行，越界的写入和联网直接失败。它管的是<strong>整台机器</strong>，
            和输入框上方那一排（都只管当前这一轮）不是一类东西；和「危险命令先问用户」
            那套审批也不是一回事（那个管<strong>要不要问</strong>）。
          </li>
          <li><strong>记忆</strong>：跨会话保留的事实与偏好，由模型主动写入，可在「记忆」页查看和关闭。</li>
          <li><strong>用量</strong>：token 消耗统计，按天看折线、按任务看表格（含已归档）。</li>
        </ul>
      </section>

      <section>
        <h4>五、一个限制</h4>
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
}

/* 内容偏长，矮窗口下自己要能滚 —— 弹窗本身不滚动 */
.doc {
  max-height: 62vh;
  overflow-y: auto;
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
