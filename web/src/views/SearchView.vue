<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { api } from '../api/client'
import type { SearchHit, SearchInfo, SearchTestResult } from '../api/types'
import { errorText } from '../utils/error'

// ElMessage 由 unplugin-auto-import 自动引入，所以这里看不到它的 import

/** 测试用的默认搜索词。挑一个中文的：中文结果好不好，是博查和 Tavily 的主要差别。 */
const DEFAULT_TEST_QUERY = '今天的科技新闻'

const info = ref<SearchInfo | null>(null)
const saving = ref(false)
const testing = ref(false)

const form = ref({
  backend: '',
  keys: {} as Record<string, string>,
  base_url: '',
  max_results: 5,
})

/** 测试词。它不是配置的一部分，只是「拿这个词试一下」 */
const testQuery = ref(DEFAULT_TEST_QUERY)
const testResult = ref<SearchTestResult | null>(null)

/** 后端的可选清单，由后端随配置一起给（多支持一家时前端不用改）。 */
const backends = computed(() => info.value?.backends ?? [])
const limits = computed(() => info.value?.limits ?? { min: 1, max: 20, default: 5 })

/** 当前选中的那个后端；清单还没回来时给一份空壳，免得模板里到处判空。 */
const current = computed(
  () => backends.value.find((item) => item.value === form.value.backend) ?? null,
)

/**
 * Key 输入框的绑定。
 *
 * 用一个带 getter/setter 的计算属性，而不是直接写 `v-model="form.keys[form.backend]"`：
 * 没配过的后端在 `keys` 里根本没有这个键，`v-model` 绑到 `undefined` 上，
 * 输入框会从「未受控」变成「受控」，滚回来一个警告。
 */
const apiKey = computed({
  get: () => form.value.keys[form.value.backend] ?? '',
  set: (value: string) => {
    form.value.keys[form.value.backend] = value
  },
})

/** 已保存的配置里，这个后端填过 Key 没有 —— 用来标「已配置 / 未配置」。 */
function savedHas(value: string): boolean {
  return Boolean(info.value?.config.keys[value]?.trim())
}

async function load(): Promise<void> {
  const data = await api.get<SearchInfo>('/search')
  info.value = data
  form.value = {
    backend: data.config.backend,
    // 拷一份再改：直接绑返回的对象的话，还没保存界面就已经变了，
    // 「改了但没存」这件事就看不出来了
    keys: { ...data.config.keys },
    base_url: data.config.base_url,
    max_results: data.config.max_results,
  }
}

/** 当前表单内容对应的请求体（保存和测试共用，免得两处字段加漏一个）。 */
function payload(): Record<string, unknown> {
  return {
    backend: form.value.backend,
    keys: form.value.keys,
    base_url: form.value.base_url.trim(),
    max_results: form.value.max_results,
  }
}

async function save(): Promise<void> {
  saving.value = true

  try {
    await api.put('/search', payload())
    // 重新拉一次：上面的「已配置」标记读的是已保存的那份配置
    await load()
    ElMessage.success('已保存')
  } catch (exc) {
    ElMessage.error(errorText(exc))
  } finally {
    saving.value = false
  }
}

/**
 * 用**当前表单里的**配置真搜一次。
 *
 * 不用先保存：先测通了再存，比存完发现 Key 是错的再改了重存省一轮往返。
 */
