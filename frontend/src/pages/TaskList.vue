<template>
  <div class="scan-page">
    <a-card class="scan-card">
      <template #title>
        <a-space>
          <span>🤖 扫描件导入</span>
          <a-tooltip title="上传 PDF/TIF 扫描件，系统自动转图并识别，之后到「审核」页校对">
            <span style="color: #999">（PDF / TIF / 图片）</span>
          </a-tooltip>
        </a-space>
      </template>
      <template #extra>
        <a-space v-if="auth.canEdit">
          <a-select
            v-model:value="uploadLineageId"
            style="width: 170px"
            placeholder="选择归属谱系（可选）"
            allow-clear
            @change="onLineageChange"
          >
            <a-select-option v-for="l in lineages" :key="l.lineage_id" :value="l.lineage_id">
              {{ l.name }}
            </a-select-option>
          </a-select>
          <a-select
            v-model:value="uploadBranchId"
            style="width: 140px"
            placeholder="房支（可选）"
            allow-clear
            :disabled="!uploadLineageId"
          >
            <a-select-option v-for="b in currentBranches" :key="b.branch_id" :value="b.branch_id">
              {{ b.name }}
            </a-select-option>
          </a-select>
          <a-tooltip title="新建谱系后回到此处选择；识别写入的人物将归属到所选谱系/房支">
            <a-button size="small" @click="openLineageModal">+ 新建谱系</a-button>
          </a-tooltip>
          <a-tooltip title="已归档任务在「归档文件 → AI 识别归档」查看；需修改/删除先在那里「取回」">
            <a-button size="small" @click="goArchive">📂 已归档（{{ archivedCount }}）</a-button>
          </a-tooltip>
          <a-upload
            :show-upload-list="false"
            :before-upload="onBeforeUpload"
            :multiple="true"
            accept=".pdf,.tif,.tiff,.png,.jpg,.jpeg"
          >
            <a-button type="primary" :loading="importing" size="large">📄 上传扫描件（可多选）开始 AI 提取</a-button>
          </a-upload>
        </a-space>
      </template>

      <div v-if="auth.canEdit" class="muted small" style="margin-bottom: 8px; line-height: 1.8">
        一次导入 = <b>一个谱系</b>：可一次多选该谱系的多个分册文件（如
        <b>J148-001-001-001.pdf</b>、<b>J148-001-001-002.pdf</b>），将自动归入同一谱系
        <b>J148-001-001</b>（文件名前 3 段匹配，找不到则自动创建；也可先在上方「+ 新建谱系」指定）。
        <br />每个文件上传后自动执行两步：<b>① 逐页 AI 提取文字与人物（只读字，快速）→
        ② 整卷统一整理（归并同名人物、推断父子/配偶关系）</b>。两步都完成才算「已完成」并可进入审核；
        第二步整卷整理耗时较长属正常，进度见下方提示。
      </div>

      <a-alert
        v-if="focusTask"
        type="info"
        show-icon
        style="margin-bottom: 16px"
        :message="`📄 ${basename(focusTask.file_path)}　${focusText(focusTask)}`"
      >
        <template #description>
          <a-progress
            :percent="progressPercent(focusTask)"
            :status="focusTask.status === 'pending' || focusTask.stage === 'ready' ? 'normal' : 'active'"
            :stroke-color="focusTask.status === 'pending' || focusTask.stage === 'ready' ? '#b0b7c3' : undefined"
          />
          <div v-if="etaText(focusTask)" class="muted small" style="margin-top: 4px">
            {{ etaText(focusTask) }}
          </div>
          <div v-if="activeTasks.length > 1" class="muted small" style="margin-top: 4px">
            💡 共 {{ activeTasks.length }} 个任务排队/处理中，点击下方任务行可切换查看其进度
          </div>
        </template>
      </a-alert>

      <!-- 重复文件警告：常驻展示，直到本批 AI 识别全部结束或手动关闭 -->
      <a-alert
        v-for="(w, i) in dupWarnings"
        :key="i"
        type="warning"
        show-icon
        closable
        style="margin-bottom: 8px"
        :message="`${w.name}：未创建识别任务（内容重复）`"
        :description="w.detail"
        @close="dismissDup(i)"
      />

      <!-- 审核入口已并入下方任务列表：识别完成(done)的行，点击「文件名/档案号」即可进入审核 -->
      <div class="muted small" style="margin-bottom: 8px">
        💡 识别并整理完成的文件进入审核并点「✅ 标记已审核」后，即可在操作列直接点<b style="color: #1677ff">「整理写入并归档」</b>
        一键写入图谱并归档为只读档案（无需再进审核页）；<b>未审核的任务不能整理写入并归档</b>，
        须先点「审核（N 页）并整理写入归档」进审核页校对整卷人物/关系并「✅ 标记已审核」。
        若对批量识别质量不满意，可在操作列点「🔄 重新识别」整卷重跑一次；仅逐页改过识别内容时，
        审核页点「💾 保存」会同步整卷人物/关系，整理写入时会自动重新整理（重新识别/重新整理后审核标记失效，须重新审核）。
        删除任务会连同其页面图片一并清除（已入库数据不受影响）。
        <b>归档只经由「整理写入并归档」产生</b>（本表无独立归档按钮，写入图谱即归档为只读档案）。
      </div>

      <!-- 任务列表工具栏：选中后出现批量操作 -->
      <div v-if="selectedRowKeys.length && auth.canEdit" class="list-toolbar">
        <a-space>
          <span class="muted small">已选择 {{ selectedRowKeys.length }} 个任务</span>
          <a-button size="small" type="primary" :loading="batchBusy === 'resume'" @click="batchContinue">
            ▶ 继续选中
          </a-button>
          <a-button size="small" :loading="batchBusy === 'pause'" @click="batchPause">
            ⏸ 暂停选中
          </a-button>
          <a-button size="small" :loading="batchBusy === 'reextract'" @click="batchAction('reextract')">
            🔄 重新识别选中
          </a-button>
          <a-button size="small" type="primary" ghost :loading="batchBusy === 'apply'" @click="batchAction('apply')">
            🧬 整理写入并归档选中（仅已审核）
          </a-button>
          <a-button size="small" danger :loading="deletingSel" @click="deleteSelected">
            🗑 删除选中任务
          </a-button>
          <a-button size="small" @click="selectedRowKeys = []">取消选择</a-button>
        </a-space>
      </div>

      <div
        ref="tableAreaRef"
        class="scan-table-area"
        @mousedown="onColResizeDown"
        @mousemove="onColHoverMove"
        @mouseleave="onColHoverLeave"
        @dblclick="onColResizeDblClick"
      >
      <!-- 列交界提示线：鼠标靠近可拖拽的交界时才出现 -->
      <div v-if="edgeLineX !== null" class="col-edge-line" :style="{ left: `${edgeLineX}px` }" />
      <a-table
        :data-source="visibleTasks"
        :loading="loading"
        row-key="task_id"
        :columns="columns"
        :row-selection="rowSelection"
        :pagination="pagination"
        size="middle"
        :scroll="tableScroll"
        :row-class-name="focusedRowClass"
        :custom-row="onCustomRow"
        @change="onTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'file'">
            <a
              v-if="record.status === 'done'"
              class="file-link"
              :title="`${record.file_path}（${record.done_pages}/${record.total_pages} 页已识别，点击进入逐页审核）`"
              @click="$router.push(`/tasks/${record.task_id}/review`)"
            >
              📄 {{ basename(record.file_path) }}
            </a>
            <span v-else>📄 {{ basename(record.file_path) }}</span>
          </template>
          <template v-else-if="column.key === 'lineage'">
            <template v-if="record.lineage_name">
              <a-tag color="blue">{{ record.lineage_name }}</a-tag>
              <a-tag v-if="record.branch_name" color="cyan">{{ record.branch_name }}</a-tag>
            </template>
            <span v-else class="muted">—</span>
          </template>
          <template v-else-if="column.key === 'status'">
            <!-- 状态列说明全部走原生 title：原为每行 3~5 个 antd a-tooltip，
                 100 行表格 = 数百个组件实例 + mousemove 监听，5s 轮询反复重建 → 卡顿 -->
            <a-tag
              :color="statusColor(record.status)"
              :title="record.status === 'paused' ? pausedTip(record) : undefined"
            >
              {{ statusText(record.status, record.stage, record) }}
            </a-tag>
            <a-tag
              v-if="record.status === 'done' && (record.failed_pages || 0) > 0"
              color="orange"
              style="cursor: pointer"
              title="点击查看失效页列表"
              @click="showFailedPages(record)"
            >
              {{ record.failed_pages }} 页失败 ▾
            </a-tag>
            <!-- 缺整卷结果/失效：无法进入整卷审核并标记已审核，须先「🔄 重新整理」补齐（黄色醒目提示） -->
            <a-tag
              v-if="isMissingConsolidation(record)"
              color="gold"
              title="缺整卷整理结果（人物归并/关系推断），暂时无法进整卷审核并「✅ 标记已审核」。请在操作列点「🔄 重新整理」补齐后再审核"
            >
              ⚠ 需重新整理
            </a-tag>
            <!-- 人工审核状态：写入图谱/归档的前置闸（整卷保存过=已审核；老任务按逐页暂存判定） -->
            <a-tag
              v-else-if="record.status === 'done'"
              :color="record.reviewed ? 'green' : 'orange'"
              :title="reviewTip(record)"
            >
              {{ record.reviewed ? '✅ 已审核' : '⚠ 未审核' }}
            </a-tag>
            <a-tag v-if="delPending(record)" color="orange" :title="delPendingTip(record)">
              ⏳ 删除申请审批中
            </a-tag>
            <a-tag
              v-if="delRejected(record)"
              color="gold"
              :title="'删除申请被驳回：' + (delRejected(record)?.review_note || '管理员未填写原因')"
            >
              删除申请被驳回
            </a-tag>
          </template>
          <template v-else-if="column.key === 'progress'">
            <a-progress
              v-if="record.status === 'running' || record.status === 'done' || record.status === 'paused'"
              :percent="record.total_pages ? Math.round((record.done_pages / record.total_pages) * 100) : 0"
              size="small"
              :status="record.status === 'done' ? (record.failed_pages ? 'normal' : 'success') : record.status === 'paused' ? 'normal' : record.stage === 'ready' ? 'normal' : 'active'"
            />
            <span v-else class="muted">—</span>
          </template>
          <template v-else-if="column.key === 'time'">{{ record.created_at?.replace('T', ' ').slice(0, 19) }}</template>
          <template v-else-if="column.key === 'action'">
            <a-space>
              <!-- done 且未归档的任务入口（归档任务已移出本表，故这里显示的 done 卷一律「尚未归档」）：
                   已审核 → 可直接「整理写入并归档」（含早期已写入但未归档的老任务：重写会覆盖旧结果并归档）；
                   未审核 → 进审核页校对并「标记已审核」后才可整理写入并归档 -->
              <!-- 行内一律用原生 title（浏览器 tooltip，零组件开销）：
                   本表最多 100 行 × 原先每行 3~6 个 antd a-tooltip = 数百个组件实例与 mousemove 监听，
                   5s 静默轮询反复重建 → 页面卡顿。说明性文案保留在 title 里，效果不变、成本归零。 -->
              <a-button
                v-if="record.status === 'done' && !record.archived && record.reviewed"
                type="primary"
                size="small"
                :loading="applyArchiveId === record.task_id"
                :title="
                  record.applied
                    ? '本卷早期已写入但尚未归档。点击将按当前审核结果重新整理并覆盖写入图谱，随后归档为只读档案'
                    : '本卷已人工审核，点击直接整理写入图谱并归档为只读档案'
                "
                @click="doApplyAndArchive(record)"
              >
                ✔ 整理写入并归档
              </a-button>
              <!-- 缺整卷结果/失效：不能进整卷审核，主操作改为「🔄 重新整理」补齐整卷结果 -->
              <a-button
                v-else-if="isMissingConsolidation(record)"
                type="primary"
                size="small"
                :loading="reconsolidateId === record.task_id"
                title="缺整卷整理结果，暂时无法进整卷审核。点击只重跑整卷整理（不重跑逐页识别），完成后即可进审核页"
                @click="doReconsolidate(record)"
              >
                🔄 重新整理
              </a-button>
              <a-button
                v-else-if="record.status === 'done' && !record.archived"
                type="primary"
                size="small"
                @click="$router.push(`/tasks/${record.task_id}/review`)"
              >
                审核（{{ record.done_pages }} 页）并整理写入归档
              </a-button>
              <a-button
                v-if="record.status === 'done'"
                size="small"
                :loading="reExtractAllId === record.task_id"
                title="对整卷全部页面（含失败页）重新 AI 识别，完成后自动重新整理（耗时长）"
                @click="doReExtractAll(record)"
              >
                🔄 重新识别
              </a-button>
              <!-- 失败且页面图仍在（MinIO/页镜像有已转页面）：可断点续跑，无需重新上传 -->
              <a-button
                v-if="record.status === 'failed' && record.can_resume"
                size="small"
                :loading="resumingId === record.task_id"
                :title="`${record.error_msg || '任务中断'}。页面图仍在，点击从断点继续，无需重新上传。`"
                @click="doResume(record)"
              >
                ↻ 断点续跑
              </a-button>
              <!-- 失败且页面图已丢失：续跑无意义，只能重新上传 -->
              <a-button
                v-if="record.status === 'failed' && !record.can_resume"
                size="small"
                disabled
                title="原始扫描件与已转页面图都已丢失，无法续跑，请重新上传该卷"
              >
                ⚠ 需重新上传
              </a-button>
              <a-button
                v-if="record.status === 'done' && (record.failed_pages || 0) > 0"
                size="small"
                :loading="resumingId === record.task_id"
                :title="record.error_msg || `有 ${record.failed_pages} 页识别失败，点击自动补识别`"
                @click="doResume(record)"
              >
                ↻ 重试失败页
              </a-button>

              <a-popconfirm
                v-if="record.status === 'done' && !delPending(record)"
                :title="delConfirmTitle(record)"
                @confirm="doDeleteTask(record)"
              >
                <a style="color: #ff4d4f">删除</a>
              </a-popconfirm>
              <a-popconfirm
                v-if="record.status === 'failed' && !delPending(record)"
                :title="delConfirmTitle(record)"
                @confirm="deleteFailedTask(record)"
              >
                <a style="color: #ff4d4f">删除</a>
              </a-popconfirm>
              <!-- 已暂停：可「继续」（断点续跑）或删除 -->
              <a-button
                v-if="record.status === 'paused'"
                size="small"
                :loading="resumingId === record.task_id"
                :title="record.error_msg || '任务已暂停。点击「继续」补齐剩余/失败页并自动整卷整理'"
                @click="doResume(record)"
              >
                ▶ 继续
              </a-button>
              <a-popconfirm
                v-if="record.status === 'paused' && !delPending(record)"
                :title="delConfirmTitle(record)"
                @confirm="deleteFailedTask(record)"
              >
                <a style="color: #ff4d4f">删除</a>
              </a-popconfirm>
              <!-- 处理中/排队中：提供暂停按钮 -->
              <a-popconfirm
                v-if="record.status === 'pending' || record.status === 'running'"
                title="暂停该任务？已识别页会保留；正在识别/整理的当前页或片段完成后即停下，可随时点「▶ 继续」恢复"
                ok-text="暂停"
                cancel-text="取消"
                @confirm="doPause(record)"
              >
                <a-button size="small" :loading="pausingId === record.task_id">⏸ 暂停</a-button>
              </a-popconfirm>
            </a-space>
          </template>
        </template>
      </a-table>
      </div>
    </a-card>

    <!-- 上传前同名预检：发现同名任务时让用户决定跳过或继续 -->
    <a-modal
      v-model:open="dupCheckOpen"
      :title="`同名文件检查（${dupRows.length} 个文件）`"
      ok-text="继续导入未勾选文件"
      cancel-text="取消本次"
      :width="680"
      @ok="confirmPrecheck"
      @cancel="cancelPrecheck"
    >
      <p v-if="dupRows.some((r) => r.existing.length)" style="margin-bottom: 12px" class="small">
        以下文件此前已导入过<b>同名</b>任务（同名通常为同一册档案，可能重复导入）。默认勾选「跳过」；
        确需重新导入请取消勾选（建议先删除旧任务，内容完全相同的文件仍会被系统拦截）。
      </p>
      <div
        v-for="(r, i) in dupRows"
        :key="r.name"
        class="precheck-row"
        :class="{ 'has-dup': r.existing.length }"
      >
        <a-checkbox v-if="r.existing.length" v-model:checked="dupRows[i].skip">跳过</a-checkbox>
        <span class="precheck-name" :class="{ muted: !r.existing.length }">📄 {{ r.name }}</span>
        <template v-if="r.existing.length">
          <div v-for="t in r.existing" :key="t.task_id" class="precheck-old">
            已有任务 {{ t.task_id }} · {{ statusText(t.status) }} · 创建于
            {{ t.created_at ? new Date(t.created_at).toLocaleString() : '—' }}
            {{ t.file_size ? ` · ${(t.file_size / 1048576).toFixed(0)} MB` : '' }}
          </div>
        </template>
        <span v-else class="muted small" style="margin-left: 6px">未导入过同名文件</span>
      </div>
    </a-modal>

    <!-- 快捷新建谱系 -->
    <a-modal
      v-model:open="lineageModal"
      title="新建谱系"
      @ok="saveQuickLineage"
      :confirm-loading="lineageSaving"
    >
      <a-form layout="vertical">
        <a-form-item label="谱系名称" required>
          <a-input v-model:value="quickLineageName" placeholder="如：温岭林家" />
        </a-form-item>
        <a-form-item label="档案编号">
          <a-input
            v-model:value="quickLineageCode"
            placeholder="如：J148-001-001（选填）"
          />
          <div class="muted small">上传文件名（前 3 段）与此编号相同的人物将自动归入本谱系</div>
        </a-form-item>
        <a-form-item label="备注">
          <a-input v-model:value="quickLineageNote" placeholder="可选" />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 失效页列表弹窗 -->
    <a-modal
      v-model:open="failedPagesModal"
      :title="`失效页列表（${failedPagesTaskName}）`"
      :footer="null"
      :width="500"
    >
      <p class="muted small" style="margin-bottom: 12px">
        以下 {{ failedPagesList.length }} 页 AI 识别失败。可进入审核页逐页重识别，或点「↻ 重试失败页」自动批量补识别。
      </p>
      <div style="display: flex; flex-wrap: wrap; gap: 8px">
        <a-tag v-for="p in failedPagesList" :key="p" color="red">第 {{ p }} 页</a-tag>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import {
  createImportTaskApi,
  createLineageApi,
  deleteTaskApi,
  getTaskApi,
  listDeleteRequestsApi,
  listLineagesApi,
  listTasksApi,
  applyArchiveTaskApi,
  pauseTaskApi,
  precheckImportNamesApi,
  reExtractAllTaskApi,
  reconsolidateTaskApi,
  resumeTaskApi,
} from '@/api'
import { useAuthStore } from '@/stores/auth'
import type { DeleteRequestItem, ImportTask, Lineage } from '@/types'

