<template>
  <div v-if="loading" class="center"><a-spin size="large" tip="加载任务…" /></div>

  <div v-else-if="!task" class="center">
    <a-empty description="任务不存在或已删除">
      <a-button type="primary" @click="$router.push('/tasks')">返回任务列表</a-button>
    </a-empty>
  </div>

  <div v-else class="review-page">
    <!-- 顶部工具栏 -->
    <a-card :bordered="false" class="toolbar-card">
      <div class="toolbar-row">
        <a-space>
          <a-button size="small" @click="goBack">{{ readOnly ? '← 归档文件' : '← 任务列表' }}</a-button>
          <span class="task-name">📄 {{ task.file_path }}</span>
          <template v-if="task.lineage_name">
            <a-tag color="blue">{{ task.lineage_name }}</a-tag>
            <a-tag v-if="task.branch_name" color="cyan">{{ task.branch_name }}</a-tag>
            <a-tooltip title="写入图谱的人物将自动归属该谱系/房支">
              <span style="color: #999; font-size: 12px">✨ 自动归属</span>
            </a-tooltip>
          </template>
          <a-tag :color="statusColor">{{ statusText }}</a-tag>
          <!-- 人工审核状态：写入图谱/归档的前置闸（整卷保存过 = 已审核） -->
          <a-tooltip :title="reviewTip">
            <a-tag v-if="task.status === 'done'" :color="reviewedNow ? 'green' : 'orange'">
              {{ reviewedNow ? '✅ 已人工审核' : '⚠ 未审核' }}
            </a-tag>
          </a-tooltip>
          <!-- 归档记录内联展示审核人/时间（无需悬停即可溯源，仅已归档时展示） -->
          <span
            v-if="readOnly && reviewedNow"
            class="reviewed-meta"
          >
            {{ reviewedMetaText }}
          </span>
          <a-tag v-if="task.archived_at" color="purple">📂 已归档（只读档案）</a-tag>
        </a-space>
        <a-space>
          <a-tooltip title="整卷跨页归并/推断的人物清单，点开查看与校对（抽屉内可编辑，不干扰下方原图对照）">
            <a-button v-if="consMode" size="small" type="dashed" @click="openCons('persons')">
              🧑 整卷人物 {{ consPersons.length }}
            </a-button>
          </a-tooltip>
          <a-tooltip title="整卷跨页推断的亲缘/配偶关系，点开查看与校对（抽屉内可编辑，不干扰下方原图对照）">
            <a-button v-if="consMode" size="small" type="dashed" @click="openCons('relations')">
              🕸 整卷关系 {{ consRelations.length }}
            </a-button>
          </a-tooltip>
          <a-button v-if="!readOnly" size="small" :loading="reExtracting" @click="reExtract">
            🔄 重新识别本页 <span class="kbd-inline">R</span>
          </a-button>
          <a-button
            v-if="!readOnly"
            type="primary"
            ghost
            size="small"
            :loading="saving"
            @click="saveStaging"
            title="保存当前审核进度：整卷模式存整卷人物/关系并同步保存有改动的页（含当前页正文/篇目）；逐页模式存本页人物/关系/篇目（断点续审，可随时回来继续）"
          >
            💾 保存 <span class="kbd-inline">S</span>
          </a-button>
          <!-- 人工审核闸门：整卷校对完成后才允许「写入图谱并归档」 -->
          <a-popconfirm
            v-if="!readOnly && consMode && !reviewedNow"
            title="确认整卷人物/关系均已人工校对完成？标记后本卷视为「已人工审核」，方可整理写入并归档（会记录审核人）；未审核时不能整理写入并归档；重新识别/重新整理后本标记失效，需重新审核。"
            ok-text="确认已校对"
            cancel-text="再看看"
            @confirm="markReviewed"
          >
            <a-button size="small" :loading="marking" title="人工确认整卷人物/关系已校对完成：落审核标记，之后才允许整理写入并归档">
              ✅ 标记已审核
            </a-button>
          </a-popconfirm>
          <a-popconfirm
            v-else-if="!readOnly && consMode && reviewedNow"
            title="取消「已人工审核」标记？误标或校对中发现差错时可改回未审核状态（已标记的卷再改动后写入图谱会被要求重新人工审核）。"
            ok-text="取消已审核"
            cancel-text="保留"
            @confirm="unmarkReviewed"
          >
            <a-button size="small" :loading="marking" title="撤掉本卷「已人工审核」标记，改回未审核状态（如误标/需继续校对）">
              ↩ 取消已审核
            </a-button>
          </a-popconfirm>
          <a-button
            v-if="!readOnly"
            type="primary"
            size="small"
            :loading="applyingAll"
            @click="doApply"
            title="一键完成：有改动就先重新整理（后台排队）→ 写入图谱 → 归档为只读档案。须先「✅ 标记已审核」（未审核时须先返回标记，不能整理写入并归档）"
          >
            ✔ 整理写入并归档 <span class="kbd-inline">A</span>
          </a-button>
          <a-divider v-if="!readOnly" type="vertical" />
          <a-popconfirm
            v-if="!readOnly && !delRequestPending"
            :title="delConfirmTitle"
            :ok-text="delOkText"
            ok-button-props="{ danger: true }"
            @confirm="removeTask"
          >
            <a-button size="small" danger :loading="deleting">🗑 {{ delOkText }}</a-button>
          </a-popconfirm>
          <a-tooltip v-else-if="!readOnly && delRequestPending" title="你已提交删除申请，管理员审批通过后本任务才会被删除">
            <a-tag color="orange">⏳ 删除申请审批中</a-tag>
          </a-tooltip>
          <a-tooltip v-if="readOnly && auth.canEdit" title="取回后恢复为普通任务，可在扫描件导入页继续审核/重识别/删除；页面图片全程保留">
            <a-button class="ro-btn-ok" size="small" type="primary" :loading="archiving" @click="doUnarchive">
              ↩ 取回档案库（恢复编辑）
            </a-button>
          </a-tooltip>
        </a-space>
      </div>
      <div class="toolbar-row keyboard-hint">
        <a-space size="small" wrap class="kh-space">
          <span class="hint-title">⌨️ 快捷键：</span>
          <span class="kbd">←/→</span>
          <span class="hint-txt">翻页</span>
          <a-button size="small" @click="prevPage" :disabled="curPageNo <= 1">◀ 上一页</a-button>
          <span class="page-pos">
            第
            <a-input-number
              v-model:value="curPageNo"
              :min="1"
              :max="pages.length"
              size="small"
              class="ro-jump"
              style="width: 70px"
              @change="goPage"
            />
            / {{ pages.length }} 页
          </span>
          <a-button size="small" @click="nextPage" :disabled="curPageNo >= pages.length">下一页 ▶</a-button>
          <template v-if="!readOnly">
            <a-divider type="vertical" />
            <span class="kbd">S</span>
            <span class="hint-txt">保存</span>
            <span class="kbd">A</span>
            <span class="hint-txt">整理写入并归档</span>
            <span class="kbd">R</span>
            <span class="hint-txt">重新识别本页</span>
          </template>
        </a-space>
        <!-- 当前卷"注意"提示（非全局提醒；悬停查看、可逐条暂时关闭、约 5 分钟重提示） -->
        <div v-if="!readOnly && bellItems.length" class="bell-wrap" style="margin-left: auto">
          <a-badge :count="bellItems.length" :offset="[-2, 2]" size="small">
            <span class="bell-ico" title="当前卷注意事项（鼠标悬停查看，非全局提醒）">⚠️ 注意</span>
          </a-badge>
          <div class="bell-panel">
            <div class="bell-head">⚠️ 注意（本卷）</div>
            <div
              v-for="it in bellItems"
              :key="it.kind"
              class="bell-item"
              :class="it.tone"
              @click="bellGo(it)"
            >
              <span class="bell-txt">{{ it.text }}</span>
              <span class="bell-x" title="暂时关闭，5 分钟后再提醒" @click.stop="bellDismiss(it.kind)">✕</span>
            </div>
          </div>
        </div>
      </div>
    </a-card>

    <!-- 卷级注意事项已收进上方「⚠ 注意」徽标（整卷结果缺失/过期、整卷片段失败、识别失败页、未人工审核），悬停可见、可逐条暂时关闭，避免在主区大段跳出 -->

    <!-- 整卷整理完成但确未提取到人物/关系：仅当确无异常提示时才作信息提示（不属铃铛告警） -->
    <a-alert
      v-if="consMode && !consStale && !consFailedChunks && !consPersons.length && !consRelations.length"
      type="info"
      show-icon
      message="整卷整理完成，但未提取到人物/关系"
      description="若该册确无世系人物可忽略；如异常请检查是否有识别失败页，或返回任务列表点「🔄 重新整理」后重试。"
    />

    <!-- 本页识别失败提示 -->
    <a-alert
      v-if="currentPage?.failed"
      type="error"
      show-icon
      :message="`第 ${currentPage.page_no} 页 AI 识别失败：${currentPage.error || '模型未返回可用结果'}`"
      description="本页未提取到内容。点右上角「🔄 重新识别本页」（长超时+分块兜底）重试，或「↻ 批量重识别失效页」。"
    />

    <!-- 整卷整理片段失败详情浮窗 -->
    <a-modal
      v-model:open="consFailModal"
      title="整卷整理部分片段失败"
      :width="560"
      :footer="null"
      @cancel="consFailModal = false"
    >
      <p>
        整卷整理有 <b class="warn-num">{{ consFailedChunks }}</b> 个片段失败，当前整卷人物/关系结果<b>不完整</b>。
        点「✔ 整理写入并归档」时会自动重新整理补齐（后台排队），再写入图谱；也可先在谱系管理中人工补录缺失关系。
      </p>
      <div class="modal-actions">
        <a-button @click="consFailModal = false">知道了</a-button>
      </div>
    </a-modal>

    <!-- AI 识别失败页详情浮窗 -->
    <a-modal
      v-model:open="failedModal"
      title="AI 识别失败页面"
      :width="680"
      :footer="null"
      @cancel="failedModal = false"
    >
      <p class="muted">
        共 {{ failedPages.length }} 页识别失败：该页未提取到内容，也不会进入整卷整理。
        点击下方页号直接跳到对应原图，用右上角「🔄 重新识别本页」逐页重试；或一键批量补识别。
      </p>
      <div class="fail-tags">
        <a-tag v-for="no in failedPages" :key="no" color="red" style="cursor: pointer" @click="jumpToFailedPage(no)">
          第 {{ no }} 页
        </a-tag>
      </div>
      <div class="modal-actions">
        <a-button type="primary" :loading="batchReExtracting" @click="batchReExtract">🔄 批量重识别失效页</a-button>
        <a-button @click="failedModal = false">知道了</a-button>
      </div>
    </a-modal>

    <!-- 整卷数据抽屉（顶栏「整卷人物/整卷关系」打开；全局跨页归并/推断结果，不随下方页面切换。
         点「证据/出现页」可跳回对应原图核对；抽屉编辑后点右上「保存」。不干扰下方原图对照与逐页审核。 -->
    <a-drawer
      v-if="consMode"
      v-model:open="consOpen"
      class="cons-drawer"
      width="980"
      :mask="true"
      :mask-style="{ background: 'rgba(0, 0, 0, 0.12)' }"
      :mask-closable="true"
      :closable="true"
      title="📋 整卷数据（当前卷全局 · 跨页归并/推断结果）"
    >
      <template #extra>
        <a-space wrap>
          <a-tag color="blue">整卷人物 {{ consPersons.length }}</a-tag>
          <a-tag color="purple">整卷关系 {{ consRelations.length }}</a-tag>
          <a-button
            type="primary"
            size="small"
            :loading="saving"
            @click="saveStaging"
            title="暂存整卷人物/关系（断点续审，可随时回来继续）"
          >
            💾 保存 <span class="kbd-inline">S</span>
          </a-button>
        </a-space>
      </template>
      <div class="cons-desc">
        ⚠️ 删除/改名前请先在原图上核对（点「证据页/出现页」可跳转对应原图）；保存并「✅ 标记已审核」后即可整理写入并归档入库。
      </div>
      <a-tabs v-model:active-key="consActiveTab" size="small">
        <a-tab-pane key="persons" :tab="`整卷人物（${consPersons.length}）`">
          <div class="cons-toolbar">
            <a-input v-model:value="consQuery" class="ro-search" size="small" allow-clear placeholder="🔍 搜姓名/籍贯/简介" style="width: 220px" />
            <a-button
              size="small"
              :type="cnOn ? 'primary' : 'default'"
              @click="cnOn = !cnOn"
              title="切换为简体仅辅助阅读显示；手工输入（简体）会自动转回繁体保存（库内/图谱始终存繁体原文）"
            >
              {{ cnOn ? '还原繁体' : '转简体显示' }}
            </a-button>
          </div>
          <a-button size="small" type="dashed" block class="add-row-btn" @click="addConsPerson">
            ＋ 人工添加整卷人物
          </a-button>
          <div v-if="!filteredConsPersons.length" class="empty-hint">
            整卷暂无匹配人物{{ consPersons.length ? '（换关键词搜索）' : '' }}
          </div>
          <a-table
            v-else
            :data-source="filteredConsPersons"
            size="small"
            :row-key="(r: any, i: number) => r.name || i"
            :pagination="consPersonPagination"
            :scroll="{ y: 'calc(100vh - 360px)' }"
          >
            <a-table-column key="name" title="姓名" width="130">
              <template #default="{ record }">
                <a-input
                  :value="zh(record.name)"
                  size="small"
                  placeholder="姓名"
                  style="width: 112px"
                  @input="setField(record, 'name', $event)"
                />
              </template>
            </a-table-column>
            <a-table-column key="gender" title="性别" width="80">
              <template #default="{ record }">
                <a-select v-model:value="record.gender" size="small">
                  <a-select-option value="male">男</a-select-option>
                  <a-select-option value="female">女</a-select-option>
                  <a-select-option value="unknown">未知</a-select-option>
                </a-select>
              </template>
            </a-table-column>
            <a-table-column key="by" title="生年" width="90">
              <template #default="{ record }">
                <a-input-number v-model:value="record.birth_year" size="small" :min="1000" :max="2100" placeholder="生年" style="width: 80px" />
              </template>
            </a-table-column>
            <a-table-column key="dy" title="卒年" width="90">
              <template #default="{ record }">
                <a-input-number v-model:value="record.death_year" size="small" :min="1000" :max="2100" placeholder="卒年" style="width: 80px" />
              </template>
            </a-table-column>
            <a-table-column key="bp" title="出生地">
              <template #default="{ record }">
                <a-input
                  :value="zh(record.birth_place)"
                  size="small"
                  placeholder="出生地"
                  @input="setField(record, 'birth_place', $event)"
                />
              </template>
            </a-table-column>
            <a-table-column key="bio" title="简介">
              <template #default="{ record }">
                <a-input
                  :value="zh(record.biography)"
                  size="small"
                  placeholder="简介（可空）"
                  @input="setField(record, 'biography', $event)"
                />
              </template>
            </a-table-column>
            <a-table-column key="pg" title="出现页" width="80" align="center">
              <template #default="{ record }">
                <!-- 原为 a-tooltip：整卷人物表可达数百行 → 数百个组件实例，
                     改原生 title（浏览器 tooltip，零组件开销），信息不丢 -->
                <a
                  v-if="evTip(record.name)"
                  class="ev-link"
                  :title="evTip(record.name)"
                  @click="goEvidence(evPagesOf(record.name)[0] ?? undefined)"
                >
                  第{{ evPagesOf(record.name)[0] }}页
                </a>
                <span v-else class="muted">—</span>
              </template>
            </a-table-column>
            <a-table-column key="op" title="" width="52" align="center">
              <template #default="{ record }">
                <a-popconfirm title="删除该人物？（不影响逐页底稿）" @confirm="removeConsPerson(record)">
                  <a style="color: #ff4d4f">删除</a>
                </a-popconfirm>
              </template>
            </a-table-column>
          </a-table>
        </a-tab-pane>
        <a-tab-pane key="relations" :tab="`整卷关系（${consRelations.length}）`">
          <div class="cons-toolbar">
            <a-input v-model:value="relQuery" class="ro-search" size="small" allow-clear placeholder="🔍 搜关系方姓名" style="width: 220px" />
            <a-button
              size="small"
              :type="cnOn ? 'primary' : 'default'"
              @click="cnOn = !cnOn"
              title="切换为简体仅辅助阅读显示；手工输入（简体）会自动转回繁体保存（库内/图谱始终存繁体原文）"
            >
              {{ cnOn ? '还原繁体' : '转简体显示' }}
            </a-button>
          </div>
          <a-button size="small" type="dashed" block class="add-row-btn" @click="addConsRelation">
            ＋ 人工添加整卷关系
          </a-button>
          <div v-if="!filteredConsRelations.length" class="empty-hint">
            整卷暂无匹配关系{{ consRelations.length ? '（换关键词搜索）' : '' }}
          </div>
          <a-table
            v-else
            :data-source="filteredConsRelations"
            size="small"
            :row-key="(r: any, i: number) => `${r.type}-${r.from_name}-${r.to_name}-${i}`"
            :pagination="consRelationPagination"
            :scroll="{ y: 'calc(100vh - 360px)' }"
          >
            <a-table-column key="type" title="类型" width="88">
              <template #default="{ record }">
                <a-select v-model:value="record.type" size="small">
                  <a-select-option value="parent_child">亲缘</a-select-option>
                  <a-select-option value="spouse">配偶</a-select-option>
                </a-select>
              </template>
            </a-table-column>
            <a-table-column key="rel" title="关系（父/夫 → 子/妻）">
              <template #default="{ record }">
                <div class="rel-fields">
                  <a-input
                    :value="zh(record.from_name)"
                    size="small"
                    placeholder="父/夫"
                    @input="setField(record, 'from_name', $event)"
                  />
                  <span class="arrow">{{ record.type === 'spouse' ? '⇄' : '→' }}</span>
                  <a-input
                    :value="zh(record.to_name)"
                    size="small"
                    placeholder="子/妻"
                    @input="setField(record, 'to_name', $event)"
                  />
                </div>
              </template>
            </a-table-column>
            <a-table-column key="md" title="婚期" width="110">
              <template #default="{ record }">
                <a-input
                  v-if="record.type === 'spouse'"
                  v-model:value="record.marriage_date"
                  size="small"
                  placeholder="婚期（可选）"
                />
                <span v-else class="muted">—</span>
              </template>
            </a-table-column>
            <a-table-column key="pg" title="证据页" width="96" align="center">
              <template #default="{ record }">
                <div class="rel-ev">
                  <span class="muted">{{ record.type === 'spouse' ? '夫' : '父' }}</span>
                  <a
                    v-if="evTip(record.from_name)"
                    class="ev-link"
                    :title="evTip(record.from_name)"
                    @click="goEvidence(relFromP(record) ?? undefined)"
                  >
                    第{{ relFromP(record) }}页
                  </a>
                  <span v-else class="muted">—</span>
                </div>
                <div class="rel-ev">
                  <span class="muted">{{ record.type === 'spouse' ? '妻' : '子' }}</span>
                  <a
                    v-if="evTip(record.to_name)"
                    class="ev-link"
                    :title="evTip(record.to_name)"
                    @click="goEvidence(relToP(record) ?? undefined)"
                  >
                    第{{ relToP(record) }}页
                  </a>
                  <span v-else class="muted">—</span>
                </div>
              </template>
            </a-table-column>
            <a-table-column key="op" title="" width="52" align="center">
              <template #default="{ record }">
                <a-popconfirm title="删除该关系？" @confirm="removeConsRelation(record)">
                  <a style="color: #ff4d4f">删除</a>
                </a-popconfirm>
              </template>
            </a-table-column>
          </a-table>
        </a-tab-pane>
      </a-tabs>
    </a-drawer>

    <!-- 主区：左图右表 -->
    <div class="review-main">
      <!-- 左：原图 -->
      <a-card :bordered="false" class="panel image-panel">
        <template #title>
          <a-space>
            <span>原图对照（第 {{ curPageNo }} 页）</span>
            <a-tag v-if="currentPage?.reviewed" color="green">已暂存</a-tag>
          </a-space>
        </template>
        <template #extra>
          <a-space>
            <a-button size="small" @click="zoomImage(1.25)">🔍+</a-button>
            <a-button size="small" @click="zoomImage(0.8)">🔍−</a-button>
            <a-button size="small" @click="rotateImage(-90)">⟲ 左转 90°</a-button>
            <a-button size="small" @click="rotateImage(90)">⟳ 右转 90°</a-button>
            <a-button size="small" @click="openPreview">⛶ 放大查看</a-button>
            <a-button size="small" @click="resetView">适应</a-button>
            <span class="muted">{{ Math.round(scale * 100) }}%{{ rotate ? ` · 旋转${rotate}°` : '' }}</span>
          </a-space>
        </template>
        <div
          ref="imgWrapRef"
          class="img-wrap"
          @wheel="onWheel"
          @mousedown="onMouseDown"
          @mousemove="onMouseMove"
          @mouseup="onMouseUp"
          @mouseleave="onMouseUp"
        >
          <div class="img-inner" :style="imgStyle">
            <img
              v-if="currentPage"
              :src="currentPage.image_url"
              :style="{ transform: imgTransform }"
              class="scan-img"
              draggable="false"
            />
          </div>
          <div class="drag-hint">滚轮缩放 · 按住拖动平移</div>
        </div>
        <div v-if="currentPage?.page_notes" class="page-notes">
          <a-alert type="warning" :message="`识别备注：${currentPage.page_notes}`" show-icon style="font-size: 12px" />
        </div>

        <!-- 浮窗放大查看（复用主图同一套缩放/旋转/平移状态） -->
        <a-modal
          v-model:open="previewOpen"
          :footer="null"
          :closable="false"
          :width="previewMaximized ? '100vw' : 'min(96vw, 1500px)'"
          :fullscreen="previewMaximized"
          :wrap-class-name="previewMaximized ? 'img-preview-modal preview-max' : 'img-preview-modal'"
          destroy-on-close
        >
          <div class="preview-bar">
            <span class="preview-title">第 {{ curPageNo }} 页 原图（浮窗）</span>
            <a-space>
              <span class="kbd-hint-inline"><span class="kbd">←/→</span>翻页</span>
              <a-button size="small" @click="zoomImage(1.25)">🔍+</a-button>
              <a-button size="small" @click="zoomImage(0.8)">🔍−</a-button>
              <a-button size="small" @click="rotateImage(-90)">⟲ 左转 90°</a-button>
              <a-button size="small" @click="rotateImage(90)">⟳ 右转 90°</a-button>
              <a-button size="small" @click="resetView">适应</a-button>
              <a-button size="small" :loading="reExtracting" @click="reExtract">🔄 重新识别本页</a-button>
              <a-button size="small" @click="previewMaximized = !previewMaximized">
                {{ previewMaximized ? '⛶ 恢复窗口' : '⛶ 最大化' }}
              </a-button>
              <span class="muted">{{ Math.round(scale * 100) }}%{{ rotate ? ` · 旋转${rotate}°` : '' }}</span>
              <a-button type="primary" size="small" @click="previewOpen = false">关闭</a-button>
            </a-space>
          </div>
          <div class="preview-body">
            <!-- 左：放大图（与主图同一套缩放/旋转/平移状态） -->
            <div class="pv-stage">
              <div
                ref="imgWrapRef"
                class="preview-img-wrap"
                @wheel="onWheel"
                @mousedown="onMouseDown"
                @mousemove="onMouseMove"
                @mouseup="onMouseUp"
                @mouseleave="onMouseUp"
              >
                <div class="img-inner" :style="imgStyle">
                  <img
                    v-if="currentPage"
                    :src="currentPage.image_url"
                    :style="{ transform: imgTransform }"
                    class="scan-img"
                    draggable="false"
                  />
                </div>
                <div class="preview-hint">滚轮缩放 · 按住拖动平移</div>
              </div>
            </div>

            <!-- 右：篇目内容（与主界面同一份数据，改动实时同步；人物/关系统一在顶部「整卷人物/整卷关系」抽屉里校对） -->
            <div class="pv-result">
              <div class="content-head">
                <span class="content-head-title">篇目内容（{{ contentParas.length || 0 }}）</span>
                <a-space :size="4" wrap class="content-head-ops">
                  <span class="muted small">字号</span>
                  <a-input-number
                    v-model:value="paraFs"
                    size="small"
                    :min="8"
                    :max="72"
                    :precision="0"
                    style="width: 76px"
                    title="可直接输入数字或点上下箭头调整（8~72px），作用于 AI 识别框与人工添加框的正文文字（对本页所有段落生效）"
                  />
                  <span class="muted small">px</span>
                  <a-button
                    size="small"
                    :type="cnOn ? 'primary' : 'default'"
                    @click="cnOn = !cnOn"
                    title="切换简体显示辅助阅读；手工输入（简体）会自动转回繁体保存（库内/图谱始终存繁体原文）"
                  >
                    {{ cnOn ? '还原繁体' : '转简体显示' }}
                  </a-button>
                  <a-button size="small" type="dashed" class="add-row-btn" @click="addParagraph">
                    ＋ 人工添加原文段落
                  </a-button>
                </a-space>
              </div>
              <div class="pv-scroll" :style="{ fontSize: (paraFs || 14) + 'px' }">
                <div v-if="!contentParas.length" class="empty-hint">
                  本页暂无识别文字（仅纯图/空白页如此）。若页面有字却为空，可点「🔄 重新识别本页」重试
                </div>
                <template v-else>
                    <div v-for="(c, idx) in contentParas" :key="idx" class="item-card">
                      <div class="item-head">
                        <a-tag v-if="c.manual" color="green" size="small">人工</a-tag>
                        <span class="item-num">#{{ idx + 1 }}</span>
                        <a-space :size="2">
                          <a
                            :class="['mv', idx === 0 ? 'mv-dis' : '']"
                            title="上移"
                            @click="moveParagraph(idx, -1)"
                            >↑</a
                          >
                          <a
                            :class="['mv', idx === contentParas.length - 1 ? 'mv-dis' : '']"
                            title="下移"
                            @click="moveParagraph(idx, 1)"
                            >↓</a
                          >
                          <a-popconfirm title="删除该段？" @confirm="removeParagraph(idx)">
                            <a style="color: #ff4d4f; font-size: 12px">删除</a>
                          </a-popconfirm>
                        </a-space>
                      </div>
                      <a-textarea
                        :value="zh(c.text)"
                        size="small"
                        :auto-size="{ minRows: 3, maxRows: 14 }"
                        placeholder="本页原文段落"
                        :style="{ fontSize: (paraFs || 14) + 'px' }"
                        @input="setField(c, 'text', $event)"
                      />
                    </div>
                  </template>
                </div>
              </div>
            </div>
        </a-modal>
      </a-card>

      <!-- 右：审核结果 -->
      <a-card :bordered="false" class="panel result-panel">
        <template #title>
          <span>识别结果（点击小图切换页面）</span>
        </template>

        <div class="thumb-strip" ref="thumbStripRef" @wheel="onThumbWheel">
          <div
            v-for="p in pages"
            :key="p.page_no"
            class="thumb"
            :class="{ active: p.page_no === curPageNo, failed: p.failed }"
            :data-no="p.page_no"
            @click="gotoPage(p.page_no)"
          >
            <img
              :src="thumbSrc(p)"
              :alt="`第${p.page_no}页`"
              loading="lazy"
              @error="onThumbError($event, p)"
            />
            <span>{{ p.page_no }}</span>
            <i v-if="p.reviewed" class="thumb-dot"></i>
            <i v-if="p.failed" class="thumb-fail" title="本页识别失败">✕</i>
          </div>
        </div>

        <!-- 统一结构：右栏只有「篇目内容」（所有任务一致）；人物/关系统一在顶部「整卷人物/整卷关系」抽屉校对 -->
        <div class="content-pane">
          <div class="content-head">
            <span class="content-head-title">篇目内容（{{ contentParas.length || 0 }}）</span>
            <span class="content-head-spacer"></span>
            <span class="muted small">字号</span>
            <a-input-number
              v-model:value="paraFs"
              size="small"
              :min="8"
              :max="72"
              :precision="0"
              style="width: 76px"
              title="可直接输入数字或点上下箭头调整（8~72px），作用于 AI 识别框与人工添加框的正文文字（对本页所有段落生效）"
            />
            <span class="muted small">px</span>
            <a-button
              size="small"
              :type="cnOn ? 'primary' : 'default'"
              @click="cnOn = !cnOn"
              title="切换为简体仅辅助阅读显示；手工输入（简体）会自动转回繁体保存（库内/图谱始终存繁体原文）"
            >
              {{ cnOn ? '还原繁体' : '转简体显示' }}
            </a-button>
            <a-button size="small" type="dashed" class="add-row-btn" @click="addParagraph">
              ＋ 人工添加原文段落
            </a-button>
          </div>
          <div class="content-scroll" :style="{ fontSize: (paraFs || 14) + 'px' }">
            <div v-if="!contentParas.length" class="empty-hint">
              本页暂无识别文字（仅纯图/空白页如此）。若页面有字却为空，可点「🔄 重新识别本页」重试。
              识别文字段落随「写入图谱」聚合到谱系管理的「谱书内容」中校对。
            </div>
            <template v-else>
              <div
                v-for="(c, idx) in contentParas"
                :key="idx"
                class="item-card"
              >
                <div class="item-head">
                  <a-tag v-if="c.manual" color="green" size="small">人工</a-tag>
                  <span class="item-num">#{{ idx + 1 }}</span>
                  <a-space :size="2">
                    <a
                      :class="['mv', idx === 0 ? 'mv-dis' : '']"
                      title="上移"
                      @click="moveParagraph(idx, -1)"
                      >↑</a
                    >
                    <a
                      :class="['mv', idx === contentParas.length - 1 ? 'mv-dis' : '']"
                      title="下移"
                      @click="moveParagraph(idx, 1)"
                      >↓</a
                    >
                    <a-popconfirm title="删除该段？" @confirm="removeParagraph(idx)">
                      <a style="color: #ff4d4f; font-size: 12px">删除</a>
                    </a-popconfirm>
                  </a-space>
                </div>
                <a-textarea
                  :value="zh(c.text)"
                  size="small"
                  :auto-size="{ minRows: 3, maxRows: 14 }"
                  placeholder="本页原文段落"
                  :style="{ fontSize: (paraFs || 14) + 'px' }"
                  @input="setField(c, 'text', $event)"
                />
              </div>
            </template>
          </div>
        </div>
      </a-card>
    </div>

    <!-- 未保存修改：离开确认 -->
    <a-modal
      v-model:open="leaveAskOpen"
      title="有未保存的修改"
      :footer="null"
      :width="460"
      :closable="!leaveSaving"
      :mask-closable="!leaveSaving"
      :keyboard="!leaveSaving"
    >
      <p style="margin-bottom: 4px">
        当前审核内容有改动但尚未「保存 / 暂存 / 写入」，直接离开将丢失人工校对成果。
      </p>
      <p class="muted small" style="margin-bottom: 16px">
        选择「保存并离开」会暂存整卷人物/关系与改动页的篇目内容（随「写入图谱」入库），可随时回来继续审核。
      </p>
      <div style="display: flex; justify-content: flex-end; gap: 8px">
        <a-button :disabled="leaveSaving" @click="settleLeave('cancel')">取消</a-button>
        <a-button danger :disabled="leaveSaving" @click="settleLeave('discard')">不保存离开</a-button>
        <a-button type="primary" :loading="leaveSaving" @click="onLeaveSave">💾 保存并离开</a-button>
      </div>
    </a-modal>

  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { Modal, message } from 'ant-design-vue'
