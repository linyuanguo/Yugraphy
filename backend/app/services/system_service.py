"""服务器运行状态采集（概览页「服务器运行状态」卡片 / 详情抽屉的数据源）。

采集项：CPU 使用率与负载、内存、磁盘容量与 IO、网络吞吐、MinIO、PostgreSQL、
Neo4j、谱书向量索引（embedding 服务 + rag_vectors）。

设计要点：
- 主机指标直接读 /proc，不引入额外依赖。容器部署时 /proc 反映宿主内核视图：
  CPU / 内存 / 网络 / 块设备 IO 为宿主整体；磁盘容量为容器内可见的挂载点
  （含 ./data/uploads 等宿主目录绑定挂载）。
- 速率类指标（CPU%、磁盘 IO、网络）按「两次采样差值 / 时间间隔」计算：首次调用
  无历史样本时做一次 0.15s 短采样得到即时值，之后每次请求顺带刷新；前端按固定
  间隔轮询即可（后端另保留最近 60 次采样供迷你折线图使用）。
- 任一数据源异常都降级为 0/未知并记录日志，不影响卡片其它部分。
"""
import logging
import os
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

import httpx
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import driver
from app.models.orm import RagVector
from app.services import rag_service

logger = logging.getLogger("genealogy.system")

GB = float(1024 ** 3)
_HIST_LEN = 60
# 最近 N 次采样的迷你折线数据（全局共享，够画趋势即可）
_hist: Dict[str, "Deque[float]"] = {
    k: deque(maxlen=_HIST_LEN) for k in ("cpu", "mem", "io", "net")
}
_prev: Dict[str, dict] = {}  # 上一次采样值（用于算差值速率）

# /proc/mounts 中跳过这些伪文件系统（无容量意义）
_SKIP_FS = {
    "proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup", "cgroup2",
    "mqueue", "securityfs", "pstore", "autofs", "hugetlbfs", "configfs",
    "debugfs", "tracefs", "nsfs", "binfmt_misc", "fusectl", "rpc_pipefs",
    "efivarfs", "bpf", "selinuxfs", "overlayfs",
}
# /proc/diskstats 中跳过虚拟/光驱设备
_SKIP_DEV_PREFIX = ("loop", "ram", "sr", "fd", "zram", "md")
_OBJECT_CAP = 20000  # MinIO 单桶对象计数上限（防止超大桶遍历过久）


def _push(key: str, val: float) -> None:
    _hist[key].append(round(float(val), 2))


def _hist_list(key: str) -> List[float]:
    return list(_hist[key])


def _read_lines(path: str) -> List[str]:
    with open(path, encoding="utf-8") as f:
        return f.readlines()


# ============ CPU ============
def _cpu_snapshot() -> Tuple[float, float]:
    """返回 (总 jiffies, 空闲 jiffies)（含 iowait）。"""
    line = _read_lines("/proc/stat")[0]
    v = [float(x) for x in line.split()[1:]]
    v += [0.0] * (8 - len(v)) if len(v) < 8 else []
    total = sum(v[:8])
    idle = v[3] + v[4]  # idle + iowait
    return total, idle


def cpu_stats() -> dict:
    total, idle = _cpu_snapshot()
    now = time.time()
    prev = _prev.get("cpu")
    _prev["cpu"] = {"total": total, "idle": idle, "ts": now}
    if prev is None:
        # 首次调用：短采样一次得到即时 CPU 使用率
        time.sleep(0.15)
        total2, idle2 = _cpu_snapshot()
        _prev["cpu"] = {"total": total2, "idle": idle2, "ts": time.time()}
        d_total, d_idle = total2 - total, idle2 - idle
    else:
        d_total, d_idle = total - prev["total"], idle - prev["idle"]

    pct = 0.0
    if d_total > 0:
        pct = max(0.0, min(100.0, (1.0 - d_idle / d_total) * 100.0))
    _push("cpu", pct)

    load1 = load5 = load15 = 0.0
    try:
        parts = _read_lines("/proc/loadavg")[0].split()
        load1, load5, load15 = float(parts[0]), float(parts[1]), float(parts[2])
    except Exception as exc:  # noqa: BLE001
        logger.debug("读取负载失败: %s", exc)

    return {
        "percent": round(pct, 1),
        "cores": int(os.cpu_count() or 0),
        "load1": round(load1, 2),
        "load5": round(load5, 2),
        "load15": round(load15, 2),
        "history": _hist_list("cpu"),
    }