const basename = (p: string) => (p || '').split('/').pop() || p

const auth = useAuthStore()
const router = useRouter()
const tasks = ref<ImportTask[]>([])
const loading = ref(false)
const importing = ref(false)

// 归档任务已移入「AI 识别归档」页（只读，图片保留）；本列表仅展示未归档任务
const archivedCount = computed(() => tasks.value.filter((t) => t.archived).length)
const visibleTasks = computed(() => tasks.value.filter((t) => !t.archived))

// ---------- 「缺整卷结果」异常判定：新管线 done 卷没有可整卷审核/标记的结果 ----------
// 后端列表标记 needs_reconsolidate = 新管线任务缺整卷结果或结果失效；此处仅针对「未审核」的
// done 卷显示「🔄 重新整理」补齐整卷结果（补齐后才能进整卷审核并「✅ 标记已审核」）。
// 已审核卷 / 老任务（本就无整卷结果、走逐页审核）一律不在此列。
const isMissingConsolidation = (t: ImportTask) =>
  t.status === 'done' && !t.archived && !t.reviewed && !!t.needs_reconsolidate

// ---------- 删除申请状态（09-07：操作员删除任务须管理员审批通过） ----------
const delReqs = ref<DeleteRequestItem[]>([])
const delPending = (t: ImportTask) =>
  delReqs.value.find(
    (r) => r.target_type === 'task' && r.target_id === t.task_id && r.status === 'pending',
  )