import { useAuthStore } from '@/stores/auth'
// 繁→简转换（本地 vendor，opencc-js）：审核阅读繁体识别内容时可切换简体显示
import { Converter as T2SConverter } from '@/vendor/opencc-t2cn'
// 简→繁转换（本地 vendor，opencc-js 反向 cn2t）：简体显示态下用户手工输入简体时，
// 写回前自动转回繁体落库，保证库内/图谱原文始终为繁体。
import { Converter as S2TConverter } from '@/vendor/opencc-c2t'
import {
  applyArchiveTaskApi,
  deleteTaskApi,
  getTaskApi,
  listDeleteRequestsApi,
  reExtractPageApi,
  resumeTaskApi,
  saveConsolidatedReviewApi,
  savePageReviewApi,
  unarchiveTaskApi,
} from '@/api'
import type {
  PageReview,
  ReviewContentPara,
  ReviewPersonItem,
  ReviewRelationItem,
  TaskDetail,
} from '@/types'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const taskId = route.params.taskId as string

const loading = ref(true)
const task = ref<TaskDetail | null>(null)
const curPageNo = ref(1)
/** 整卷数据抽屉（顶栏「整卷人物/整卷关系」入口打开；当前卷全局数据） */
const consOpen = ref(false)
const consActiveTab = ref<'persons' | 'relations'>('persons')
/** 打开整卷数据抽屉并定位到指定 tab */
const openCons = (k: 'persons' | 'relations') => {
  consActiveTab.value = k
  consOpen.value = true
}
const thumbStripRef = ref<HTMLElement | null>(null)

