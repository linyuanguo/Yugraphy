<template>
  <div>
    <a-card :bordered="false">
      <template #title>⚙️ 系统设置</template>
      <a-tabs v-model:activeKey="activeTab" type="card">
        <!-- ============ 用户管理 ============ -->
        <a-tab-pane key="users" tab="用户管理">
          <UsersPanel />
        </a-tab-pane>

        <!-- ============ 操作日志 ============ -->
        <a-tab-pane key="logs" tab="操作日志">
          <AuditLogPanel />
        </a-tab-pane>

        <!-- ============ 存储清理 ============ -->
        <a-tab-pane key="storage" tab="存储清理">
          <StorageCleanPanel />
        </a-tab-pane>

        <!-- ============ 模型配置 ============ -->
        <a-tab-pane key="models" tab="模型配置">
          <a-alert
            type="info"
            show-icon
            style="margin-bottom: 16px"
            message="模型列表用于「访客问答 → 启用 AI 增强解析」的模型名称下拉。灰色行是服务器部署的内置默认模型（识别与问答默认使用），API 地址与密钥由服务器环境变量提供，页面不可修改；其余模型可单独配置 API 地址与密钥（OpenAI 兼容接口）。"
          />
          <a-table
            :data-source="models"
            row-key="__key"
            size="middle"
            :pagination="false"
            :columns="modelColumns"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'name'">
                <a-input
                  v-if="record.builtin"
                  :value="record.name"
                  disabled
                  title="服务器部署的内置默认模型，不可修改"
                />
                <a-input v-else v-model:value="record.name" placeholder="模型名，如 qwen3-vl-30b-fp8" />
              </template>
              <template v-else-if="column.key === 'label'">
                <a-input
                  v-model:value="record.label"
                  :disabled="record.builtin"
                  placeholder="显示名（缺省用模型名）"
                />
              </template>
              <template v-else-if="column.key === 'note'">
                <a-input v-model:value="record.note" placeholder="备注（可选）" />
              </template>
              <template v-else-if="column.key === 'api_base'">
                <a-input
                  v-if="record.builtin"
                  :value="'（服务器环境变量）'"
                  disabled
                  title="内置模型：API 地址来自服务器环境变量，不可配置"
                />
                <a-input
                  v-else
                  v-model:value="record.api_base"
                  placeholder="如 https://dashscope.aliyuncs.com/compatible-mode（可选）"
                />
              </template>
              <template v-else-if="column.key === 'api_key'">
                <a-input-password
                  v-if="record.builtin"
                  :value="'（服务器环境变量）'"
                  disabled
                  title="内置模型：API 密钥来自服务器环境变量，不可配置"
                />
                <a-input-password
                  v-else
                  v-model:value="record.api_key"
                  placeholder="该模型的 API 密钥（可选）"
                />
              </template>
              <template v-else-if="column.key === 'action'">
                <a-popconfirm
                  v-if="!record.builtin"
                  title="确认移除该模型？"
                  @confirm="removeModel(record)"
                >
                  <a style="color: #ff4d4f">移除</a>
                </a-popconfirm>
                <span v-else class="muted" title="内置默认模型不可移除">内置</span>
              </template>
            </template>
          </a-table>
          <a-space style="margin-top: 16px">
            <a-button @click="addModel">＋ 添加模型</a-button>
            <a-button type="primary" :loading="savingModels" @click="saveModels">
              保存模型列表
            </a-button>
          </a-space>

          <!-- ============ AI 识别参数（扫描件逐页提取） ============ -->
          <a-divider orientation="left">AI 识别参数</a-divider>
          <div style="max-width: 860px">
            <a-alert
              type="info"
              show-icon
              style="margin-bottom: 12px"
              message="「扫描件导入」逐页识别所用的提示词（人物 / 谱书正文提取模板），仅管理员可改。"
            />
            <a-form layout="vertical">
              <a-form-item label="谱系识别提示词（作用于扫描件导入的逐页提取）">
                <a-textarea
                  v-model:value="scanPrompt"
                  :rows="14"
                  placeholder="提示词模板（输出 JSON 的结构约束），修改前建议先复制备份当前内容"
                />
                <div class="hint">
                  留空并保存 = 恢复服务端代码内置默认模板。修改后对<b>之后执行</b>的识别生效（新导入、单页补扫、整卷重扫），已识别页不会自动重跑。
                </div>
              </a-form-item>
              <a-space>
                <a-button type="primary" :loading="savingScan" @click="saveScan">
                  保存 AI 识别参数
                </a-button>
                <a-button :loading="savingScan" @click="resetScan">恢复默认（清空自定义）</a-button>
              </a-space>
            </a-form>
          </div>

          <!-- ============ AI 整理参数（卷级归并） ============ -->
          <a-divider orientation="left">AI 整理参数</a-divider>
          <div style="max-width: 860px">
            <a-alert
              type="warning"
              show-icon
              style="margin-bottom: 12px"
              message="整卷整理（第二段）只做「人物跨页归并 + 父子/配偶关系推断」，不改写页面正文，因此不影响正文检索结果。可在此调整整理提示词，例如放宽 / 收紧关系推断、补充需忽略的人名规则。"
            />
            <a-form layout="vertical">
              <a-form-item label="卷级整理提示词（{start} / {end} / {chunk} 为占位符）">
                <a-textarea
                  v-model:value="consolidatePrompt"
                  :rows="14"
                  placeholder="提示词模板（输出 JSON 的结构约束），修改前建议先复制备份当前内容"
                />
                <div class="hint">
                  留空并保存 = 恢复服务端代码内置默认模板。修改后对<b>之后执行</b>的整理生效（新识别完成自动整理、手动「🔄 重新整理」），已有的整理结果不会自动重跑。
                </div>
              </a-form-item>
              <a-space>
                <a-button type="primary" :loading="savingCons" @click="saveConsolidate">
                  保存 AI 整理参数
                </a-button>
                <a-button :loading="savingCons" @click="resetConsolidate">
                  恢复默认（清空自定义）
                </a-button>
              </a-space>
            </a-form>
          </div>

          <!-- ============ 访客问答参数 ============ -->
          <a-divider orientation="left">访客问答参数</a-divider>
          <a-form layout="vertical" style="max-width: 760px">
            <a-form-item label="访客欢迎语">
              <a-input v-model:value="qa.qa_welcome" placeholder="访客打开问答时显示的引导语" />
            </a-form-item>
            <a-form-item label="启用 AI 增强解析">
              <a-switch v-model:checked="qa.qa_enable_llm" />
              <div class="hint">
                默认关闭：仅用本地规则引擎（确定、快速、不依赖外部大模型）。开启后，规则无法解析的问题会交给大模型识别意图。
              </div>
            </a-form-item>
            <a-form-item v-if="qa.qa_enable_llm" label="模型名称">
              <a-select
                v-model:value="qa.qa_llm_model"
                placeholder="请选择模型"
                :options="modelOptions"
              />
              <div class="hint">选项来自上方模型列表；如需其他模型请先在模型列表中添加。</div>
            </a-form-item>
            <a-form-item label="意图识别 Prompt">
              <a-textarea
                v-model:value="qa.qa_llm_prompt"
                :rows="6"
                placeholder="把用户问题解析为 JSON 的提示词，{question} 为占位符"
              />
            </a-form-item>
            <a-form-item>
              <a-button type="primary" :loading="savingQa" @click="saveQa">
                保存问答参数
              </a-button>
            </a-form-item>
          </a-form>
        </a-tab-pane>

        <!-- ============ 删除审批 ============ -->
        <a-tab-pane key="delete-approvals" tab="删除审批">
          <DeleteApprovalPanel />
        </a-tab-pane>

        <!-- ============ 家谱内容检索（RAG） ============ -->
        <a-tab-pane key="rag" tab="家谱内容检索（RAG）">
          <div style="max-width: 880px">
            <a-alert
              type="info"
              show-icon
              style="margin-bottom: 16px"
              message="配置谱书内容检索所用模型与参数。地址/模型留空 = 使用服务器 .env 内置默认；保存后对之后的检索与索引同步生效。修改了 Embedding 模型或切片参数后，请点「重建向量索引」重算全部历史向量。"
            />
            <a-form layout="vertical">
              <a-row :gutter="16">
                <a-col :span="12">
                  <a-form-item label="Embedding 服务地址（OpenAI 兼容 /v1/embeddings）">
                    <a-input v-model:value="rag.embedding_url" placeholder="默认 http://172.16.199.206:30010" />
                  </a-form-item>
                </a-col>
                <a-col :span="12">
                  <a-form-item label="Embedding 模型名">
                    <a-input v-model:value="rag.embedding_model" placeholder="默认 Qwen3-Embedding-0.6B-Q8_0.gguf" />
                  </a-form-item>
                </a-col>
              </a-row>
              <a-form-item label="Embedding API 密钥">
                <a-input-password
                  v-model:value="rag.embedding_api_key"
                  placeholder="留空 = 匿名访问，401 时自动带服务器内置密钥重试"
                />
              </a-form-item>
              <a-row :gutter="16">
                <a-col :span="12">
                  <a-form-item label="Reranker 服务地址（/v1/rerank）">
                    <a-input v-model:value="rag.rerank_url" placeholder="默认 http://172.16.199.206:30011" />
                  </a-form-item>
                </a-col>
                <a-col :span="12">
                  <a-form-item label="Reranker 模型名">
                    <a-input v-model:value="rag.rerank_model" placeholder="默认 qwen3-reranker-0.6b-q8_0.gguf" />
                  </a-form-item>
                </a-col>
              </a-row>
            </a-form>

            <a-divider style="margin: 4px 0 20px">切片 / 检索参数</a-divider>
            <a-form layout="vertical">
              <a-row :gutter="16">
                <a-col :span="8">
                  <a-form-item label="向量化单条文本上限（字）">
                    <a-input-number v-model:value="rag.max_emb_chars" :min="50" :max="5000" style="width: 100%" />
                    <div class="hint">每条谱书原文参与向量化的最大长度（切片上限）</div>
                  </a-form-item>
                </a-col>
                <a-col :span="8">
                  <a-form-item label="余弦召回候选数">
                    <a-input-number v-model:value="rag.top_candidates" :min="5" :max="200" style="width: 100%" />
                    <div class="hint">交 rerank 精排前的候选条数</div>
                  </a-form-item>
                </a-col>
                <a-col :span="8">
                  <a-form-item label="默认返回命中数">
                    <a-input-number v-model:value="rag.search_limit" :min="1" :max="50" style="width: 100%" />
                    <div class="hint">检索结果默认返回条数</div>
                  </a-form-item>
                </a-col>
              </a-row>
              <a-row :gutter="16">
                <a-col :span="8">
                  <a-form-item label="单次批量嵌入条数">
                    <a-input-number v-model:value="rag.embedding_batch" :min="1" :max="64" style="width: 100%" />
                    <div class="hint">分批向量化，服务排队时可调小</div>
                  </a-form-item>
                </a-col>
                <a-col :span="8">
                  <a-form-item label="命中原文展示截断（字）">
                    <a-input-number v-model:value="rag.hit_text_chars" :min="50" :max="5000" style="width: 100%" />
                    <div class="hint">结果原文返回长度（前端可展开全文）</div>
                  </a-form-item>
                </a-col>
              </a-row>
            </a-form>

            <a-divider style="margin: 4px 0 20px">向量数据库</a-divider>
            <a-alert
              type="warning"
              show-icon
              style="margin-bottom: 12px"
              message="当前检索引擎 = 内置向量库（谱书原文向量存 PostgreSQL 的 rag_vectors 表，全量余弦召回）。外部向量库可「添加」登记，用于后续引擎接入的清单管理；服务端接入前，默认向量库固定为内置库。"
            />
            <a-table
              :data-source="vectorDbs"
              row-key="id"
              size="middle"
              :pagination="false"
              :columns="vdbColumns"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'name'">
                  <a-typography-text strong>{{ record.name }}</a-typography-text>
                  <a-tag v-if="record.builtin" color="green" style="margin-left: 8px">当前默认</a-tag>
                </template>
                <template v-else-if="column.key === 'type'">{{ record.type }}</template>
                <template v-else-if="column.key === 'endpoint'">
                  <span v-if="record.endpoint" class="mono">{{ record.endpoint }}</span>
                  <span v-else class="muted">—</span>
                </template>
                <template v-else-if="column.key === 'note'">
                  <span class="muted">{{ record.note || '—' }}</span>
                </template>
                <template v-else-if="column.key === 'action'">
                  <a-popconfirm
                    v-if="!record.builtin"
                    title="确认移除该向量库登记？"
                    @confirm="removeVdb(record)"
                  >
                    <a style="color: #ff4d4f">移除</a>
                  </a-popconfirm>
                  <span v-else class="muted">内置</span>
                </template>
              </template>
            </a-table>
            <a-space style="margin-top: 16px">
              <a-button @click="vdbAddOpen = true">＋ 添加向量库</a-button>
              <a-button type="primary" :loading="savingRag" @click="saveRag">
                保存 RAG 设置
              </a-button>
              <a-button :loading="rebuilding" @click="rebuildRag">重建向量索引</a-button>
            </a-space>

            <a-modal v-model:open="vdbAddOpen" title="添加向量库（登记，供后续引擎接入）" :footer="null">
              <a-form layout="vertical">
                <a-form-item label="名称" required>
                  <a-input v-model:value="vdbForm.name" placeholder="如：Milvus 主库" />
                </a-form-item>
                <a-form-item label="引擎类型">
                  <a-select v-model:value="vdbForm.type" :options="vdbTypeOptions" />
                </a-form-item>
                <a-form-item label="连接地址（endpoint / host:port / DSN）">
                  <a-input v-model:value="vdbForm.endpoint" placeholder="如 http://192.168.1.10:19530" />
                </a-form-item>
                <a-form-item label="备注">
                  <a-input v-model:value="vdbForm.note" placeholder="集合/库名、账号等说明（可选）" />
                </a-form-item>
                <a-space>
                  <a-button type="primary" @click="addVdb">添加</a-button>
                  <a-button @click="vdbAddOpen = false">取消</a-button>
                </a-space>
              </a-form>
            </a-modal>

            <a-divider style="margin: 8px 0 20px">🧪 检索链路测试：向量召回 vs Rerank 精排</a-divider>
            <a-alert
              type="info"
              show-icon
              style="margin-bottom: 12px"
              message="输入一句话，分别展示 Embedding 余弦召回（top_candidates 候选）与 Rerank 精排后的顺序与分数，直观验证当前模型/参数配置的实际召回效果（仅走检索链路，不生成 AI 文案）。修改模型或参数后请先「保存 RAG 设置」再测试。"
            />
            <div class="rag-test-tool">
              <a-select
                v-model:value="testLineageId"
                placeholder="全部谱系"
                style="width: 170px"
                allow-clear
              >
                <a-select-option v-for="l in ragLineages" :key="l.lineage_id" :value="l.lineage_id">
                  {{ l.name }}
                </a-select-option>
              </a-select>
              <a-input
                v-model:value="testQuestion"
                placeholder="输入测试问题，如：徐氏源流迁居何处 / 始祖是谁 / 家规有哪些"
                allow-clear
                @press-enter="runRagTest"
              />
              <a-button type="primary" :loading="testingRag" @click="runRagTest">
                🚀 测试检索链路
              </a-button>
            </div>

            <a-alert
              v-if="ragTest && ragTest.error"
              type="warning"
              show-icon
              style="margin-top: 12px"
              :message="ragTest.error"
            />

            <template v-if="ragTest && !ragTest.error && ragTest.total">
              <div class="rag-test-meta">
                <a-tag color="blue">Embedding：{{ ragTest.embedding_model }}</a-tag>
                <a-tag color="purple">Reranker：{{ ragTest.rerank_model }}</a-tag>
                <a-tag color="green">余弦召回 {{ ragTest.total }} 条</a-tag>
                <a-tag :color="ragTest.rerank_used ? 'cyan' : 'orange'">
                  {{ ragTest.rerank_used ? 'Rerank 精排已生效' : 'Rerank 不可用（按余弦序）' }}
                </a-tag>
                <span class="muted">
                  向量维度 {{ ragTest.vector_dim ?? '—' }} · 每侧展示前 {{ RECALL_SHOW }} 名（点行展开原文）
                </span>
              </div>
              <a-row :gutter="16">
                <a-col :span="12">
                  <div class="rag-test-panel">
                    <div class="rag-test-panel-title">① Embedding 余弦召回（相似度 ↓）</div>
                    <a-table
                      :data-source="recallRows"
                      row-key="entry_id"
                      size="small"
                      :pagination="false"
                      :columns="recallColumns"
                    >
                      <template #bodyCell="{ column, record }">
                        <template v-if="column.key === 'rank'">
                          <span class="rag-test-no">{{ record.rank }}</span>
                        </template>
                        <template v-else-if="column.key === 'sim'">
                          <span class="rag-test-sim">{{ (record.sim ?? 0).toFixed(4) }}</span>
                        </template>
                        <template v-else-if="column.key === 'type'">
                          <a-tag>{{ record.type }}</a-tag>
                        </template>
                        <template v-else-if="column.key === 'title'">
                          <span class="rag-test-title">{{ record.title || '（无标题）' }}</span>
                        </template>
                      </template>
                      <template #expandedRowRender="{ record }">
                        <pre class="rag-test-text">{{ record.text }}</pre>
                      </template>
                    </a-table>
                  </div>
                </a-col>
                <a-col :span="12">
                  <div class="rag-test-panel">
                    <div class="rag-test-panel-title">② Rerank 精排（分数 ↓）</div>
                    <a-table
                      :data-source="rankedRows"
                      row-key="entry_id"
                      size="small"
                      :pagination="false"
                      :columns="rankedColumns"
                    >
                      <template #bodyCell="{ column, record }">
                        <template v-if="column.key === 'rank'">
                          <span class="rag-test-no">{{ record.rank }}</span>
                        </template>
                        <template v-else-if="column.key === 'rerank_score'">
                          <span class="rag-test-score">
                            {{ record.rerank_score == null ? '—' : Number(record.rerank_score).toFixed(4) }}
                          </span>
                        </template>
                        <template v-else-if="column.key === 'delta'">
                          <span
                            v-if="record.from_rank != null && record.from_rank !== record.rank"
                            :class="record.from_rank > record.rank ? 'rag-test-up' : 'rag-test-down'"
                          >
                            {{ record.from_rank > record.rank ? '↑ 升' : '↓ 降' }}
                            {{ Math.abs(record.from_rank - record.rank) }}
                          </span>
                          <span v-else class="muted">—</span>
                        </template>
                        <template v-else-if="column.key === 'type'">
                          <a-tag>{{ record.type }}</a-tag>
                        </template>
                        <template v-else-if="column.key === 'title'">
                          <span class="rag-test-title">{{ record.title || '（无标题）' }}</span>
                        </template>
                      </template>
                      <template #expandedRowRender="{ record }">
                        <pre class="rag-test-text">{{ record.text }}</pre>
                      </template>
                    </a-table>
                  </div>
                </a-col>
              </a-row>
            </template>
            <a-empty
              v-else-if="ragTest && !ragTest.error && !ragTest.total"
              description="未召回任何内容，换个说法或去掉谱系筛选再试"
              style="margin-top: 12px"
            />
          </div>
        </a-tab-pane>

        <!-- ============ HTTPS 白名单（IP 访问控制） ============ -->
        <a-tab-pane key="https" tab="HTTPS 白名单">
          <div style="max-width: 760px">
            <a-alert
              type="info"
              show-icon
              style="margin-bottom: 16px"
              message="限制可访问本系统的 IP。默认未启用（不限制任何 IP）；一旦填写了 IP 段即视为启用，仅命中网段/网卡的 IP 可访问。保存后即时生效（热加载，无需重启）。"
            />
            <a-form layout="vertical">
              <a-form-item label="允许访问的 IP / 网段（每行一条，可写单 IP 或 CIDR 网段）">
                <a-textarea
                  v-model:value="ipWhitelistText"
                  :rows="8"
                  :disabled="ipSaving"
                  placeholder="例如（每行一条）：&#10;172.16.199.0/24&#10;10.0.0.5&#10;192.168.1.0/32"
                />
                <div class="hint">
                  留空 = 未启用（不限制 IP）。支持 IPv4 / IPv6，可写单 IP（自动按 /32 处理）或 CIDR 网段。
                </div>
              </a-form-item>
              <a-space>
                <a-tag :color="ipEnabled ? 'green' : 'default'" style="font-size: 13px">
                  {{ ipEnabled ? `已启用 · 共 ${ipSegments.length} 段` : '未启用 · 不限制 IP' }}
                </a-tag>
              </a-space>
              <a-divider />
              <a-space>
                <a-button type="primary" :loading="ipSaving" @click="saveIp">
                  保存白名单（即时生效）
                </a-button>
                <a-popconfirm
                  title="清空后所有 IP 均可访问（回到未启用状态），确认？"
                  @confirm="clearIp"
                >
                  <a-button :loading="ipSaving">清空（停用）</a-button>
                </a-popconfirm>
              </a-space>
            </a-form>
          </div>
        </a-tab-pane>
      </a-tabs>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { message } from 'ant-design-vue'
