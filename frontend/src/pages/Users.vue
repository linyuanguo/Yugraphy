<template>
  <div>
    <a-card>
      <a-alert
        type="info"
        show-icon
        style="margin-bottom: 12px"
        message="账号分两种角色：管理员（admin）可管理用户/系统设置/操作日志等全部功能；操作员（editor）负责扫描件导入识别、审核写入图谱与家谱数据维护，不可进入系统设置。操作员的「可访问模块」由管理员按需勾选：未勾选的模块，菜单自动隐藏且后端拒绝其操作。"
      />
      <div class="toolbar">
        <a-input-search v-model:value="search" placeholder="搜索用户名" style="width: 240px" allow-clear @search="load" />
        <a-button type="primary" @click="openCreate">+ 新增用户</a-button>
      </div>
      <a-table :data-source="users" :columns="columns" :loading="loading" row-key="id" :row-class-name="rowClass" :pagination="{ pageSize: 20 }" size="middle">
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'role'">
            <template v-if="record.username === 'admin'">
              <span style="color: #bbb">管理员（系统内置）</span>
            </template>
            <template v-else-if="record.role !== 'viewer'">
              <a-select
                :value="record.role"
                style="width: 110px"
                @change="(v: string) => updateUser(record, { role: v })"
              >
                <a-select-option value="admin">管理员</a-select-option>
                <a-select-option value="editor">操作员</a-select-option>
              </a-select>
              <div class="role-hint">
                {{ record.role === 'admin' ? '全部权限（含系统设置/日志）' : moduleHint(record) }}
              </div>
            </template>
            <a-tag v-else color="default">只读（旧账号）</a-tag>
          </template>
          <template v-else-if="column.key === 'active'">
            <a-switch
              :checked="record.is_active"
              :disabled="record.username === 'admin'"
              size="small"
              @change="(v: boolean) => updateUser(record, { is_active: v })"
            />
          </template>
          <template v-else-if="column.key === 'time'">{{ record.created_at?.replace('T', ' ').slice(0, 19) }}</template>
          <template v-else-if="column.key === 'action'">
            <a-space>
              <a @click="openReset(record)">修改密码</a>
              <a v-if="record.username !== 'admin' && record.role === 'editor'" @click="openPerm(record)">模块权限</a>
              <a-popconfirm v-if="record.username !== 'admin'" title="确认删除该用户？" @confirm="remove(record)">
                <a style="color: #ff4d4f">删除</a>
              </a-popconfirm>
            </a-space>
          </template>
        </template>
      </a-table>
    </a-card>

    <a-modal
      v-model:open="modalOpen"
      :title="editId ? (lockedAdmin ? '修改密码' : '编辑用户') : '新增用户'"
      @ok="submit"
      :confirm-loading="submitting"
    >
      <a-form layout="vertical">
        <a-alert v-if="lockedAdmin" type="info" show-icon style="margin-bottom: 16px" message="admin 为系统内置账号，仅可修改登录密码，姓名/邮箱/角色不可变更。" />
        <a-form-item label="用户名" required>
          <a-input v-model:value="form.username" :disabled="!!editId" placeholder="登录名" />
        </a-form-item>
        <a-form-item label="姓名">
          <a-input v-model:value="form.full_name" :disabled="lockedAdmin" placeholder="姓名" />
        </a-form-item>
        <a-form-item label="邮箱">
          <a-input v-model:value="form.email" :disabled="lockedAdmin" placeholder="邮箱（可选）" />
        </a-form-item>
        <a-form-item label="角色">
          <a-select v-model:value="form.role" :disabled="lockedAdmin">
            <a-select-option value="admin">管理员</a-select-option>
            <a-select-option value="editor">操作员</a-select-option>
            <a-select-option v-if="editId && form.role === 'viewer'" value="viewer">只读（旧账号）</a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item v-if="form.role === 'editor' && !lockedAdmin" label="可访问模块">
          <div class="perm-tree">
            <a-checkbox-group v-model:value="moduleTop">
              <a-checkbox v-for="m in TOP_OPTIONS" :key="m.code" :value="m.code" class="perm-leaf">
                {{ m.label }}
              </a-checkbox>
            </a-checkbox-group>
            <div class="perm-group">
              <a-checkbox
                :checked="groupChecked(moduleVisit)"
                :indeterminate="groupIndeterminate(moduleVisit)"
                @change="(e: any) => toggleVisitGroup(e.target.checked)"
              >
                {{ VISIT_GROUP.label }}（勾上 = 以下三项全开）
              </a-checkbox>
              <a-checkbox-group v-model:value="moduleVisit" class="perm-children">
                <a-checkbox v-for="c in VISIT_GROUP.children" :key="c.code" :value="c.code">
                  {{ c.label }}
                </a-checkbox>
              </a-checkbox-group>
            </div>
          </div>
          <div class="role-hint">未勾选的模块：该操作员的菜单自动隐藏，后端接口也会拒绝其操作；一个都不勾则只能登录。</div>
        </a-form-item>
        <a-form-item :label="editId ? (lockedAdmin ? '新密码' : '新密码（留空则不修改）') : '初始密码'" :required="!editId || lockedAdmin">
          <a-input-password
            v-model:value="form.password"
            autocomplete="off"
            :placeholder="editId ? '至少 3 位' : '至少 3 位（可设弱密码）'"
          />
          <div class="role-hint">
            填写弱密码（≤6 位或仅含一种字符）的账号，以及操作员账号，首次登录后须先强制改密才能操作
          </div>
        </a-form-item>
      </a-form>
    </a-modal>

    <a-modal v-model:open="permOpen" title="操作员模块权限" @ok="savePerm" :confirm-loading="permSaving">
      <p v-if="permUser" style="margin-bottom: 8px">
        「{{ permUser.full_name || permUser.username }}」可访问以下模块：
      </p>
      <div class="perm-tree">
        <a-checkbox-group v-model:value="permTop" style="display: block">
          <a-checkbox
            v-for="m in TOP_OPTIONS"
            :key="m.code"
            :value="m.code"
            class="perm-leaf"
            style="display: flex; padding: 5px 0"
          >
            {{ m.label }}
          </a-checkbox>
        </a-checkbox-group>
        <div class="perm-group">
          <a-checkbox
            :checked="groupChecked(permVisit)"
            :indeterminate="groupIndeterminate(permVisit)"
            @change="(e: any) => toggleVisitGroupPerm(e.target.checked)"
          >
            {{ VISIT_GROUP.label }}（勾上 = 以下三项全开）
          </a-checkbox>
          <a-checkbox-group v-model:value="permVisit" class="perm-children">
            <a-checkbox v-for="c in VISIT_GROUP.children" :key="c.code" :value="c.code">
              {{ c.label }}
            </a-checkbox>
          </a-checkbox-group>
        </div>
      </div>
      <div class="role-hint">一个都不勾 = 该操作员登录后无可用模块；全部勾选等价于未限制。</div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { createUserApi, deleteUserApi, listUsersApi, updateUserApi } from '@/api'