/** 小图条缩略图 URL：/files/scans/{task}/page_NNN.png → /files/thumbs/{task}/page_NNN.jpg
 *  缩略图约 10KB；古籍透印页原图可达 7MB+，一两百页整页加载会卡死载入。
 *  老任务尚无缩略图（404）时自动回退原图（见 scripts/backfill_thumbs.py 补齐）。 */
const thumbSrc = (p: any) => {
  const u: string = p?.image_url || ''
  return u.includes('/scans/') ? u.replace('/scans/', '/thumbs/').replace(/\.png$/i, '.jpg') : u
}
const onThumbError = (e: Event, p: any) => {
  const el = e.target as HTMLImageElement | null
  if (!el || el.dataset.fb === '1') return
  el.dataset.fb = '1'
  el.src = p?.image_url
}

/** 缩略图条：把当前页小图横向滚动到可视区中间（跳页后小图能跟随选中，否则停在原处看不见） */
function scrollThumbIntoView(no: number) {
  nextTick(() => {
    const strip = thumbStripRef.value
    if (!strip) return
    const el = strip.querySelector<HTMLElement>(`.thumb[data-no="${no}"]`)
    if (!el) return
    const cRect = strip.getBoundingClientRect()
    const eRect = el.getBoundingClientRect()
    const delta = eRect.left + eRect.width / 2 - (cRect.left + cRect.width / 2)
    const left = Math.max(0, Math.min(strip.scrollWidth - strip.clientWidth, strip.scrollLeft + delta))
    strip.scrollTo({ left, behavior: 'smooth' })
  })
}
/** 任何途径切页（上一页/下一页/页码输入/点小图/失败页跳转）都让小图条跟随 */
watch(curPageNo, (no) => scrollThumbIntoView(no))