import { getSystemSettingsApi, listLineagesApi, ragTestSearchApi, rebuildRagIndexApi, updateSystemSettingsApi } from '@/api'
import type { Lineage, LlmModel, RagConfig, RagTestResult, SystemSettings, VectorDb } from '@/types'
import UsersPanel from './Users.vue'
import AuditLogPanel from './AuditLog.vue'
import StorageCleanPanel from './StorageClean.vue'
import DeleteApprovalPanel from './DeleteApproval.vue'

const route = useRoute()
const activeTab = ref<string>((route.query.tab as string) || 'users')

// 顶栏铃铛/系统设置跳「删除审批」等 tab 时，同页 query 变化也要切 tab
watch(
  () => route.query.tab as string | undefined,
  (tab) => {
    if (tab && tab !== activeTab.value) activeTab.value = tab
  },
)

// ---------- 用户管理（UsersPanel 自带加载） ----------

// ---------- 模型配置 ----------
const MODELS_NAME_RE = /^[A-Za-z0-9._\-]+$/
const models = ref<LlmModel[]>([])
let modelSeq = 0
const modelColumns = [
  { title: '模型名', key: 'name', dataIndex: 'name' },
  { title: '显示名', key: 'label', dataIndex: 'label', width: 200 },
  { title: '备注', key: 'note', dataIndex: 'note' },
  { title: 'API 地址', key: 'api_base', dataIndex: 'api_base', width: 260 },
  { title: 'API 密钥', key: 'api_key', dataIndex: 'api_key', width: 220 },
  { title: '操作', key: 'action', width: 80 },
]