import type { UserInfo } from '@/types'

const users = ref<UserInfo[]>([])
const loading = ref(false)
const search = ref('')

// admin 为系统内置账号：整行置灰锁定，不可编辑/禁用/删除
const rowClass = (record: UserInfo) => (record.username === 'admin' ? 'row-sysadmin' : '')

const columns = [
  { title: 'ID', dataIndex: 'id', key: 'id', width: 70 },
  { title: '用户名', dataIndex: 'username', key: 'username' },
  { title: '姓名', dataIndex: 'full_name', key: 'full_name' },
  { title: '邮箱', dataIndex: 'email', key: 'email' },
  { title: '角色', key: 'role', width: 200 },
  { title: '启用', key: 'active', width: 80 },
  { title: '创建时间', key: 'time', width: 170 },
  { title: '操作', key: 'action', width: 200 },
]

// 操作员可被授权的功能模块（与后端 MODULE_CODES 一致）
type ModuleOption = { code: string; label: string; children?: ModuleOption[] }
const MODULE_OPTIONS: ModuleOption[] = [
  { code: 'dashboard', label: '概览（仪表盘）' },
  { code: 'tree', label: '家谱树' },
  { code: 'lineages', label: '谱系管理' },
  { code: 'documents', label: 'AI 识别归档' },
  { code: 'tasks', label: '扫描件导入' },
  // 分享访问（父项）：勾上 = 其下三项全开；三项也可单独勾选
  {
    code: 'visit',
    label: '分享访问',
    children: [
      { code: 'visit_3d', label: '3D 谱系' },
      { code: 'visit_share', label: '谱系分享' },
      { code: 'visit_dash', label: '大屏分享' },
    ],
  },
]
const ALL_CODES = MODULE_OPTIONS.map((m) => m.code)
/** 普通模块（无子项）：与「分享访问」子项分开两个 checkbox-group 渲染 ——
 *  父项复选框没有 value，若放进同一个 group，点击时 antd 会把 undefined push 进数组，
 *  提交后后端 List[str] 校验失败（422「Input should be a valid string」）。 */