/** 缩略图条：鼠标滚轮 → 横向滚动 */
function onThumbWheel(e: WheelEvent) {
  const el = thumbStripRef.value
  if (!el) return
  e.preventDefault()
  el.scrollLeft += e.deltaY
}

const saving = ref(false)
const applyingAll = ref(false)
const reExtracting = ref(false)
const batchReExtracting = ref(false)
const deleting = ref(false)
/** 本任务是否已有待审批的删除申请（操作员删除须管理员审批，09-07） */
const delRequestPending = ref(false)
const delConfirmTitle = computed(() => {
  const base =
    '将删除该次上传的扫描件与全部页面图（不可恢复）；已写入图谱的人物/谱书内容不受影响。'
  return auth.isAdmin
    ? `删除本任务？${base}`
    : `提交删除申请？${base}（操作员删除须管理员审批通过后才会真正执行）`
})
const delOkText = computed(() => (auth.isAdmin ? '删除任务' : '提交申请'))
/** 顶部小警示条对应详情浮窗开关 */
const consFailModal = ref(false)
const failedModal = ref(false)

const pages = computed(() => task.value?.pages || [])
/** 识别失败的页号列表（人工审核缺页提示） */
const failedPages = computed<number[]>(() =>
  pages.value.filter((p) => p.failed).map((p) => p.page_no),
)
const failedText = computed(() => {
  const list = failedPages.value
  if (list.length <= 20) return list.join('、')
  return `${list.slice(0, 20).join('、')} 等`
})
const currentPage = computed<PageReview | undefined>(() =>
  pages.value.find((p) => p.page_no === curPageNo.value),
)
/** 当前页可编辑的正文段落（新扁平识别链路主字段 content） */
const contentParas = computed<ReviewContentPara[]>(() => currentPage.value?.content || [])
const statusText = computed(() => {
  const s = task.value?.status
  if (!s) return '排队中'
  const map: Record<string, string> = { pending: '排队中', running: '处理中', done: '已完成', failed: '失败' }
  return map[s] || s
})
const statusColor = computed(() => {
  const s = task.value?.status
  if (!s) return 'default'
  const map: Record<string, string> = { pending: 'default', running: 'processing', done: 'success', failed: 'error' }
  return map[s] || 'default'
})

// ============ 归档（只读档案）模式 ============
/** 只读归档：任务已归档到「归档文件 → AI 识别归档」（archived_at 非空），或 URL 带 readonly=1。
 *  只读时禁止一切写操作（写入/暂存/重识别/删除/编辑）；页面原图与识别结果仍可像审核一样浏览。 */
const readOnly = computed(() => route.query.readonly === '1' || !!task.value?.archived_at)
/** 只读视图下禁掉整页编辑交互（输入框/下拉/勾选/删除链接等；缩放旋转翻页、分页、证据跳转等查看交互保留） */
watch(
  readOnly,
  (ro) => {
    document.body.classList.toggle('viewer-ro', ro)
    route.meta.title = ro ? '归档文件查看' : '扫描件审核'
  },
  { immediate: true },
)
onBeforeUnmount(() => document.body.classList.remove('viewer-ro'))

const archiving = ref(false)

/** 从服务端重拉任务详情（归档/取回后刷新状态用） */
const refreshTask = async () => {
  task.value = await getTaskApi(taskId)
  fillConsDefaults()
  refreshSnap()
}

/** 从档案库取回：解除只读，恢复可编辑（图片全程保留） */
const doUnarchive = async () => {
  archiving.value = true
  try {
    const res: any = await unarchiveTaskApi(taskId)
    const pg = res?.purged
    const extra =
      pg && pg.removed_persons >= 0
        ? `，已清除该任务原写入的谱系数据（内容 ${pg.removed_entries}、向量 ${pg.removed_vectors}、专属人物 ${pg.removed_persons}）`
        : ''
    message.success('已取回档案库：任务恢复可编辑' + extra)
    await refreshTask()
    router.replace({ path: `/tasks/${taskId}/review` })
  } catch {
    /* 请求拦截器已提示 */
  } finally {
    archiving.value = false
  }
}
/** 只读归档视图顶部「返回」去向：档案库归档 tab；普通审核回任务列表 */
const backTarget = computed(() => (readOnly.value ? '/documents?tab=archive' : '/tasks'))
const goBack = () => router.push(backTarget.value)

// ============ 图片缩放/平移 ============
const imgWrapRef = ref<HTMLDivElement>()
const scale = ref(1)
const tx = ref(0)
const ty = ref(0)
const rotate = ref(0) // 页面图方向已由转图/纠偏保证正确，默认不旋转
const dragging = ref(false)
const dragStart = reactive({ x: 0, y: 0, tx: 0, ty: 0 })
const previewOpen = ref(false)
const previewMaximized = ref(false)

const imgStyle = computed(() => ({
  width: '100%',
  height: '100%',
  overflow: 'hidden',
}))

/** transform：先平移(tx,ty)，再缩放，最后绕中心旋转 —— translate 处于旋转坐标系内 */
const imgTransform = computed(
  () => `rotate(${rotate.value}deg) scale(${scale.value}) translate(${tx.value}px, ${ty.value}px)`,
)

/** 屏幕位移 → 旋转后坐标系内的位移（旋转 90/180/270 时修正拖动方向） */
const rotateCompensate = (dx: number, dy: number) => {
  const r = ((rotate.value % 360) + 360) % 360
  if (r === 90) return [dy, -dx]
  if (r === 180) return [-dx, -dy]
  if (r === 270) return [-dy, dx]
  return [dx, dy]
}

const onWheel = (e: WheelEvent) => {
  e.preventDefault()
  const factor = e.deltaY < 0 ? 1.12 : 0.89
  scale.value = Math.min(6, Math.max(0.3, scale.value * factor))
}
const onMouseDown = (e: MouseEvent) => {
  dragging.value = true
  dragStart.x = e.clientX
  dragStart.y = e.clientY
  dragStart.tx = tx.value
  dragStart.ty = ty.value
}
const onMouseMove = (e: MouseEvent) => {
  if (!dragging.value) return
  const [ox, oy] = rotateCompensate(e.clientX - dragStart.x, e.clientY - dragStart.y)
  tx.value = dragStart.tx + ox
  ty.value = dragStart.ty + oy
}
const onMouseUp = () => (dragging.value = false)
const zoomImage = (f: number) => {
  scale.value = Math.min(6, Math.max(0.3, scale.value * f))
}
const resetView = () => {
  scale.value = 1
  tx.value = 0
  ty.value = 0
  rotate.value = 0 // 回到默认不旋转
}
const rotateImage = (d: number) => {
  rotate.value = (rotate.value + d + 360) % 360
}
const openPreview = () => {
  previewOpen.value = true
}
const gotoPage = (no: number) => {
  curPageNo.value = no
  resetView()
}

/** 从识别失败弹窗跳到对应页原图（先关弹窗便于对照） */
const jumpToFailedPage = (no: number) => {
  failedModal.value = false
  gotoPage(no)
}

const prevPage = () => curPageNo.value > 1 && gotoPage(curPageNo.value - 1)
const nextPage = () => curPageNo.value < pages.value.length && gotoPage(curPageNo.value + 1)
const goPage = (v: number | null) => v && gotoPage(v)

// ============ 繁体↔简体 转换 ============
// 显示切换只读：简体显示态（cnOn=true）下 zh() 把繁体原文转简体供阅读，不改底层。
// 写回规则：凡用户手工输入（不限显示模式），setField 一律在落库前把简体自动转回繁体，
// 保证底层/图谱始终存繁体原文（谱书/图谱规范）。审核人员多按简体输入，据此恒转。
const cnOn = ref(false)
let _t2s: ((t: string) => string) | null = null
let _s2t: ((s: string) => string) | null = null
const zh = (v: unknown): string => {
  const s = v == null ? '' : String(v)
  if (!cnOn.value || !s) return s
  // from 必须用 't'（OpenCC 通用繁体）：本 vendor 是 t2cn 裁剪版，只内置 t→cn 词典，
  // 传 'tw'（台湾正体）取不到源词典会导致大量繁体字（尤其古籍用字）转换不生效。
  if (!_t2s) _t2s = T2SConverter({ from: 't', to: 'cn' })
  return _t2s(s)
}
/** 简体→繁体（cn2t 裁剪版，OpenCC 通用繁体）：与 t2cn 同源反向，词组级消歧较准 */
const s2t = (s: string): string => {
  if (!_s2t) _s2t = S2TConverter({ from: 'cn', to: 't' })
  return _s2t(s)
}
// ============ 篇目正文（AI 识别框 / 人工添加框）字号：可下拉可选也可直接输入数字（8~72px） ============
const paraFs = ref(14)

/** 控件统一写回：用户输入的文本一律先做简体→繁体再保存，保证库内/图谱始终为繁体原文。
 * 已识别文本为繁体、直接写回不受影响；简体字会转成对应繁体。 */
const setField = (rec: Record<string, any>, key: string, ev: Event) => {
  const el = ev.target as HTMLInputElement | HTMLTextAreaElement | null
  let v = el?.value ?? ''
  if (v) v = s2t(v)
  rec[key] = v
}

// ============ 交互 ============
const saveStaging = async () => {
  // 整卷模式：暂存整卷人物/关系（断点续审）+ 改动页的正文/人物/关系/篇目内容
  if (consMode.value) {
    saving.value = true
    try {
      await saveConsolidatedReviewApi(taskId, {
        persons: consPersons.value.map((p: any) => ({ ...p })),
        relations: consRelations.value.map((r: any) => ({ ...r })),
      })
      markConsSaved()
      // 正文编辑在主内容区逐页进行：点「保存」也应把改动页（含当前页 content）落库，
      // 否则 isDirty() 仍判有改动，离开时误弹「有未保存的修改」。
      const dirty = dirtyPageNos()
      if (dirty.length) await flushDirtyPages()
      const msg =
        (reviewedNow.value
          ? '整卷人物/关系已暂存'
          : '整卷人物/关系已暂存；全部校对完成后请点顶部「✅ 标记已审核」再写入图谱') +
        (dirty.length ? `；已同步保存 ${dirty.length} 个改动页的正文内容` : '')
      message.success(msg)
    } catch {
      /* 请求拦截器已提示 */
    } finally {
      saving.value = false
    }
    return
  }
  // 逐页暂存（含篇目正文段落）
  const p = currentPage.value
  if (!p) return
  saving.value = true
  try {
    await savePageReviewApi(taskId, p.page_no, {
      persons: p.persons,
      relations: p.relations,
      content: contentPayloadOf(p),
      page_notes: p.page_notes ?? undefined,
      reviewed: true,
    })
    p.reviewed = true
    markPageSaved(p.page_no)
    message.success(`第 ${p.page_no} 页已暂存（含篇目正文）`)
  } finally {
    saving.value = false
  }
}