const savingModels = ref(false)

const rowModel = (m: LlmModel) => ({
  ...m,
  label: m.label || '',
  note: m.note || '',
  api_base: m.api_base || '',
  api_key: m.api_key || '',
  __key: ++modelSeq,
})

const addModel = () => {
  models.value.push(rowModel({ name: '', label: '', note: '' }))
}

const removeModel = (record: LlmModel & { __key: number }) => {
  models.value = models.value.filter((m) => (m as any).__key !== record.__key)
}

const saveModels = async () => {
  const seen = new Set<string>()
  for (const m of models.value) {
    const name = (m.name || '').trim()
    if (!name) {
      message.error('存在空的模型名，请填写或移除')
      return
    }
    if (!MODELS_NAME_RE.test(name)) {
      message.error(`模型名「${name}」仅允许字母/数字/._-`)
      return
    }
    if (seen.has(name)) {
      message.error(`模型名「${name}」重复`)
      return
    }
    seen.add(name)
  }
  if (!models.value.length) {
    message.error('模型列表不能为空，请至少保留一个模型')
    return
  }
  savingModels.value = true
  try {
    await updateSystemSettingsApi({
      llm_models: models.value.map((m) => ({
        name: (m.name || '').trim(),
        label: (m.label || '').trim() || undefined,
        note: (m.note || '').trim() || undefined,
        api_base: (m.api_base || '').trim() || undefined,
        api_key: (m.api_key || '').trim() || undefined,
      })),
    })
    message.success('模型列表已保存')
    await load()
  } finally {
    savingModels.value = false
  }
}

