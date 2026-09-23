<script setup lang="ts">
import { QuestionFilled } from '@element-plus/icons-vue'
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

/**
 * 「使用说明」入口，跟在侧边栏品牌区的 LOGO 与应用名后面（见 Sidebar.vue）。
 *
 * 早先在顶栏右侧，后来挪进了品牌区：顶栏右侧留给「执行权限」那个锁 —— 它是整机级别的
 * 开关，得在任何页面都够得着，而说明是「第一次打开看一眼」的东西，放品牌区更合适。
 *
 * 弹窗里的内容刻意写得很短：它只解决「第一次打开该点哪儿」，完整的设计说明在
 * README-developer.md / ARCHITECTURE.md 里，这里不重复一遍 —— 两处长文迟早会对不上。
 *
 * **正文由数据渲染，不写死在模板里**：文案得跟着语言走，六节正文全在 `locales/` 里
 * （键是 `help.<节>.<段>`）。段内的行内标签（`<code>` / `<strong>` / `<kbd>`）因此也留在
 * 文案里，用 `v-html` 渲染 —— 这些字符串是我们自己写的，不含任何用户输入。
 */
const { t } = useI18n()
const open = ref(false)

/**
 * 每节由哪几段组成。顺序就是阅读顺序。
 *
 * 段名写成字面量而不是循环拼 key：`t()` 的参数在类型上要求是文案表里真实存在的键，
 * 拼出来的字符串过不了类型检查，也就失去了「文案漏了编译期就报」的好处。
 */
const SECTIONS = computed(() => [
  {
    title: t('help.model.title'),
    blocks: [t('help.model.p1'), t('help.model.p2'), t('help.model.p3')],
  },
  {
    title: t('help.mode.title'),
    blocks: [t('help.mode.p1'), t('help.mode.p2')],
  },
  {
    title: t('help.chat.title'),
    blocks: [t('help.chat.list')],
  },
  {
    title: t('help.panels.title'),
    blocks: [t('help.panels.list'), t('help.panels.note')],
  },
  {
    title: t('help.concepts.title'),
    blocks: [t('help.concepts.list')],
  },
  {
    title: t('help.limit.title'),
    blocks: [t('help.limit.text')],
  },
])
</script>

<template>
  <el-button
    class="help"
    text
    :icon="QuestionFilled"
    :title="t('help.title')"
    @click="open = true"
  />

  <!-- append-to-body 必须留着：弹窗得挂在 body 下。四块玻璃板都有 backdrop-filter，
       它会给板子建一个层叠上下文 —— 弹窗若渲染在板子里，`position: fixed` 就改成
       相对那块板子定位（弹窗跑到板子中间、遮罩也只盖住板子）。 -->
  <el-dialog v-model="open" :title="t('help.title')" width="640px" append-to-body>
    <div class="doc">
      <section v-for="section in SECTIONS" :key="section.title">
        <h4>{{ section.title }}</h4>
        <!-- 每一段自带外层标签（`<p>` / `<ul>`）：段落在文案里，不在这里拼 -->
        <div v-for="(block, index) in section.blocks" :key="index" class="block" v-html="block" />
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
  font-size: var(--fs-sm);
  font-weight: 600;
}

.block + .block {
  margin-top: 6px;
}

/* 正文来自 v-html，**scoped 样式够不到它**（作用域属性加不到 v-html 生成的那些元素
 * 上）—— 所以这几条必须走 `:deep()`，否则整段正文会退化成没有样式的裸文本。
 * `.block` 与 `h4` 是模板里的，照旧用普通选择器。 */
.doc :deep(p) {
  margin: 0;
  color: var(--text-soft);
  font-size: var(--fs-sm);
}

.doc :deep(ul) {
  margin: 0;
  padding-left: 18px;
  color: var(--text-soft);
  font-size: var(--fs-sm);
}

.doc :deep(li + li) {
  margin-top: 4px;
}

.doc :deep(code) {
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--bg-soft);
  color: var(--gold);
  font-family: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, monospace;
  font-size: var(--fs-xs);
}

.doc :deep(kbd) {
  padding: 1px 5px;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--bg-soft);
  font-family: inherit;
  font-size: var(--fs-2xs);
}

.doc :deep(strong) {
  color: var(--text);
}
</style>