const APPLY_FB_KEY = 'apply-feedback'

/** 把一页的正文段落规整为后端扁平 content 格式 [{text, manual}] */
const contentPayloadOf = (p: PageReview): ReviewContentPara[] =>
  (p.content || []).map((c) => ({ text: (c.text || '').trim(), manual: !!c.manual }))

const addParagraph = () => {
  const p = currentPage.value
  if (!p) return
  if (!p.content) p.content = []
  p.content.push({ text: '', manual: true })
  message.info('已新增 1 段人工原文，填写后点页面顶部「💾 保存」统一保存')
}

const removeParagraph = (idx: number) => {
  const p = currentPage.value
  if (!p?.content) return
  p.content.splice(idx, 1)
}
/** 上下移动篇目段落：直接调整 content 数组顺序（人工理顺阅读顺序用） */
const moveParagraph = (idx: number, delta: number) => {
  const p = currentPage.value
  if (!p?.content) return
  const to = idx + delta
  if (to < 0 || to >= p.content.length) return
  const [item] = p.content.splice(idx, 1)
  p.content.splice(to, 0, item)
  message.info(`已把第 ${idx + 1} 段移到第 ${to + 1} 位，记得点「💾 保存」`)
}

/** 写入图谱前：把所有有改动的页的正文段落（含人工补录）先暂存到服务端。
 *  因 apply 的谱书内容聚合（sync_task_entries）读取的是服务端 page.content，
 *  不先保存会导致未保存的人工段落漏入库。人物/关系以内存为准随 apply 一并上传。 */
const flushDirtyPages = async () => {
  const t = task.value
  if (!t) return
  for (const no of dirtyPageNos()) {
    const p = (t.pages || []).find((x) => x.page_no === no)
    if (!p) continue
    await savePageReviewApi(taskId, no, {
      persons: p.persons,
      relations: p.relations,
      content: contentPayloadOf(p),
      page_notes: p.page_notes ?? undefined,
      reviewed: p.reviewed,
    })
    markPageSaved(no)
  }
}

const marking = ref(false)
/** 人工确认「本卷已校对完成」：落审核标记（写入图谱/归档的前置闸），并记录审核人 */
const markReviewed = async () => {
  if (!task.value?.consolidated) return
  marking.value = true
  try {
    await saveConsolidatedReviewApi(taskId, {
      persons: consPersons.value.map((p: any) => ({ ...p })),
      relations: consRelations.value.map((r: any) => ({ ...r })),
      reviewed: true,
    })
    const c: any = task.value.consolidated
    c.reviewed = true
    c.reviewed_at = new Date().toISOString().slice(0, 19)
    c.reviewed_by_name = auth.user?.username || undefined
    markConsSaved()
    message.success('已标记「本卷已人工校对完成」，现在可点「✔ 整理写入并归档」（也可回扫描件导入列表一键操作）')
  } catch {
    /* 请求拦截器已提示 */
  } finally {
    marking.value = false
  }
}

/** 取消「本卷已人工校对完成」标记（误标 / 校对中发现差错，改回未审核，写入图谱前需重新标记） */
const unmarkReviewed = async () => {
  if (!task.value?.consolidated) return
  marking.value = true
  try {
    await saveConsolidatedReviewApi(taskId, {
      persons: consPersons.value.map((p: any) => ({ ...p })),
      relations: consRelations.value.map((r: any) => ({ ...r })),
      reviewed: false,
    })
    const c: any = task.value.consolidated
    c.reviewed = false
    c.reviewed_at = undefined
    c.reviewed_by_name = undefined
    message.success('已取消「已人工审核」标记，本卷改回未审核；修改完成后再点「✅ 标记已审核」即可')
  } catch {
    /* 请求拦截器已提示 */
  } finally {
    marking.value = false
  }
}

/** 轮询等待后台「整理 → 写入 → 归档」完成（服务端执行，可离开页面） */
const pollUntilArchived = async (key: string) => {
  const deadline = Date.now() + 90 * 60 * 1000
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 5000))
    try {
      const t = await getTaskApi(taskId)
      task.value = t
      fillConsDefaults()
      if (t.archived) {
        message.success({ content: '✅ 已整理写入并归档到「AI 识别归档」', key, duration: 6 })
        return true
      }
      const stageTxt =
        t.stage === 'consolidating' || (!t.consolidated && t.status === 'done')
          ? '① 整卷重新整理中'
          : '② 整理写入中'
      message.loading({
        content: `${stageTxt}…（${t.done_pages}/${t.total_pages} 页）后台执行，可稍后回任务列表查看`,
        key,
        duration: 0,
      })
    } catch {
      /* 单次轮询失败忽略，继续等 */
    }
  }
  message.destroy(key)
  message.warning('处理超时：任务仍在后台执行，请稍后在任务列表查看结果')
  return false
}

/** 一键「整理写入并归档」：有改动 → 先重新整理（后台排队）→ 写入 → 归档；无改动 → 直接整理写入并归档 */
const runApplyArchive = async (force: boolean) => {
  applyingAll.value = true
  try {
    await flushDirtyPages() // 先把改动页正文落库，保证篇目随写入入库
    const res = await applyArchiveTaskApi(taskId, { force })
    message.loading({
      content: res.consolidate
        ? '已排队：先重新整理整卷（后台执行，约 10~40 分钟），完成后自动整理写入并归档…'
        : '正在整理写入并归档…',
      key: APPLY_FB_KEY,
      duration: 0,
    })
    const ok = await pollUntilArchived(APPLY_FB_KEY)
    if (ok) {
      refreshSnap() // 已入库并归档，以当前数据为基准
      await refreshTask()
    }
  } catch {
    message.destroy(APPLY_FB_KEY)
    /* 具体错误提示由请求拦截器弹出 */
  } finally {
    applyingAll.value = false
  }
}

/** 统一入口：一键「整理写入并归档」。
 *  已标记人工审核后，再按本卷「是否需要重新整理整卷」区分提示：
 *  - 需整理（结果缺失/过期）：给较多确认说明（会先后台重整理→自动写入并归档，任务列表见处理中/排队中）；
 *  - 无需整理：直接整理写入并归档（不弹多余确认）。
 *  未审核时提示去标记，不直接整理写入归档。 */
const doApply = () => {
  if (!reviewedNow.value) {
    Modal.confirm({
      title: '⚠ 未审核不能整理写入并归档',
      content:
        (consMode.value
          ? '整卷人物/关系还没点「✅ 标记已审核」，AI 结果可能未经过人工校对。\n'
          : '本卷仍有页面未人工暂存校对，AI 结果可能未经过人工核对。\n') +
        '请先校对内容并点「✅ 标记已审核」（整卷）或完成逐页「💾 保存」（老任务），即可整理写入并归档（写入后人物/关系即刻进入谱系树与人物管理）。',
      okText: '知道了',
      cancelText: '返回继续校对',
      onOk: () => {},
    })
    return
  }
  if (needsReconsolidate.value) {
    // 需先重新整理整卷：给较多确认，说明会后台排队、任务列表可见处理中/排队中
    Modal.confirm({
      title: '将先重新整理整卷，再写入图谱并归档',
      content:
        '当前整卷人物/关系结果缺失或已过期，点「确定」后将先自动重新整理整卷（后台排队执行，约 10~40 分钟），' +
        '整理完成后自动写入图谱并归档，无需再手动操作。\n\n' +
        '整理期间可在「扫描件导入」任务列表看到本卷处于处理中/排队中。',
      okText: '确定整理写入',
      cancelText: '取消',
      onOk: () => runApplyArchive(false),
    })
    return
  }
  // 无需重新整理：直接整理写入并归档（减掉多余提示）
  runApplyArchive(false)
}

// ================= 整卷整理结果（第二段）审核 =================
// 整卷模式：任务存在 result.consolidated（后端整卷归并+关系推断产物）
const consMode = computed(() => !!task.value?.consolidated)
const consStale = computed(() => !!task.value?.consolidation_stale)

/** 本卷是否已人工审核（整卷模式看整卷保存标记；老任务看后端逐页汇总）——写入图谱/归档的前置闸 */
const reviewedNow = computed(() =>
  consMode.value ? !!task.value?.consolidated?.reviewed : !!task.value?.reviewed,
)
/** 归档只读页内联展示的审核溯源文本：审核人 · 审核时间（便于无需悬停即可核验） */
const reviewedMetaText = computed(() => {
  const c: any = task.value?.consolidated
  const who = c?.reviewed_by_name || task.value?.reviewed_by_name
  const at = c?.reviewed_at || task.value?.reviewed_at || ''
  const atTxt = at ? String(at).replace('T', ' ').slice(0, 19) : ''
  return `审核：${who ? '由 ' + who : '—'}` + (atTxt ? ` · ${atTxt}` : '')
})
const reviewTip = computed(() => {
  if (reviewedNow.value) {
    const c: any = task.value?.consolidated
    const who = c?.reviewed_by_name ? `由 ${c.reviewed_by_name}` : '已'
    const at = c?.reviewed_at
      ? String(c.reviewed_at).replace('T', ' ')
      : task.value?.reviewed_at
        ? String(task.value.reviewed_at).replace('T', ' ').slice(0, 19)
        : ''
    return `${who}人工审核${at ? ' · ' + at : ''}；重新识别／重新整理后本标记失效，需重新审核`
  }
  if (consMode.value) {
    return '整卷人物/关系尚未人工审核：请打开「🧑 整卷人物／🕸 整卷关系」校对勾选后点「💾 保存」，再写入图谱'
  }
  const left = (task.value?.review_total_pages || 0) - (task.value?.reviewed_pages || 0)
  return `本卷仍有 ${left} 页未人工暂存：请逐页核对后点「💾 保存」，全部页审完才可写入图谱`
})
const consFailedChunks = computed(() => task.value?.consolidated?.failed_chunks || 0)
/** 新流程任务（逐页不再产关系）：所有页面 relations 均为空 */
const isNewPipeline = computed(() => {
  const ps = pages.value
  return ps.length > 0 && ps.every((p) => !(p.relations && p.relations.length))
})
/** 整卷结果缺失或已过期 → 需先「重新整理整卷」 */
const needsReconsolidate = computed(
  () => consStale.value || (!consMode.value && isNewPipeline.value && task.value?.status === 'done'),
)

// ================= 卷级「⚠ 注意」提示（可逐条暂时关闭、约 5 分钟重提示） =================
const BELL_DISMISS_MS = 5 * 60 * 1000
/** 各待办类目被用户暂时关闭的时间戳（0=未关闭） */
const bellDismissAt = reactive<Record<string, number>>({})
/** 周期 tick：推动 computed 按时间重算，使“关闭满 5 分钟再次提示”生效 */
const bellTick = ref(0)
let bellTimer: number | undefined
const startBellTimer = () => {
  if (bellTimer) return
  bellTimer = window.setInterval(() => {
    bellTick.value++
  }, 30_000)
}
const stopBellTimer = () => {
  if (bellTimer) {
    window.clearInterval(bellTimer)
    bellTimer = undefined
  }
}
/** 判断某待办是否应展示：需未处于“刚被关闭 5 分钟内” */
const bellVisible = (kind: string, active: boolean) => {
  if (!active) return false
  const at = bellDismissAt[kind]
  return !at || Date.now() - at >= BELL_DISMISS_MS
}
const bellDismiss = (kind: string) => {
  bellDismissAt[kind] = Date.now()
  bellTick.value++
}
const bellReconsolidateTip = () =>
  message.info('点顶部「✔ 整理写入并归档」时会先自动重新整理整卷（后台排队），整理完成即自动写入并归档，无需手动处理。')