# ============ 内存 ============
def memory_stats() -> dict:
    data: Dict[str, float] = {}
    try:
        for line in _read_lines("/proc/meminfo"):
            k, _, rest = line.partition(":")
            try:
                data[k.strip()] = float(rest.split()[0]) * 1024.0  # kB -> B
            except (ValueError, IndexError):
                continue
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取内存信息失败: %s", exc)

    total = data.get("MemTotal", 0.0)
    avail = data.get("MemAvailable", data.get("MemFree", 0.0))
    used = max(0.0, total - avail)
    pct = (used / total * 100.0) if total else 0.0
    swap_total = data.get("SwapTotal", 0.0)
    swap_used = max(0.0, swap_total - data.get("SwapFree", swap_total))
    _push("mem", pct)

    return {
        "total_gb": round(total / GB, 2),
        "used_gb": round(used / GB, 2),
        "available_gb": round(avail / GB, 2),
        "percent": round(pct, 1),
        "swap_total_gb": round(swap_total / GB, 2),
        "swap_used_gb": round(swap_used / GB, 2),
        "history": _hist_list("mem"),
    }


# ============ 磁盘容量 ============
def disk_capacity() -> List[dict]:
    try:
        rows = [ln.split() for ln in _read_lines("/proc/mounts") if len(ln.split()) >= 3]
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取挂载点失败: %s", exc)
        return []

    items: List[dict] = []
    seen = set()
    for dev, mount, fstype in ((r[0], r[1], r[2]) for r in rows):
        if fstype in _SKIP_FS or not mount.startswith("/") or dev in seen:
            continue
        # 跳过 /etc/resolv.conf、/etc/hosts 之类的单文件挂载（容量等同宿主根分区，无意义）
        if not os.path.isdir(mount):
            continue
        try:
            st = os.statvfs(mount)
        except OSError:
            continue
        total = st.f_blocks * st.f_frsize
        free = st.f_bfree * st.f_frsize
        if total <= 0:
            continue
        seen.add(dev)
        used = total - free
        items.append(
            {
                "mount": mount,
                "device": dev,
                "total_gb": round(total / GB, 2),
                "used_gb": round(used / GB, 2),
                "free_gb": round(free / GB, 2),
                "percent": round(used / total * 100.0, 1),
            }
        )
    items.sort(key=lambda x: -x["total_gb"])
    return items[:6]


# ============ 磁盘 IO ============
def disk_io() -> dict:
    """块设备读写速率 / IOPS / 繁忙度（跨设备汇总，取最忙设备的繁忙度）。"""
    empty = {
        "read_kbps": 0.0,
        "write_kbps": 0.0,
        "read_iops": 0.0,
        "write_iops": 0.0,
        "busy": 0.0,
        "history": _hist_list("io"),
    }
    now = time.time()
    try:
        lines = _read_lines("/proc/diskstats")
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取磁盘 IO 失败: %s", exc)
        return empty

    cur: Dict[str, Tuple[int, int, int, int, int]] = {}
    for ln in lines:
        p = ln.split()
        if len(p) < 14:
            continue
        name = p[2]
        if name.startswith(_SKIP_DEV_PREFIX):
            continue
        try:
            cur[name] = (
                int(p[3]),   # reads completed
                int(p[5]),   # sectors read
                int(p[7]),   # writes completed
                int(p[9]),   # sectors written
                int(p[12]),  # io_ticks(ms)：设备有 IO 在飞的时间
            )
        except ValueError:
            continue

    prev = _prev.get("diskio")
    _prev["diskio"] = {"ts": now, "devs": cur}
    if not prev:
        return empty

    dt = max(0.001, now - float(prev["ts"]))
    old: Dict[str, Tuple[int, int, int, int, int]] = prev.get("devs", {})
    rkb = wkb = 0.0
    riops = wiops = 0.0
    max_busy = 0.0
    for name, (reads, rsect, writes, wsect, ticks) in cur.items():
        o = old.get(name)
        if not o:
            continue
        riops += max(0, reads - o[0])
        rkb += max(0, rsect - o[1]) * 512.0 / 1024.0
        wiops += max(0, writes - o[2])
        wkb += max(0, wsect - o[3]) * 512.0 / 1024.0
        max_busy = max(max_busy, min(100.0, max(0, ticks - o[4]) / (dt * 1000.0) * 100.0))

    _push("io", (rkb + wkb) / dt)
    return {
        "read_kbps": round(rkb / dt, 1),
        "write_kbps": round(wkb / dt, 1),
        "read_iops": round(riops / dt, 1),
        "write_iops": round(wiops / dt, 1),
        "busy": round(max_busy, 1),
        "history": _hist_list("io"),
    }


