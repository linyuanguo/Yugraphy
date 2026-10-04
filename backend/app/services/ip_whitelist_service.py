"""HTTPS IP 白名单（管理员在「系统设置」配置）。

规则（与需求一致）：
- 白名单为空 = 未启用，不限制任何 IP（默认行为）。
- 白名单非空 = 启用，仅命中的 IP（CIDR/网段或单 IP）可访问。

热加载：
- 保存时由 settings 接口调用 ``apply`` 立即更新进程内缓存，无需重启后端。
- 应用启动时由 main.py 从 DB 载入一次。
"""
import ipaddress
import json
import logging
import threading

from sqlalchemy.orm import Session

from app.models.orm import AppSetting

logger = logging.getLogger("genealogy.ip_whitelist")

KEY = "https_ip_whitelist"

_lock = threading.Lock()
# 已规整的网络对象列表；空列表 = 未启用（不限制）
_networks: list = []


def _to_network(raw):
    """把 IP / 网段字符串规整为 network；空串返回 None，非法抛 ValueError。"""
    if not isinstance(raw, str):
        raise ValueError(f"IP/网段必须是字符串: {raw!r}")
    s = raw.strip()
    if not s:
        return None
    if "/" not in s:  # 单 IP → /32 或 /128
        ip = ipaddress.ip_address(s)  # 非法会抛 ValueError
        s = f"{ip}/{128 if ip.version == 6 else 32}"
    return ipaddress.ip_network(s, strict=False)


def validate(cidrs):
    """校验并去重（保序）。非法 IP/网段直接抛 ValueError 供接口返回 400。"""
    if not isinstance(cidrs, list):
        raise ValueError("白名单必须是 IP/网段列表")
    out = []
    seen = set()
    for raw in cidrs:
        try:
            net = _to_network(raw)
        except ValueError as e:
            raise ValueError(f"非法 IP/网段：{raw!r}（{e}）")
        if net is None:
            continue
        key = str(net)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def apply(cidrs) -> list:
    """校验并热更新白名单缓存，返回规整后的 IP/网段列表。"""
    normalized = validate(cidrs)
    with _lock:
        _networks[:] = [ipaddress.ip_network(n) for n in normalized]
    if normalized:
        logger.info("HTTPS IP 白名单已启用（%d 段）: %s", len(normalized), normalized)
    else:
        logger.info("HTTPS IP 白名单已禁用（不限制 IP）")
    return normalized


def is_enabled() -> bool:
    with _lock:
        return len(_networks) > 0


def is_allowed(client_ip: str) -> bool:
    """白名单未启用 → 一律放行；启用后取不到 IP 或不在名单内 → 拒绝。"""
    with _lock:
        nets = list(_networks)
    if not nets:
        return True
    if not client_ip:
        return False
    try:
        ip = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    return any(ip in n for n in nets)


def load(db: Session) -> list:
    """从 DB 载入白名单并热更新（启动时调用）。"""
    row = db.query(AppSetting).filter(AppSetting.key == KEY).first()
    raw: list = []
    if row and row.value:
        try:
            parsed = json.loads(row.value)
            if isinstance(parsed, list):
                raw = parsed
        except (TypeError, ValueError):
            raw = []
    return apply(raw)
