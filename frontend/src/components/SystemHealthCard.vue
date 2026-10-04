<template>
  <a-card class="sys-card" size="small">
    <template #title>🖥 服务器运行状态</template>
    <template #extra>
      <a-space>
        <a-tag :color="healthColor">{{ healthText }}</a-tag>
        <a-popover
          v-model:open="cfgOpen"
          title="监控刷新间隔（保存在本机浏览器）"
          trigger="click"
          placement="bottomRight"
        >
          <template #content>
            <div class="cfg-panel">
              <div class="cfg-row">
                <label>平时</label>
                <a-input-number
                  v-model:value="cfgForm.idleSec"
                  :min="0"
                  :max="3600"
                  :step="5"
                  addon-after="秒"
                  style="width: 132px"
                />
                <span class="muted small">0 = 关闭</span>
              </div>
              <div class="cfg-row">
                <label>看详情</label>
                <a-input-number
                  v-model:value="cfgForm.openSec"
                  :min="0"
                  :max="3600"
                  :step="5"
                  addon-after="秒"
                  style="width: 132px"
                />
                <span class="muted small">0 = 关闭</span>
              </div>
              <div class="muted small cfg-hint">
                默认 30s / 10s。调小会更实时但增加后端负担（含 MinIO 统计，约 10 分钟一次较慢）
              </div>
              <div class="cfg-actions">
                <a-button size="small" @click="resetCfg">恢复默认</a-button>
                <a-button size="small" type="primary" @click="saveCfg">保存</a-button>
              </div>
            </div>
          </template>
          <a-button size="small">⏱ 刷新间隔</a-button>
        </a-popover>
        <a-button size="small" type="primary" @click="openDetail">详情</a-button>
        <a-button size="small" :loading="loading" @click="load">🔄</a-button>
      </a-space>
    </template>

    <!-- 三块核心指标：CPU / 内存 / 磁盘（带迷你趋势图） -->
    <a-row :gutter="[12, 8]">
      <a-col v-for="g in gauges" :key="g.key" :xs="24" :md="8">
        <div class="gauge">
          <div class="g-top">
            <span class="g-name">{{ g.name }}</span>
            <span class="g-val" :style="{ color: g.color }">{{ g.value }}</span>
          </div>
          <a-progress
            :percent="g.percent"
            :show-info="false"
            size="small"
            :stroke-color="g.color"
          />
          <div class="g-foot">
            <SparkLine :points="g.history" :color="g.color" :width="110" :height="26" />
            <span class="muted small">{{ g.foot }}</span>
          </div>
        </div>
      </a-col>
    </a-row>

    <!-- 依赖服务状态灯 -->
    <div class="svc">
      <a-tooltip v-for="sv in services" :key="sv.name" :title="sv.tip">
        <span class="pill" :class="sv.ok ? 'ok' : 'bad'">
          <i class="dot" />
          <b>{{ sv.name }}</b>
          <span class="muted small">{{ sv.value }}</span>
        </span>
      </a-tooltip>
      <span class="muted small right">
        已运行 {{ s?.uptime_h ?? 0 }} h · 每 {{ intervalSec }}s 自动刷新 · 更新于 {{ updatedText }}
      </span>
    </div>

    <!-- ============ 详情抽屉 ============ -->
    <a-drawer
      v-model:open="open"
      title="🖥 服务器运行状态详情"
      placement="right"
      :width="drawerWidth"
    >
      <div v-if="!s" class="empty">加载中…</div>
      <div v-else class="detail">
        <!-- CPU -->
        <div class="sec">
          <div class="sec-title">处理器 CPU</div>
          <div class="kv-row">
            <div class="kv">
              <span class="kv-label">使用率</span>
              <span class="kv-val" :style="{ color: pctColor(s.cpu.percent) }">
                {{ s.cpu.percent }}%
              </span>
            </div>
            <div class="kv"><span class="kv-label">核心数</span><span class="kv-val">{{ s.cpu.cores }}</span></div>
            <div class="kv">
              <span class="kv-label">负载 1/5/15</span>
              <span class="kv-val">{{ s.cpu.load1 }} / {{ s.cpu.load5 }} / {{ s.cpu.load15 }}</span>
            </div>
          </div>
          <div class="chart">
            <SparkLine :points="s.cpu.history" :color="pctColor(s.cpu.percent)" :width="640" :height="64" />
            <div class="muted small">最近 {{ s.cpu.history.length }} 次采样（每次请求刷新）</div>
          </div>
        </div>

        <!-- 内存 -->
        <div class="sec">
          <div class="sec-title">内存</div>
          <div class="kv-row">
            <div class="kv">
              <span class="kv-label">已用</span>
              <span class="kv-val" :style="{ color: pctColor(s.memory.percent) }">
                {{ s.memory.used_gb }} / {{ s.memory.total_gb }} GB（{{ s.memory.percent }}%）
              </span>
            </div>
            <div class="kv">
              <span class="kv-label">可用</span><span class="kv-val">{{ s.memory.available_gb }} GB</span>
            </div>
            <div class="kv">
              <span class="kv-label">交换分区</span>
              <span class="kv-val">{{ s.memory.swap_used_gb }} / {{ s.memory.swap_total_gb }} GB</span>
            </div>
          </div>
          <div class="chart">
            <SparkLine :points="s.memory.history" :color="pctColor(s.memory.percent)" :width="640" :height="64" />
            <div class="muted small">内存使用率趋势</div>
          </div>
        </div>

        <!-- 磁盘容量 -->
        <div class="sec">
          <div class="sec-title">磁盘容量</div>
          <div v-if="!s.disk.items.length" class="empty">未采集到挂载点</div>
          <div v-for="d in s.disk.items" :key="d.mount + d.device" class="mount-row">
            <a-tooltip :title="`设备 ${d.device}`">
              <span class="mount-name">{{ d.mount }}</span>
            </a-tooltip>
            <a-progress
              class="mount-bar"
              :percent="d.percent"
              size="small"
              :stroke-color="pctColor(d.percent)"
            />
            <span class="mount-size">{{ d.used_gb }} / {{ d.total_gb }} GB</span>
          </div>
        </div>

        <!-- 磁盘 IO / 网络 -->
        <div class="sec">
          <div class="sec-title">磁盘 IO / 网络</div>
          <div class="kv-row">
            <div class="kv"><span class="kv-label">读取</span><span class="kv-val">{{ s.disk.read_kbps }} KB/s</span></div>
            <div class="kv"><span class="kv-label">写入</span><span class="kv-val">{{ s.disk.write_kbps }} KB/s</span></div>
            <div class="kv">
              <span class="kv-label">IOPS 读/写</span>
              <span class="kv-val">{{ s.disk.read_iops }} / {{ s.disk.write_iops }}</span>
            </div>
            <div class="kv">
              <span class="kv-label">磁盘繁忙</span>
              <span class="kv-val" :style="{ color: pctColor(s.disk.busy) }">{{ s.disk.busy }}%</span>
            </div>
            <div class="kv"><span class="kv-label">网络下载</span><span class="kv-val">{{ s.network.rx_kbps }} KB/s</span></div>
            <div class="kv"><span class="kv-label">网络上传</span><span class="kv-val">{{ s.network.tx_kbps }} KB/s</span></div>
          </div>
          <div class="chart">
            <SparkLine :points="s.disk.history" color="#722ed1" :width="640" :height="56" />
            <div class="muted small">磁盘读写合计速率趋势（KB/s）</div>
          </div>
        </div>

        <!-- MinIO -->
        <div class="sec">
          <div class="sec-title">
            对象存储 MinIO
            <a-tag :color="s.minio.ok ? 'green' : 'red'">{{ s.minio.ok ? '正常' : '异常' }}</a-tag>
            <span class="muted small">{{ s.minio.endpoint }} · 响应 {{ s.minio.latency_ms }} ms</span>
          </div>
          <div class="kv-row">
            <div class="kv"><span class="kv-label">对象总数</span><span class="kv-val">{{ objText() }}</span></div>
            <div class="kv"><span class="kv-label">占用容量</span><span class="kv-val">{{ s.minio.size_gb }} GB</span></div>
            <div class="kv"><span class="kv-label">桶数量</span><span class="kv-val">{{ s.minio.buckets.length }}</span></div>
          </div>
          <a-table
            :data-source="s.minio.buckets"
            :columns="bucketCols"
            row-key="name"
            size="small"
            :pagination="false"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'objects'">{{ fmtNum(record.objects) }}</template>
              <template v-else-if="column.key === 'size'">{{ fmtSize(record.size_bytes) }}</template>
            </template>
            <template #emptyText><span class="muted">无桶数据</span></template>
          </a-table>
          <div v-if="s.minio.error" class="err">⚠ {{ s.minio.error }}</div>
        </div>

        <!-- PostgreSQL -->
        <div class="sec">
          <div class="sec-title">
            数据库 PostgreSQL
            <a-tag :color="s.database.ok ? 'green' : 'red'">{{ s.database.ok ? '正常' : '异常' }}</a-tag>
            <span class="muted small">响应 {{ s.database.latency_ms }} ms</span>
          </div>
          <div class="kv-row">
            <div class="kv"><span class="kv-label">版本</span><span class="kv-val">{{ s.database.version || '—' }}</span></div>
            <div class="kv"><span class="kv-label">库大小</span><span class="kv-val">{{ s.database.size_gb }} GB</span></div>
            <div class="kv">
              <span class="kv-label">连接 / 活跃</span>
              <span class="kv-val">{{ s.database.connections }} / {{ s.database.active }}</span>
            </div>
            <div class="kv">
              <span class="kv-label">空闲事务</span>
              <span class="kv-val" :style="{ color: s.database.idle_in_tx ? '#faad14' : undefined }">
                {{ s.database.idle_in_tx }}
              </span>
            </div>
            <div class="kv"><span class="kv-label">运行时长</span><span class="kv-val">{{ s.database.uptime_h }} h</span></div>
          </div>
          <div v-if="s.database.error" class="err">⚠ {{ s.database.error }}</div>
        </div>

        <!-- Neo4j -->
        <div class="sec">
          <div class="sec-title">
            图数据库 Neo4j
            <a-tag :color="s.neo4j.ok ? 'green' : 'red'">{{ s.neo4j.ok ? '正常' : '异常' }}</a-tag>
            <span class="muted small">响应 {{ s.neo4j.latency_ms }} ms</span>
          </div>
          <div v-if="s.neo4j.error" class="err">⚠ {{ s.neo4j.error }}</div>
        </div>

        <!-- 向量索引 -->
        <div class="sec">
          <div class="sec-title">
            谱书向量索引
            <a-tag v-if="s.vector.building" color="blue">构建中</a-tag>
            <a-tag v-else :color="s.vector.ready ? 'green' : 'orange'">
              {{ s.vector.ready ? '已就绪' : '未就绪' }}
            </a-tag>
          </div>
          <div class="kv-row">
            <div class="kv">
              <span class="kv-label">已索引 / 总数</span>
              <span class="kv-val">{{ fmtNum(s.vector.indexed) }} / {{ fmtNum(s.vector.total) }}</span>
            </div>
            <div class="kv">
              <span class="kv-label">最近更新</span>
              <span class="kv-val">{{ fmtTime(s.vector.last_updated) }}</span>
            </div>
          </div>
          <a-progress
            :percent="vectorPct"
            size="small"
            :status="s.vector.ready ? 'success' : 'active'"
          />
          <div class="svc-slim">
            <span class="pill" :class="s.vector.embedding_ok ? 'ok' : 'bad'">
              <i class="dot" /><b>Embedding</b>
              <span class="muted small">{{ s.vector.embedding_latency_ms }} ms</span>
            </span>
            <span class="pill" :class="s.vector.rerank_ok ? 'ok' : 'bad'">
              <i class="dot" /><b>Rerank</b>
              <span class="muted small">{{ s.vector.rerank_latency_ms }} ms</span>
            </span>
          </div>
          <div class="muted small break">Embedding：{{ s.vector.embedding_url }}</div>
          <div class="muted small break">Rerank：{{ s.vector.rerank_url }}</div>
          <div v-if="s.vector.error" class="err">⚠ {{ s.vector.error }}</div>
        </div>

        <div class="muted small">采集时间：{{ fmtTime(s.generated_at) }}</div>
      </div>
    </a-drawer>
  </a-card>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import type { TableColumnsType } from 'ant-design-vue'