const delRejected = (t: ImportTask) =>
  auth.isAdmin || delPending(t)
    ? undefined
    : delReqs.value.find(
        (r) => r.target_type === 'task' && r.target_id === t.task_id && r.status === 'rejected',
      )
const delPendingTip = (t: ImportTask) => {
  const r = delPending(t)
  if (!r) return '删除申请待管理员审批'
  const who = r.submitted_by_name ? `（申请人：${r.submitted_by_name}）` : ''
  return `已有删除申请待管理员审批${who}，通过前该任务不可再删除`
}
const reviewTip = (t: ImportTask) => {
  if (!t.reviewed) {
    return '尚未人工审核：未审核不能整理写入并归档。请在审核页校对整卷人物/关系后点「✅ 标记已审核」，再回列表点「整理写入并归档」'
  }
  const who = t.reviewed_by_name ? `（${t.reviewed_by_name}）` : ''
  const when = t.reviewed_at ? ` · ${String(t.reviewed_at).replace('T', ' ').slice(0, 19)}` : ''
  return `已人工审核${who}${when}；重新识别/重新整理后需重新审核`
}
const refreshDelReqs = async () => {
  try {
    delReqs.value = await listDeleteRequestsApi({ target_type: 'task' })
  } catch {
    /* 不阻塞任务列表 */
  }
}
const goArchive = () => router.push({ path: '/documents', query: { tab: 'archive' } })

// ============ 一键「整理写入并归档」（后台：需整理先整理→写入→归档）============
const applyArchiveId = ref('')
/** 后台等待「整理→写入→归档」完成（服务端执行，可离开页面，轮询列表即可） */
const pollArchiveDone = async (taskId: string, key: string, consolidate: boolean) => {
  const deadline = Date.now() + 90 * 60 * 1000
  message.loading({
    content: consolidate
      ? '已排队：先重新整理整卷（内容有变动/结果缺失），整理完成自动整理写入并归档…'
      : '正在整理写入并归档（后台执行，可稍后回来查看）…',
    key,
    duration: 0,
  })
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 5000))
    try {
      const hit = await getTaskApi(taskId)
      if (hit?.archived) {
        message.success({ content: '✅ 已整理写入并归档到「AI 识别归档」', key, duration: 6 })
        return true
      }
    } catch {
      /* 单次轮询失败忽略 */
    }
  }
  message.destroy(key)
  message.warning('处理超时：任务仍在后台执行，请稍后在列表确认是否已归档')
  return false
}
/** 一键「整理写入并归档」：需整理则先重新整理（后台排队）→ 写入图谱 → 归档。仅已审核可执行；未审核一律拦截 */
const doApplyAndArchive = (t: ImportTask) => {
  if (!t.reviewed) {
    message.warning('未审核不能整理写入并归档：请先进入审核页校对整卷人物/关系并点「✅ 标记已审核」')
    return
  }
  Modal.confirm({
    title: `将「${basename(t.file_path)}」整理写入并归档？`,
    content:
      '该卷已人工审核，可按整卷整理结果写入图谱：若整理结果已缺失/失效（如逐页修改过），会先自动重新整理（后台排队，与其它 AI 任务不冲突），' +
      '整理完成自动写入图谱并归档为只读档案：页面图片保留不删，可在「归档文件 → AI 识别归档」查看，发现问题随时可取回继续修改。',
    okText: '整理写入并归档',
    cancelText: '取消',
    onOk: async () => {
      applyArchiveId.value = t.task_id
      const key = `apply-archive-${t.task_id}`
      try {
        const res = await applyArchiveTaskApi(t.task_id, { force: false })
        await pollArchiveDone(t.task_id, key, res.consolidate)
        load()
      } catch {
        message.destroy(key)
        /* 请求拦截器已提示 */
      } finally {
        applyArchiveId.value = ''
      }
    },
  })
}

/** 批量「整理写入并归档」单卷：触发后台写入→归档并轮询到归档完成；超时抛错以便批量计数为失败 */
const batchApplyArchive = async (t: ImportTask) => {
  if (!t.reviewed) throw new Error('未人工审核，不能整理写入并归档')
  const res = await applyArchiveTaskApi(t.task_id, { force: false })
  const key = `batch-apply-archive-${t.task_id}`
  const done = await pollArchiveDone(t.task_id, key, res.consolidate)
  if (!done) {
    message.destroy(key)
    throw new Error('归档超时：任务仍在后台执行，请稍后在列表确认是否已归档')
  }
}

// 阶段文案 + 进度百分比 + 剩余时间估算
const progressPercent = (t: ImportTask) =>
  t.total_pages ? Math.min(100, Math.round((t.done_pages / t.total_pages) * 100)) : 0

const runningText = (t: ImportTask) => {
  if (!t.total_pages) return '解析文档中…'
  if (t.stage === 'converting') return `转图 ${t.done_pages}/${t.total_pages} 页`
  if (t.stage === 'extracting') return `AI 识别 ${t.done_pages}/${t.total_pages} 页`
  if (t.stage === 'consolidating') return `整卷整理（归并人物 / 推断关系）${t.done_pages}/${t.total_pages}`
  if (t.stage === 'ready') return '转图完成，等待 AI 识别'
  return `处理中 ${t.done_pages}/${t.total_pages} 页`
}

const etas = reactive<Record<string, number>>({})
// 识别阶段起始探测（进入 extracting 后的首个进度样本），用整体平均速率估算，
// 避免「两轮轮询恰好跨过一页完成点」被当成瞬时高速、产生几十分钟的乐观误差
const probeStart = new Map<string, { done: number; at: number }>()

const etaText = (t: ImportTask) => {
  const sec = etas[t.task_id]
  if (!sec || sec <= 0) return ''
  const h = Math.floor(sec / 3600)
  const m = Math.max(1, Math.round((sec % 3600) / 60))
  return h > 0 ? `预计还需 ${h} 时 ${m} 分` : `预计还需约 ${m} 分钟`
}

const lineages = ref<Lineage[]>([])
const uploadLineageId = ref<string>()
const uploadBranchId = ref<string>()

const lineageModal = ref(false)
const lineageSaving = ref(false)
const quickLineageName = ref('')
const quickLineageNote = ref('')
const quickLineageCode = ref('')

const currentBranches = computed(() => {
  const l = lineages.value.find((x) => x.lineage_id === uploadLineageId.value)
  return l?.branches || []
})

let pollTimer: number | undefined

// 后台标签页切回前台时补一次刷新（静默），避免轮询被后台跳过导致进度显示落后
const onVisChange = () => {
  if (!document.hidden) void load(true)
}

// 活跃任务（排队 + 处理中）：运行中的排前面（真正有进度），同状态按创建时间先后排（排队顺序）
const activeTasks = computed(() =>
  tasks.value
    .filter((t) => t.status === 'pending' || t.status === 'running')
    .sort((a, b) => {
      if (a.status !== b.status) return a.status === 'running' ? -1 : 1
      return String(a.created_at || '').localeCompare(String(b.created_at || ''))
    }),
)
// 顶部进度条当前展示的任务：点击表格行后跟随该行；未点击时展示第一个活跃任务
// （若点击的行已不在活跃队列=已完成/失败，则回落展示当前活跃任务）
const activeRowKey = ref<string>()
const focusTask = computed(() => {
  const act = activeTasks.value
  if (!act.length) return undefined
  if (activeRowKey.value) {
    const hit = act.find((t) => t.task_id === activeRowKey.value)
    if (hit) return hit
  }
  return act[0]
})
const focusText = (t: ImportTask) =>
  t.status === 'pending' ? '⏳ 排队中（等待开始）' : runningText(t)
