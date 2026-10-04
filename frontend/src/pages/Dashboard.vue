<template>
  <div class="dash">
    <div class="dash-head">
      <a-space align="center">
        <span class="dash-title">📊 概览</span>
        <span class="muted">全库数据总览（图谱 / 扫描识别 / 谱书内容）</span>
      </a-space>
      <a-space>
        <span v-if="cfgText" class="dash-cfg-hint">{{ cfgText }}</span>
        <a-popover
          v-model:open="cfgOpen"
          title="大屏展示参数（概览页与 /d 公开大屏共用）"
          trigger="click"
          placement="bottomRight"
        >
          <template #content>
            <div class="cfg-panel">
              <div class="cfg-row">
                <label>自动刷新</label>
                <a-input-number
                  v-model:value="cfgForm.refresh_seconds"
                  :min="0"
                  :max="3600"
                  :step="5"
                  addon-after="秒"
                  style="width: 132px"
                />
                <span class="muted small">0 = 关闭</span>
              </div>
              <div class="cfg-row">
                <label>面板轮播</label>
                <a-input-number
                  v-model:value="cfgForm.switch_seconds"
                  :min="0"
                  :max="3600"
                  :step="5"
                  addon-after="秒"
                  style="width: 132px"
                />
                <span class="muted small">0 = 关闭</span>
              </div>
              <div class="cfg-row">
                <label>数字滚动</label>
                <a-switch v-model:checked="cfgForm.roll_numbers" />
                <span class="muted small">KPI 数字跳动动画</span>
              </div>
              <div class="cfg-actions">
                <a-button size="small" @click="cfgOpen = false">取消</a-button>
                <a-button
                  size="small"
                  type="primary"
                  :loading="cfgSaving"
                  @click="saveCfg"
                >
                  保存
                </a-button>
              </div>
            </div>
          </template>
          <a-button size="small">⚙️ 大屏参数</a-button>
        </a-popover>
        <a-button size="small" :loading="loading" @click="load">🔄 刷新</a-button>
      </a-space>
    </div>

    <!-- KPI 卡片 -->
    <a-row :gutter="[12, 12]">
      <a-col v-for="c in kpis" :key="c.key" :xs="12" :sm="8" :md="6" :xl="4">
        <a-card class="kpi" :body-style="{ padding: '14px 16px' }">
          <div class="kpi-label">{{ c.label }}</div>
          <div class="kpi-value" :style="{ color: c.color }">
            <AnimatedNumber v-if="cfgForm.roll_numbers" :value="c.value" />
            <template v-else>{{ fmtNum(c.value) }}</template>
          </div>
          <div class="kpi-foot">{{ c.foot }}</div>
        </a-card>
      </a-col>
    </a-row>

    <!-- 服务器运行状态（CPU / 内存 / 磁盘 IO / MinIO / 数据库 / 向量；详情见抽屉） -->
    <a-row :gutter="[12, 12]" class="mt">
      <a-col :xs="24">
        <SystemHealthCard />
      </a-col>
    </a-row>

    <a-row :gutter="[12, 12]" class="mt">
      <!-- 识别进度 -->
      <a-col :xs="24" :lg="10">
        <a-card title="扫描识别进度" size="small">
          <div class="prog">
            <div class="prog-top">
              <span>已识别 {{ fmtNum(d?.pages_done || 0) }} / {{ fmtNum(d?.pages_total || 0) }} 页</span>
              <span class="muted">{{ pagePct }}%</span>
            </div>
            <a-progress :percent="pagePct" :status="pagePct >= 100 ? 'success' : 'active'" />
          </div>
          <a-descriptions :column="2" size="small" bordered class="mt-sm">
            <a-descriptions-item label="任务总数">{{ d?.tasks_total || 0 }}</a-descriptions-item>
            <a-descriptions-item label="已完成">
              <a-tag color="green">{{ d?.tasks_done || 0 }}</a-tag>
            </a-descriptions-item>
            <a-descriptions-item label="处理中/排队">
              <a-tag color="blue">{{ d?.tasks_running || 0 }}</a-tag>
            </a-descriptions-item>
            <a-descriptions-item label="失败任务">
              <a-tag :color="(d?.tasks_failed || 0) ? 'red' : 'default'">
                {{ d?.tasks_failed || 0 }}
              </a-tag>
            </a-descriptions-item>
            <a-descriptions-item label="识别失败页">
              <a-tag :color="(d?.failed_pages || 0) ? 'orange' : 'default'">
                {{ d?.failed_pages || 0 }} 页
              </a-tag>
            </a-descriptions-item>
            <a-descriptions-item label="检索向量">{{ fmtNum(d?.vectors || 0) }}</a-descriptions-item>
          </a-descriptions>
        </a-card>
      </a-col>

      <!-- 世代分布 -->
      <a-col :xs="24" :lg="14">
        <a-card title="世代分布（人物）" size="small">
          <div v-if="!generations.length" class="empty">暂无世代数据</div>
          <div v-else class="bars">
            <div v-for="g in generations" :key="g.label" class="bar-row">
              <span class="bar-label" :title="g.label">{{ g.label }}</span>
              <div class="bar-track">
                <div class="bar-fill" :style="{ width: barWidth(g.value, generations) }" />
              </div>
              <span class="bar-value">{{ g.value }}</span>
            </div>
          </div>
        </a-card>
      </a-col>
    </a-row>

    <a-row :gutter="[12, 12]" class="mt">
      <!-- 房支 TOP -->
      <a-col :xs="24" :lg="14">
        <a-card title="房支人数 TOP10" size="small">
          <div v-if="!branchesTop.length" class="empty">暂无房支归属数据</div>
          <div v-else class="bars">
            <div v-for="b in branchesTop" :key="b.label" class="bar-row">
              <span class="bar-label wide" :title="b.label">{{ b.label }}</span>
              <div class="bar-track">
                <div
                  class="bar-fill purple"
                  :style="{ width: barWidth(b.value, branchesTop) }"
                />
              </div>
              <span class="bar-value">{{ b.value }}</span>
            </div>
          </div>
        </a-card>
      </a-col>

      <!-- 性别分布 -->
      <a-col :xs="24" :lg="10">
        <a-card title="性别分布" size="small">
          <div v-if="!genders.length" class="empty">暂无人物数据</div>
          <div v-else>
            <div v-for="g in genders" :key="g.label" class="bar-row">
              <span class="bar-label">{{ genderLabel(g.label) }}</span>
              <div class="bar-track">
                <div
                  class="bar-fill"
                  :class="genderClass(g.label)"
                  :style="{ width: barWidth(g.value, genders) }"
                />
              </div>
              <span class="bar-value">{{ g.value }}</span>
            </div>
          </div>
        </a-card>
      </a-col>
    </a-row>

    <!-- 最近任务 -->
    <a-card title="最近导入任务" size="small" class="mt">
      <a-table
        :data-source="d?.recent_tasks || []"
        :columns="columns"
        row-key="task_id"
        size="small"
        :pagination="false"
        :loading="loading"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'status'">
            <a-tag :color="statusMeta(record.status).color">
              {{ statusMeta(record.status).label }}
            </a-tag>
          </template>
          <template v-else-if="column.key === 'progress'">
            {{ record.done_pages }} / {{ record.total_pages }}
          </template>
          <template v-else-if="column.key === 'updated_at'">
            {{ fmtTime(record.updated_at) }}
          </template>
        </template>
        <template #emptyText>
          <span class="muted">暂无导入任务</span>
        </template>
      </a-table>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import type { TableColumnsType } from 'ant-design-vue'
