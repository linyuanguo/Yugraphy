<template>
  <div class="share-dash">
    <!-- 顶部品牌栏（固定常驻） -->
    <div class="share-dash-top">
      <div class="share-dash-brand">📜 家谱 · 数据大屏</div>
      <div class="share-dash-top-right">
        <span v-if="refreshSec > 0 && data" class="live-chip">
          <span class="live-dot" />
          每 {{ refreshSec }}s 自动刷新
        </span>
        <a class="share-dash-home" href="/">返回家谱首页</a>
      </div>
    </div>

    <!-- 全屏自适应主体 -->
    <div class="share-dash-body">
      <div v-if="loading" class="share-dash-state">
        <a-spin size="large" />
        <p>正在加载大屏数据…</p>
      </div>
      <div v-else-if="error" class="share-dash-state">
        <div class="err-ico">🔗</div>
        <h2>{{ error }}</h2>
        <p>链接无效、已过期或已撤销，请联系分享方获取最新链接。</p>
        <a href="/" class="btn-home">返回家谱首页</a>
      </div>
      <div v-else-if="data" class="stage">
        <!-- 标题区 -->
        <div class="share-dash-title">
          <h1>{{ data.name }}</h1>
          <p v-if="data.note" class="share-dash-note">{{ data.note }}</p>
          <p class="share-dash-updated">
            数据更新：
            {{
              data.generated_at
                ? new Date(data.generated_at).toLocaleString('zh-CN', { hour12: false })
                : '—'
            }}
          </p>
        </div>

        <!-- KPI 卡（数字滚动动画跟随全局参数） -->
        <div class="kpi-row">
          <div class="kpi-card">
            <div class="kpi-label">收录谱系</div>
            <div class="kpi-value">
              <AnimatedNumber v-if="data.roll_numbers" :value="data.lineages" />
              <template v-else>{{ data.lineages }}</template>
            </div>
            <div class="kpi-sub">vol. 部</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">谱系房支</div>
            <div class="kpi-value">
              <AnimatedNumber v-if="data.roll_numbers" :value="data.branches" />
              <template v-else>{{ data.branches }}</template>
            </div>
            <div class="kpi-sub">branches</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">收录人物</div>
            <div class="kpi-value">
              <AnimatedNumber v-if="data.roll_numbers" :value="data.persons" />
              <template v-else>{{ data.persons }}</template>
            </div>
            <div class="kpi-sub">persons</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">谱书条目</div>
            <div class="kpi-value">
              <AnimatedNumber v-if="data.roll_numbers" :value="data.content_entries" />
              <template v-else>{{ data.content_entries }}</template>
            </div>
            <div class="kpi-sub">entries</div>
          </div>
        </div>

        <!-- 分布图（自动聚焦轮播跟随全局参数） -->
        <div class="chart-row">
          <a-card
            class="chart-card"
            :class="{ 'chart-focus': focusIdx === 0 }"
            :bordered="false"
            title="世代分布"
          >
            <a-empty v-if="!data.generations.length" description="暂无数据" />
            <div v-for="g in data.generations" :key="g.label" class="bar-row">
              <span class="bar-label">{{ g.label }}</span>
              <a-progress
                :percent="percentOf(g.value, data.generations)"
                :show-info="false"
                stroke-color="#1677ff"
              />
              <span class="bar-num">{{ g.value }}</span>
            </div>
          </a-card>
          <a-card
            class="chart-card"
            :class="{ 'chart-focus': focusIdx === 1 }"
            :bordered="false"
            title="房支规模 TOP10"
          >
            <a-empty v-if="!data.branches_top.length" description="暂无数据" />
            <div v-for="b in data.branches_top" :key="b.label" class="bar-row">
              <span class="bar-label">{{ b.label }}</span>
              <a-progress
                :percent="percentOf(b.value, data.branches_top)"
                :show-info="false"
                stroke-color="#2f9e6e"
              />
              <span class="bar-num">{{ b.value }}</span>
            </div>
          </a-card>
          <a-card
            class="chart-card"
            :class="{ 'chart-focus': focusIdx === 2 }"
            :bordered="false"
            title="人物性别分布"
          >
            <a-empty v-if="!data.genders.length" description="暂无数据" />
            <div v-for="s in data.genders" :key="s.label" class="bar-row">
              <span class="bar-label">{{ s.label }}</span>
              <a-progress
                :percent="percentOf(s.value, data.genders)"
                :show-info="false"
                stroke-color="#c0568a"
              />
              <span class="bar-num">{{ s.value }}</span>
            </div>
          </a-card>
        </div>
      </div>
    </div>

    <AppCopyright />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import AnimatedNumber from '@/components/AnimatedNumber.vue'