const TOP_OPTIONS = MODULE_OPTIONS.filter((m) => !m.children)
const VISIT_GROUP = MODULE_OPTIONS.find((m) => m.children) as ModuleOption
/** 分享访问的三个子项编码 */
const VISIT_SUBS = ['visit_3d', 'visit_share', 'visit_dash']
/** 库 → 界面：旧数据里的父码 visit 展开为三个子项（界面一律按子项勾选） */
const expandPerms = (list?: string[] | null): string[] => {
  const out = new Set((list || []).filter((c) => typeof c === 'string' && c))
  if (out.has('visit')) {
    out.delete('visit')
    VISIT_SUBS.forEach((c) => out.add(c))
  }
  return [...out]
}
/** 界面 → 库：三项全勾折叠为父码 visit，只勾部分则仅存勾中的子项 */
const collapsePerms = (list: string[]): string[] => {
  // 防御：剔除非字符串/空值，避免异常项导致后端 422
  const clean = [...new Set(list.filter((c) => typeof c === 'string' && c.trim()))]
  const all = VISIT_SUBS.every((c) => clean.includes(c))
  const rest = clean.filter((c) => !VISIT_SUBS.includes(c))
  return all ? [...rest, 'visit'] : [...rest, ...clean.filter((c) => VISIT_SUBS.includes(c))]
}
const groupChecked = (list: string[]) => VISIT_SUBS.every((c) => list.includes(c))
const groupIndeterminate = (list: string[]) =>
  VISIT_SUBS.some((c) => list.includes(c)) && !groupChecked(list)
const toggleGroup = (list: string[], checked: boolean) =>
  checked
    ? [...new Set([...list, ...VISIT_SUBS])]
    : list.filter((c) => !VISIT_SUBS.includes(c))
/** 两个弹窗各自的「分享访问」父项开关：只作用于分享访问子项数组 */
const toggleVisitGroup = (checked: boolean) => {
  moduleVisit.value = toggleGroup(moduleVisit.value, checked)
}
const toggleVisitGroupPerm = (checked: boolean) => {
  permVisit.value = toggleGroup(permVisit.value, checked)
}

/** 把完整模块码列表拆成「普通模块」+「分享访问子项」两段。
 *  两个 checkbox-group 必须各用独立数组：antd 的 CheckboxGroup 在勾选/取消子项时会
 *  基于本组全部勾选状态整体回写 v-model，若两组合享同一个数组，改分享访问子项会
 *  把普通模块（另一个 group）的勾选全部清空。 */
const splitTopVisit = (list: string[]) => ({
  top: list.filter((c) => !VISIT_SUBS.includes(c)),
  visit: list.filter((c) => VISIT_SUBS.includes(c)),
})
const mergeTopVisit = (top: string[], visit: string[]) => [...top, ...visit]

// 主弹窗中的模块勾选（角色为操作员时展示）
const moduleTop = ref<string[]>([])
const moduleVisit = ref<string[]>([])

// 「模块权限」快捷弹窗
const permOpen = ref(false)
const permSaving = ref(false)
const permUser = ref<UserInfo | null>(null)
const permTop = ref<string[]>([])
const permVisit = ref<string[]>([])

const modalOpen = ref(false)
const submitting = ref(false)
const editId = ref<number | null>(null)
// admin 系统内置账号打开弹窗时：仅允许修改密码
const lockedAdmin = ref(false)
const form = reactive({
  username: '',
  full_name: '',
  email: '',
  role: 'editor',
  password: '',
})

const load = async () => {
  loading.value = true
  try {
    users.value = await listUsersApi(search.value || undefined)
  } finally {
    loading.value = false
  }
}

const openCreate = () => {
  editId.value = null
  lockedAdmin.value = false
  Object.assign(form, { username: '', full_name: '', email: '', role: 'editor', password: '' })
  const { top, visit } = splitTopVisit(expandPerms(ALL_CODES)) // 新建操作员默认全模块，可按需减少
  moduleTop.value = top
  moduleVisit.value = visit
  modalOpen.value = true
}

/** 角色为操作员时展示该账号已授权的模块（null=全部） */
const moduleHint = (u: UserInfo) => {
  const perms = u.permissions
  if (!perms) return '模块权限：全部'
  const expanded = expandPerms(perms)
  const flat = [...MODULE_OPTIONS.filter((m) => !m.children).map((m) => m.code), ...VISIT_SUBS]
  if (flat.every((c) => expanded.includes(c))) return '模块权限：全部'
  const subs = MODULE_OPTIONS.flatMap((m) => m.children || [])
  const labelOf = (c: string) =>
    MODULE_OPTIONS.find((m) => m.code === c)?.label ?? subs.find((s) => s.code === c)?.label ?? c
  const sel = flat.filter((c) => expanded.includes(c)).map(labelOf)
  return `模块权限：${sel.join('、') || '无'}`
}