/** 当前应展示的待办列表（文字精简的动态列表） */
const bellItems = computed(() => {
  // 依赖 tick 以随 5 分钟周期重算；取当前时间戳确保 dismissed 判定实时
  void bellTick.value
  const items: { kind: string; text: string; tone: string; go?: () => void }[] = []
  // A 整卷人物/关系结果缺失或已过期
  if (bellVisible('A', needsReconsolidate.value)) {
    items.push({
      kind: 'A',
      tone: 'warn',
      text: '整卷人物/关系缺失或已过期：保存后写入时会自动重整理',
      go: bellReconsolidateTip,
    })
  }
  // B 整卷整理有片段失败
  if (bellVisible('B', consMode.value && consFailedChunks.value > 0)) {
    items.push({
      kind: 'B',
      tone: 'warn',
      text: `整卷整理 ${consFailedChunks.value} 个片段失败，结果不完整`,
      go: () => (consFailModal.value = true),
    })
  }
  // C AI 识别失败页
  if (bellVisible('C', failedPages.value.length > 0)) {
    items.push({
      kind: 'C',
      tone: 'error',
      text: `AI 识别失败 ${failedPages.value.length} 页`,
      go: () => (failedModal.value = true),
    })
  }
  // D 未人工审核（整卷模式且任务已完成，尚未「✅ 标记已审核」）
  if (bellVisible('D', !!task.value && task.value.status === 'done' && consMode.value && !reviewedNow.value)) {
    items.push({
      kind: 'D',
      tone: 'info',
      text: '整卷人物/关系尚未「✅ 标记已审核」，校对完成后请标记再写入',
    })
  }
  return items
})
const bellGo = (it: { go?: () => void }) => it.go?.()

const consPersons = computed<any[]>(() => task.value?.consolidated?.persons || [])
const consRelations = computed<any[]>(() => task.value?.consolidated?.relations || [])

/** 为整卷条目补齐 confirmed 默认值（后端产物不含该字段） */
const fillConsDefaults = () => {
  const c = task.value?.consolidated
  if (!c) return
  c.persons = (c.persons || []).map((p: any) => ({ confirmed: p.confirmed !== false, ...p }))
  c.relations = (c.relations || []).map((r: any) => ({ confirmed: r.confirmed !== false, ...r }))
}

const consQuery = ref('')
const relQuery = ref('')
const filteredConsPersons = computed(() => {
  const q = consQuery.value.trim()
  const list = consPersons.value
  if (!q) return list
  return list.filter((p) => [p.name, p.birth_place, p.biography].some((s) => s && String(s).includes(q)))
})
const filteredConsRelations = computed(() => {
  const q = relQuery.value.trim()
  const list = consRelations.value
  if (!q) return list
  return list.filter((r) => (r.from_name || '').includes(q) || (r.to_name || '').includes(q))
})

/**
 * 整卷两张表的分页配置。
 * 关键：对象必须用稳定引用（不能是模板内联字面量），且不能传受控的 current/pageSize——
 * usePagination 内 mergedPagination = extendsObject(inner, props.pagination)，外层会覆盖内部状态，
 * 传固定 pageSize/current 会导致「选 50/100/200 无效、翻页被拉回第 1 页」。
 * 用非受控 defaultPageSize/defaultCurrent，由 Table 内部自管状态，切换即生效。
 */
const consPersonPagination = {
  defaultCurrent: 1,
  defaultPageSize: 20,
  showSizeChanger: true,
  pageSizeOptions: ['20', '50', '100', '200'],
  showTotal: (t: number) => `共 ${t} 人`,
}
const consRelationPagination = {
  defaultCurrent: 1,
  defaultPageSize: 20,
  showSizeChanger: true,
  pageSizeOptions: ['20', '50', '100', '200'],
  showTotal: (t: number) => `共 ${t} 条`,
}

/**
 * 整卷证据跳转：第二段是「多页成块」的纯文本推断，只带块页范围；
 * 这里用第一段逐页底稿的识别人名清单重建「精确出现页」索引，
 * 让整卷人物/关系行能跳到对应人物真正出现的那一页原图核对（跨页关系常见：夫在 100 页、妻在 101 页）。
 */
const _normN = (s?: string) => String(s || '').replace(/\s+/g, '')
const pagePersonIdx = computed(() => {
  const m = new Map<string, number[]>()
  for (const p of pages.value) {
    for (const x of p.persons || []) {
      const k = _normN(x.name)
      if (!k) continue
      const arr = m.get(k)
      if (arr) {
        if (!arr.includes(p.page_no)) arr.push(p.page_no)
      } else {
        m.set(k, [p.page_no])
      }
    }
  }
  return m
})
/** 某人的证据页列表：底稿精确页优先；未命中（如整卷才推断出的名）退回其整卷人物块页范围 */
const evPagesOf = (name?: string): number[] => {
  const k = _normN(name)
  if (!k) return []
  const exact = pagePersonIdx.value.get(k)
  if (exact && exact.length) return exact.slice()
  const p = (task.value?.consolidated?.persons || []).find((x: any) => _normN(x.name) === k)
  return (p && Array.isArray(p.pages) && p.pages.length ? p.pages : [])
}
const evTip = (name?: string) => {
  const ps = evPagesOf(name)
  if (!ps.length) return ''
  return ps.length === 1 ? `底稿出现于第 ${ps[0]} 页` : `底稿出现于第 ${ps.join('、')} 页`
}
const relFromP = (r: any) => evPagesOf(r.from_name)[0] ?? null
const relToP = (r: any) => evPagesOf(r.to_name)[0] ?? null

const addConsPerson = () => {
  const c = task.value?.consolidated
  if (!c) return
  c.persons = c.persons || []
  c.persons.push({
    name: '',
    gender: 'unknown',
    birth_year: null,
    death_year: null,
    birth_place: '',
    biography: '',
    confidence: 1,
    confirmed: true,
    pages: [],
  })
  message.info('已新增 1 条整卷人物，填写姓名后随「写入图谱（整卷）」入库')
}
const addConsRelation = () => {
  const c = task.value?.consolidated
  if (!c) return
  c.relations = c.relations || []
  c.relations.push({
    type: 'parent_child',
    from_name: '',
    to_name: '',
    marriage_date: null,
    confidence: 1,
    confirmed: true,
    pages: [],
  })
  message.info('已新增 1 条整卷关系，填写双方姓名后随「写入图谱（整卷）」入库')
}
const removeConsPerson = (item: any) => {
  const arr = task.value?.consolidated?.persons
  if (!arr) return
  const i = arr.indexOf(item)
  if (i >= 0) arr.splice(i, 1)
}
const removeConsRelation = (item: any) => {
  const arr = task.value?.consolidated?.relations
  if (!arr) return
  const i = arr.indexOf(item)
  if (i >= 0) arr.splice(i, 1)
}
/** 跳到证据页看原图 */
const goEvidence = (no?: number) => {
  if (!no) return
  if (pages.value.some((x) => x.page_no === no)) gotoPage(no)
  else message.info('该证据页不在当前任务页面中')
}

/** 重识别前先落库当前页未保存改动（方案 C：不丢人工编辑/勾选）。
 *  后端 re_extract_page 读取 DB 里最新页，据此保留 manual 框、merge 旧独有人名；
 *  若不先保存，前端内存里刚改的内容（绿标「人工」框、勾选、修正文本）会被整页重识别冲掉。 */
const persistCurrentPageBeforeReExtract = async (p: PageReview) => {
  if (!dirtyPageNos().includes(p.page_no)) return // 无未保存改动，无需先落
  await savePageReviewApi(taskId, p.page_no, {
    persons: p.persons,
    relations: p.relations,
    content: contentPayloadOf(p),
    page_notes: p.page_notes ?? undefined,
    reviewed: !!p.reviewed,
  })
  markPageSaved(p.page_no)
}

const reExtract = async () => {
  const p = currentPage.value
  if (!p) return
  reExtracting.value = true
  try {
    // 方案 C：先把当前页未保存改动暂存到服务端，再重识别，避免人工内容被整页覆盖丢失
    await persistCurrentPageBeforeReExtract(p)
    const page = await reExtractPageApi(taskId, p.page_no)
    // 用新结果替换当前页（含篇目内容）
    p.persons = page.persons as ReviewPersonItem[]
    p.relations = page.relations as ReviewRelationItem[]
    p.content = (page.content || []) as ReviewContentPara[]
    p.page_notes = (page.notes ?? page.page_notes ?? p.page_notes) as string | null | undefined
    p.failed = false // 重识别成功：退出失败页状态（服务端已同步清除参考草稿）
    p.error = null
    p.reviewed = false
    message.success(`第 ${p.page_no} 页已重新识别`)
    // 单页内容变化 → 整卷整理结果失效：点「✔ 整理写入并归档」时会先自动重新整理再写入
    if (consMode.value) {
      task.value!.consolidated = null
      ;(task.value as any).consolidation_stale = true
      message.warning('本页已重新识别；整卷人物/关系结果已失效，点「✔ 整理写入并归档」时会自动重新整理后再写入', 6)
    }
  } catch {
    message.error('重新识别失败，请检查 AI 服务是否可用')
  } finally {
    reExtracting.value = false
  }
}

/** 批量重识别所有失效页（调用断点续跑，后台自动补识别） */
const batchReExtract = async () => {
  batchReExtracting.value = true
  try {
    await resumeTaskApi(taskId)
    message.success('已开始批量重识别失效页，进度见任务列表；完成后刷新本页或重新进入审核')
  } catch {
    /* 请求拦截器已提示 */
  } finally {
    batchReExtracting.value = false
  }
}

const removeTask = async () => {
  if (!task.value) return
  deleting.value = true
  try {
    const res = await deleteTaskApi(taskId)
    if (res?.requested) {
      // 操作员：不真正删除，生成待审批申请（页面保留，顶部标记为审批中）
      delRequestPending.value = true
      message.success('删除申请已提交，待管理员审批通过后才会真正删除')
      return
    }
    message.success('任务已删除')
    refreshSnap() // 任务将删除，内存数据作废：不触发"未保存"拦截
    router.push('/tasks')
  } catch {
    /* 拦截器已提示 */
  } finally {
    deleting.value = false
  }
}

// ============ 未保存修改检测与离开确认 ============
// 方案：对「最近一次加载/保存」拍数据快照（仅含可编辑字段），离开时与当前数据对比，
// 有差异即视为未保存。快照排除 image_url/error 等无关字段，缩放/翻页不会误判。
const snapOfPage = (p: any) =>
  JSON.stringify({
    persons: p?.persons || [],
    relations: p?.relations || [],
    content: p?.content || [],
    page_notes: p?.page_notes || '',
    reviewed: !!p?.reviewed,
  })
