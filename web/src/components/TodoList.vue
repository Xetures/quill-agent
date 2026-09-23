<script setup lang="ts">
/**
 * 任务清单卡片。
 *
 * 两处用它，长得一样但来历不同：
 *
 *     任务页的流末尾  —— 运行中那份（`live`），随 `todo` 事件实时刷新；
 *     消息里的正文旁  —— 跑完落盘的那份，回看时还在。
 *
 * 合成一个组件是因为**它们必须长得一样**：同一份清单在跑完前后换一副面孔，
 * 用户会以为那是两回事。差别只有描边（实线 = 已经落进会话，虚线 = 还在跑）。
 */
import { ArrowDown } from '@element-plus/icons-vue'
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import type { TodoItem, TodoStatus } from '../api/types'

const { t } = useI18n()

const props = defineProps<{
  items: TodoItem[]
  /** 正在跑的那份（实时进度）。 */
  live?: boolean
  /**
   * 可折叠：默认只留一行（标题 + 进度），点开才展开。
   *
   * 给浮层里那份用的：它可能十几项，摊开会把输出区吃掉一大块，而多数时候用户
   * 只想知道「还剩几步」。**消息里那份定稿不给它** —— 回看历史时那份要一眼看全。
   */
  collapsible?: boolean
}>()

/** 展开状态。不可折叠时恒为展开。 */
const open = ref(!props.collapsible)

const done = computed(() => props.items.filter((item) => item.status === 'completed').length)

/** 完成度百分比。清单不可能是空的（后端会拒绝空清单），所以不用防除零。 */
const percent = computed(() => Math.round((done.value / props.items.length) * 100))

const allDone = computed(() => done.value === props.items.length)

/**
 * 每一项前面那个记号。
 *
 * 用文字符号而不是图标组件：这一列只有三态、又很小，图标在两种字号下不容易对齐；
 * 而符号自带「勾 / 点在跳 / 空圈」的语感，和它后面那行文字一样是文本。
 */
const MARKS: Record<TodoStatus, string> = {
  completed: '✓',
  in_progress: '●',
  pending: '○',
}
</script>

<template>
  <div class="todos" :class="{ live }">
    <div class="head" :class="{ clickable: collapsible }" @click="collapsible && (open = !open)">
      <span>{{ t('todoList.title') }}</span>
      <!-- 数字和进度条都要：条是「一眼看到还剩多少」，数字是「确切还剩几步」。
           收起时只剩这个数字 —— 它本来就是这一行里最要紧的信息 -->
      <span class="count mono">{{ done }}/{{ items.length }}</span>
      <el-icon v-if="collapsible" class="arrow" :class="{ open }"><ArrowDown /></el-icon>
    </div>

    <template v-if="open">
      <div
        class="bar"
        role="progressbar"
        :aria-valuenow="percent"
        aria-valuemin="0"
        aria-valuemax="100"
      >
        <div class="bar-fill" :class="{ full: allDone }" :style="{ width: `${percent}%` }" />
      </div>

      <ul class="list">
        <li v-for="(item, index) in items" :key="index" :class="item.status">
          <span class="mark">{{ MARKS[item.status] }}</span>
          <span class="text">{{ item.content }}</span>
        </li>
      </ul>
    </template>
  </div>
</template>

<style scoped>
.todos {
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-soft);
}

/* 还在跑的那份用虚线描边，和「待确认」「子代理看板」同一个语感：
 * 它是此刻的状态，还不是已经落在会话里的内容 */
.todos.live {
  border-style: dashed;
}

.head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: var(--fs-sm);
  font-weight: 600;
}

.head.clickable {
  cursor: pointer;
  /* 连点两下展开时会选中文字，看着像出了 bug */
  user-select: none;
}

.arrow {
  align-self: center;
  color: var(--text-soft);
  font-size: var(--fs-xs);
  transition: transform 0.2s ease;
}

/* 展开时箭头朝下指，收起时朝上「请点我」—— 用旋转而不是换图标，
 * 这样切换是连续的，不会闪一下 */
.arrow.open {
  transform: rotate(180deg);
}

.count {
  margin-left: auto;
  color: var(--text-soft);
  font-size: var(--fs-xs);
  font-weight: 400;
}

.bar {
  margin-top: 8px;
  height: 4px;
  overflow: hidden;
  border-radius: 2px;
  background: var(--bg);
}

.bar-fill {
  height: 100%;
  border-radius: 2px;
  background: var(--warn, #c8933f);
  /* 每推进一步都是整体替换，动一下让「又前进了一格」看得出来 */
  transition: width 0.3s ease;
}

/* 走满就转成完成色：这时候「还剩多少」的答案已经变成「没了」，
 * 不该还留着催促味的琥珀色 */
.bar-fill.full {
  background: var(--ok, #3f9142);
}

.list {
  margin: 10px 0 0;
  padding: 0;
  list-style: none;
  font-size: var(--fs-sm);
  line-height: 1.9;
}

.list li {
  display: flex;
  gap: 8px;
}

.mark {
  flex-shrink: 0;
  width: 12px;
  color: var(--text-soft);
}

/* 做完的压暗但仍留着字：用户回头要能对一眼「当初说要做哪几件」 */
.completed .text {
  color: var(--text-soft);
}

.completed .mark {
  color: var(--ok, #3f9142);
}

/* 唯一要抢注意力的那一项。整份清单里同时只有一项是 in_progress
 * （后端会提醒模型，但不拦），所以这里可以放心加粗 */
.in_progress .text {
  font-weight: 600;
}

/* 记号一闪一闪：静态的话它和「未开始」的圈看着太像，传达不了「正在做」 */
.in_progress .mark {
  color: var(--warn, #c8933f);
  animation: pulse 1.4s ease-in-out infinite;
}

@keyframes pulse {
  50% {
    opacity: 0.3;
  }
}
</style>