import { message } from 'ant-design-vue'
import AnimatedNumber from '@/components/AnimatedNumber.vue'
import SystemHealthCard from '@/components/SystemHealthCard.vue'
import { getDashboardApi, updateDashDisplayConfigApi } from '@/api'
import type { Dashboard, DashboardSlice, DashboardTaskItem } from '@/types'

const loading = ref(false)
const d = ref<Dashboard | null>(null)

// ============ 大屏展示参数（内部概览 + /d 公开大屏共用一份全局配置） ============
const cfgOpen = ref(false)
const cfgSaving = ref(false)
const cfgForm = reactive({ refresh_seconds: 30, switch_seconds: 20, roll_numbers: true })
let refreshTimer: number | undefined

const cfgText = computed(() => {
  if (!d.value) return ''
  if (!cfgForm.refresh_seconds) return '自动刷新已关闭'
  const parts = [`每 ${cfgForm.refresh_seconds}s 自动刷新`]
  if (cfgForm.switch_seconds) parts.push(`轮播 ${cfgForm.switch_seconds}s`)
  return parts.join(' · ')
})

function syncCfgFromData() {
  const v = d.value
  if (v && typeof v.refresh_seconds === 'number') {
    cfgForm.refresh_seconds = v.refresh_seconds
    cfgForm.switch_seconds = v.switch_seconds
    cfgForm.roll_numbers = v.roll_numbers
  }
}

