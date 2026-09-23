<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { locale, setLocale, type Locale } from '../locales'
import { fontSize, setFontSize, type FontSize } from '../stores/font-size'
import { setTheme, themeMode, type ThemeMode } from '../stores/theme'

/**
 * 显示设置：明暗 / 字号 / 语言。
 *
 * 文件名还叫 SettingsThemeView、地址还是 `/settings/theme` —— 它原先只管配色，
 * 后来字号与语言也归到这一页（都是「在这台机器上显示成什么样」）。改名要动路由和
 * 既有链接，收益只是名字好看，所以留着；导航上叫「显示」。
 *
 * 三组用同一种卡片：一个预览块 + 名称 + 一句说明。预览各不相同 —— 配色看色块、
 * 字号看示例字、语言看一个「中 / A」，一眼能认出这张卡在说什么。
 *
 * 选项都由 `t()` 生成，所以是 computed 而不是模块级常量：语言一换就得重算。
 */
const { t } = useI18n()

const THEMES = computed<{ value: ThemeMode; label: string; hint: string }[]>(() =>
  (['light', 'dark', 'auto'] as const).map((value) => ({
    value,
    label: t(`display.theme.${value}.label`),
    hint: t(`display.theme.${value}.hint`),
  })),
)

const SIZES = computed<{ value: FontSize; label: string; hint: string }[]>(() =>
  (['small', 'standard', 'large'] as const).map((value) => ({
    value,
    label: t(`display.fontSize.${value}.label`),
    hint: t(`display.fontSize.${value}.hint`),
  })),
)

const LANGUAGES = computed<{ value: Locale; label: string; hint: string }[]>(() =>
  (['zh-CN', 'en-US'] as const).map((value) => ({
    value,
    label: t(`display.language.${value}.label`),
    hint: t(`display.language.${value}.hint`),
  })),
)
</script>

<template>
  <div class="display">
    <section>
      <h2 class="group-title">{{ t('display.theme.title') }}</h2>
      <div class="cards">
        <button
          v-for="item in THEMES"
          :key="item.value"
          type="button"
          class="card"
          :class="{ active: themeMode === item.value }"
          @click="setTheme(item.value)"
        >
          <!-- 用两个色块示意「侧边栏 + 主区」，比放一张真截图轻，换主题时也不用维护 -->
          <span class="preview" :class="item.value">
            <span class="preview-side" />
            <span class="preview-main" />
          </span>
          <span class="label">{{ item.label }}</span>
          <span class="hint">{{ item.hint }}</span>
        </button>
      </div>
    </section>

    <section>
      <h2 class="group-title">{{ t('display.fontSize.title') }}</h2>
      <div class="cards">
        <button
          v-for="item in SIZES"
          :key="item.value"
          type="button"
          class="card"
          :class="{ active: fontSize === item.value }"
          @click="setFontSize(item.value)"
        >
          <!-- 示例字按各自的倍数画，并且**跟着当前档位一起缩放**：
               这样不管当前是哪一档，三张卡之间的比例始终看得见（写死 px 就看不出来了） -->
          <span class="preview-sample" :class="item.value">Aa</span>
          <span class="label">{{ item.label }}</span>
          <span class="hint">{{ item.hint }}</span>
        </button>
      </div>
    </section>

    <section>
      <h2 class="group-title">{{ t('display.language.title') }}</h2>
      <div class="cards">
        <button
          v-for="item in LANGUAGES"
          :key="item.value"
          type="button"
          class="card"
          :class="{ active: locale === item.value }"
          @click="setLocale(item.value)"
        >
          <span class="preview-sample lang">{{ item.value === 'zh-CN' ? '中' : 'A' }}</span>
          <span class="label">{{ item.label }}</span>
          <span class="hint">{{ item.hint }}</span>
        </button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.display {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.group-title {
  margin: 0 0 10px;
  font-size: var(--fs-sm);
  font-weight: 600;
}

.cards {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

.card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  padding: 10px;
  width: 148px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--bg-card);
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition:
    border-color 0.15s ease,
    box-shadow 0.15s ease,
    transform 0.15s ease;
}

.card:hover {
  border-color: var(--accent);
  box-shadow: var(--shadow);
}

.card.active {
  border-color: var(--accent);
  /* 选中态用主色描边 + 一圈柔光，比换背景色克制 —— 卡片里已经有预览了 */
  box-shadow: 0 0 0 3px var(--accent-soft);
}

/* 所有预览块共用同一块「画布」：高度一致，三组卡片才对得齐 */
.preview,
.preview-sample {
  display: block;
  width: 100%;
  height: 56px;
  margin-bottom: 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: hidden;
}

.preview {
  position: relative;
}

.preview-side {
  position: absolute;
  inset: 0 auto 0 0;
  width: 30%;
}

.preview-main {
  position: absolute;
  inset: 0 0 0 30%;
}

/* 三种预览各用一组写死的颜色 —— 这里刻意不用主题变量：
 * 卡片要展示的正是「另一套主题长什么样」，跟着当前主题走就没意义了 */
.preview.light .preview-side {
  background: #f3ecdf;
}
.preview.light .preview-main {
  background: #faf6ee;
}

.preview.dark .preview-side {
  background: #152836;
}
.preview.dark .preview-main {
  background: #0f1e2b;
}

.preview.auto .preview-side {
  background: linear-gradient(160deg, #152836 50%, #f3ecdf 50%);
}
.preview.auto .preview-main {
  background: linear-gradient(160deg, #0f1e2b 50%, #faf6ee 50%);
}

/* 字号与语言的预览：一块浅底，中间放示例字 */
.preview-sample {
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-soft);
  color: var(--text);
  line-height: 1;
}

.preview-sample.small {
  font-size: calc(var(--fs-base) * var(--font-scale-small));
}
.preview-sample.standard {
  font-size: var(--fs-base);
}
.preview-sample.large {
  font-size: calc(var(--fs-base) * var(--font-scale-large));
}

/* 语言那组：示例字放大一号、用强调色，和字号那组的「Aa」区分开 */
.preview-sample.lang {
  font-size: var(--fs-xl);
  font-weight: 600;
  color: var(--gold);
}

.label {
  font-size: var(--fs-sm);
  font-weight: 500;
}

.card .hint {
  font-size: var(--fs-2xs);
  color: var(--text-soft);
  line-height: 1.4;
}
</style>