const openPerm = (record: UserInfo) => {
  permUser.value = record
  const { top, visit } = splitTopVisit(
    record.permissions ? expandPerms(record.permissions) : expandPerms(ALL_CODES),
  )
  permTop.value = top
  permVisit.value = visit
  permOpen.value = true
}

const savePerm = async () => {
  if (!permUser.value) return
  permSaving.value = true
  try {
    // 分享访问三项全勾时折叠为父码 visit 存库
    const perms = collapsePerms(mergeTopVisit(permTop.value, permVisit.value))
    await updateUserApi(permUser.value.id, { permissions: perms })
    Object.assign(permUser.value, { permissions: perms })
    message.success('已更新模块权限')
    permOpen.value = false
  } finally {
    permSaving.value = false
  }
}

const submit = async () => {
  if (!form.username.trim()) {
    message.warning('请输入用户名')
    return
  }
  if (!editId.value && form.password.length < 3) {
    message.warning('密码至少 3 位')
    return
  }
  if (form.role === 'editor' && moduleTop.value.length + moduleVisit.value.length === 0) {
    message.warning('操作员请至少勾选一个可访问模块，否则登录后无任何可用功能')
    return
  }
  submitting.value = true
  try {
    if (editId.value) {
      const data: any = {}
      if (lockedAdmin.value) {
        // admin 仅允许修改密码
        if (!form.password) {
          message.warning('请输入新密码')
          return
        }
        if (form.password.length < 3) {
          message.warning('密码至少 3 位')
          return
        }
        data.password = form.password
      } else {
        data.full_name = form.full_name
        data.email = form.email
        data.role = form.role
        // 模块权限仅对操作员生效；切到其它角色时清空（分享访问三项全勾折叠为 visit）
        data.permissions =
          form.role === 'editor'
            ? collapsePerms(mergeTopVisit(moduleTop.value, moduleVisit.value))
            : null
        if (form.password) data.password = form.password
      }
      await updateUserApi(editId.value, data)
      message.success('已更新')
    } else {
      await createUserApi({
        ...form,
        permissions:
          form.role === 'editor'
            ? collapsePerms(mergeTopVisit(moduleTop.value, moduleVisit.value))
            : null,
      })
      message.success('已创建')
    }
    modalOpen.value = false
    load()
  } finally {
    submitting.value = false
  }
}

const updateUser = async (record: UserInfo, data: any) => {
  await updateUserApi(record.id, data)
  Object.assign(record, data)
  message.success('已更新')
}

const openReset = (record: UserInfo) => {
  editId.value = record.id
  lockedAdmin.value = record.username === 'admin'
  Object.assign(form, {
    username: record.username,
    full_name: record.full_name,
    email: record.email,
    role: record.role,
    password: '',
  })
  const { top, visit } = splitTopVisit(
    record.role === 'editor' && record.permissions
      ? expandPerms(record.permissions)
      : expandPerms(ALL_CODES),
  )
  moduleTop.value = top
  moduleVisit.value = visit
  modalOpen.value = true
}

const remove = async (record: UserInfo) => {
  await deleteUserApi(record.id)
  message.success('已删除')
  load()
}

onMounted(load)
</script>

<style scoped>
/* admin 系统内置账号：整行置灰（行由 antd 动态渲染，需 :deep 前缀匹配） */
:deep(.row-sysadmin) {
  background: #fafafa;
}
:deep(.row-sysadmin):hover > td {
  background: #f2f2f2 !important;
}
:deep(.row-sysadmin td) {
  color: #bbb !important;
}
/* admin 行仅「修改密码」可点，链接保持正常色 */
:deep(.row-sysadmin td a) {
  color: #1677ff !important;
}
.toolbar {
  display: flex;
  justify-content: space-between;
  margin-bottom: 16px;
}
.role-hint {
  font-size: 11px;
  color: #999;
  line-height: 1.4;
  max-width: 170px;
}
/* 模块权限：分享访问为父项，其下三个子项缩进展示 */
.perm-tree .perm-leaf {
  display: flex;
  padding: 3px 0;
}
.perm-group {
  padding: 3px 0;
}
.perm-children {
  padding: 2px 0 2px 24px;
  border-left: 1px dashed #e6e6e6;
  margin-left: 8px;
}
.perm-children :deep(.ant-checkbox-wrapper) {
  display: flex;
  padding: 2px 0;
}
</style>