import { getSystemStatsApi } from '@/api'
import type { SystemStats } from '@/types'
import SparkLine from './SparkLine.vue'

// ============ 轮询间隔（右上角可设置，默认 = 性能优化预设：平时 30s / 看详情 10s） ============
const CFG_KEY = 'sys_health_refresh_cfg'
const CFG_DEFAULTS = { idleSec: 30, openSec: 10 } // 0 = 关闭自动刷新
const cfgOpen = ref(false)
const cfgForm = reactive({ ...CFG_DEFAULTS })

/** 规整：0 表示关闭；非 0 需在 5~3600s */
function normalizeSec(v: unknown, dft: number) {
  const n = Number(v)
  if (!Number.isFinite(n) || n < 0) return dft
  if (n === 0) return 0
  return Math.round(Math.min(3600, Math.max(5, n)))
}

function loadCfg() {
  try {
    const raw = localStorage.getItem(CFG_KEY)
    if (raw) {
      const j = JSON.parse(raw)
      cfgForm.idleSec = normalizeSec(j?.idleSec, CFG_DEFAULTS.idleSec)
      cfgForm.openSec = normalizeSec(j?.openSec, CFG_DEFAULTS.openSec)
      return
    }
  } catch {
    /* 读取失败用默认值 */
  }
  Object.assign(cfgForm, CFG_DEFAULTS)
}

