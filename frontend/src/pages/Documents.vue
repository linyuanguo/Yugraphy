<template>
  <div>
    <a-card :bordered="false">
      <template #title>📂 归档文件</template>
      <div class="toolbar" style="align-items: center">
        <div class="muted" style="line-height: 1.7">
          已写入图谱并归档的<b>扫描件 AI 识别任务</b>（页面图片保留、只读保存，不再随任务删除而清除）。
          点文件名可像审核页一样查看原图与识别结果，但<b>不能修改</b>；发现问题可「↩ 取回」恢复编辑。
        </div>
      </div>
      <a-table
        v-if="archiveTasks.length || archiveLoading"
        :data-source="archiveTasks"
        :columns="archiveColumns"
        :loading="archiveLoading"
        row-key="task_id"
        size="middle"
        :pagination="{ pageSize: 10 }"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'file'">
            <a style="font-weight: 500" @click="openArchive(record)">{{ record.file_path }}</a>
            <div class="muted">{{ record.total_pages }} 页 · 已识别 {{ record.done_pages }} 页</div>
          </template>
          <template v-else-if="column.key === 'lineage'">
            <template v-if="record.lineage_name">
              <a-tag color="blue">{{ record.lineage_name }}</a-tag>
              <a-tag v-if="record.branch_name" color="cyan">{{ record.branch_name }}</a-tag>
            </template>
            <span v-else class="muted">—</span>
          </template>
          <!-- 人工审核标记（归档记录仍展示审核人/时间，便于溯源；重识别/重整理后置失效需重新审核） -->
          <template v-else-if="column.key === 'review'">
            <a-tag
              v-if="record.reviewed"
              color="green"
              :title="'已人工审核' + (record.reviewed_by_name ? ' · 由 ' + record.reviewed_by_name : '') + (record.reviewed_at ? ' · ' + fmtTime(record.reviewed_at) : '')"
              >✅ 已审核</a-tag
            >
            <a-tag
              v-else
              color="orange"
              title="归档前未经人工整卷审核（强制写入或整理后未重审）。可先「取回」在审核页标记已审核后再归档"
              >⚠ 未审核</a-tag
            >
          </template>
          <template v-else-if="column.key === 'time'">
            <div>写入图谱：{{ fmtTime(record.applied_at) }}</div>
            <div class="muted">
              归档：{{ fmtTime(record.archived_at) }}
              <template v-if="record.archived_by_name"> · {{ record.archived_by_name }}</template>
            </div>
          </template>
          <template v-else-if="column.key === 'action'">
            <a-space>
              <a @click="openArchive(record)">👁 只读查看</a>
              <a-popconfirm
                v-if="auth.canEdit"
                title="取回后恢复为普通任务（可在扫描件导入页继续审核/重识别/删除），页面图片全程保留。确认取回？"
                ok-text="取回"
                @confirm="unarchive(record)"
              >
                <a style="color: #1677ff">↩ 取回</a>
              </a-popconfirm>
            </a-space>
          </template>
        </template>
      </a-table>
      <a-empty
        v-if="!archiveLoading && !archiveTasks.length"
        description="暂无归档的识别任务。识别任务写入图谱后，在审核页/任务列表点「归档文件」，即可归档到这里只读保存。"
      />
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { listTasksApi, unarchiveTaskApi } from '@/api'
import { useAuthStore } from '@/stores/auth'
import type { ImportTask } from '@/types'

const auth = useAuthStore()

// ============ AI 识别归档（扫描件任务只读档案） ============
const router = useRouter()
const archiveTasks = ref<ImportTask[]>([])
const archiveLoading = ref(false)
const archiveColumns = [
  { title: '文件（点击进入只读审核）', key: 'file' },
  { title: '谱系 / 房支', key: 'lineage', width: 200 },
  { title: '人工审核', key: 'review', width: 150 },
  { title: '写入图谱 / 归档时间', key: 'time', width: 200 },
  { title: '操作', key: 'action', width: 170 },
]
const fmtTime = (s?: string | null) => (s ? s.replace('T', ' ').slice(0, 19) : '—')

const loadArchive = async () => {
  archiveLoading.value = true
  try {
    const data = await listTasksApi({ archived: true })
    archiveTasks.value = data
  } catch {
    /* 拦截器已提示 */
  } finally {
    archiveLoading.value = false
  }
}
const openArchive = (t: ImportTask) =>
  router.push({ path: `/tasks/${t.task_id}/review`, query: { readonly: '1' } })
const unarchive = async (t: ImportTask) => {
  const res: any = await unarchiveTaskApi(t.task_id)
  const pg = res?.purged
  const extra =
    pg && pg.removed_persons >= 0
      ? `，已清除原写入谱系的数据（内容 ${pg.removed_entries}、向量 ${pg.removed_vectors}、专属人物 ${pg.removed_persons}${pg.removed_lineages ? `、卷号谱系 ${pg.removed_lineages} 个` : ''}）`
      : ''
  message.success('已取回，可在「扫描件导入」中继续处理' + extra)
  loadArchive()
}

onMounted(loadArchive)
</script>

<style scoped>
.toolbar {
  display: flex;
  justify-content: space-between;
  margin-bottom: 16px;
}
.muted {
  color: #999;
  font-size: 12px;
}
</style>
