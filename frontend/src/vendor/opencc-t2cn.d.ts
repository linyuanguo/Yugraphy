/**
 * opencc-js 繁→简转换模块（本地 vendor，来自 npm opencc-js@1.0.5 dist/esm/t2cn.js）。
 * 仅用到 Converter（Locale 预设繁→简）。
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
