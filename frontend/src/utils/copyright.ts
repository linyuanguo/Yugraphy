// 页面版权信息(取自 frontend/.env 的 VITE_COPYRIGHT_TEXT,构建时注入;留空则不显示)
export const COPYRIGHT_TEXT = String((import.meta as any).env?.VITE_COPYRIGHT_TEXT ?? '').trim()