function resetTimer() {
  if (refreshTimer) {
    window.clearInterval(refreshTimer)
    refreshTimer = undefined
  }
  const sec = cfgForm.refresh_seconds || 0
  if (sec > 0) refreshTimer = window.setInterval(load, sec * 1000)
}

async function saveCfg() {
  cfgSaving.value = true
  try {
    const c = await updateDashDisplayConfigApi({
      refresh_seconds: cfgForm.refresh_seconds,
      switch_seconds: cfgForm.switch_seconds,
      roll_numbers: cfgForm.roll_numbers,
    })
    if (d.value) {
      d.value = {
        ...d.value,
        refresh_seconds: c.refresh_seconds,
        switch_seconds: c.switch_seconds,
        roll_numbers: c.roll_numbers,
      }
    }
    syncCfgFromData()
    resetTimer()
    cfgOpen.value = false
    message.success('大屏展示参数已保存，概览页与 /d 公开大屏即时生效')
  } catch {
    /* 请求拦截器已提示 */
  } finally {
    cfgSaving.value = false
  }
}

const kpis = computed(() => {
  const v = d.value
  return [
    { key: 'lineages', label: '📚 谱系', value: v?.lineages || 0, foot: '套宗谱', color: '#1677ff' },
    { key: 'branches', label: '🌿 房支', value: v?.branches || 0, foot: '个房支', color: '#52c41a' },
    { key: 'persons', label: '👤 人物', value: v?.persons || 0, foot: '已入图谱', color: '#722ed1' },
    {
      key: 'entries',
      label: '📖 谱书内容',
      value: v?.content_entries || 0,
      foot: '条文字条目',
      color: '#fa8c16',
    },
    { key: 'vectors', label: '🔎 检索向量', value: v?.vectors || 0, foot: '条已索引', color: '#13c2c2' },
    { key: 'tasks', label: '🤖 导入任务', value: v?.tasks_total || 0, foot: '个任务', color: '#eb2f96' },
  ]
})

const generations = computed<DashboardSlice[]>(() => d.value?.generations || [])
const branchesTop = computed<DashboardSlice[]>(() => d.value?.branches_top || [])
const genders = computed<DashboardSlice[]>(() => d.value?.genders || [])

const pagePct = computed(() => {
  const total = d.value?.pages_total || 0
  if (!total) return 0
  return Math.min(100, Math.round(((d.value?.pages_done || 0) / total) * 100))
})

const columns: TableColumnsType = [
  { title: '文件', key: 'name', dataIndex: 'name', ellipsis: true },
  { title: '状态', key: 'status', dataIndex: 'status', width: 100 },
  { title: '进度(页)', key: 'progress', width: 110 },
  { title: '更新时间', key: 'updated_at', width: 180 },
]

function fmtNum(n?: number) {
  return (n ?? 0).toLocaleString('zh-CN')
}

function barWidth(value: number, list: DashboardSlice[]) {
  const max = Math.max(1, ...list.map((x) => x.value || 0))
  return `${Math.max(2, Math.round(((value || 0) / max) * 100))}%`
}