// 点击任意任务行：顶部进度条切换到该任务并高亮该行，同时刷新列表（获取该任务最新状态）
const onCustomRow = (record: ImportTask): Record<string, unknown> => ({
  onClick: () => {
    // 刚拖完列宽的那一下 click 不算「选中该行」
    if (justResized) return
    activeRowKey.value = record.task_id
    load()
  },
})
const focusedRowClass = (record: ImportTask) =>
  record.task_id === activeRowKey.value ? 'focused-row' : ''

// 暂停原因判定：优先用后端 pause_reason（manual=人工 / priority_ai=整卷整理让位给 AI 识别）；
// 老数据无该字段时回退按 error_msg 是否含「让位」判断
const isYieldPaused = (t: ImportTask) =>
  t.pause_reason ? t.pause_reason === 'priority_ai' : !!t.error_msg && t.error_msg.includes('让位')
const statusText = (s: string, stage?: string, t?: ImportTask | null) => {
  if (s === 'running' && stage === 'consolidating') return '整卷整理中'
  if (s === 'running' && stage === 'ready') return '待识别'
  if (s === 'paused' && stage === 'consolidating')
    return t && isYieldPaused(t) ? '已暂停（整理中断·识别优先）' : '已暂停（整理中断·人工）'
  return ({ pending: '排队中', running: '处理中', done: '已完成', failed: '失败', paused: '已暂停' }[s] || s)
}
const pausedTip = (t: ImportTask) => {
  if (t.stage === 'consolidating') {
    if (isYieldPaused(t)) {
      return '已暂停（整理中断），原因：本卷整理自动让位给排队中的 AI 识别任务（识别优先）。待识别任务跑完后会自动续跑整理，无需手动操作'
    }
    return '已暂停（整理中断），原因：人工暂停（整卷整理未完成，半成品已保留）。可点「▶ 继续」或「🔄 重新整理」让整卷重跑整理'
  }
  return t.error_msg || '已暂停：当前页/片段识别完成后即在安全点停下，已识别页保留；可随时点「▶ 继续」从断点恢复'
}
const statusColor = (s: string) =>
  ({ pending: 'default', running: 'processing', done: 'success', failed: 'error', paused: 'warning' }[s] || 'default')

const load = async (silent = false) => {
  // 后台轮询 = 静默刷新：不闪 loading 遮罩（否则每 4 秒整表转圈闪烁）；页面切到后台标签页时跳过本轮到前台再刷
  if (silent && typeof document !== 'undefined' && document.hidden) return
  if (!silent) loading.value = true
  try {
    const data = await listTasksApi()
    tasks.value = data
    // 归档任务已移入档案库（本表不再展示），顺带清掉其残留勾选，避免误删归档任务
    const archIds = new Set(data.filter((t) => t.archived).map((t) => t.task_id))
    if (archIds.size && selectedRowKeys.value.some((id) => archIds.has(id))) {
      selectedRowKeys.value = selectedRowKeys.value.filter((id) => !archIds.has(id))
    }
    // 刷新/删除后行数可能变化：当前页超出最大页时回落到最后一页（避免显示空页）
    const lastPage = Math.max(1, Math.ceil(visibleTasks.value.length / pagination.pageSize))
    if (pagination.current > lastPage) pagination.current = lastPage
    // ETA：仅 AI 识别（extracting）阶段估算，用进入识别以来的整体平均速率
    const now = Date.now()
    for (const t of tasks.value) {
      if (t.status === 'running' && t.stage === 'extracting' && t.total_pages > 0 && t.done_pages > 0) {
        const st = probeStart.get(t.task_id)
        if (!st) {
          probeStart.set(t.task_id, { done: t.done_pages, at: now })
        } else if (st.at && now - st.at > 60000) {
          // 已完成页数足够（>=2）才开始估算，避免开头个别页抖动
          const finished = t.done_pages - st.done
          if (finished >= 2) {
            const secPerPage = (now - st.at) / 1000 / finished
            etas[t.task_id] = Math.round((t.total_pages - t.done_pages) * secPerPage)
          }
        }
      } else {
        probeStart.delete(t.task_id)
        delete etas[t.task_id]
      }
    }
  } finally {
    loading.value = false
    nextTick(() => requestAnimationFrame(measureTable))
  }
  // 仅存在活跃任务（排队/处理中）时才保持轮询刷新；无活动任务时列表静止，点任务行才刷新
  const hasActive = tasks.value.some(
    (t) => t.status === 'pending' || t.status === 'running',
  )
  if (hasActive) {
    if (!pollTimer) pollTimer = window.setInterval(() => void load(true), 5000)
  } else if (pollTimer) {
    window.clearInterval(pollTimer)
    pollTimer = undefined
  }
}

const loadLineages = async () => {
  try {
    lineages.value = await listLineagesApi()
  } catch {
    /* 忽略 */
  }
}

const onLineageChange = () => {
  uploadBranchId.value = undefined
}

// ============ 整卷重新整理（仅重跑第二段人物归并/关系推断，补齐缺失或失效的整卷结果） ============
const reconsolidateId = ref('')
const doReconsolidate = (t: ImportTask) => {
  const name = basename(t.file_path)
  Modal.confirm({
    title: `为「${name}」重新整理整卷人物/关系？`,
    content:
      '本卷识别已完成，但缺整卷整理结果（或结果已失效），暂时无法进入整卷审核并「✅ 标记已审核」。\n' +
      '点击将只重跑「整卷整理」（归并人物 / 推断世系关系），不重跑逐页识别，较快完成；\n' +
      '完成后即可进审核页校对整卷人物/关系并标记已审核，再整理写入并归档。',
    okText: '🔄 重新整理',
    cancelText: '取消',
    onOk: async () => {
      reconsolidateId.value = t.task_id
      try {
        await reconsolidateTaskApi(t.task_id)
        message.success('已开始整卷重新整理，完成后即可进审核页校对整卷人物/关系并标记已审核')
        load()
      } catch {
        /* request 拦截器已提示 */
      } finally {
        reconsolidateId.value = ''
      }
    },
  })
}

// ============ 整卷重新识别（全部页重跑 AI 提取，完成后自动重新整理整卷） ============
const reExtractAllId = ref('')
const doReExtractAll = (t: ImportTask) => {
  const total = t.total_pages || 0
  const mins = total ? Math.max(3, Math.round((total * 6) / 60)) : undefined
  Modal.confirm({
    title: `整卷重新 AI 识别「${basename(t.file_path)}」？`,
    content:
      `将对全部 ${total || '?'} 页（含失败页）重新调用 AI 识别，` +
      (mins ? `预计约 ${mins} 分钟，` : '') +
      '完成后自动重新整理整卷人物/关系。\n' +
      '已写入图谱的数据不受影响；审核页尚未「暂存」的人工校对会被新识别覆盖。',
    okText: '开始重新识别',
    cancelText: '取消',
    onOk: async () => {
      reExtractAllId.value = t.task_id
      try {
        await reExtractAllTaskApi(t.task_id)
        message.success('已开始整卷重新识别，进度见上方；完成后自动重新整理整卷')
        load()
      } catch {
        /* request 已提示 */
      } finally {
        reExtractAllId.value = ''
      }
    },
  })
}

// ============ 断点续跑（失败/失败页自动补识别，无需重传；已暂停任务亦可「继续」） ============
const resumingId = ref('')
const doResume = async (t: ImportTask) => {
  resumingId.value = t.task_id
  try {
    await resumeTaskApi(t.task_id)
    message.success(
      t.status === 'paused'
        ? t.stage === 'consolidating'
          ? '已开始续跑：将重新执行整卷整理（人物归并/关系推断），进度见上'
          : '已开始续跑：继续补齐剩余/失败页面并自动整卷整理，无需重新上传，进度见上'
        : '已开始续跑：继续识别剩余/失败页面，无需重新上传，进度见上',
    )
    load()
  } catch {
    /* request 已提示 */
  } finally {
    resumingId.value = ''
  }
}

// ============ 暂停导入任务（处理中/排队中；后台在安全点收尾，已识别页保留） ============
const pausingId = ref('')
const doPause = async (t: ImportTask) => {
  pausingId.value = t.task_id
  try {
    await pauseTaskApi(t.task_id)
    message.success('已请求暂停：当前页/片段识别完成后即停下，已识别页保留，可随时点「▶ 继续」恢复')
    load()
  } catch {
    /* request 已提示 */
  } finally {
    pausingId.value = ''
  }
}

// ============ 失效页列表弹窗 ============
const failedPagesModal = ref(false)
const failedPagesList = ref<number[]>([])
const failedPagesTaskName = ref('')

const showFailedPages = async (t: ImportTask) => {
  try {
    const detail = await getTaskApi(t.task_id)
    failedPagesList.value = (detail.pages || []).filter((p) => p.failed).map((p) => p.page_no)
    failedPagesTaskName.value = basename(t.file_path)
    failedPagesModal.value = true
  } catch {
    /* 请求拦截器已提示 */
  }
}

