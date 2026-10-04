<template>
  <div>
    <a-alert
      type="info"
      show-icon
      style="margin-bottom: 12px"
      message="系统每天自动扫描一次「没用的数据」：孤儿任务目录、孤儿页面图（MinIO）、上传根目录散落文件、孤儿谱书内容与检索向量（图谱中已无对应谱系）。扫描结果列在下方供人工核对，只有你勾选并点「删除选中」才会真正删除——系统绝不自动删除。正在使用的任务在库中有记录，不会出现在清单里。"
    />

    <a-card size="small" style="margin-bottom: 12px">
      <a-space wrap align="center">
        <a-button type="primary" size="small" :loading="scanning" @click="doScan(true)">
          🔍 立即扫描
        </a-button>
        <a-button
          danger
          size="small"
          :disabled="!selectedKeys.length"
          :loading="cleaning"
          @click="confirmCleanup"
        >
          🗑️ 删除选中（{{ selectedKeys.length }}）
        </a-button>
        <span class="hint-txt">
          上次扫描：{{ lastScanText }}
          <template v-if="scan">
            · 共 {{ scan.total_items }} 项 / {{ fmtSize(scan.total_size) }}
          </template>
        </span>
      </a-space>
    </a-card>

    <a-table
      :data-source="items"
      :columns="columns"
      :row-selection="rowSelection"
      row-key="key"
      size="small"
      :pagination="false"
      :loading="loading"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'kind'">
          <a-tag :color="kindMeta(record.kind).color">{{ kindMeta(record.kind).label }}</a-tag>
        </template>
        <template v-else-if="column.key === 'path'">
          <span class="path-txt" :title="record.path">{{ record.path }}</span>
        </template>
        <template v-else-if="column.key === 'size'">
          {{ fmtSize(record.size) }}
        </template>
        <template v-else-if="column.key === 'mtime'">
          {{ fmtTime(record.mtime) }}
        </template>
        <template v-else-if="column.key === 'note'">
          <span class="hint-txt">{{ record.note }}</span>
        </template>
      </template>
      <template #emptyText>
        <span class="hint-txt">暂无可清理的数据（干净 ✅）</span>
      </template>
    </a-table>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import type { TableColumnsType } from 'ant-design-vue'
import { cleanupStorageApi, getStorageScanApi } from '@/api'
import type { StorageItem, StorageScan } from '@/types'

const loading = ref(false)
const scanning = ref(false)
const cleaning = ref(false)
const scan = ref<StorageScan | null>(null)
const selectedKeys = ref<string[]>([])

const items = computed<StorageItem[]>(() => scan.value?.items || [])

const columns: TableColumnsType = [
  { title: '类型', key: 'kind', dataIndex: 'kind', width: 120 },
  { title: '名称', key: 'name', dataIndex: 'name', width: 180 },
  { title: '路径 / 源目录', key: 'path', dataIndex: 'path', ellipsis: true },
  { title: '大小', key: 'size', dataIndex: 'size', width: 100, align: 'right' },
  { title: '文件数', key: 'files', dataIndex: 'files', width: 80, align: 'right' },
  { title: '修改时间', key: 'mtime', dataIndex: 'mtime', width: 170 },
  { title: '说明', key: 'note', dataIndex: 'note', width: 260 },
]

const rowSelection = computed(() => ({
  selectedRowKeys: selectedKeys.value,
  onChange: (keys: (string | number)[]) => {
    selectedKeys.value = keys.map(String)
  },
}))

function kindMeta(kind: StorageItem['kind']) {
  if (kind === 'local_dir') return { label: '本地目录', color: 'orange' }
  if (kind === 'minio_prefix') return { label: '页面图(MinIO)', color: 'blue' }
  if (kind === 'orphan_content') return { label: '孤儿内容/向量', color: 'red' }
  return { label: '散落文件', color: 'purple' }
}

function fmtSize(b?: number) {
  const n = Number(b || 0)
  if (!n) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i += 1
  }
  return `${i ? v.toFixed(1) : Math.round(v)} ${units[i]}`
}

/** 后端返回的是 UTC 时间，统一按 UTC 解析后转浏览器本地时区展示 */
function fmtTime(iso?: string | null) {
  if (!iso) return '—'
  const s = /(Z|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : `${iso}Z`
  const d = new Date(s)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('zh-CN')
}

const lastScanText = computed(() => fmtTime(scan.value?.scanned_at))

async function doScan(refresh = false) {
  if (refresh) scanning.value = true
  else loading.value = true
  try {
    scan.value = await getStorageScanApi(refresh)
    selectedKeys.value = []
  } catch {
    /* 拦截器已提示 */
  } finally {
    scanning.value = false
    loading.value = false
  }
}

function confirmCleanup() {
  const picked = items.value.filter((i) => selectedKeys.value.includes(i.key))
  if (!picked.length) return
  const total = picked.reduce((sum, i) => sum + (i.size || 0), 0)
  Modal.confirm({
    title: `确认删除选中的 ${picked.length} 项？`,
    width: 560,
    content: `将释放约 ${fmtSize(total)}。此操作不可恢复，但不会影响图谱与系统中已写入的数据（任务记录本身已不存在才会被列为孤儿）。`,
    okText: '确认删除',
    okButtonProps: { danger: true },
    cancelText: '取消',
    onOk: async () => {
      cleaning.value = true
      try {
        const res = await cleanupStorageApi(selectedKeys.value)
        const skipped = res.skipped?.length || 0
        if (res.removed?.length) {
          message.success(
            `已清理 ${res.removed.length} 项，释放 ${fmtSize(res.freed)}${skipped ? `（${skipped} 项跳过）` : ''}`,
          )
        } else {
          message.warning(`没有可删除的项${skipped ? `（${skipped} 项跳过）` : ''}`)
        }
        await doScan(true)
      } catch {
        /* 拦截器已提示 */
      } finally {
        cleaning.value = false
      }
    },
  })
}

onMounted(() => {
  doScan(false)
})
</script>

<style scoped>
.hint-txt {
  color: rgba(0, 0, 0, 0.45);
  font-size: 12px;
}
.path-txt {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
}
</style>