def disk_stats() -> dict:
    return {"items": disk_capacity(), **disk_io()}


# ============ 网络 ============
def network_stats() -> dict:
    empty = {"rx_kbps": 0.0, "tx_kbps": 0.0, "history": _hist_list("net")}
    now = time.time()
    rx = tx = 0.0
    try:
        for ln in _read_lines("/proc/net/dev")[2:]:
            iface, _, rest = ln.partition(":")
            if not iface.strip() or iface.strip() == "lo":
                continue
            p = rest.split()
            if len(p) < 9:
                continue
            rx += float(p[0])
            tx += float(p[8])
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取网络流量失败: %s", exc)
        return empty

    prev = _prev.get("net")
    _prev["net"] = {"ts": now, "rx": rx, "tx": tx}
    if not prev:
        return empty
    dt = max(0.001, now - float(prev["ts"]))
    rkb = max(0.0, rx - float(prev["rx"])) / 1024.0 / dt
    tkb = max(0.0, tx - float(prev["tx"])) / 1024.0 / dt
    _push("net", rkb + tkb)
    return {
        "rx_kbps": round(rkb, 1),
        "tx_kbps": round(tkb, 1),
        "history": _hist_list("net"),
    }


def host_uptime_hours() -> float:
    try:
        return round(float(_read_lines("/proc/uptime")[0].split()[0]) / 3600.0, 1)
    except Exception:  # noqa: BLE001
        return 0.0


def collect_host() -> dict:
    """主机指标（含 0.15s 级短采样，放在线程里跑避免阻塞事件循环）。"""
    return {
        "cpu": cpu_stats(),
        "memory": memory_stats(),
        "disk": disk_stats(),
        "network": network_stats(),
        "uptime_h": host_uptime_hours(),
    }


