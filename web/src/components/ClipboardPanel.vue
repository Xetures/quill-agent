<script setup lang="ts">
import { ArrowLeft, ArrowRight, Close, CopyDocument, Delete } from '@element-plus/icons-vue'

import { clipboard, clearClipboard, removeClipboardItem } from '../stores/clipboard'

/**
 * 右侧的公共剪贴板：一个贴在边缘的箭头，点开拉出面板。
 *
 * 为什么挂在最外层（App.vue）而不是某一页里：它服务的是「把内容从 A 页拿到 B 页」
 * 这件事 —— 本身就是跨页面的，挂在任何一页里都会在切页时消失。
 */

/** 只显示时分：同一分钟内复制的东西靠顺序就能分辨，写全日期反而占地方。 */
function timeText(at: number): string {
  const date = new Date(at)
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}

/** 把这一条重新写进系统剪贴板（面板里的内容不重复入列，它本来就在）。 */
async function reCopy(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制')
  } catch {
    ElMessage.error('复制失败：浏览器拒绝了剪贴板访问。')
  }
}
</script>

<template>
  <!-- 触发区：**整条右侧空隙**（任务区玻璃板右缘 ↔ 窗口右边界），不是那个小箭头。
       鼠标落进这条里任何地方都算「对准了它」—— 整条亮起来表示可点，点哪都能开合。
       默认无底无框，箭头也淡：它是个常驻入口，不该在每一页都抢一次注意力。 -->
  <button
    class="clip-zone"
    :class="{ 'is-open': clipboard.open }"
    :title="clipboard.open ? '收起公共剪贴板' : '公共剪贴板'"
    @click="clipboard.open = !clipboard.open"
  >
    <el-icon>
      <component :is="clipboard.open ? ArrowRight : ArrowLeft" />
    </el-icon>
  </button>

  <Transition name="clip-slide">
    <aside v-if="clipboard.open" class="clip-panel">
      <header class="clip-head">
        <span class="title">公共剪贴板</span>
        <span class="muted count">{{ clipboard.items.length }} 条</span>
        <el-button
          text
          size="small"
          :disabled="!clipboard.items.length"
          class="clear"
          @click="clearClipboard"
        >
          清空
        </el-button>
        <el-button text size="small" :icon="Close" title="收起" @click="clipboard.open = false" />
      </header>

      <el-scrollbar class="clip-body">
        <p v-if="!clipboard.items.length" class="empty muted">
          应用内复制的内容会自动收到这里 —— 比如消息下面那对「复制 / 删除」里的复制。
        </p>

        <ul v-else class="list">
          <li v-for="item in clipboard.items" :key="item.id" class="item">
            <div class="meta">
              <span class="from">{{ item.from || '复制' }}</span>
              <span class="muted time">{{ timeText(item.at) }}</span>
              <el-button
                text
                size="small"
                :icon="CopyDocument"
                title="再复制一次"
                class="act"
                @click="reCopy(item.text)"
              />
              <el-button
                text
                size="small"
                :icon="Delete"
                title="从剪贴板里删掉"
                class="act"
                @click="removeClipboardItem(item.id)"
              />
            </div>

            <!-- 不折叠：内容原样铺开。整条太长时滚的是面板本身，不是这一条 -->
            <pre class="text">{{ item.text }}</pre>
          </li>
        </ul>
      </el-scrollbar>
    </aside>
  </Transition>
</template>

<style scoped>
/* 触发区：**整条右侧空隙**，从顶到底。
 *
 * 宽度取 16px —— 这正是 `.shell` 的 padding 留出来的那道空隙（见 App.vue），
 * 所以一像素也不会压到左边的任务区；再宽就压上了。
 *
 * 高亮给的是**整条**：鼠标进到这条里任何位置，它整条亮起来（「这块能点」），
 * 点哪都能开合 —— 只在箭头上做高亮的话，得精确瞄中那个 16×46 的小方块才行。 */
.clip-zone {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  z-index: 1500;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  padding: 0;
  background: transparent;
  border: none;
  color: var(--text-faint);
  font-size: 12px;
  cursor: pointer;
  transition: color 0.15s ease;
}

/* 高亮：**从中间往上下两端渐隐**（中间最实、越往上/下越淡）。
 * 一条均匀的色带从顶贯到底太生硬，像贴了张纸条；渐隐才像「这一条在发光」。
 *
 * 用伪元素 + opacity 过渡，而不是直接过渡 background —— 渐变本身没法做过渡动画 */
.clip-zone::before {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(
    to bottom,
    rgba(255, 255, 255, 0) 0%,
    var(--bg-hover) 50%,
    rgba(255, 255, 255, 0) 100%
  );
  opacity: 0;
  transition: opacity 0.18s ease;
  pointer-events: none;
}

.clip-zone:hover,
.clip-zone.is-open {
  color: var(--accent);
}

.clip-zone:hover::before,
.clip-zone.is-open::before {
  opacity: 1;
}

/* 面板：紧贴箭头左侧拉出（不是从屏幕边缘），**高度撑满整个视口** ——
 * 它是「随手取用」的东西，铺到顶和底就不用滚着找 */
.clip-panel {
  position: fixed;
  top: 0;
  right: 16px;
  bottom: 0;
  z-index: 1500;
  display: flex;
  flex-direction: column;
  width: 340px;
  overflow: hidden;

  background: var(--glass-bg);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-right: none;
  border-radius: var(--glass-radius) 0 0 var(--glass-radius);
  box-shadow: var(--glass-shadow);
}

.clip-slide-enter-active,
.clip-slide-leave-active {
  transition:
    transform 0.22s ease,
    opacity 0.22s ease;
}

.clip-slide-enter-from,
.clip-slide-leave-to {
  transform: translateX(16px);
  opacity: 0;
}

.clip-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
  padding: 12px 12px 10px 16px;
  border-bottom: 1px solid var(--border);
}

.title {
  font-size: 13px;
  font-weight: 600;
}

.count {
  font-size: 12px;
}

/* 「清空」推到右边，紧挨着收起按钮 */
.clear {
  margin-left: auto;
}

.clip-body {
  flex: 1;
  min-height: 0;
}

.empty {
  margin: 16px;
  font-size: 12px;
  line-height: 1.6;
}

.list {
  margin: 0;
  padding: 10px;
  list-style: none;
}

.item {
  padding: 8px 10px 10px;
  border-radius: 8px;
  background: var(--bg-card);
}

.item + .item {
  margin-top: 8px;
}

.meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}

.from {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-soft);
}

.time {
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

/* 两个操作按钮推到右边，和消息下面那对按钮一个位置习惯 */
.act {
  --el-button-text-color: var(--text-soft);
  --el-button-hover-text-color: var(--accent);
  --el-button-active-text-color: var(--accent);
  margin-left: 0;
  padding: 2px;
}

.meta .act:first-of-type {
  margin-left: auto;
}

/* 内容原样铺开：保留换行与缩进（贴过来的是代码 / 提示词时尤其重要），
 * 长串能断则断，别把面板顶宽 */
.text {
  margin: 0;
  font-family:
    ui-monospace, SFMono-Regular, 'SF Mono', Menlo, monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