// ---------- 访客问答参数 ----------
const qa = reactive({
  qa_enable_llm: false,
  qa_welcome: '',
  qa_llm_model: '',
  qa_llm_prompt: '',
})
const savingQa = ref(false)

const modelOptions = computed(() =>
  models.value.map((m) => ({ value: m.name, label: m.label || m.name })),
)

// ---------- AI 识别参数（扫描件导入的提示词） ----------
const scanPrompt = ref('')
const savingScan = ref(false)

// ---------- AI 整理参数（卷级归并提示词） ----------
const consolidatePrompt = ref('')
const savingCons = ref(false)

const saveScan = async () => {
  savingScan.value = true
  try {
    await updateSystemSettingsApi({ scan_prompt: scanPrompt.value })
    message.success('识别参数已保存（对之后执行的识别生效）')
    await load()
  } finally {
    savingScan.value = false
  }
}

// 恢复默认：清空自定义存储，服务端回退代码内置模板
const resetScan = async () => {
  savingScan.value = true
  try {
    await updateSystemSettingsApi({ scan_prompt: '' })
    message.success('已恢复服务端默认识别提示词')
    await load()
  } finally {
    savingScan.value = false
  }
}

const saveConsolidate = async () => {
  savingCons.value = true
  try {
    await updateSystemSettingsApi({ consolidate_prompt: consolidatePrompt.value })
    message.success('AI 整理参数已保存（对之后执行的整理生效）')
    await load()
  } finally {
    savingCons.value = false
  }
}