function saveCfg() {
  cfgForm.idleSec = normalizeSec(cfgForm.idleSec, CFG_DEFAULTS.idleSec)
  cfgForm.openSec = normalizeSec(cfgForm.openSec, CFG_DEFAULTS.openSec)
  try {
    localStorage.setItem(CFG_KEY, JSON.stringify({ ...cfgForm }))
  } catch {
    /* 隐私模式等写入失败：本次会话内仍生效 */
  }
  startTimer()
  cfgOpen.value = false
  message.success(
    cfgForm.idleSec || cfgForm.openSec
      ? `已保存：平时 ${cfgForm.idleSec || '关闭'}s / 看详情 ${cfgForm.openSec || '关闭'}s`
      : '已保存：自动刷新已关闭（点 🔄 手动刷新）',
  )
}

function resetCfg() {
  Object.assign(cfgForm, CFG_DEFAULTS)
  saveCfg()
}

loadCfg()

const intervalSec = computed(() => (open.value ? cfgForm.openSec : cfgForm.idleSec))
const s = ref<SystemStats | null>(null)
const loading = ref(false)
const open = ref(false)
const winW = ref(typeof window === 'undefined' ? 1440 : window.innerWidth)
let timer: number | undefined

const onResize = () => {
  winW.value = window.innerWidth
}

