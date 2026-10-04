-- 用户表
CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    username    VARCHAR(50) UNIQUE NOT NULL,
    password    VARCHAR(255) NOT NULL,
    full_name   VARCHAR(100),
    email       VARCHAR(100),
    role        VARCHAR(20) DEFAULT 'viewer',  -- admin / editor / viewer
    is_active   BOOLEAN DEFAULT TRUE,
    must_change_password BOOLEAN DEFAULT FALSE,  -- 操作员新建/重置密码后为 TRUE：登录后须先改密
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);

-- 文件元数据
CREATE TABLE IF NOT EXISTS file_metadata (
    id          SERIAL PRIMARY KEY,
    doc_id      VARCHAR(50) UNIQUE NOT NULL,
    person_id   VARCHAR(50),
    title       VARCHAR(200),
    type        VARCHAR(50),
    file_path   VARCHAR(500) NOT NULL,
    file_size   BIGINT,
    mime_type   VARCHAR(100),
    uploaded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMP DEFAULT NOW()
);

-- 扫描件导入任务表（异步进度追踪）
CREATE TABLE IF NOT EXISTS import_tasks (
    id          SERIAL PRIMARY KEY,
    task_id     VARCHAR(50) UNIQUE NOT NULL,
    file_path   VARCHAR(500) NOT NULL,
    file_type   VARCHAR(20),
    total_pages INTEGER DEFAULT 0,
    done_pages  INTEGER DEFAULT 0,
    status      VARCHAR(20) DEFAULT 'pending',  -- pending/running/done/failed
    -- 暂停原因（status='paused' 时有效）：manual=人工/运维暂停；
    -- priority_ai=整卷整理为给 AI 识别让位而自动暂停（识别优先，识别完自动续跑）。
    -- 二者必须区分：人工暂停的整理任务不允许被「让位自动续跑」逻辑恢复。
    pause_reason VARCHAR(20),
    result      JSONB,
    error_msg   TEXT,
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);

-- 操作日志
CREATE TABLE IF NOT EXISTS audit_logs (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action      VARCHAR(50) NOT NULL,
    target_type VARCHAR(50),
    target_id   VARCHAR(50),
    detail      TEXT,
    ip_address  VARCHAR(50),
    created_at  TIMESTAMP DEFAULT NOW()
);

-- 访客分享（管理后台生成的带 token 入口）
CREATE TABLE IF NOT EXISTS visit_shares (
    id           SERIAL PRIMARY KEY,
    token        VARCHAR(64) UNIQUE NOT NULL,
    name         VARCHAR(100) NOT NULL,
    note         TEXT,
    allow_search BOOLEAN DEFAULT TRUE,
    allow_chat   BOOLEAN DEFAULT FALSE,
    expires_at   TIMESTAMP,               -- NULL = 永不过期
    revoked      BOOLEAN DEFAULT FALSE,
    created_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at   TIMESTAMP DEFAULT NOW()
);

-- 系统级 key-value 设置（访客问答 prompt 等）
CREATE TABLE IF NOT EXISTS app_settings (
    key        VARCHAR(100) PRIMARY KEY,
    value      TEXT,
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 初始管理员（密码 admin123，部署后请立即修改）
-- bcrypt hash of "admin123"
INSERT INTO users (username, password, full_name, role)
VALUES ('admin', '$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW', '系统管理员', 'admin')
ON CONFLICT (username) DO NOTHING;