// 恢复默认：清空自定义存储，服务端回退代码内置模板
const resetConsolidate = async () => {
  savingCons.value = true
  try {
    await updateSystemSettingsApi({ consolidate_prompt: '' })
    message.success('已恢复服务端默认整理提示词')
    await load()
  } finally {
    savingCons.value = false
  }
}

const saveQa = async () => {
  savingQa.value = true
  try {
    await updateSystemSettingsApi({
      qa_enable_llm: qa.qa_enable_llm,
      qa_welcome: qa.qa_welcome,
      qa_llm_model: qa.qa_llm_model,
      qa_llm_prompt: qa.qa_llm_prompt,
    })
    message.success('问答参数已保存')
    await load()
  } finally {
    savingQa.value = false
  }
}

// ---------- 家谱内容检索（RAG：embedding/reranker/切片参数 + 向量库登记） ----------
const rag = reactive<RagConfig>({
  embedding_url: '',
  embedding_model: '',
  embedding_api_key: '',
  rerank_url: '',
  rerank_model: '',
  embedding_batch: 24,
  max_emb_chars: 1200,
  top_candidates: 60,
  search_limit: 12,
  hit_text_chars: 1200,
})
const vectorDbs = ref<VectorDb[]>([])
const vectorDbActive = ref('builtin_pg')
const savingRag = ref(false)
const rebuilding = ref(false)
const vdbAddOpen = ref(false)
const vdbForm = reactive({ name: '', type: '其它', endpoint: '', note: '' })
const vdbTypeOptions = [
  'Milvus',
  'Chroma',
  'Qdrant',
  'Weaviate',
  'pgvector（PostgreSQL）',
  'Faiss',
  '其它',
].map((v) => ({ value: v, label: v }))
const vdbColumns = [
  { title: '名称', key: 'name', dataIndex: 'name' },
  { title: '引擎类型', key: 'type', dataIndex: 'type', width: 200 },
  { title: '连接地址', key: 'endpoint', dataIndex: 'endpoint' },
  { title: '说明', key: 'note', dataIndex: 'note' },
  { title: '操作', key: 'action', width: 90 },
]