let savedPageSnaps: Record<number, string> = {}
let savedConsSnap = '~none~'
/** 以当前内存数据为基准重新拍照（加载完成 / 全部写入图谱后调用） */
const refreshSnap = () => {
  const t = task.value
  savedPageSnaps = {}
  if (!t) {
    savedConsSnap = '~none~'
    return
  }
  for (const p of t.pages || []) savedPageSnaps[p.page_no] = snapOfPage(p)
  savedConsSnap = t.consolidated ? JSON.stringify(t.consolidated) : '~none~'
}
/** 单页保存成功后：仅将该页视为已保存（不影响其它页的快照） */
const markPageSaved = (no: number) => {
  const p = (task.value?.pages || []).find((x) => x.page_no === no)
  if (p) savedPageSnaps[no] = snapOfPage(p)
}
/** 整卷人物/关系保存成功后：仅更新整卷快照 */
const markConsSaved = () => {
  const t = task.value
  savedConsSnap = t?.consolidated ? JSON.stringify(t.consolidated) : '~none~'
}
const dirtyPageNos = (): number[] => {
  const t = task.value
  if (!t) return []
  return (t.pages || [])
    .filter((p) => (savedPageSnaps[p.page_no] ?? '') !== snapOfPage(p))
    .map((p) => p.page_no)
}
const consDirty = (): boolean => {
  const t = task.value
  if (!t) return false
  const cur = t.consolidated ? JSON.stringify(t.consolidated) : '~none~'
  return cur !== savedConsSnap
}
/** 是否有未保存修改（人物/关系/篇目/整卷任一有改动） */
const isDirty = () => dirtyPageNos().length > 0 || consDirty()

// ============ 离开确认（路由守卫 + 弹窗三选一：保存并离开 / 不保存离开 / 取消） ============
const leaveAskOpen = ref(false)
const leaveSaving = ref(false)
let leaveResolver: ((a: 'save' | 'discard' | 'cancel') => void) | null = null
const askLeaveAction = () =>
  new Promise<'save' | 'discard' | 'cancel'>((resolve) => {
    leaveResolver = resolve
    leaveAskOpen.value = true
  })
const settleLeave = (a: 'save' | 'discard' | 'cancel') => {
  leaveAskOpen.value = false
  leaveSaving.value = false
  const r = leaveResolver
  leaveResolver = null
  r?.(a)
}
/** 「保存并离开」：暂存改动过的整卷人物/关系 + 所有改动页（人物/关系/篇目内容） */
const doSaveAllBeforeLeave = async (): Promise<boolean> => {
  const t = task.value
  if (!t) return true
  try {
    if (consMode.value && t.consolidated && consDirty()) {
      await saveConsolidatedReviewApi(taskId, {
        persons: consPersons.value.map((x: any) => ({ ...x })),
        relations: consRelations.value.map((x: any) => ({ ...x })),
      })
      markConsSaved()
    }
    await flushDirtyPages()
    message.success('已保存修改，正在离开审核页')
    return true
  } catch {
    message.error('保存失败，请检查网络后重试，或选择「不保存离开」')
    return false
  }
}
const onLeaveSave = async () => {
  if (leaveSaving.value) return
  leaveSaving.value = true
  try {
    const ok = await doSaveAllBeforeLeave()
    if (ok) settleLeave('save')
  } finally {
    leaveSaving.value = false
  }
}

let guardBusy = false
onBeforeRouteLeave(async () => {
  if (guardBusy) return false
  // 正在提交（保存/写入/删除）中放行，避免打断自己的请求流程
  if (saving.value || applyingAll.value || deleting.value) return true
  if (!isDirty()) return true
  guardBusy = true
  try {
    const act = await askLeaveAction()
    if (act === 'save' || act === 'discard') return true
    return false
  } finally {
    guardBusy = false
  }
})
// 关闭标签页/刷新兜底（浏览器原生提示，文案不可定制）
const onBeforeUnload = (e: BeforeUnloadEvent) => {
  if (!isDirty()) return
  e.preventDefault()
  e.returnValue = ''
}

// ============ 键盘流 ============
const onKeydown = (e: KeyboardEvent) => {
  const tag = (e.target as HTMLElement)?.tagName
  const inInput = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
  // 只读归档：跳过 保存(S)/写入(A)/重识别(R)；左右翻页浏览仍可用
  if (readOnly.value && ['s', 'a', 'r'].includes(e.key.toLowerCase())) return
  if (e.key === 'ArrowLeft') {
    if (!inInput) prevPage()
    return
  }
  if (e.key === 'ArrowRight') {
    if (!inInput) nextPage()
    return
  }
  if (e.key.toLowerCase() === 's') {
    if (!inInput) {
      e.preventDefault()
      saveStaging()
    }
    return
  }
  if (e.key.toLowerCase() === 'a') {
    if (!inInput) {
      e.preventDefault()
      doApply()
    }
    return
  }
  if (e.key.toLowerCase() === 'r') {
    if (!inInput) {
      e.preventDefault()
      reExtract()
    }
    return
  }
}

onMounted(async () => {
  try {
    task.value = await getTaskApi(taskId)
    fillConsDefaults()
    // 若本任务已存在待审批删除申请，加载后置为「审批中」并隐藏删除入口
    try {
      const reqs = await listDeleteRequestsApi({ target_type: 'task' })
      if (reqs.some((r) => r.target_id === taskId && r.status === 'pending')) {
        delRequestPending.value = true
      }
    } catch {
      /* 不阻塞审核页 */
    }
  } catch {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
  refreshSnap() // 加载完成后建立"未保存"基准快照
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('beforeunload', onBeforeUnload)
  startBellTimer() // 铃铛关闭满 5 分钟后自动再次提示
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('beforeunload', onBeforeUnload)
  stopBellTimer()
})
</script>

<style scoped>
.review-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: calc(100vh - 120px);
  overflow-y: auto;
}
/* 整卷数据卡（顶部全宽，当前卷全局数据） */
/* 整卷数据抽屉：描述提示行 + 抽屉内 tab 导航留白 */
.cons-desc {
  color: #a08963;
  font-size: 12.5px;
  line-height: 1.6;
  background: #fffaf0;
  border: 1px dashed #e5cf9f;
  border-radius: 8px;
  padding: 7px 10px;
  margin-bottom: 10px;
}
.cons-drawer .ant-drawer-body {
  padding-top: 12px;
}
.cons-drawer .ant-tabs > .ant-tabs-nav {
  margin-bottom: 6px;
}
/* 顶部小警示条（AI 识别失败 / 整卷片段失败）：点击弹详情浮窗，不占主区 */
.mini-alert {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border-radius: 8px;
  font-size: 12.5px;
  line-height: 1.6;
  cursor: pointer;
  border: 1px solid;
  margin-bottom: 8px;
  transition: box-shadow 0.15s;
}
.mini-alert:hover {
  box-shadow: 0 1px 6px rgba(0, 0, 0, 0.14);
}
.mini-alert.error {
  background: #fff1f0;
  border-color: #ffccc7;
  color: #cf1322;
}
.mini-alert.warn {
  background: #fffbe6;
  border-color: #ffe58f;
  color: #d46b08;
}
.mini-alert .mini-ico {
  font-weight: 700;
}
.mini-alert .mini-act {
  margin-left: auto;
  font-weight: 600;
  white-space: nowrap;
}
.warn-num {
  color: #fa8c16;
  font-size: 15px;
}
.modal-actions {
  display: flex;
  gap: 10px;
  margin-top: 14px;
}
.fail-tags {
  max-height: 220px;
  overflow: auto;
  margin-top: 4px;
}
.fail-tags .ant-tag {
  margin-bottom: 6px;
}
.toolbar-card {
  flex-shrink: 0;
}
.toolbar-card :deep(.ant-card-body) {
  padding: 10px 16px;
}
.toolbar-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.keyboard-hint {
  font-size: 12px;
  color: #8c8c8c;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed #e8e8e8;
  justify-content: flex-start;
  gap: 4px;
}
.hint-title {
  white-space: nowrap;
  color: #595959;
}
/* 归档只读页内联展示审核人/时间（溯源） */
.reviewed-meta {
  white-space: nowrap;
  font-size: 12px;
  color: #237804;
  margin-left: 2px;
}
.hint-txt {
  white-space: nowrap;
}
.page-pos {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
  color: #595959;
}
.kbd {
  display: inline-block;
  border: 1px solid #d9d9d9;
  border-bottom-width: 2px;
  border-radius: 3px;
  padding: 0 5px;
  background: #fafafa;
  font-family: monospace;
  margin: 0 2px;
}
.kbd-inline {
  display: inline-block;
  border: 1px solid #d9d9d9;
  border-bottom-width: 2px;
  border-radius: 3px;
  padding: 0 4px;
  background: #fafafa;
  font-family: monospace;
  font-size: 11px;
  margin-left: 6px;
  opacity: 0.8;
}
.kbd-hint-inline {
  font-size: 12px;
  color: #8c8c8c;
  margin-right: 8px;
}
.review-main {
  flex: 1;
  display: flex;
  gap: 12px;
  min-height: 0;
}
.review-main > .panel {
  min-height: 0;
}
.panel {
  display: flex;
  flex-direction: column;
}
.image-panel {
  flex: 0 0 55%;
  min-width: 0;
}
.result-panel {
  flex: 1;
  min-width: 0;
}
/* 卡片 body 纵向 flex，图片画布高度受卡片/视口约束，避免被超高原图撑长（画布过长） */
.image-panel :deep(.ant-card-body) {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}
.result-panel :deep(.ant-card-body) {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}
/* 识别结果卡内部：小图条固定顶部不滚走；下方 tab 内容区独立上下滚动，
   篇目内容再多也只在该区滚动，不会把上方小图条挤掉/遮挡。 */