// ============ 删除失败任务（释放失败残留） ============
const deleteFailedTask = (t: ImportTask) => {
  const isAdmin = auth.isAdmin
  Modal.confirm({
    title: isAdmin ? `删除任务「${t.file_path}」？` : `提交删除申请「${t.file_path}」？`,
    content: isAdmin
      ? '任务记录、已识别缓存与页面图片将一并删除。若该任务已有部分内容写入图谱，已写入的数据不受影响。'
      : '提交后系统不会立即删除：操作员删除任务须管理员审批通过后才会真正执行（含页面图片一并清除）。',
    okText: isAdmin ? '删除' : '提交申请',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      try {
        const res = await deleteTaskApi(t.task_id)
        if (res?.requested) {
          message.success('删除申请已提交，待管理员审批通过后才会真正删除')
        } else {
          message.success('任务已删除')
        }
      } catch {
        /* 请求拦截器已提示 */
      }
      await refreshDelReqs()
      load()
    },
  })
}

// ============ 列定义 + 行选择 + 删除（全选 / 批量 / 单行） ============
// 表头可点击排序（本地排序：后端最多返回 100 条）。状态按「处理中→排队→已暂停→失败→完成」
// 的业务关注度排，进度按完成百分比排，时间默认倒序（最新在前，与后端默认一致）。
const statusSortRank: Record<string, number> = {
  running: 0,
  pending: 1,
  paused: 2,
  failed: 3,
  done: 4,
}
const pctOf = (t: ImportTask) =>
  t.total_pages ? (t.done_pages || 0) / t.total_pages : 0
type TaskCol = {
  title: string
  key: string
  width?: number
  sorter?: (a: ImportTask, b: ImportTask) => number
  defaultSortOrder?: 'ascend' | 'descend'
  filters?: { text: string; value: string }[]
  filterMultiple?: boolean
  onFilter?: (value: unknown, record: ImportTask) => boolean
}
const columns = reactive<TaskCol[]>([
  {
    title: '文件 / 档案号',
    key: 'file',
    sorter: (a: ImportTask, b: ImportTask) =>
      String(a.file_path || '').localeCompare(String(b.file_path || ''), 'zh'),
  },
  {
    title: '谱系',
    key: 'lineage',
    sorter: (a: ImportTask, b: ImportTask) =>
      String(a.lineage_name || a.lineage_id || '').localeCompare(
        String(b.lineage_name || b.lineage_id || ''),
        'zh',
      ),
  },
  {
    title: '状态',
    key: 'status',
    sorter: (a: ImportTask, b: ImportTask) =>
      (statusSortRank[a.status] ?? 9) - (statusSortRank[b.status] ?? 9),
    // 表头漏斗：按状态筛选（可多选）。选项文案复用 statusText，保证与列表里显示的文字一致
    filters: ['running', 'pending', 'paused', 'failed', 'done'].map((s) => ({
      text: statusText(s),
      value: s,
    })),
    filterMultiple: true,
    onFilter: (value: unknown, record: ImportTask) => record.status === value,
  },
  {
    title: '进度',
    key: 'progress',
    sorter: (a: ImportTask, b: ImportTask) => pctOf(a) - pctOf(b),
  },
  {
    title: '上传时间',
    key: 'time',
    defaultSortOrder: 'descend' as const,
    sorter: (a: ImportTask, b: ImportTask) =>
      String(a.created_at || '').localeCompare(String(b.created_at || '')),
  },
  { title: '操作', key: 'action' },
])

// ============ 列宽可拖拽调节 ============
// 做法：不动 antd 的表头渲染（否则容易盖掉排序箭头/筛选漏斗），只用事件委托在
// 「相邻两列的交界处」（交界左右各 5px 内）响应拖拽。
// ⚠️ 命中判断必须按「表头边界的绝对坐标」算，不能按「鼠标所在 th 的右边缘」算：
//    交界处鼠标稍微偏向右边那一列时，closest('th') 会命中右列，而右列的右边缘离得
//    很远，于是被判成不可拖 —— 旧版就是这么失效的（只有交界左侧几像素有反应，
//    鼠标一偏右就"闪一下"然后没反应）。按边界坐标算则交界左右两侧都能命中。
// 首次拖拽先把各列当前实测宽度冻结成固定宽度（否则总宽算不准、切 fixed 布局会跳），
// 之后按鼠标位移调整；结果存 localStorage，下次进页面自动恢复。
// 双击交界处 = 该列恢复内容自适应。
const COL_WIDTHS_KEY = 'tasklist_col_widths_v1'
const EDGE_HIT_PX = 5
const MIN_COL_WIDTH = 60

let colDrag: { index: number; startX: number; startWidth: number } | null = null
let justResized = false
// 交界提示线相对表格容器左边的 px（null = 不显示）
const edgeLineX = ref<number | null>(null)

const headerThs = (): HTMLElement[] => {
  const row = tableAreaRef.value?.querySelector('.ant-table-thead tr')
  return row ? (Array.from(row.children) as HTMLElement[]) : []
}
// 表头首列是勾选框（ant-table-selection-column），它不是 columns 里的一项，需跳过
const colIndexOffset = () => {
  const ths = headerThs()
  return ths.length && ths[0].classList.contains('ant-table-selection-column') ? 1 : 0
}
// 命中「列交界」：返回被拖动的列（交界左侧那一列）的下标 + 交界线的视口 x 坐标
const hitColumnEdge = (e: MouseEvent): { index: number; right: number } | null => {
  const ths = headerThs()
  if (!ths.length) return null
  const offset = colIndexOffset()
  for (let i = offset; i < ths.length; i++) {
    const right = ths[i].getBoundingClientRect().right
    if (Math.abs(e.clientX - right) <= EDGE_HIT_PX) return { index: i - offset, right }
  }
  return null
}
const containerLeft = () => tableAreaRef.value?.getBoundingClientRect().left ?? 0
// 只在数值真正变化时写响应式变量，避免每个 mousemove 都触发一次组件更新
const showEdgeLine = (clientX: number | null) => {
  const next = clientX === null ? null : clientX - containerLeft()
  if (next !== edgeLineX.value) edgeLineX.value = next
}
// th 及其子元素自带 cursor 会盖住父容器的设置，所以用 class + !important 统一控制
let cursorState: string | null = null
const setColCursor = (active: boolean, dragging = false) => {
  const key = `${active}|${dragging}`
  if (key === cursorState) return
  cursorState = key
  const el = tableAreaRef.value
  if (el) {
    el.classList.toggle('col-edge-active', active && !dragging)
    el.classList.toggle('col-resizing', dragging)
  }
  // 拖拽中鼠标可能移出表格区域，光标设在 body 上才跟手
  document.body.style.cursor = dragging ? 'col-resize' : ''
  document.body.style.userSelect = dragging ? 'none' : ''
}
const totalWidth = () => columns.reduce((sum, c) => sum + (c.width || 0), 0)
const syncScrollX = () => {
  const w = totalWidth()
  // 所有列都有固定宽度时用固定总宽（antd 据此切到 table-layout: fixed），否则按内容自适应
  tableScroll.x = columns.every((c) => c.width) && w > 0 ? w : 'max-content'
}
const freezeColumnWidths = () => {
  const ths = headerThs()
  const offset = colIndexOffset()
  const needInit = columns.some((c) => !c.width)
  ths.forEach((th, i) => {
    const idx = i - offset
    if (idx >= 0 && idx < columns.length && !columns[idx].width) {
      columns[idx].width = Math.round(th.getBoundingClientRect().width)
    }
  })
  // 仅首次冻结时把与容器宽的差额补给最后一列（原本 max-content 是铺满容器的，
  // 不补的话一切到 fixed 布局右侧就会突然留白）。
  // 之后不再补，这样用户主动拖窄某列时总宽会如实变小，而不是被最后一列又撑满。
  if (needInit) {
    const gap = (tableAreaRef.value?.clientWidth || 0) - totalWidth()
    const last = columns[columns.length - 1]
    if (gap > 0 && last) last.width = (last.width || 0) + gap
  }
  syncScrollX()
}
const saveColWidths = () => {
  const saved: Record<string, number> = {}
  for (const c of columns) if (c.width) saved[c.key] = c.width
  try {
    localStorage.setItem(COL_WIDTHS_KEY, JSON.stringify(saved))
  } catch {
    /* 隐私模式下 localStorage 可能不可用，忽略即可 */
  }
}
const restoreColWidths = () => {
  try {
    const raw = localStorage.getItem(COL_WIDTHS_KEY)
    if (!raw) return
    const saved = JSON.parse(raw) as Record<string, number>
    for (const c of columns) {
      const w = saved[c.key]
      if (typeof w === 'number' && w >= MIN_COL_WIDTH) c.width = w
    }
    syncScrollX()
  } catch {
    /* 存档损坏就用默认自适应 */
  }
}
// 鼠标停在列交界处时给 col-resize 光标 + 显示交界提示线，平时不干扰表头样式
const onColHoverMove = (e: MouseEvent) => {
  if (colDrag) return
  const hit = hitColumnEdge(e)
  setColCursor(!!hit)
  showEdgeLine(hit ? hit.right : null)
}
const onColHoverLeave = () => {
  if (colDrag) return
  setColCursor(false)
  showEdgeLine(null)
}
const onColResizeDown = (e: MouseEvent) => {
  if (e.button !== 0) return
  const hit = hitColumnEdge(e)
  if (!hit) return
  freezeColumnWidths()
  colDrag = { index: hit.index, startX: e.clientX, startWidth: columns[hit.index].width || 0 }
  e.preventDefault()
  e.stopPropagation()
  setColCursor(true, true)
  showEdgeLine(e.clientX)
  document.addEventListener('mousemove', onColResizeMove)
  document.addEventListener('mouseup', onColResizeUp)
}
const onColResizeMove = (e: MouseEvent) => {
  if (!colDrag) return
  showEdgeLine(e.clientX)
  const next = Math.max(MIN_COL_WIDTH, Math.round(colDrag.startWidth + (e.clientX - colDrag.startX)))
  if (columns[colDrag.index].width === next) return
  columns[colDrag.index].width = next
  syncScrollX()
}
const onColResizeUp = () => {
  document.removeEventListener('mousemove', onColResizeMove)
  document.removeEventListener('mouseup', onColResizeUp)
  setColCursor(false)
  showEdgeLine(null)
  if (!colDrag) return
  colDrag = null
  saveColWidths()
  // 抑制紧接着触发的行 click（否则拖完会被当成点了这一行）
  justResized = true
  setTimeout(() => (justResized = false), 120)
  nextTick(() => requestAnimationFrame(measureTable))
}
const onColResizeDblClick = (e: MouseEvent) => {
  const hit = hitColumnEdge(e)
  if (!hit) return
  columns[hit.index].width = undefined
  // 该列回到自适应后不再是「所有列都有固定宽度」→ syncScrollX 会把 scroll.x 还原成 max-content
  syncScrollX()
  saveColWidths()
}