async function test(): Promise<void> {
  testing.value = true
  testResult.value = null

  try {
    const result = await api.post<SearchTestResult>('/search/test', {
      ...payload(),
      query: testQuery.value.trim(),
    })
    testResult.value = result

    if (result.ok) ElMessage.success(result.message)
    else ElMessage.error(result.message || '测试失败')
  } catch (exc) {
    ElMessage.error(errorText(exc))
  } finally {
    testing.value = false
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <!-- 这一页现在是「偏好设置」里的一节：页面标题和二级导航由 SettingsLayout 提供，
       所以这里不带 `.page` 外壳（套两层 .page 会多出一层滚动区） -->
  <div>
    <section class="group">
      <h2>搜索服务</h2>
      <p class="muted desc">
        搜索本身不自己实现，交给第三方服务。两个都填好之后随时切换，各自的 Key 都会留着。
      </p>

      <el-form :model="form" label-width="110px" @submit.prevent>
        <el-form-item label="服务">
          <el-select v-model="form.backend" class="backend-select">
            <el-option
              v-for="item in backends"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
          <el-tag v-if="savedHas(form.backend)" size="small" type="success" class="state">
            已配置
          </el-tag>
          <el-tag v-else size="small" type="info" class="state">未配置</el-tag>
        </el-form-item>

        <el-form-item label="API Key">
          <el-input v-model="apiKey" type="password" show-password placeholder="粘贴该服务的 Key" />
          <p v-if="current" class="note muted">
            {{ current.label }} 的 Key：{{ current.hint }}。
            <strong>Key 按服务分开存</strong>，切换服务不会把另一个的 Key 抹掉。
          </p>
        </el-form-item>

        <el-form-item label="接口地址">
          <el-input v-model="form.base_url" :placeholder="current?.endpoint ?? ''" />
          <p class="note muted">
            留空就用服务默认的地址。只有走自建服务或中转站时才需要改。
          </p>
        </el-form-item>

        <el-form-item label="默认条数">
          <el-input-number v-model="form.max_results" :min="limits.min" :max="limits.max" />
          <span class="inline-hint muted">模型没指定条数时用这个值</span>
        </el-form-item>

        <el-form-item label=" ">
          <el-button type="primary" :loading="saving" :disabled="!form.backend" @click="save">
            保存
          </el-button>
        </el-form-item>
      </el-form>
    </section>

    <section class="group">
      <h2>测试</h2>
      <p class="muted desc">
        用上面填的配置真搜一次 ——
        <strong>不需要先保存</strong>，改完直接测，通了再存。
      </p>

      <div class="test-bar">
        <el-input
          v-model="testQuery"
          placeholder="搜索词"
          class="query-input"
          @keyup.enter="test"
        />
        <el-button :loading="testing" :disabled="!form.backend" @click="test">测试</el-button>
      </div>

      <template v-if="testResult">
        <p class="result-msg" :class="testResult.ok ? 'ok' : 'bad'">{{ testResult.message }}</p>

        <!-- 搜到 0 条也是「通了」（Key 和网络没问题），所以这里单独说一句，
             免得看到空列表以为又失败了 -->
        <p v-if="testResult.ok && !testResult.hits.length" class="note muted">
          搜到 0 条只说明这个词没结果，Key 和网络是通的。
        </p>

        <ul class="hits">
          <li v-for="(hit, index) in testResult.hits" :key="index" class="hit">
            <a class="hit-title" :href="hit.url" target="_blank" rel="noreferrer">
              {{ hit.title }}
            </a>
            <div class="hit-url mono">{{ hit.url }}</div>
            <div v-if="hit.snippet" class="hit-snippet muted">{{ hit.snippet }}</div>
          </li>
        </ul>
      </template>
    </section>

    <section class="group">
      <h2>怎么用上</h2>
      <p class="muted desc">
        配好之后，到
        <RouterLink to="/tools">工具</RouterLink>
        页把
        <code>web_search</code>
        和
        <code>web_fetch</code>
        加进要让 Agent 用的那个工具组 —— 工具不是配好就默认全给，
        给哪些由工具组决定。
      </p>
      <p class="note muted">
        <code>web_search</code> 只返回标题、地址和摘要；
        <code>web_fetch</code> 才去读正文，而且必须带上「要从这一页拿什么」——
        整页正文动辄几千字，全塞进上下文就把它本来该干活的窗口占满了。
      </p>
    </section>
  </div>
</template>

<style scoped>
/* ---------- 配置表单 ---------- */

.group {
  max-width: 720px;
}

.group + .group {
  margin-top: 28px;
  padding-top: 24px;
  border-top: 1px solid var(--border);
}

h2 {
  margin: 0 0 4px;
  font-size: 15px;
  font-weight: 600;
}

.desc {
  margin: 0 0 14px;
  font-size: 13px;
  line-height: 1.6;
}

.desc code,
.note code {
  padding: 1px 4px;
  border-radius: 3px;
  background: var(--bg-soft);
  font-family: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, monospace;
  font-size: 12px;
}

.backend-select {
  width: 200px;
}

/* 「已配置 / 未配置」的标记。占固定宽度：切换服务时它才会在原位换字，而不是把
 * 前面的下拉框推来推去 */
.state {
  flex-shrink: 0;
  margin-left: 10px;
}

.inline-hint {
  margin-left: 10px;
  font-size: 12px;
}

/* 字段下方的说明文字：比正文小一号，别和控件挤在一起 */
.note {
  width: 100%;
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.5;
}

/* ---------- 测试 ---------- */

.test-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.query-input {
  width: 320px;
}

.result-msg {
  margin: 0 0 10px;
  font-size: 13px;
}

.result-msg.ok {
  color: var(--success, var(--accent));
}

.result-msg.bad {
  color: var(--danger, #c45656);
}

.hits {
  margin: 0;
  padding: 0;
  list-style: none;
}

.hit {
  padding: 10px 0;
  border-top: 1px solid var(--border);
}

.hit-title {
  font-size: 13px;
  font-weight: 500;
  text-decoration: none;
}

.hit-title:hover {
  text-decoration: underline;
}

.hit-url {
  margin-top: 2px;
  font-size: 11px;
  color: var(--text-soft);
  word-break: break-all;
}

.hit-snippet {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.6;
}
</style>