# ============ PostgreSQL ============
def postgres_stats(db: Session) -> dict:
    t0 = time.time()
    try:
        version = db.execute(text("select version()")).scalar() or ""
        size = db.execute(text("select pg_database_size(current_database())")).scalar() or 0
        conns = db.execute(text("select count(*) from pg_stat_activity")).scalar() or 0
        active = (
            db.execute(text("select count(*) from pg_stat_activity where state = 'active'")).scalar()
            or 0
        )
        idle_tx = (
            db.execute(
                text(
                    "select count(*) from pg_stat_activity "
                    "where state = 'idle in transaction'"
                )
            ).scalar()
            or 0
        )
        uptime = (
            db.execute(
                text("select extract(epoch from (now() - pg_postmaster_start_time()))")
            ).scalar()
            or 0
        )
        return {
            "ok": True,
            "version": " ".join(str(version).split()[:2]),
            "size_gb": round(float(size) / GB, 3),
            "connections": int(conns),
            "active": int(active),
            "idle_in_tx": int(idle_tx),
            "uptime_h": round(float(uptime) / 3600.0, 1),
            "latency_ms": round((time.time() - t0) * 1000, 1),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("PostgreSQL 状态采集失败: %s", exc)
        return {
            "ok": False,
            "version": "",
            "size_gb": 0.0,
            "connections": 0,
            "active": 0,
            "idle_in_tx": 0,
            "uptime_h": 0.0,
            "latency_ms": 0.0,
            "error": f"{type(exc).__name__}: {exc}",
        }


# ============ MinIO ============
_minio_cache: Dict[str, object] = {"ts": 0.0, "data": None}
# 遍历对象较慢（实测 2 万对象约 2.5~2.8s），结果缓存 10 分钟：
# 概览页 10~30s 轮询一次，缓存期间请求仅 45ms 左右，避免每两分钟拖一次全量 list_objects
_MINIO_TTL = 600.0


def minio_stats(force: bool = False) -> dict:
    """桶/对象统计（带 2 分钟缓存，避免每次刷新都全量遍历对象）。"""
    now = time.time()
    cached = _minio_cache.get("data")
    if not force and isinstance(cached, dict) and now - float(_minio_cache.get("ts") or 0) < _MINIO_TTL:
        return dict(cached)

    t0 = time.time()
    endpoint = settings.MINIO_ENDPOINT
    try:
        from minio import Minio

        client = Minio(
            endpoint,
            access_key=settings.MINIO_USER,
            secret_key=settings.MINIO_PASSWORD,
            secure=False,
        )
        buckets = list(client.list_buckets())
        items: List[dict] = []
        objects = 0
        size = 0
        capped = False  # 是否触及单桶计数上限（真实数量 ≥ 显示值）
        for b in buckets:
            n = 0
            sz = 0
            try:
                for o in client.list_objects(b.name, recursive=True):
                    n += 1
                    sz += int(o.size or 0)
                    if n >= _OBJECT_CAP:
                        capped = True
                        break
            except Exception as exc:  # noqa: BLE001
                logger.warning("MinIO 桶 %s 统计失败: %s", b.name, exc)
            objects += n
            size += sz
            items.append({"name": b.name, "objects": n, "size_bytes": sz})
        out = {
            "ok": True,
            "endpoint": endpoint,
            "latency_ms": round((time.time() - t0) * 1000, 1),
            "objects": objects,
            "objects_capped": capped,
            "size_gb": round(size / GB, 3),
            "buckets": items,
            "error": None,
        }
        _minio_cache["ts"] = time.time()
        _minio_cache["data"] = out
        return dict(out)
    except Exception as exc:  # noqa: BLE001
        logger.warning("MinIO 状态采集失败: %s", exc)
        return {
            "ok": False,
            "endpoint": endpoint,
            "latency_ms": round((time.time() - t0) * 1000, 1),
            "objects": 0,
            "objects_capped": False,
            "size_gb": 0.0,
            "buckets": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


# ============ 向量索引（embedding / rerank + rag_vectors） ============
_ping_cache: Dict[str, Tuple[bool, float, float]] = {}  # url -> (可用, 耗时ms, 采样时刻)
_PING_TTL = 60.0  # 探活结果缓存 1 分钟（概览页高频轮询不必每次都打 206）


def _http_ping(url: str, timeout: float = 3.0) -> Tuple[bool, float]:
    """探测 HTTP 服务可用性，返回 (可用, 耗时ms)。依次试 /v1/models、/health、/。"""
    base = (url or "").rstrip("/")
    if not base:
        return False, 0.0
    cached = _ping_cache.get(base)
    if cached and time.time() - cached[2] < _PING_TTL:
        return cached[0], cached[1]
    ok, ms = False, 0.0
    for path in ("/v1/models", "/health", "/"):
        try:
            r = httpx.get(base + path, timeout=timeout)
            ms = round(r.elapsed.total_seconds() * 1000, 1) if r.elapsed else 0.0
            ok = r.status_code < 500
            break
        except Exception:  # noqa: BLE001
            continue
    _ping_cache[base] = (ok, ms, time.time())
    return ok, ms


def vector_stats(db: Session) -> dict:
    try:
        cfg = rag_service.load_rag_runtime(db) or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("装载 RAG 运行配置失败: %s", exc)
        cfg = {}
    emb_url = str(cfg.get("embedding_url") or settings.EMBEDDING_URL)
    rr_url = str(cfg.get("rerank_url") or settings.RERANK_URL)
    emb_ok, emb_ms = _http_ping(emb_url)
    rr_ok, rr_ms = _http_ping(rr_url, timeout=2.0)

    try:
        st = rag_service.rag_status(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("向量索引状态采集失败: %s", exc)
        st = {"ready": False, "building": False, "indexed": 0, "total": 0}

    last_updated: Optional[str] = None
    try:
        last = db.query(func.max(RagVector.updated_at)).scalar()
        last_updated = last.isoformat() if last else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("查询最近向量更新时间失败: %s", exc)

    return {
        "embedding_ok": bool(emb_ok),
        "embedding_url": emb_url,
        "embedding_latency_ms": emb_ms,
        "rerank_ok": bool(rr_ok),
        "rerank_url": rr_url,
        "rerank_latency_ms": rr_ms,
        "indexed": int(st.get("indexed", 0)),
        "total": int(st.get("total", 0)),
        "ready": bool(st.get("ready", False)),
        "building": bool(st.get("building", False)),
        "last_updated": last_updated,
        "error": None if emb_ok else "embedding 服务不可达（检索将降级为关键词召回）",
    }


# ============ Neo4j ============
async def neo4j_stats() -> dict:
    t0 = time.time()
    try:
        async with driver.session() as session:
            res = await session.run("RETURN 1 AS ok")
            await res.single()
        return {
            "ok": True,
            "latency_ms": round((time.time() - t0) * 1000, 1),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Neo4j 状态采集失败: %s", exc)
        return {
            "ok": False,
            "latency_ms": round((time.time() - t0) * 1000, 1),
            "error": f"{type(exc).__name__}: {exc}",
        }