// ============ 分页：每页 20/50/100 条，默认 20 ============
const pagination = reactive({
  current: 1,
  pageSize: 20,
  showSizeChanger: true,
  pageSizeOptions: ['20', '50', '100'],
  showTotal: (total: number) => `共 ${total} 条`,
})
// 记住上一次的状态筛选：只有筛选条件变化才回到第一页（否则会停在被筛掉的空页上）；
// 单纯翻页 / 改每页条数时不重置，避免翻页被弹回第一页。
let lastStatusFilterKey = ''
const onTableChange = (
  p: { current?: number; pageSize?: number },
  filters?: Record<string, unknown>,
) => {
  const key = JSON.stringify(filters?.status ?? null)
  const filterChanged = key !== lastStatusFilterKey
  lastStatusFilterKey = key
  if (filterChanged) {
    pagination.current = 1
  } else if (p.current) {
    pagination.current = p.current
  }
  if (p.pageSize) pagination.pageSize = p.pageSize
  nextTick(() => requestAnimationFrame(measureTable))
}

// ============ 工作台式：外壳锁可视高，操作条常驻，表格在剩余高度内滚动 ============
const tableAreaRef = ref<HTMLDivElement>()
const tableScroll = reactive<{ x: string | number; y: number }>({ x: 'max-content', y: 400 })
const measureTable = () => {
  const wrap = tableAreaRef.value
  if (!wrap || !wrap.isConnected) return
  const pag = wrap.querySelector<HTMLElement>('.ant-pagination')
  const head =
    wrap.querySelector<HTMLElement>('.ant-table-header') ||
    wrap.querySelector<HTMLElement>('.ant-table-thead')
  const used = (pag ? pag.offsetHeight : 46) + (head ? head.offsetHeight : 0) + 4
  const h = Math.max(200, Math.floor(wrap.clientHeight - used))
  if (h !== tableScroll.y) tableScroll.y = h
}
let tableResizeObs: ResizeObserver | undefined

const selectedRowKeys = ref<string[]>([])
const rowSelection = computed(() => ({
  selectedRowKeys: selectedRowKeys.value,
  preserveSelectedRowKeys: true,
  onChange: (keys: any[]) => {
    selectedRowKeys.value = keys as string[]
  },
}))

const deletingSel = ref(false)

// 单行删除（done / failed 均可；已写入图谱的数据不受影响）
// 单行删除确认文案：管理员=直接删除；操作员=提交删除申请（须管理员审批）
const delConfirmTitle = (t: ImportTask) => {
  const base =
    t.status === 'done'
      ? '删除该任务及其全部页面图片？（已写入图谱的数据不受影响，删除后无法再对照原图校对）'
      : t.status === 'paused'
        ? '删除该已暂停任务？（识别成果与页面图一并清除；已写入图谱的数据不受影响）'
        : '删除该失败任务？（页面图与结果一并清除）'
  return auth.isAdmin ? base : `提交删除申请：${base}（提交后须管理员审批通过才会真正删除）`
}

// 单行删除（done / failed / paused 均可；已写入图谱的数据不受影响）
const doDeleteTask = async (t: ImportTask) => {
  try {
    const res = await deleteTaskApi(t.task_id)
    if (res?.requested) {
      message.success('删除申请已提交，待管理员审批通过后才会真正删除')
      return
    }
    message.success('任务已删除')
    selectedRowKeys.value = selectedRowKeys.value.filter((id) => id !== t.task_id)
  } catch {
    /* 请求拦截器已提示 */
  } finally {
    await refreshDelReqs()
    load()
  }
}

// 批量删除选中的任务（09-07：操作员提交删除申请；管理员直接删除）
const deleteSelected = () => {
  const isAdmin = auth.isAdmin
  const all = tasks.value.filter((t) => selectedRowKeys.value.includes(t.task_id))
  // 已有待审批删除申请的任务从本次操作中排除（避免绕过审批中心重复操作）
  const sel = all.filter((t) => !delPending(t))
  if (!sel.length) {
    message.info(
      isAdmin
        ? '所选任务均已有待审批的删除申请，请在「系统设置 → 删除审批」中处理'
        : '所选任务均已有待审批的删除申请，无需重复提交',
    )
    return
  }
  const skipped = all.length - sel.length
  const runningCount = sel.filter((t) => t.status === 'pending' || t.status === 'running').length
  Modal.confirm({
    title: isAdmin
      ? `删除选中的 ${sel.length} 个任务？`
      : `提交 ${sel.length} 个任务的删除申请？`,
    content:
      (isAdmin
        ? `任务记录与页面图片将一并删除，已写入图谱的数据不受影响。`
        : `提交后系统不会立即删除，每个任务都须管理员在「系统设置 → 删除审批」逐条通过后才会真正删除。`) +
      (runningCount
        ? `\n⚠ 其中 ${runningCount} 个任务仍在处理中：删除将中断识别，未写入图谱的识别成果会丢失！`
        : '') +
      (skipped
        ? `\n（另有 ${skipped} 个任务已存在待审批删除申请，已自动跳过）`
        : ''),
    okText: isAdmin ? '删除' : '提交申请',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      deletingSel.value = true
      try {
        let removed = 0
        let requested = 0
        for (const t of sel) {
          try {
            const res = await deleteTaskApi(t.task_id)
            if (res?.requested) requested++
            else removed++
          } catch {
            /* 单条失败不阻断，继续处理其余 */
          }
        }
        if (isAdmin) {
          message.success(`已删除 ${removed}/${sel.length} 个任务`)
        } else {
          message.success(
            requested ? `已提交 ${requested} 条删除申请，待管理员审批` : `未能提交删除申请（${removed}/${sel.length}）`,
          )
        }
        selectedRowKeys.value = []
      } finally {
        deletingSel.value = false
        await refreshDelReqs()
        load()
      }
    },
  })
}

// ============ 批量「继续」（选中任务：已暂停 / 失败 / 含失败页 → 逐个断点续跑） ============
const batchBusy = ref('') // '' | 'resume' | 'pause' | 'reextract' | 'apply'
const isResumable = (t: ImportTask) =>
  t.status === 'paused' ||
  // 失败任务只有页面图还在（can_resume）才能续跑；页图已丢的只能重新上传
  (t.status === 'failed' && !!t.can_resume) ||
  (t.status === 'done' && (t.failed_pages || 0) > 0)

const batchContinue = () => {
  if (batchBusy.value) return
  const sel = tasks.value.filter(
    (t) => selectedRowKeys.value.includes(t.task_id) && !t.archived && isResumable(t),
  )
  if (!sel.length) {
    message.info('所选任务中没有可「继续」的（需为已暂停 / 失败 / 含失败页），请重新勾选')
    return
  }
  const skipped = tasks.value.filter(
    (t) => selectedRowKeys.value.includes(t.task_id) && !(!t.archived && isResumable(t)),
  ).length
  Modal.confirm({
    title: `继续选中的 ${sel.length} 个任务？`,
    content:
      '将逐个从断点续跑：补齐剩余/失败页面并自动整卷整理（人物归并、关系推断），无需重新上传，后台排队执行，可离开本页稍后回来查看。\n' +
      '其中已暂停(含整理中断)的任务会从其断点恢复继续。' +
      (skipped ? `\n（另有 ${skipped} 个已勾选任务不满足继续条件，已自动跳过）` : ''),
    okText: '开始继续',
    cancelText: '取消',
    onOk: async () => {
      batchBusy.value = 'resume'
      let ok = 0
      const errs: Array<{ name: string; msg: string }> = []
      const key = 'batch_resume'
      try {
        for (let i = 0; i < sel.length; i++) {
          const t = sel[i]
          message.loading({
            content: `正在继续「${basename(t.file_path)}」（${i + 1}/${sel.length}）…`,
            key,
            duration: 0,
          })
          try {
            await resumeTaskApi(t.task_id)
            ok++
          } catch (e: any) {
            errs.push({
              name: basename(t.file_path),
              msg: (e?.response?.data?.detail as string) || e?.message || '失败',
            })
          }
        }
        message.destroy(key)
        if (!errs.length) {
          message.success(`已发起 ${ok}/${sel.length} 个任务继续，进度见上方/任务行`)
        } else {
          const first = errs.slice(0, 3).map((e) => `${e.name}：${e.msg}`).join('\n')
          message.warning(
            `成功 ${ok}/${sel.length} 个；失败 ${errs.length} 个：\n${first}` +
              (errs.length > 3 ? `\n…等共 ${errs.length} 个` : ''),
            8,
          )
        }
        // 保留勾选：任务恢复为处理中后可立即用「⏸ 暂停选中」反向控制，无需重新勾选
      } finally {
        batchBusy.value = ''
        message.destroy(key)
        load()
      }
    },
  })
}

