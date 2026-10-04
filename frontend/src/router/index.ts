import { message } from 'ant-design-vue'
import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/pages/Login.vue'),
    },
    {
      path: '/license',
      name: 'license',
      component: () => import('@/pages/License.vue'),
      meta: { public: true, title: '开源协议' },
    },
    {
      path: '/',
      component: () => import('@/layouts/MainLayout.vue'),
      redirect: '/dashboard',
      children: [
        {
          path: 'dashboard',
          name: 'dashboard',
          component: () => import('@/pages/Dashboard.vue'),
          meta: { title: '仪表盘/大屏', module: 'dashboard' },
        },
        {
          path: 'tree',
          name: 'tree',
          component: () => import('@/pages/TreeView.vue'),
          meta: { title: '家谱树', module: ['tree', 'lineages'] },
        },
        {
          // 任务2：两级 3D 画布（一级=谱系卷总览，二级=卷内人物 3D，大卷自动降房支聚合）
          // 内部浏览入口在「分享访问 · 3D 谱系」；同一页面同时承担谱系分享短链的访客 3D 渲染
          path: 'tree3d',
          name: 'tree3d',
          component: () => import('@/pages/Tree3D.vue'),
          // 分享访问子菜单：3D 谱系接受「分享访问(visit)」或其子项 visit_3d 授权
          meta: { title: '3D 谱系', module: ['tree', 'lineages', 'visit', 'visit_3d'] },
        },
        {
          path: 'lineages',
          name: 'lineages',
          component: () => import('@/pages/Lineages.vue'),
          meta: { title: '谱系管理', module: 'lineages' },
        },
        {
          // 谱系详情子页面：房支管理 / 人物管理 / 谱书内容（人物内嵌可编辑，不跳 /persons）
          path: 'lineages/:lineageId',
          name: 'lineage-detail',
          component: () => import('@/pages/LineageDetail.vue'),
          meta: { title: '谱系详情', module: 'lineages' },
        },
        {
          path: 'persons',
          name: 'persons',
          component: () => import('@/pages/PersonList.vue'),
          meta: { title: '人物管理', module: ['lineages', 'tree'] },
        },
        {
          path: 'persons/new',
          name: 'person-new',
          component: () => import('@/pages/PersonEdit.vue'),
          meta: { title: '新增人物', module: ['lineages', 'tree'] },
        },
        {
          path: 'persons/:id',
          name: 'person-edit',
          component: () => import('@/pages/PersonEdit.vue'),
          meta: { title: '人物详情', module: ['lineages', 'tree'] },
        },
        {
          path: 'documents',
          name: 'documents',
          component: () => import('@/pages/Documents.vue'),
          meta: { title: 'AI 识别归档', module: ['documents', 'tasks'] },
        },
        {
          path: 'tasks',
          name: 'tasks',
          component: () => import('@/pages/TaskList.vue'),
          meta: { title: '扫描件导入', module: 'tasks' },
        },
        {
          path: 'tasks/:taskId/review',
          name: 'scan-review',
          component: () => import('@/pages/ScanReview.vue'),
          meta: { title: '扫描件审核', module: ['tasks', 'documents'] },
        },
        {
          // 用户管理已并入「系统设置」页，旧地址兼容跳转
          path: 'users',
          redirect: { path: '/settings', query: { tab: 'users' } },
        },
        {
          path: 'settings',
          name: 'settings',
          component: () => import('@/pages/Settings.vue'),
          meta: { title: '系统设置', adminOnly: true },
        },
        {
          path: 'visits',
          name: 'visits',
          component: () => import('@/pages/VisitManage.vue'),
          meta: { title: '谱系分享', editOnly: true, module: ['visit', 'visit_share'] },
        },
        {
          // 分享访问 · 大屏分享（管理端）；模块权限走 dashboard
          path: 'dash-shares',
          name: 'dash-shares',
          component: () => import('@/pages/DashboardShareManage.vue'),
          // 分享访问子菜单：大屏分享接受「概览(dashboard)」「分享访问(visit)」或子项 visit_dash 授权
          meta: { title: '大屏分享', editOnly: true, module: ['dashboard', 'visit', 'visit_dash'] },
        },
      ],
    },
    {
      // 分享访问短链统一入口：图谱树 <根>/<code>；仪表盘 <根>/d<code>（按 code 形态分发）
      path: '/:code',
      name: 'share',
      component: () => import('@/pages/ShareEntry.vue'),
      meta: { public: true, title: '族谱浏览' },
    },
    {
      // 旧 /s/、/visit?token= 及未知路径 → 统一「链接无效」页
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('@/pages/ShareInvalid.vue'),
      meta: { public: true, title: '链接无效' },
    },
  ],
})

/**
 * 拒绝进入：站内跳转（点菜单）时留在原页并明确提示——此前只是静默跳回落地页，
 * 若落地页就是当前页则毫无变化，表现为「点了没反应」；
 * 仅在直接输地址/首次进入（from 无路由名）时才落到有权访问的首个模块页。
 */
function deny(from: { name?: unknown }, title: string, reason: string) {
  if (from.name) {
    message.warning(`${reason}「${title}」，请联系管理员开通相应权限`)
    return false
  }
  const auth = useAuthStore()
  return { name: auth.firstAllowedName }
}

router.beforeEach((to, from) => {
  const auth = useAuthStore()
  if (!to.meta.public && to.name !== 'login' && !auth.isLoggedIn) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.meta.adminOnly && !auth.isAdmin) {
    return deny(from, (to.meta.title as string) || '系统设置', '当前账号无管理员权限，无法访问')
  }
  if (to.meta.editOnly && !auth.canEdit) {
    return deny(from, (to.meta.title as string) || '该功能', '只读账号无法访问')
  }
  // 操作员模块权限：未授予该模块时的处理见 deny()
  if (to.meta.module && !auth.canAccessModule(to.meta.module as string | string[])) {
    return deny(from, (to.meta.title as string) || '该功能', '当前账号未开通该模块的访问权限')
  }
  if (to.name === 'login' && auth.isLoggedIn) {
    return { name: auth.firstAllowedName }
  }
  return true
})

export default router
