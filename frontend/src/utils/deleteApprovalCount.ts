import { reactive } from 'vue'
import { deleteRequestsPendingCountApi } from '@/api'

/**
 * 删除审批待审数（管理员顶栏铃铛 / 「系统设置」菜单徽标共用，全局单例）。
 * - MainLayout 挂载后立即拉取并定时轮询（管理员）；
 * - 删除审批台操作后经 notifyDeleteApprovalChanged() 广播，触发立即刷新。
 */
export const deleteApprovalCount = reactive({ pending: 0 })

export async function refreshDeleteApprovalCount(role?: string | null) {
  if (!role || role !== 'admin') {
    deleteApprovalCount.pending = 0
    return
  }
  try {
    const r = await deleteRequestsPendingCountApi()
    deleteApprovalCount.pending = r.pending ?? 0
  } catch {
    /* 网络异常保持原值，下一轮轮询再试 */
  }
}

/** 审批台完成通过/驳回后广播，触发布局徽标即时刷新 */
export function notifyDeleteApprovalChanged() {
  window.dispatchEvent(new Event('delete-approval-changed'))
}