// ============ 批量「暂停」（选中任务：排队中/处理中 → 逐个请求暂停，安全点收尾，已识别页保留） ============
const isPausable = (t: ImportTask) => t.status === 'pending' || t.status === 'running'

const batchPause = () => {
  if (batchBusy.value) return
  const sel = tasks.value.filter(
    (t) => selectedRowKeys.value.includes(t.task_id) && !t.archived && isPausable(t),
  )
  if (!sel.length) {
    message.info('所选任务中没有可「暂停」的（需为排队中/处理中），请重新勾选')
    return
  }
  const skipped = tasks.value.filter(
    (t) => selectedRowKeys.value.includes(t.task_id) && !(!t.archived && isPausable(t)),
  ).length
  Modal.confirm({
    title: `暂停选中的 ${sel.length} 个任务？`,
    content:
      '正在识别/整理的当前片段或页面完成后即停下（已识别页保留），任务状态变为「已暂停」。\n' +
      '随时可再勾选这些任务点「▶ 继续选中」从断点恢复。' +
      (skipped ? `\n（另有 ${skipped} 个已勾选任务不满足暂停条件，已自动跳过）` : ''),
    okText: '暂停',
    cancelText: '取消',
    onOk: async () => {
      batchBusy.value = 'pause'
      let ok = 0
      const errs: Array<{ name: string; msg: string }> = []
      const key = 'batch_pause'
      try {
        for (let i = 0; i < sel.length; i++) {
          const t = sel[i]
          message.loading({
            content: `正在暂停「${basename(t.file_path)}」（${i + 1}/${sel.length}）…`,
            key,
            duration: 0,
          })
          try {
            await pauseTaskApi(t.task_id)
            ok++
          } catch (e: any) {
            errs.push({
              name: basename(t.file_path),
              msg: (e?.response?.data?.detail as string) || e?.message || '失败',
            })
          }
        }
        message.destroy(key)
        if (!errs.length) {
          message.success(`已请求暂停 ${ok}/${sel.length} 个任务，安全点收尾后变「已暂停」`)
        } else {
          const first = errs.slice(0, 3).map((e) => `${e.name}：${e.msg}`).join('\n')
          message.warning(
            `成功 ${ok}/${sel.length} 个；失败 ${errs.length} 个：\n${first}` +
              (errs.length > 3 ? `\n…等共 ${errs.length} 个` : ''),
            8,
          )
        }
      } finally {
        batchBusy.value = ''
        message.destroy(key)
        load()
      }
    },
  })
}

// ============ 批量操作：重新识别 / 整理写入（仅已审核，作用于勾选任务） ============
const BATCH_CONF = {
  reextract: {
    label: '重新识别',
    title: (n: number) => `整卷重新识别选中的 ${n} 个任务？`,
    content:
      '将逐个整卷重跑 AI 识别（含失败页），后台执行、完成后自动重新整理整卷；' +
      '每卷约需「页数 × 6 秒 + 整卷整理」时间，排队进行，可离开本页稍后回来查看进度。\n' +
      '未完成（排队/处理中/失败/暂停）或已归档的任务会自动跳过。',
    okText: '开始重新识别',
    act: (t: ImportTask) => reExtractAllTaskApi(t.task_id),
  },
  apply: {
    label: '整理写入并归档',
    title: (n: number) => `将选中的 ${n} 个已审核任务整理写入并归档？`,
    content:
      '逐个把已标记「人工已审核」任务的整理结果写入图谱并归档为只读档案（页面图片保留不删，可在「归档文件 → AI 识别归档」查看）：' +
      '整卷结果优先，老任务按逐页结果汇总；整卷整理结果缺失或失效的任务会先自动重新整理再写入并归档，卷多时排队逐卷执行。\n' +
      '⚠ 仅「已完成、已人工审核、未归档」的任务参与：未审核的卷不能整理写入并归档（须先人工审核），已归档会自动跳过。',
    okText: '开始整理写入并归档',
  },
} as const

const batchAction = (kind: 'reextract' | 'apply') => {
  if (batchBusy.value) return
  const conf = BATCH_CONF[kind]
  const baseSel = tasks.value.filter(
    (t) => selectedRowKeys.value.includes(t.task_id) && t.status === 'done' && !t.archived,
  )
  // 「整理写入并归档」要求已人工审核：未审核的卷不能整理写入并归档
  const sel = kind === 'apply' ? baseSel.filter((t) => t.reviewed) : baseSel
  if (!sel.length) {
    message.info(
      kind === 'apply'
        ? '所选任务中没有「已完成、已人工审核、未归档」的任务（未审核的卷不能整理写入并归档），请重新勾选'
        : '所选任务中没有「已完成」且未归档的任务，请重新勾选',
    )
    return
  }
  const skippedNotDone = baseSel.length - sel.length
  const skipped = tasks.value.filter(
    (t) => selectedRowKeys.value.includes(t.task_id) && !(t.status === 'done' && !t.archived),
  ).length + (skippedNotDone)
  const unrev = baseSel.filter((t) => !t.reviewed).length
  const unrevTip =
    kind === 'apply' && unrev
      ? `\n⚠ 其中 ${unrev} 卷尚未标记「人工已审核」，未审核不能整理写入并归档，已自动跳过。`
      : ''
  Modal.confirm({
    title: conf.title(sel.length),
    content:
      conf.content +
      unrevTip +
      (skipped ? `\n（另有 ${skipped} 个任务不满足条件，已自动跳过）` : ''),
    okText: conf.okText,
    cancelText: '取消',
    onOk: async () => {
      batchBusy.value = kind
      let ok = 0
      const errs: Array<{ name: string; msg: string }> = []
      const key = `batch_${kind}`
      try {
        for (let i = 0; i < sel.length; i++) {
          const t = sel[i]
          message.loading({
            content: `正在${kind === 'apply' ? '整理写入并归档' : conf.label}「${basename(t.file_path)}」（${i + 1}/${sel.length}）…`,
            key,
            duration: 0,
          })
          try {
            // 「整理写入」→ 写入并归档一体（后台执行，轮询到该卷归档完成再继续下一卷）；重新识别 → 逐个发起后台重跑
            if (kind === 'apply') await batchApplyArchive(t)
            else await BATCH_CONF.reextract.act(t)
            ok++
          } catch (e: any) {
            errs.push({
              name: basename(t.file_path),
              msg: (e?.response?.data?.detail as string) || e?.message || '失败',
            })
          }
        }
        message.destroy(key)
        if (!errs.length) {
          message.success(`已对 ${ok}/${sel.length} 个任务${kind === 'apply' ? '完成整理写入并归档' : '发起' + conf.label}`)
        } else {
          const first = errs.slice(0, 3).map((e) => `${e.name}：${e.msg}`).join('\n')
          message.warning(
            `成功 ${ok}/${sel.length} 个任务；失败 ${errs.length} 个：\n${first}` +
              (errs.length > 3 ? `\n…等共 ${errs.length} 个` : ''),
            8,
          )
        }
        selectedRowKeys.value = []
      } finally {
        batchBusy.value = ''
        message.destroy(key)
        load()
      }
    },
  })
}

const openLineageModal = () => {
  quickLineageName.value = ''
  quickLineageNote.value = ''
  quickLineageCode.value = ''
  lineageModal.value = true
}

const saveQuickLineage = async () => {
  if (!quickLineageName.value.trim()) {
    message.warning('请填写谱系名称')
    return
  }
  lineageSaving.value = true
  try {
    const l = await createLineageApi({
      name: quickLineageName.value,
      note: quickLineageNote.value,
      code: quickLineageCode.value || undefined,
    })
    uploadLineageId.value = l.lineage_id
    message.success('谱系已创建')
    lineageModal.value = false
    await loadLineages()
  } finally {
    lineageSaving.value = false
  }
}

// ============ 批量上传：多文件排队并发提交 ============
const MAX_PARALLEL_UPLOAD = 2
const MSG_KEY = 'batch_upload'
const batch = reactive({ total: 0, done: 0, failed: 0, rejected: 0, active: 0 })
const waitingQueue: Array<() => void> = []

// 重复文件警告（409）：常驻展示直到本批任务不再有活跃项，或用户手动关闭
const dupWarnings = ref<Array<{ name: string; detail: string }>>([])
let dupClearTimer: number | undefined

const dismissDup = (i: number) => {
  dupWarnings.value.splice(i, 1)
}

watch(
  () => activeTasks.value.length,
  (n) => {
    if (dupClearTimer) {
      window.clearTimeout(dupClearTimer)
      dupClearTimer = undefined
    }
    // 识别全部结束（无排队/处理中任务）后，延迟收起重复提示，避免刚闪完又消失
    if (n === 0 && dupWarnings.value.length) {
      dupClearTimer = window.setTimeout(() => {
        dupWarnings.value = []
        dupClearTimer = undefined
      }, 60000)
    }
  },
)

const acquireSlot = () =>
  new Promise<void>((resolve) => {
    if (batch.active < MAX_PARALLEL_UPLOAD) {
      batch.active++
      resolve()
    } else {
      waitingQueue.push(() => {
        batch.active++
        resolve()
      })
    }
  })