const drawerWidth = computed(() => (winW.value < 900 ? winW.value - 24 : 760))

const pctColor = (p: number) => (p >= 85 ? '#ff4d4f' : p >= 65 ? '#faad14' : '#52c41a')

const gauges = computed(() => {
  const v = s.value
  const diskPct = v?.disk.items?.length ? Math.max(...v.disk.items.map((d) => d.percent)) : 0
  return [
    {
      key: 'cpu',
      name: 'CPU',
      value: `${v?.cpu.percent ?? 0}%`,
      percent: v?.cpu.percent ?? 0,
      color: pctColor(v?.cpu.percent ?? 0),
      history: v?.cpu.history || [],
      foot: `${v?.cpu.cores ?? 0} 核 · 负载 ${v?.cpu.load1 ?? 0}`,
    },
    {
      key: 'mem',
      name: '内存',
      value: `${v?.memory.percent ?? 0}%`,
      percent: v?.memory.percent ?? 0,
      color: pctColor(v?.memory.percent ?? 0),
      history: v?.memory.history || [],
      foot: `${v?.memory.used_gb ?? 0} / ${v?.memory.total_gb ?? 0} GB`,
    },
    {
      key: 'disk',
      name: '磁盘',
      value: `${diskPct}%`,
      percent: diskPct,
      color: pctColor(diskPct),
      history: v?.disk.history || [],
      foot: `读 ${v?.disk.read_kbps ?? 0} · 写 ${v?.disk.write_kbps ?? 0} KB/s`,
    },
  ]
})