const saveRag = async () => {
  savingRag.value = true
  try {
    await updateSystemSettingsApi({
      rag_config: { ...rag },
      vector_dbs: vectorDbs.value,
    })
    message.success('RAG 设置已保存（对之后的检索生效）')
    await load()
  } finally {
    savingRag.value = false
  }
}

const rebuildRag = async () => {
  rebuilding.value = true
  try {
    const r = await rebuildRagIndexApi()
    message.success(`向量索引重建完成：入库 ${r.synced} 条，共 ${r.indexed}/${r.total} 条就绪`)
  } finally {
    rebuilding.value = false
  }
}

const addVdb = () => {
  const name = vdbForm.name.trim()
  if (!name) {
    message.error('请填写向量库名称')
    return
  }
  vectorDbs.value.push({
    id: `vdb_${Date.now().toString(36)}`,
    name,
    kind: 'external',
    type: vdbForm.type || '其它',
    endpoint: vdbForm.endpoint.trim(),
    note: vdbForm.note.trim(),
    builtin: false,
  })
  vdbForm.name = ''
  vdbForm.type = '其它'
  vdbForm.endpoint = ''
  vdbForm.note = ''
  vdbAddOpen.value = false
  message.info('已加入列表，点「保存 RAG 设置」生效')
}

const removeVdb = (record: VectorDb) => {
  vectorDbs.value = vectorDbs.value.filter((v) => v.id !== record.id)
}