import { getDashboardSharePublicApi } from '@/api'
import type { DashboardSharePublic } from '@/types'

const route = useRoute()

/** 访问链接形态 /d<share_code>：去掉前缀 d 后即为 share_code */
const code = computed(() => String(route.params.code || '').toUpperCase().replace(/^D/, ''))
const data = ref<DashboardSharePublic | null>(null)
const loading = ref(true)
const error = ref('')

const refreshSec = ref(0)
const switchSec = ref(0)
const focusIdx = ref(0)
let refreshTimer: number | undefined
let switchTimer: number | undefined

const percentOf = (v: number, list: { value: number }[]) => {
  const max = Math.max(1, ...list.map((x) => x.value))
  return Math.round((v / max) * 100)
}

function clearTimers() {
  if (refreshTimer) {
    window.clearInterval(refreshTimer)
    refreshTimer = undefined
  }
  if (switchTimer) {
    window.clearInterval(switchTimer)
    switchTimer = undefined
  }
}

function scheduleTimers() {
  clearTimers()
  const v = data.value
  if (!v) return
  refreshSec.value = v.refresh_seconds || 0
  switchSec.value = v.switch_seconds || 0
  if (refreshSec.value > 0) {
    refreshTimer = window.setInterval(() => {
      getDashboardSharePublicApi(code.value)
        .then((fresh) => {
          data.value = fresh
          // 数据可能变化，按新参数重建定时器
          scheduleTimers()
        })
        .catch(() => {
          /* 静默：下个周期再试，避免公共页抖动 */
        })
    }, refreshSec.value * 1000)
  }
  if (switchSec.value > 0) {
    switchTimer = window.setInterval(() => {
      focusIdx.value = (focusIdx.value + 1) % 3
    }, switchSec.value * 1000)
  }
}

const load = async () => {
  loading.value = true
  error.value = ''
  data.value = null
  if (!code.value) {
    error.value = '分享链接无效'
    loading.value = false
    return
  }
  try {
    data.value = await getDashboardSharePublicApi(code.value)
    focusIdx.value = 0
    scheduleTimers()
  } catch {
    error.value = '分享链接无效'
  } finally {
    loading.value = false
  }
}

watch(() => route.params.code, load)
void load()

onBeforeUnmount(clearTimers)
</script>