const services = computed(() => {
  const v = s.value
  return [
    {
      name: 'MinIO',
      ok: !!v?.minio.ok,
      value: `${objText()} 对象`,
      tip: v?.minio.ok
        ? `对象存储正常（${v.minio.endpoint}），响应 ${v.minio.latency_ms} ms，共 ${objText()} 个对象 / ${v.minio.size_gb} GB（统计缓存 2 分钟）`
        : `对象存储异常：${v?.minio.error || '无法连接'}`,
    },
    {
      name: 'PostgreSQL',
      ok: !!v?.database.ok,
      value: `${v?.database.size_gb ?? 0} GB`,
      tip: v?.database.ok
        ? `数据库正常（${v.database.version}）：${v.database.connections} 连接 / ${v.database.active} 活跃，库 ${v.database.size_gb} GB，已运行 ${v.database.uptime_h} 小时`
        : `数据库异常：${v?.database.error || '无法连接'}`,
    },
    {
      name: 'Neo4j',
      ok: !!v?.neo4j.ok,
      value: `${v?.neo4j.latency_ms ?? 0} ms`,
      tip: v?.neo4j.ok
        ? `图数据库正常，响应 ${v.neo4j.latency_ms} ms`
        : `图数据库异常：${v?.neo4j.error || '无法连接'}`,
    },
    {
      name: '向量索引',
      ok: !!v?.vector.embedding_ok,
      value: `${v?.vector.indexed ?? 0}/${v?.vector.total ?? 0}`,
      tip: v?.vector.embedding_ok
        ? `Embedding 服务正常（响应 ${v.vector.embedding_latency_ms} ms），已索引 ${v.vector.indexed}/${v.vector.total} 条${v.vector.building ? '（构建中）' : ''}`
        : `Embedding 服务不可达，谱书检索将降级为关键词召回`,
    },
  ]
})

const healthText = computed(() => {
  if (!s.value) return '采集中'
  const bad = services.value.filter((x) => !x.ok).length
  return bad ? `${bad} 项异常` : '全部正常'
})
const healthColor = computed(() => {
  if (!s.value) return 'default'
  return healthText.value === '全部正常' ? 'green' : 'red'
})

const vectorPct = computed(() => {
  const v = s.value?.vector
  if (!v || !v.total) return 0
  return Math.min(100, Math.round((v.indexed / v.total) * 100))
})

const updatedText = computed(() => {
  const t = s.value?.generated_at
  if (!t) return '—'
  return fmtTime(t).slice(-8)
})

const bucketCols: TableColumnsType = [
  { title: '桶', key: 'name', dataIndex: 'name' },
  { title: '对象数', key: 'objects', width: 100 },
  { title: '容量', key: 'size', width: 110 },
]

function fmtNum(n?: number) {
  return (n ?? 0).toLocaleString('zh-CN')
}

/** MinIO 对象数（触及统计上限时以 ≥ 提示真实数量更多） */
function objText() {
  const m = s.value?.minio
  if (!m) return '0'
  return `${fmtNum(m.objects)}${m.objects_capped ? '+' : ''}`
}

function fmtSize(bytes?: number) {
  const b = bytes || 0
  if (b >= 1024 ** 3) return `${(b / 1024 ** 3).toFixed(2)} GB`
  if (b >= 1024 ** 2) return `${(b / 1024 ** 2).toFixed(1)} MB`
  return `${(b / 1024).toFixed(0)} KB`
}

