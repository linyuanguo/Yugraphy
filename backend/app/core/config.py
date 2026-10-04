from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    APP_NAME: str = "家谱管理系统"
    DEBUG: bool = True

    # ===== AI 服务（跨机器，172.16.199.206 上的 SGLang）=====
    SGLANG_URL: str = "http://172.16.199.206:30000"
    QWEN_MODEL_NAME: str = "qwen3-vl-30b-fp8"
    SGLANG_API_KEY: str = "EMPTY"

    # ===== RAG（谱书内容语义检索）=====
    # embedding 服务（OpenAI 兼容 /v1/embeddings）：谱书文本向量化
    EMBEDDING_URL: str = "http://172.16.199.206:30010"
    EMBEDDING_MODEL: str = "Qwen3-Embedding-0.6B-Q8_0.gguf"
    # rerank 精排服务（/v1/rerank，jina 风格响应）
    RERANK_URL: str = "http://172.16.199.206:30011"
    RERANK_MODEL: str = "qwen3-reranker-0.6b-q8_0.gguf"
    # embedding/rerank 服务密钥：留空先试匿名，401 再带示例密钥重试
    EMBEDDING_API_KEY: str = ""
    # RAG 检索默认返回条数与单条原文截断长度
    RAG_TOP_K: int = 12
    RAG_HIT_TEXT_CHARS: int = 240
    # 单次 LLM 请求超时上限（秒）：非流式请求下 read 超时 = 生成完整个 JSON 的耗时。
    # 逐页正常 3~10s、慢页 30~90s；旧值 60s 会把「只是慢」的长名单/功德碑页判死
    # （重试同样超时；httpx 超时异常无消息 → 失败页 error 只剩空串，无法定位）。
    # 取 180s：足够慢页出结果；透印死循环页（实测要 338s 才被 max_tokens 截断）
    # 也能早点断掉转分块兜底，不白占 206 槽位。
    VLM_TIMEOUT: int = 180
    # 单页识别「总」超时（秒）：用 asyncio.wait_for 硬限一页从开始到结束的总时长
    # （含 retries=1 的 2 次尝试 + 重试间隔）。必须 > 2 × VLM_TIMEOUT（2×180+6=366），
    # 否则第二次尝试还没跑完就被硬超时砍掉、白等一场。
    # 仅作最后保险：顽固页最多卡 N 秒即抛错，上层记失败页/转分块兜底，其余页继续。
    VLM_PAGE_TIMEOUT: int = 420
    # ===== 审核端重识别（「重新识别本页」/「批量重识别失效页」/整卷重扫）=====
    # 不走导入流水线的 VLM_PAGE_TIMEOUT=180：主动重试时页面图已按 1600 长边+去噪
    # 预处理，正常页更快；顽固透印/噪声页则放宽单页硬超时，并允许整页失败后自动
    # 「横向切块」兜底（每块视觉 token 少、上下文小，不易整页死循环）。
    # 此值须小于前端 axios 对本接口的超时（reExtractPageApi 已单独放宽到 900s）。
    REDO_PAGE_TIMEOUT: int = 300
    # 整页超时/熔断失败后是否自动切块分块兜底识别（兜底结果页 note 会注明）
    REDO_TILED: bool = True
    # 兜底横向切块数：每块约整页高度 1/N（含重叠），块数越多单块越小、越稳，
    # 但跨块读序割裂越明显；3 为「成功率与可读性」折中。
    REDO_TILED_STRIPS: int = 3
    # 常规分块（N 带）全部失败时的「加密分块」二次兜底带数（0 = 不重试）。
    # 块更小 → 单块视觉 token 与待解对象更少，更不容易整页式死循环；
    # 代价是竖排长段落被切得更碎（读序更乱），故仅在常规分块全败时才使用。
    REDO_TILED_RETRY_STRIPS: int = 6
    # 相邻块垂直重叠比例（0~0.3）：防止文字行正好被切在边界导致整行漏读
    REDO_TILED_OVERLAP: float = 0.08
    # 分块兜底合并后最多保留的 content 段落数（每块先按单页上限 100 归一，再合并截断）
    REDO_TILED_MAX_ENTRIES: int = 100
    # 导入流水线：逐页识别失败时，是否自动再走一次「审核端重识别」兜底
    # （REDO_PAGE_TIMEOUT 长超时 → 仍失败则横向分块）。
    # 默认开：顽固页经分块后大多能出内容，代价仅是个别页多等几分钟，
    # 换来「新导入任务几乎零失效页」，避免事后逐页手工补扫。
    IMPORT_REDO_FALLBACK: bool = True
    # 单次 LLM 识别/整理请求的输出 token 上限。206 上下文为 256K；
    # 扫描页（尤其长名单/功德碑/捐资页）输出 JSON 易超旧值 8192 被截断成坏 JSON
    # （表现为连续「JSON 解析失败」重试 + 失败页），提到 16384 后单页基本不再截断。
    VISION_MAX_TOKENS: int = 16384
    # 逐页识别的重复抑制（SGLang repetition_penalty）：透印/噪声严重的页会让模型陷入
    # 「同一姓名无限重复」死循环，直到 max_tokens 截断（实测 003-002 第241页输出
    # 40605 字符、耗时 338s），既识别失败又白占 206 槽位。
    # 1.05 只对高度重复的 token 施加惩罚，但会误伤正常的登记/名册页（如"宗谱分存记"
    # 每行都是"地名 + 一部/一本"），导致模型写到相似行就写不动、只出前几条。
    # 改为 1.0 关闭 token 级重复惩罚，死循环兜底改由 VISION_REPEAT_BLOCK 结构熔断、
    # 25000 字符总长熔断、重试 + 横向分块兜底共同承担；正常重复版式不再被压制。
    VISION_REPETITION_PENALTY: float = 1.0
    # ===== 暗字/磨损字增强（路径1：Qwen3-VL 单模型）=====
    # 结构级重复熔断：输出中同一「人物块指纹」相邻连续重复 ≥ 该值 或 累计出现 ≥ 2 倍该值
    # 时，判定死循环（如旧 p241「董翁」JSON 无限复读）。优先于总长度熔断
    # （25000 字符）拦截——复读页可能不到总长阈值就先重复成灾。
    VISION_REPEAT_BLOCK: int = 3
    # ===== 整页识别后的自动复查（页面类型门控）=====
    # 世系页「结果存疑才补跑」：凡首轮结果被判定为世系页（entries 含 type=世系）且
    # 存疑（page_notes 声明疑似/模糊/不清等，或 persons 存在 <0.6 低置信人名，或正文
    # 「□」占位≥2）→ 才做一轮横向分块放大复查（VISION_VERIFY_PROMPT 专抓漏人），
    # 把首轮漏掉的人名补入 persons 并强制标低置信 ≤0.5（审核界面「忽略低置信 <0.6」
    # 可先筛除，人工复核后确认）。无疑点的世系页直接返回不复查——后面有人工审核把关。
    # 成本：每个「疑点」世系页约额外 3 块 VLM 调用（量大可在 .env 关掉）。
    # 功德名单页与其它正文页一律不复查（直接返回）。
    VISION_AUTO_VERIFY_SHI: bool = True
    # 空结果自动复查：persons/entries 全空、页图却非空白 → 横向分块复查一次
    # （沿用整页同款提示词尽量补回全文；成本：每空结果页额外 3 块 VLM）。
    VISION_AUTO_VERIFY_EMPTY: bool = True
    # 历史开关（已不再参与判定）：原「persons 低置信占比高 → 复查」已被 VISION_AUTO_VERIFY_SHI
    # 的世系页双跑取代；保留字段仅为兼容旧 .env 引用，不再读用。
    VISION_AUTO_VERIFY_LOWCONF: bool = False
    # ===== 格线页「切格识别」页型分流（2026-09-10 新增，两轮 6 页实测后落地）=====
    # 背景：世系格子页整页喂 VLM 会跨格横拼/跳行/丢字段（p235 整页 90 字 vs 切格 196 字）；
    # 但正文页（序跋/家规等无格线页）切格有害（p138 崩到 18 字）→ 必须按页型分流。
    # 命中格线页才走 grid_extract.extract_page_grid_norm（切格逐格 OCR + 文本结构化抽取），
    # 未命中一律走原整页链路；切格链路任何异常也会回退整页，不改变原有成功率。
    OCR_GRID_ENABLED: bool = True
    # 页型判据：横线带数 ≥ 该值 且 单元数在 [MIN_UNITS, MAX_UNITS] 区间才算格线页。
    # 实测：世系格页 5~8 带 / 11~13 单元 → 命中；正文页 p138 为 3 带/4 单元 → 不命中；
    # p67 为 3 带/3 单元 → 不命中（切了也没收益：164→169）。
    OCR_GRID_MIN_BANDS: int = 3
    OCR_GRID_MIN_UNITS: int = 4
    # 内部横线数下限（最关键的一条）：正文页往往只有版心上下两条框线（p138 仅 2 条），
    # 真世系格页实测 6~8 条。仅靠带数/单元数会把正文页误判成格线页 → 必须加此门槛。
    OCR_GRID_MIN_H_LINES: int = 4
    # 单元数上限（防误判）：切得过碎说明格线检测把正文切烂了，宁可回退整页。
    OCR_GRID_MAX_UNITS: int = 16
    # 单元墨占比下限：低于此值视为页边留白/空单元，丢弃不送 VLM。
    # 0.004 太低（几乎空白的格也会被送进去，实测吐一整格「□」还白占 206 槽位）→ 提到 0.01。
    OCR_GRID_MIN_INK: float = 0.01
    # 切格链路单页总超时（秒）：N 格 OCR + 1 次结构化抽取，需比整页更宽松。
    OCR_GRID_TIMEOUT: int = 600
    # 单格 OCR 的输出 token 上限：一格最多几百字，封 1024 即可。
    # 不封（沿用 16384）时模糊/空白格会让模型一路生成到上限，实测单格 180s 超时、
    # 整页被拖到 410s（整页链路只要 26s）→ 封顶后既提速又防复读。
    OCR_GRID_UNIT_MAX_TOKENS: int = 1024
    # 单格 OCR 硬超时（秒）：超时就丢该格（不阻断其余格），避免一页被一格拖死。
    OCR_GRID_UNIT_TIMEOUT: int = 90
    # 结构化抽取（纯文本，无图）的输出 token 上限
    OCR_GRID_STRUCT_MAX_TOKENS: int = 8192
    # 单页内「逐格 OCR」的并发数：206 的 max_running_requests=4，串行跑 11 格要 40~70s，
    # 4 路并发可把切格单页压到约 1/3。不要超过 206 槽位（4），否则排队反而更慢。
    OCR_GRID_UNIT_CONCURRENCY: int = 4
    # 复查分块 crop 的放大倍数（>1 时逐带等比放大后再送 VLM；2x 对暗字/磨损小字最直接，
    # 1 = 不放大）。仅作用于「世系补漏/空结果复查」，常规失败兜底分块不放大（保持原状）。
    VISION_VERIFY_UPSCALE: float = 2.0

    # ===== 扫描件处理参数 =====
    # 扫描件页面图最长边（px）。2400 大图会让 VLM 视觉 token 暴涨（透印噪声页
    # 实测 334s，远超 VLM_PAGE_TIMEOUT=180 被误判失败页）；1600 后单页 token
    # 约减半、透印页实测压回正常区间。若发现识别漏字可谨慎上调。
    MAX_IMAGE_LONG_EDGE: int = 1600
    PREPROCESS_DESKEW: bool = True
    # 古籍透印/噪声页建议开：抑制背景噪声，减小处理图体积，避免 VLM 把噪声
    # 当字陷入「同一对象无限重复」死循环（实测该场景输出 40605 字符）
    PREPROCESS_DENOISE: bool = True
    PREPROCESS_ENHANCE: bool = True
    PREPROCESS_BINARIZE: bool = False

    # 同时处理的导入任务数（其余排队，避免大批量扫描件同时转图打满 CPU/内存）
    MAX_CONCURRENT_IMPORT_TASKS: int = 2
    # 上传扫描件时是否按文件名档案编号自动归属谱系（如 J148-001-001-001.pdf → J148-001-001）
    AUTO_ATTACH_LINEAGE_BY_FILENAME: bool = True
    # 转图完成后是否保留原始扫描件（默认删除：后续流程只依赖 MinIO 页面图）
    KEEP_ORIGINAL_SCAN: bool = False
    # 任务结束后是否保留本地中间图（raw/work，MinIO 已有副本）
    KEEP_LOCAL_PAGE_IMAGES: bool = False
    # 单页 AI 调用的并发数。默认 1 = 串行（服务器并发槽通常为 1，排队无益）。
    # 若 206 的 SGLang 已放开 max_running_requests>1，可在 .env 调到 2~4 获得成倍提速。
    MAX_CONCURRENT_VISION_CALLS: int = 1
    # 卷级整理（第二段）的块级并发：块循环以「滑动窗口 + 全局信号量」运行，与逐页识别
    # 共享 206；建议 ≤ 206 的 max_running_requests（当前 4），默认 4。
    CONSOLIDATE_MAX_CONCURRENCY: int = 4
    # 预处理（纠偏/增强）并发线程数：预处理与后续 VLM 分离后并行，避免逐页串行拖慢
    PREPROCESS_CONCURRENCY: int = 4
    # PDF/TIF 转图（converting）并行 worker 数：转图是 CPU 密集（渲染/PNG 编码），
    # 原单线程整本串行是「转图慢」的主因；按机器核数并行，默认 4。12 核机器建议在 .env
    # 设 CONVERT_CONCURRENCY=8（接近打满，留 4 核给预处理/整理/系统）；核少机器无需配置。
    CONVERT_CONCURRENCY: int = 4
    # 同时进行「转图 + 页面预处理」的任务数。转图/预处理是本机 CPU/IO，与 AI 识别
    # （走 206）解耦：不再受 MAX_CONCURRENT_IMPORT_TASKS 排队，后上传的书可立即先转图，
    # 页面就绪后停在 stage='ready' 等待识别。注意每本会各自开 CONVERT_CONCURRENCY 个
    # 线程，同时转多本 CPU 占用叠加：16 核建议 2~3，核少保持 1。
    MAX_CONCURRENT_CONVERT: int = 2
    # 提取结果落库检查点（每 N 页写一次完整 result；避免每页全量序列化大 JSON）
    RESULT_CHECKPOINT_PAGES: int = 10
    # 卷级整理（第二段）：识别全部页后自动执行，对整卷文本做人物归并与关系推断。
    # 关思考调用（实测 qwen3-vl-30b 开思考时 reasoning 占满 max_tokens 把 content 挤空，
    # 返回空串致解析失败，且慢 5 倍；关思考 2000 字块约 1 分钟稳定产出 JSON）
    CONSOLIDATE_CHUNK_CHARS: int = 2000  # 每块目标字符数（越小越快；5000 字实测 300s+）
    CONSOLIDATE_OVERLAP_PAGES: int = 2  # 相邻块重叠页数，防止父子恰好被分块切开
    # 分块兜底方向：vertical=按列切竖条（竖排古籍推荐：每块只含几列，读序更准）；
    # horizontal=横向切带（历史默认，适合横排版式）。纵向切条若整页无产出会自动回退横向。
    REDO_TILED_DIRECTION: str = "vertical"
    # 纵向切条的条数（每列约 60~120px，故条数要明显多于横向切带）
    # 09-16 实测（7 页真 GT）：2 条 78.5% / 4 条 92.6% / 6 条更慢且更易超时 → 定 4。
    REDO_TILED_VERTICAL_STRIPS: int = 4
    # 切块「单块」超时上限（秒），0 = 沿用 REDO_PAGE_TIMEOUT。
    # 单块只是整页的一小部分，无需整页那么长时间。
    REDO_TILED_BLOCK_TIMEOUT: int = 180
    # ---- 版式自适应分流（09-17，见 vision_service.classify_layout_sync）----
    # auto（默认）= 整页识别后按 notes 类别判型：规则版式（刻本正文/多栏/表格）改用
    #   「列边界对齐竖切」重跑，不规则版式（世系表）保持整页；
    # whole = 关闭分流，只用整页精读（**一键回退旧行为**，改后 `docker compose up -d backend`）；
    # col_grid = 强制列切（调试用，风险自担）。
    OCR_LAYOUT_MODE: str = "auto"
    # 列切分组组数、单组超时、整页总超时、切图长边
    OCR_COL_TILE_GROUPS: int = 3
    OCR_COL_TILE_TIMEOUT: int = 90
    OCR_COL_TILE_LONG: int = 1600
    # 切块「整页」总预算（秒），0 = per_block × min(条数,4)。
    # 必须小于前端对重识别接口的超时（900s），否则页面先报错、后端还在跑。
    REDO_TILED_TOTAL_TIMEOUT: int = 600
    CONSOLIDATE_MAX_RETRIES: int = 2  # 单块整理失败重试次数
    # 卷级整理「半成品落盘」节流：每完成一块就把整卷 result（含全部页正文）整体
    # 序列化写库 + 重建页镜像，块数多时（500+ 页大卷）会反复造上百 MB 临时对象、
    # glibc 不归还 → 实测吃满 32G 宿主内存被 OOM 杀掉（625 页卷 id=93、523 页卷）。
    # 改为「每 N 块 / 每 X 秒」落一次，进度仍每块轻量更新（只写 done_pages）。
    CONSOLIDATE_SAVE_EVERY_BLOCKS: int = 5  # 每完成 N 块落一次完整 result
    CONSOLIDATE_SAVE_EVERY_SECONDS: int = 180  # 距上次落盘超过 X 秒也落一次
    # 卷级整理单块 LLM 调用超时（秒）：关思考 2000 字块约 1 分钟，600s 为宽裕保险
    CONSOLIDATE_TIMEOUT: int = 600
    # 卷内 AI 全局消歧 pass（块间归并收口）：整卷人物 ≥ 该值才跑（小卷块间重复少，跳过省时）
    CONSOLIDATE_DISAMBIG_MIN_PERSONS: int = 200
    # 卷内消歧每批人物清单的目标字符上限（超长分批送 LLM）
    CONSOLIDATE_DISAMBIG_BATCH_CHARS: int = 6000
    # 卷内消歧自动合并的置信下限：低于该值的合并建议不自动执行（留给人工审核把关）
    CONSOLIDATE_DISAMBIG_MIN_CONF: float = 0.9

    # ===== 数据库 =====
    NEO4J_URI: str = "bolt://neo4j:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "ChangeMe_Neo4j_2024"

    PG_HOST: str = "postgres"
    PG_PORT: int = 5432
    PG_DB: str = "genealogy"
    PG_USER: str = "genealogy"
    PG_PASSWORD: str = "ChangeMe_PG_2024"

    # ===== MinIO =====
    MINIO_ENDPOINT: str = "minio:9000"
    MINIO_USER: str = "minioadmin"
    MINIO_PASSWORD: str = "ChangeMe_MinIO_2024"
    MINIO_BUCKET: str = "genealogy"

    # ===== 安全 =====
    SECRET_KEY: str = "ChangeMe_JWT_Secret_Random_String"
    ALGORITHM: str = "HS256"
    # 常规登录有效期（关闭浏览器即失效，前端只存 sessionStorage）
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 120
    # 勾选「记住我」时的有效期
    REMEMBER_TOKEN_EXPIRE_DAYS: int = 1

    # 上传文件本地目录（容器内）
    UPLOAD_DIR: str = "/app/uploads"

    @property
    def sqlalchemy_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.PG_USER}:{self.PG_PASSWORD}"
            f"@{self.PG_HOST}:{self.PG_PORT}/{self.PG_DB}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