// ---------- 检索链路测试（Embedding 余弦召回 vs Rerank 精排） ----------
const ragLineages = ref<Lineage[]>([])
const testQuestion = ref('')
const testLineageId = ref<string | undefined>(undefined)
const testingRag = ref(false)
const ragTest = ref<RagTestResult | null>(null)
const RECALL_SHOW = 20 // 每阶段表格默认展示前 20 名（总数见 meta 标签）
const recallRows = computed(() => (ragTest.value?.recall || []).slice(0, RECALL_SHOW))
const rankedRows = computed(() => (ragTest.value?.ranked || []).slice(0, RECALL_SHOW))

const recallColumns = [
  { title: '#', key: 'rank', width: 48 },
  { title: '相似度', key: 'sim', width: 96 },
  { title: '类型', key: 'type', width: 96 },
  { title: '标题', key: 'title', ellipsis: true },
  { title: '页码', key: 'page_no', width: 70 },
]
const rankedColumns = [
  { title: '#', key: 'rank', width: 48 },
  { title: 'Rerank 分', key: 'rerank_score', width: 100 },
  { title: '排名变化', key: 'delta', width: 108 },
  { title: '类型', key: 'type', width: 96 },
  { title: '标题', key: 'title', ellipsis: true },
  { title: '页码', key: 'page_no', width: 70 },
]