function genderLabel(k: string) {
  if (k === 'male') return '男'
  if (k === 'female') return '女'
  return '未知'
}

function genderClass(k: string) {
  if (k === 'male') return 'blue'
  if (k === 'female') return 'pink'
  return 'grey'
}

function statusMeta(status: string) {
  const map: Record<string, { label: string; color: string }> = {
    done: { label: '已完成', color: 'green' },
    failed: { label: '失败', color: 'red' },
    running: { label: '识别中', color: 'blue' },
    pending: { label: '排队中', color: 'orange' },
    consolidating: { label: '整理中', color: 'cyan' },
    converting: { label: '转图中', color: 'geekblue' },
  }
  return map[status] || { label: status || '—', color: 'default' }
}

function fmtTime(iso?: string | null) {
  if (!iso) return '—'
  const s = /(Z|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : `${iso}Z`
  const dt = new Date(s)
  return Number.isNaN(dt.getTime()) ? '—' : dt.toLocaleString('zh-CN')
}

async function load() {
  if (loading.value) return
  loading.value = true
  try {
    d.value = await getDashboardApi()
  } catch {
    /* 请求拦截器已提示；保留旧数据，自动刷新继续 */
  } finally {
    loading.value = false
    syncCfgFromData()
    resetTimer()
  }
}

onMounted(load)

onBeforeUnmount(() => {
  if (refreshTimer) window.clearInterval(refreshTimer)
})
</script>

<style scoped>
/* 填满右栏可视区，内容超高时仅本页内部出现一条滚动条（避免 .content 双滚动） */
.dash {
  height: 100%;
  min-height: 0;
  overflow-y: auto;
  padding: 2px 4px 8px;
}
.dash-cfg-hint {
  color: #1677ff;
  font-size: 12px;
  background: #e6f4ff;
  border-radius: 4px;
  padding: 2px 8px;
}
.cfg-panel {
  min-width: 320px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 2px 0;
}
.cfg-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.cfg-row label {
  width: 64px;
  flex: none;
  font-size: 12px;
  color: rgba(0, 0, 0, 0.65);
}
.cfg-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 4px;
}
.dash-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.dash-title {
  font-size: 17px;
  font-weight: 600;
}
.muted {
  color: rgba(0, 0, 0, 0.45);
  font-size: 12px;
}
.mt {
  margin-top: 12px;
}
.mt-sm {
  margin-top: 8px;
}
.kpi-label {
  font-size: 12px;
  color: rgba(0, 0, 0, 0.55);
}
.kpi-value {
  font-size: 26px;
  font-weight: 600;
  line-height: 1.3;
}
.kpi-foot {
  font-size: 12px;
  color: rgba(0, 0, 0, 0.35);
}
.prog-top {
  display: flex;
  justify-content: space-between;
  margin-bottom: 6px;
  font-size: 13px;
}
.bars {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.bar-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}
.bar-label {
  width: 62px;
  flex: none;
  text-align: right;
  color: rgba(0, 0, 0, 0.65);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.bar-label.wide {
  width: 150px;
}
.bar-track {
  flex: 1;
  height: 12px;
  background: #f0f2f5;
  border-radius: 6px;
  overflow: hidden;
}
.bar-fill {
  height: 100%;
  background: linear-gradient(90deg, #1677ff, #69b1ff);
  border-radius: 6px;
}
.bar-fill.purple {
  background: linear-gradient(90deg, #722ed1, #b37feb);
}
.bar-fill.blue {
  background: linear-gradient(90deg, #1677ff, #69b1ff);
}
.bar-fill.pink {
  background: linear-gradient(90deg, #eb2f96, #ff85c0);
}
.bar-fill.grey {
  background: linear-gradient(90deg, #8c8c8c, #bfbfbf);
}
.bar-value {
  width: 44px;
  flex: none;
  text-align: right;
  color: rgba(0, 0, 0, 0.75);
}
.empty {
  color: rgba(0, 0, 0, 0.35);
  font-size: 12px;
  padding: 8px 0;
}
</style>