<style scoped>
/* 全屏自适应：占满整个视口、页面自身不滚动，超高内容在各卡/面板内滚动 */
.share-dash {
  height: 100vh;
  min-height: 640px;
  overflow: hidden;
  background: linear-gradient(160deg, #0b2238 0%, #14314e 45%, #1d3d5f 100%);
  color: #fff;
  display: flex;
  flex-direction: column;
}
.share-dash-top {
  flex: none;
  height: 54px;
  padding: 0 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: rgba(9, 24, 40, 0.72);
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}
.share-dash-brand {
  font-size: 17px;
  font-weight: 700;
  letter-spacing: 0.5px;
}
.share-dash-top-right {
  display: flex;
  align-items: center;
  gap: 18px;
}
.live-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: rgba(255, 255, 255, 0.72);
  font-size: 12px;
  background: rgba(47, 158, 110, 0.16);
  border: 1px solid rgba(47, 158, 110, 0.5);
  border-radius: 20px;
  padding: 2px 10px;
}
.live-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #2f9e6e;
  box-shadow: 0 0 6px #2f9e6e;
  animation: live-blink 1.6s ease-in-out infinite;
}
@keyframes live-blink {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}
.share-dash-home {
  color: rgba(255, 255, 255, 0.8);
  font-size: 13px;
  text-decoration: none;
}
.share-dash-home:hover {
  color: #fff;
  text-decoration: underline;
}
.share-dash-body {
  flex: 1;
  min-height: 0;
  padding: 16px 24px 6px;
  width: 100%;
  box-sizing: border-box;
}
.stage {
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.share-dash-state {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  color: rgba(255, 255, 255, 0.85);
}
.share-dash-state p {
  color: rgba(255, 255, 255, 0.65);
}
.err-ico {
  font-size: 46px;
}
.btn-home {
  display: inline-block;
  margin-top: 12px;
  color: #fff;
  background: #1677ff;
  padding: 8px 22px;
  border-radius: 6px;
  text-decoration: none;
}
.share-dash-title {
  flex: none;
  text-align: center;
}
.share-dash-title h1 {
  color: #fff;
  font-size: 25px;
  margin: 0 0 4px;
  line-height: 1.2;
}
.share-dash-note {
  color: rgba(255, 255, 255, 0.75);
  margin: 0 0 2px;
}
.share-dash-updated {
  color: rgba(255, 255, 255, 0.45);
  font-size: 12px;
  margin: 0;
}
.kpi-row {
  flex: none;
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
}
.kpi-card {
  background: rgba(255, 255, 255, 0.07);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 12px;
  padding: 14px 20px 12px;
  backdrop-filter: blur(4px);
}
.kpi-label {
  color: rgba(255, 255, 255, 0.6);
  font-size: 13px;
  margin-bottom: 5px;
}
.kpi-value {
  font-size: 34px;
  font-weight: 700;
  color: #fff;
  line-height: 1.05;
  font-variant-numeric: tabular-nums;
}
.kpi-sub {
  margin-top: 5px;
  color: rgba(255, 255, 255, 0.35);
  font-size: 11px;
}
.chart-row {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
}
.chart-card {
  min-height: 0;
  background: rgba(255, 255, 255, 0.07);
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  transition: border-color 0.4s ease, box-shadow 0.4s ease, opacity 0.4s ease;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
/* 自动聚焦轮播：当前聚焦卡发光，其余轻微降透明 */
.chart-card:not(.chart-focus) {
  opacity: 0.68;
}
.chart-card.chart-focus {
  border-color: rgba(22, 119, 255, 0.75);
  box-shadow: 0 0 22px rgba(22, 119, 255, 0.35);
}
.chart-card :deep(.ant-card-head) {
  flex: none;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}
.chart-card :deep(.ant-card-head-title) {
  color: rgba(255, 255, 255, 0.85);
}
.chart-card :deep(.ant-card-body) {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 8px 18px 12px;
}
.bar-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.bar-label {
  min-width: 88px;
  color: rgba(255, 255, 255, 0.85);
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.bar-row :deep(.ant-progress) {
  flex: 1;
  margin: 0;
}
.bar-num {
  min-width: 34px;
  text-align: right;
  color: rgba(255, 255, 255, 0.85);
  font-size: 13px;
}
.share-dash > :deep(.app-copyright) {
  flex: none;
}
@media (max-width: 960px) {
  .kpi-row {
    grid-template-columns: repeat(2, 1fr);
  }
  .chart-row {
    grid-template-columns: 1fr;
    grid-template-rows: repeat(3, 1fr);
    overflow: hidden;
  }
}
</style>
