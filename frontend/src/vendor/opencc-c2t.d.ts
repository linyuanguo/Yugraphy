/**
 * opencc-js 简→繁转换模块（本地 vendor，来自 npm opencc-js@1.0.5 dist/esm/cn2t.js）。
 * 该裁剪版仅内置 cn→t（简体→通用繁体）词典，用于审核时把用户以简体输入的手工内容
 * 自动转回繁体后写回（保证库内/图谱原文始终为繁体）。
 * 注意：此 bundle 只含简→繁方向，不含繁→简（繁→简请用同目录 opencc-t2cn.js）。
 */
export interface ConverterOptions {
  from?: string
  to?: string
  custom?: any
}

export declare function Converter(options: ConverterOptions): (text: string) => string
export declare const ConverterFactory: (from: any, to: any) => (text: string) => string
export declare const CustomConverter: (dicts: any[]) => (text: string) => string
export declare const HTMLConverter: (options: ConverterOptions) => (html: string) => string
export declare const Locale: Record<string, any>
export declare const Trie: any
export default Record<string, any>