function fmtTime(iso?: string | null) {
  if (!iso) return '—'
  const v = /(Z|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : `${iso}Z`
  const dt = new Date(v)
  if (Number.isNaN(dt.getTime())) return '—'
  return dt.toLocaleString('zh-CN', { hour12: false })
}

async function load() {
  if (loading.value) return
  loading.value = true
  try {
    s.value = await getSystemStatsApi()
  } catch {
    /* 拦截器已提示；保留旧数据 */
  } finally {
    loading.value = false
  }
}

function openDetail() {
  open.value = true
  if (!s.value) load()
}

function startTimer() {
  if (timer) {
    window.clearInterval(timer)
    timer = undefined
  }
  const sec = intervalSec.value
  if (!sec) return // 0 = 关闭自动刷新（仍可点 🔄 手动刷新）
  timer = window.setInterval(() => {
    if (typeof document !== 'undefined' && document.hidden) return
    load()
  }, sec * 1000)
}

// 抽屉开合切换轮询频率
watch(open, () => {
  startTimer()
})

onMounted(() => {
  load()
  startTimer()
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer)
  window.removeEventListener('resize', onResize)
})
</script>

<style scoped>
.gauge {
  border: 1px solid #f0f0f0;
  border-radius: 8px;
  padding: 10px 12px;
  background: linear-gradient(180deg, #fafcff, #fff);
}
.g-top {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 4px;
}
.g-name {
  font-size: 12px;
  color: rgba(0, 0, 0, 0.55);
}
.g-val {
  font-size: 20px;
  font-weight: 600;
  line-height: 1.1;
}
.g-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 2px;
}
.svc {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}
.svc .right {
  margin-left: auto;
}
.pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 9px;
  border-radius: 11px;
  font-size: 12px;
  background: #f6f6f6;
  cursor: default;
  transition: background 0.3s;
}
.pill.ok {
  background: #f6ffed;
}
.pill.bad {
  background: #fff1f0;
}
.pill b {
  font-weight: 600;
}
.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #d9d9d9;
  flex: none;
}
.pill.ok .dot {
  background: #52c41a;
  box-shadow: 0 0 0 0 rgba(82, 196, 26, 0.6);
  animation: dot-pulse 1.8s ease-out infinite;
}
.pill.bad .dot {
  background: #ff4d4f;
}
@keyframes dot-pulse {
  0% {
    box-shadow: 0 0 0 0 rgba(82, 196, 26, 0.6);
  }
  100% {
    box-shadow: 0 0 0 6px rgba(82, 196, 26, 0);
  }
}
.muted {
  color: rgba(0, 0, 0, 0.45);
}
.small {
  font-size: 12px;
}
.empty {
  color: rgba(0, 0, 0, 0.35);
  font-size: 12px;
  padding: 6px 0;
}
.err {
  margin-top: 6px;
  color: #cf1322;
  font-size: 12px;
}
.detail {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.sec {
  border: 1px solid #f0f0f0;
  border-radius: 8px;
  padding: 10px 12px;
}
.sec-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}
.kv-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 22px;
}
.kv {
  display: flex;
  flex-direction: column;
  min-width: 110px;
}
.kv-label {
  font-size: 12px;
  color: rgba(0, 0, 0, 0.45);
}
.kv-val {
  font-size: 15px;
  font-weight: 600;
}
.chart {
  margin-top: 6px;
}
.mount-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
  font-size: 12px;
}
.mount-name {
  width: 132px;
  flex: none;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: rgba(0, 0, 0, 0.65);
}
.mount-bar {
  flex: 1;
  margin-bottom: 0 !important;
}
.mount-size {
  width: 140px;
  flex: none;
  text-align: right;
  color: rgba(0, 0, 0, 0.75);
}
.svc-slim {
  display: flex;
  gap: 8px;
  margin: 8px 0 4px;
}
.break {
  word-break: break-all;
}
</style>