.result-panel .thumb-strip {
  flex-shrink: 0;
}
.result-panel :deep(.ant-tabs) {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.result-panel :deep(.ant-tabs .ant-tabs-nav) {
  flex-shrink: 0;
  margin-bottom: 8px;
}
.result-panel :deep(.ant-tabs .ant-tabs-content-holder) {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}
.img-wrap {
  position: relative;
  flex: 1;
  overflow: hidden;
  background: #262626;
  border-radius: 6px;
  min-height: 320px;
  cursor: grab;
}
.img-wrap:active {
  cursor: grabbing;
}
.img-inner {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}
.scan-img {
  max-width: none;
  max-height: none;
  transform-origin: center;
  user-select: none;
}
.drag-hint {
  position: absolute;
  bottom: 8px;
  right: 10px;
  color: rgba(255, 255, 255, 0.6);
  font-size: 12px;
  background: rgba(0, 0, 0, 0.4);
  padding: 2px 8px;
  border-radius: 4px;
  pointer-events: none;
}
.page-notes {
  margin-top: 8px;
}
.thumb-strip {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  padding-bottom: 8px;
}
.thumb {
  position: relative;
  width: 58px;
  height: 78px;
  flex-shrink: 0;
  border: 2px solid transparent;
  border-radius: 4px;
  overflow: hidden;
  cursor: pointer;
  background: #fafafa;
}
.thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.thumb span {
  position: absolute;
  left: 2px;
  bottom: 2px;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  font-size: 11px;
  padding: 0 4px;
  border-radius: 3px;
}
.thumb.active {
  border-color: #1677ff;
  box-shadow: 0 0 0 2px rgba(22, 119, 255, 0.35);
}
.thumb-dot {
  position: absolute;
  top: 3px;
  right: 3px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #52c41a;
}
.thumb.failed {
  border-color: #ff4d4f;
}
/* 失败页本身是红框，选中时需明显区分 → 蓝框 + 外圈高亮（必须写在 .failed 之后才生效） */
.thumb.failed.active {
  border-color: #1677ff;
  box-shadow: 0 0 0 2px rgba(22, 119, 255, 0.5);
}
.thumb-fail {
  position: absolute;
  top: 2px;
  left: 2px;
  font-style: normal;
  background: #ff4d4f;
  color: #fff;
  font-size: 10px;
  width: 14px;
  height: 14px;
  line-height: 14px;
  text-align: center;
  border-radius: 50%;
}
.empty-hint {
  color: #bbb;
  text-align: center;
  padding: 24px 0;
}
.add-row-btn {
  margin-bottom: 8px;
}
.tab-op-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.tab-op-row .add-row-btn {
  flex: 1;
  min-width: 160px;
  margin-bottom: 0;
}
/* ============ 篇目内容：标题行 =「篇目内容(N)」与 字号/转简体/人工添加 同行，正文独立滚动 ============ */
.content-pane {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.content-head {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  padding: 2px 0 8px;
  border-bottom: 1px solid #f0f0f0;
  margin-bottom: 8px;
}
.content-head-spacer {
  flex: 1;
}
.content-head-title {
  font-weight: 600;
  font-size: 14px;
  color: rgba(0, 0, 0, 0.88);
  white-space: nowrap;
}
.content-head .add-row-btn {
  margin-bottom: 0;
}
.content-head-ops {
  align-items: center;
}
.content-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}
/* AI 识别框 / 人工添加框统一字号：textarea 继承容器字号（由「字号」下拉注入到容器的 font-size） */
.content-scroll :deep(.item-card textarea) {
  font-size: inherit;
}
.item-card {
  border: 1px solid #f0f0f0;
  border-radius: 6px;
  padding: 8px;
  margin-bottom: 8px;
  transition: border-color 0.2s;
}
.item-card.active {
  border-color: #1677ff;
  box-shadow: 0 0 0 1px #1677ff;
}
.item-card.rejected {
  opacity: 0.55;
}
.item-head {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  margin-bottom: 6px;
}
.conf-badge {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 3px;
  white-space: nowrap;
}
.item-title {
  font-weight: 600;
}
.item-num {
  margin-left: auto;
  color: #bbb;
  font-size: 12px;
}
.del-link {
  color: #ff4d4f;
  font-size: 12px;
  margin-left: 4px;
  user-select: none;
}
.item-fields {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  align-items: center;
}
.arrow {
  color: #999;
}
.entry-text {
  white-space: pre-wrap;
  line-height: 1.7;
  color: #333;
  font-size: 13px;
  background: #fafafa;
  border: 1px solid #f0f0f0;
  border-radius: 4px;
  padding: 8px 10px;
}
.center {
  height: 60vh;
  display: flex;
  align-items: center;
  justify-content: center;
}
.muted {
  color: #999;
}
/* 整卷整理结果工具栏 / 关系行 */
.cons-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.rel-fields {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 220px;
}
.rel-ev {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 3px;
  white-space: nowrap;
  line-height: 19px;
}
.ev-link {
  color: #1677ff;
  cursor: pointer;
  white-space: nowrap;
}
.ev-link:hover {
  text-decoration: underline;
}
</style>

<style>
/* 浮窗大图 + 结果对照（Modal 内容 teleport 到 body，scoped 样式不生效，单独非 scoped） */
.img-preview-modal .ant-modal-body {
  padding: 12px;
}
.img-preview-modal .preview-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}
.img-preview-modal .preview-title {
  font-weight: 600;
}
.img-preview-modal .preview-body {
  height: 76vh;
  display: flex;
  flex-direction: row;
  gap: 12px;
}
.img-preview-modal .pv-stage {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.img-preview-modal .pv-result {
  flex: 0 0 430px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  border: 1px solid #f0f0f0;
  border-radius: 6px;
  padding: 4px 10px;
  background: #fff;
}
.img-preview-modal .pv-result .content-head {
  flex-shrink: 0;
  margin-bottom: 6px;
}
.img-preview-modal .pv-result .pv-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}
/* 浮窗（a-modal teleport 到 body）文字区：textarea 默认用 UA 固定字号、不随「字号」控件变化。
   本块为非 scoped <style>，无需也不能用 :deep；直接普通选择器命中弹窗内的 textarea，
   令其继承 .pv-scroll 上由 paraFs 注入的容器字号。 */
.img-preview-modal .pv-result .pv-scroll .item-card textarea {
  font-size: inherit;
}
.img-preview-modal .preview-img-wrap {
  position: relative;
  flex: 1;
  min-height: 0;
  overflow: hidden;
  background: #141414;
  border-radius: 6px;
  cursor: grab;
}
.img-preview-modal .preview-img-wrap:active {
  cursor: grabbing;
}
.img-preview-modal .preview-img-wrap .img-inner {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}
.img-preview-modal .preview-img-wrap .scan-img {
  max-width: none;
  max-height: none;
  transform-origin: center;
  user-select: none;
}
.img-preview-modal .preview-hint {
  position: absolute;
  bottom: 8px;
  right: 10px;
  color: rgba(255, 255, 255, 0.6);
  font-size: 12px;
  background: rgba(0, 0, 0, 0.4);
  padding: 2px 8px;
  border-radius: 4px;
  pointer-events: none;
}
/* 浮窗右侧结果列表（值保持与主界面 scoped 样式一致） */
.img-preview-modal .pv-result .item-card {
  border: 1px solid #f0f0f0;
  border-radius: 6px;
  padding: 8px;
  margin-bottom: 8px;
  transition: border-color 0.2s;
}
.img-preview-modal .pv-result .item-card.active {
  border-color: #1677ff;
  box-shadow: 0 0 0 1px #1677ff;
}
.img-preview-modal .pv-result .item-card.rejected {
  opacity: 0.55;
}
.img-preview-modal .pv-result .item-head {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  margin-bottom: 6px;
}
.img-preview-modal .pv-result .conf-badge {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 3px;
  white-space: nowrap;
}
.img-preview-modal .pv-result .item-title {
  font-weight: 600;
}
.img-preview-modal .pv-result .item-num {
  margin-left: auto;
  color: #bbb;
  font-size: 12px;
}
.img-preview-modal .pv-result .item-fields {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  align-items: center;
}
.img-preview-modal .pv-result .arrow {
  color: #999;
}
.img-preview-modal .pv-result .entry-text {
  white-space: pre-wrap;
  line-height: 1.7;
  color: #333;
  font-size: 13px;
  background: #fafafa;
  border: 1px solid #f0f0f0;
  border-radius: 4px;
  padding: 8px 10px;
}
.img-preview-modal .pv-result .empty-hint {
  color: #bbb;
  text-align: center;
  padding: 24px 0;
}
.img-preview-modal .pv-result .add-row-btn {
  margin-bottom: 8px;
}
.img-preview-modal .pv-tab-ops {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.img-preview-modal .pv-tab-ops .add-row-btn {
  flex: 1;
  min-width: 120px;
  margin-bottom: 0;
}
/* 浮窗最大化：铺满视口（对未原生支持 fullscreen 的版本兜底） */
.img-preview-modal.preview-max .ant-modal {
  top: 0;
  max-width: 100vw;
  width: 100vw !important;
  margin: 0;
  padding-bottom: 0;
}
.img-preview-modal.preview-max .ant-modal-content {
  min-height: 100vh;
}
.img-preview-modal.preview-max .preview-body {
  height: calc(100vh - 120px);
}
.img-preview-modal.preview-max .pv-result {
  flex-basis: min(42vw, 560px);
}
/* 卷级「⚠ 注意」徽标：悬停弹出精简列表、可逐条暂时关闭 */
.bell-wrap {
  position: relative;
  display: inline-flex;
  align-items: center;
  cursor: pointer;
}
.bell-ico {
  font-size: 12.5px;
  line-height: 1;
  display: inline-block;
  color: #faad14;
  font-weight: 600;
  padding: 1px 4px;
  border: 1px solid #ffd591;
  border-radius: 4px;
  background: #fffbe6;
  white-space: nowrap;
}
.bell-wrap:hover .bell-panel {
  display: block;
}
.bell-panel {
  display: none;
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  z-index: 30;
  min-width: 320px;
  max-width: 440px;
  background: #fff;
  border: 1px solid #e8e8e8;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.16);
  overflow: hidden;
}
.bell-head {
  padding: 8px 12px;
  font-weight: 600;
  font-size: 12.5px;
  color: #666;
  background: #fafafa;
  border-bottom: 1px solid #f0f0f0;
}
.bell-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 10px 8px 14px;
  font-size: 12.5px;
  line-height: 1.55;
  border-bottom: 1px solid #f7f7f7;
  border-left: 3px solid transparent;
  transition: background 0.15s;
}
.bell-item:last-child {
  border-bottom: none;
}
.bell-item:hover {
  background: #f6f6f6;
}
.bell-item.warn {
  border-left-color: #faad14;
  color: #d46b08;
}
.bell-item.error {
  border-left-color: #ff4d4f;
  color: #cf1322;
}
.bell-item.info {
  border-left-color: #1677ff;
  color: #1677ff;
}
.bell-item .bell-txt {
  flex: 1;
  white-space: normal;
}
.bell-item .bell-x {
  flex: none;
  color: #bbb;
  font-size: 12px;
  line-height: 1;
  padding: 2px 2px 0;
  cursor: pointer;
  border-radius: 3px;
}
.bell-item .bell-x:hover {
  color: #ff4d4f;
  background: #fff1f0;
}
</style>

<!-- 只读归档模式（body.viewer-ro，由 ScanReview 只读视图挂载）：
     Modal/Drawer 经 Teleport 渲染到 body，故需非 scoped 才能命中整卷抽屉与放大浮窗内的编辑控件。
     禁编辑输入/勾选/删除链接等；保留：分页/翻页、页码跳转、抽屉搜索、缩放旋转翻页、证据跳转、取回按钮。 -->
<style>
body.viewer-ro .ant-input,
body.viewer-ro textarea,
body.viewer-ro .ant-input-affix-wrapper,
body.viewer-ro .ant-input-number,
body.viewer-ro .ant-picker,
body.viewer-ro .ant-select .ant-select-selector,
body.viewer-ro .ant-select:not(.ant-select-open) .ant-select-selector,
body.viewer-ro .ant-checkbox-wrapper,
body.viewer-ro .ant-radio-wrapper,
body.viewer-ro .ant-switch,
body.viewer-ro .del-link,
body.viewer-ro .add-row-btn {
  pointer-events: none !important;
  background-color: #f5f5f5 !important;
  color: #555 !important;
}
body.viewer-ro .ant-btn-primary,
body.viewer-ro .ant-btn-dangerous {
  pointer-events: none !important;
  opacity: 0.55 !important;
}
/* 只读仍可用的操作 */
body.viewer-ro .ro-btn-ok,
body.viewer-ro .ant-btn-primary.ro-btn-ok {
  pointer-events: auto !important;
  opacity: 1 !important;
}
/* 整卷表分页器：翻页/切页大小可用 */
body.viewer-ro .ant-pagination {
  pointer-events: auto !important;
}
/* 页码跳转输入（ro-jump）与抽屉搜索框（ro-search）只读可输入 */
body.viewer-ro .ro-jump .ant-input-number,
body.viewer-ro .ro-jump .ant-input-number-input,
body.viewer-ro .ro-search .ant-input-affix-wrapper,
body.viewer-ro .ro-search .ant-input,
body.viewer-ro .ro-search {
  pointer-events: auto !important;
  background-color: #fff !important;
}
body.viewer-ro .ant-input-number-disabled,
body.viewer-ro input[disabled],
body.viewer-ro textarea[disabled] {
  color: #555 !important;
}
/* 篇目段落上移/下移 */
.mv {
  color: #1677ff;
  font-size: 13px;
  padding: 0 3px;
  user-select: none;
}
.mv-dis {
  color: #c0c4cc;
  pointer-events: none;
}
</style>