const releaseSlot = () => {
  batch.active--
  const next = waitingQueue.shift()
  if (next) next()
}

const currentUploadPct = ref(0)

const showProgress = () => {
  const sizeHint = batch.done + batch.failed >= batch.total ? '' : '（其余排队中）'
  const pct = batch.active > 0 && currentUploadPct.value ? ` ${currentUploadPct.value}%` : ''
  message.loading({
    content: `上传中 ${batch.done}/${batch.total}${pct}${batch.failed ? `，失败 ${batch.failed}` : ''}${sizeHint}`,
    key: MSG_KEY,
    duration: 0,
  })
}

// ============ 上传前同名预检（真正上传大文件之前先查同名任务） ============
interface PrecheckExisting {
  task_id: string
  status: string
  file_size?: number
  created_at?: string
}
interface PrecheckRow {
  name: string
  file: File
  existing: PrecheckExisting[]
  skip: boolean
}

const pendingFiles = ref<File[]>([])
const dupCheckOpen = ref(false)
const dupRows = ref<PrecheckRow[]>([])
let busyPrecheck = false
let flushTimer: number | undefined

// 拦截 antd 默认上传：先收集本次选择的文件，再做同名预检
const onBeforeUpload = (raw: any) => {
  if (busyPrecheck || dupCheckOpen.value) {
    message.warning('正在处理上一批文件，请稍候再选')
    return false
  }
  const f: File = raw?.originFileObj || raw
  if (f) pendingFiles.value.push(f)
  if (flushTimer) window.clearTimeout(flushTimer)
  flushTimer = window.setTimeout(runPrecheck, 150)
  return false
}

const runPrecheck = async () => {
  flushTimer = undefined
  const files = pendingFiles.value.slice()
  pendingFiles.value = []
  if (!files.length) return
  busyPrecheck = true
  // 同批内同名只保留一个，其余直接计为跳过
  const firstByName = new Map<string, File>()
  let extraSkipped = 0
  for (const f of files) {
    if (firstByName.has(f.name)) extraSkipped++
    else firstByName.set(f.name, f)
  }
  const names = [...firstByName.keys()]
  let results: Array<{ name: string; existing: PrecheckExisting[] }> = []
  try {
    results = await precheckImportNamesApi(names)
  } catch {
    busyPrecheck = false
    return
  }
  dupRows.value = results.map((r) => ({
    name: r.name,
    file: firstByName.get(r.name)!,
    existing: r.existing || [],
    skip: (r.existing || []).length > 0,
  }))
  if (dupRows.value.some((r) => r.existing.length)) {
    // 发现同名任务：弹窗让用户勾选跳过或继续
    dupCheckOpen.value = true
    return
  }
  // 无同名：直接开始上传
  beginUpload(dupRows.value, extraSkipped)
}

const confirmPrecheck = () => {
  dupCheckOpen.value = false
  const toUpload = dupRows.value.filter((r) => !r.skip)
  const skipped = dupRows.value.filter((r) => r.skip).length
  beginUpload(toUpload, skipped)
}

const cancelPrecheck = () => {
  dupCheckOpen.value = false
  busyPrecheck = false
  message.info('已取消，未上传任何文件')
}

const beginUpload = (rows: PrecheckRow[], skippedCount: number) => {
  if (!rows.length) {
    busyPrecheck = false
    if (skippedCount) message.success(`所选文件均已有同名任务，已全部跳过（${skippedCount} 个）`)
    return
  }
  if (skippedCount) message.info(`跳过 ${skippedCount} 个已导入过的同名文件`)
  rows.forEach((r) => void uploadOne(r.file))
}

// ============ 批量上传：多文件排队并发提交 ============
const uploadOne = async (file: File) => {
  importing.value = true
  batch.total++
  currentUploadPct.value = 0
  showProgress()
  await acquireSlot()
  try {
    const task = await createImportTaskApi(
      file,
      {
        lineage_id: uploadLineageId.value,
        branch_id: uploadBranchId.value,
      },
      (pct) => {
        currentUploadPct.value = pct
        showProgress()
      },
      true, // 409 内容重复时静默 toast，改为页面常驻提示
    )
    batch.done++
    load()
  } catch (e: any) {
    const status = e?.response?.status
    if (status === 409) {
      // 内容重复（重度查重，通常为同名不同册文件）：计入 rejected 并常驻提示
      batch.rejected++
      dupWarnings.value.push({
        name: file.name,
        detail: e?.response?.data?.detail || '已存在内容相同的文件（可能在上传/识别中），未重复创建任务',
      })
    } else {
      batch.failed++
    }
  } finally {
    releaseSlot()
    if (batch.done + batch.failed + batch.rejected >= batch.total) {
      // 本批全部结束
      const parts: string[] = []
      if (batch.done) parts.push(`创建 ${batch.done} 个`)
      if (batch.rejected) parts.push(`重复跳过 ${batch.rejected} 个`)
      if (batch.failed) parts.push(`失败 ${batch.failed} 个`)
      if (batch.failed === 0 && batch.done) {
        message.success({ content: `${parts.join('，')}，开始 AI 提取`, key: MSG_KEY })
      } else if (batch.failed) {
        message.warning({ content: parts.join('，'), key: MSG_KEY })
      } else if (batch.rejected) {
        // 仅重复拦截（无成功/失败项）：覆盖并关闭"上传中"气泡
        message.warning({
          content: `${parts.join('，')}：内容相同的文件已在下方任务列表中，未重复创建任务`,
          key: MSG_KEY,
        })
      } else {
        message.destroy(MSG_KEY)
      }
      batch.total = 0
      batch.done = 0
      batch.failed = 0
      batch.rejected = 0
      importing.value = false
      busyPrecheck = false
      load()
    } else {
      showProgress()
    }
  }
}

onMounted(() => {
  load()
  loadLineages()
  refreshDelReqs()
  // 恢复上次拖拽调整过的列宽
  restoreColWidths()
  // 从后台标签页切回时立即静默刷新一次（轮询在后台已被跳过，避免进度落后）
  document.addEventListener('visibilitychange', onVisChange)
  // 表格高度自适应：容器尺寸变化（窗口缩放/提示区增减）时重新测量
  if (typeof ResizeObserver !== 'undefined' && tableAreaRef.value) {
    tableResizeObs = new ResizeObserver(() => requestAnimationFrame(measureTable))
    tableResizeObs.observe(tableAreaRef.value)
  }
  // 轮询由 load() 按活跃任务自动启停；无活跃任务时列表静止，点任务行才刷新
})

onBeforeUnmount(() => {
  if (pollTimer) window.clearInterval(pollTimer)
  document.removeEventListener('visibilitychange', onVisChange)
  tableResizeObs?.disconnect()
})
</script>

<style scoped>
.muted {
  color: #999;
}
.small {
  font-size: 12px;
}
.file-link {
  color: #1677ff;
  cursor: pointer;
  font-weight: 500;
}
.file-link:hover {
  text-decoration: underline;
}
.precheck-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 8px 10px;
  margin-bottom: 8px;
  border: 1px solid #f0f0f0;
  border-radius: 6px;
}
.precheck-row.has-dup {
  border-color: #ffe58f;
  background: #fffbe6;
}
.precheck-name {
  font-weight: 600;
}
.precheck-old {
  width: 100%;
  margin-left: 24px;
  font-size: 12px;
  color: #ad6800;
  line-height: 1.6;
}
.list-toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 8px;
}
/* 点击选中的任务行高亮（顶部进度条跟随显示该行进度） */
.focused-row > td {
  background: #e6f4ff !important;
}

/* ============ 工作台式：外壳锁可视高，表头卡/上传常驻，表格在剩余高度内滚动 ============ */
.scan-page {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.scan-page :deep(.scan-card) {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.scan-page :deep(.scan-card .ant-card-head) {
  flex: none;
}
.scan-page :deep(.scan-card .ant-card-body) {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  /* 极矮窗口下的兜底：说明文案/提示过多时可整体内部滚动 */
  overflow-y: auto;
}
.scan-table-area {
  position: relative; /* 列交界提示线的定位基准 */
  flex: 1;
  min-height: 200px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
/* 列交界提示线：hover 到可拖拽交界 / 拖拽中才显示 */
.col-edge-line {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  margin-left: -1px;
  background: #1677ff;
  opacity: 0.45;
  pointer-events: none;
  z-index: 6;
}
.scan-table-area.col-resizing .col-edge-line {
  opacity: 1;
}
/* 交界命中时表头显示 col-resize；th 及其子元素自带 cursor 会覆盖父级设置，故需 !important */
.scan-table-area.col-edge-active :deep(.ant-table-thead th),
.scan-table-area.col-edge-active :deep(.ant-table-thead th *) {
  cursor: col-resize !important;
}
/* 拖拽中整个表格都显示 col-resize，并禁止选中文字 */
.scan-table-area.col-resizing :deep(.ant-table),
.scan-table-area.col-resizing :deep(.ant-table *) {
  cursor: col-resize !important;
  user-select: none;
}
.scan-table-area :deep(.ant-spin-nested-loading),
.scan-table-area :deep(.ant-spin-container) {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.scan-table-area :deep(.ant-table-wrapper) {
  flex: 1;
  min-height: 0;
}
.scan-table-area :deep(.ant-pagination) {
  flex: none;
  margin: 8px 0 0;
  padding-right: 4px;
}
</style>