const runRagTest = async () => {
  const q = testQuestion.value.trim()
  if (!q) {
    message.info('请输入要测试的家谱内容问题')
    return
  }
  testingRag.value = true
  ragTest.value = null
  try {
    ragTest.value = await ragTestSearchApi(q, testLineageId.value || undefined)
    if (ragTest.value?.error && !ragTest.value.total) {
      message.warning(ragTest.value.error)
    }
  } catch {
    /* request 已提示 */
  } finally {
    testingRag.value = false
  }
}

// ---------- HTTPS IP 白名单 ----------
const ipWhitelistText = ref('')
const ipSaving = ref(false)
const ipSegments = computed(() =>
  ipWhitelistText.value
    .split(/[\n,]+/)
    .map((s) => s.trim())
    .filter(Boolean),
)
const ipEnabled = computed(() => ipSegments.value.length > 0)

const saveIp = async () => {
  ipSaving.value = true
  try {
    await updateSystemSettingsApi({ https_ip_whitelist: ipSegments.value })
    message.success(
      ipSegments.value.length
        ? `白名单已启用（${ipSegments.value.length} 段），已即时生效`
        : '白名单已停用（不限制 IP），已即时生效',
    )
    await load()
  } finally {
    ipSaving.value = false
  }
}

const clearIp = async () => {
  ipWhitelistText.value = ''
  ipSaving.value = true
  try {
    await updateSystemSettingsApi({ https_ip_whitelist: [] })
    message.success('白名单已停用（不限制 IP），已即时生效')
    await load()
  } finally {
    ipSaving.value = false
  }
}

const load = async () => {
  try {
    const st = (await getSystemSettingsApi()) as SystemSettings
    qa.qa_enable_llm = st.qa_enable_llm
    qa.qa_welcome = st.qa_welcome
    qa.qa_llm_model = st.qa_llm_model
    qa.qa_llm_prompt = st.qa_llm_prompt
    scanPrompt.value = st.scan_prompt || ''
    consolidatePrompt.value = st.consolidate_prompt || ''
    models.value = (st.llm_models || []).map(rowModel)
    Object.assign(rag, st.rag_config || {})
    vectorDbs.value = (st.vector_dbs || []).map((v) => ({ ...v }))
    vectorDbActive.value = st.vector_db_active || 'builtin_pg'
    ipWhitelistText.value = (st.https_ip_whitelist || []).join('\n')
  } catch {
    /* request 已提示 */
  }
}

onMounted(async () => {
  await load()
  try {
    ragLineages.value = await listLineagesApi()
  } catch {
    /* 忽略：测试区谱系筛选留空（全部谱系） */
  }
})
</script>

<style scoped>
.hint {
  font-size: 12px;
  color: #999;
  margin-top: 6px;
  line-height: 1.6;
}
.muted {
  color: #999;
  font-size: 13px;
}
.mono {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 12px;
  word-break: break-all;
}
.rag-test-tool {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.rag-test-tool :deep(.ant-input) {
  flex: 1;
  min-width: 220px;
}
.rag-test-meta {
  margin: 12px 0;
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  align-items: center;
}
.rag-test-panel {
  border: 1px solid #f0f0f0;
  border-radius: 10px;
  padding: 10px;
  margin-bottom: 16px;
  background: #fcfcfc;
}
.rag-test-panel-title {
  font-weight: 600;
  margin-bottom: 8px;
  color: #333;
}
.rag-test-no {
  font-weight: 600;
  color: #999;
}
.rag-test-sim {
  color: #1677ff;
  font-weight: 600;
}
.rag-test-score {
  color: #722ed1;
  font-weight: 600;
}
.rag-test-up {
  color: #52c41a;
  font-weight: 600;
}
.rag-test-down {
  color: #ff4d4f;
  font-weight: 600;
}
.rag-test-title {
  font-size: 12.5px;
}
.rag-test-text {
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 12px;
  color: #555;
  max-height: 220px;
  overflow: auto;
  margin: 0;
  background: #fff;
  padding: 8px;
  border-radius: 6px;
  border: 1px solid #f0f0f0;
}
</style>
