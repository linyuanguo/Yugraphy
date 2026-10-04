"""登录动态码（简化 HOTP，服务端签发、与用户名无关的一次性短码）。

「服务端取有时效性的动态码」落地：登录前先向本服务取一个 6 位动态码
（默认 60 秒有效、校验一次即作废）。前端**打开登录页即自动取码并展示**，
用户人工抄入后随登录表单一并提交。

与用户名解耦的原因：取码发生在用户输入用户名之前（页面一打开就要展示），
无法绑定账号。要防的仍是「不先取码就直接撞登录接口」的脚本探测/爆破：
脚本每次尝试都必须先调 /auth/hotp-code 换取新码，且每次登录尝试都会
消费一个码。

后续升级 TOTP（用户 secret 持久化 + 客户端按时算码）时，只需替换本模块
issue / verify 的实现，auth.py 的调用点不变。

注意：本实现为进程内存储，适用于当前「单 worker uvicorn」部署方式
（docker-compose backend 未带 --workers）；若改为多 worker/多实例，须换成
PG / Redis 共享存储，否则不同进程签发的码彼此不认。
"""
import random
import threading
import time

# 动态码有效窗口（秒）
# 90 秒（09-19 用户定：180 太久、60 又太紧张——09-08 曾因 60s 出现
# 「码过期反复提示、填对码也进不去」，故折中 90）。
# 码本就是明文展示给登录者本人、且一次性消费，时长放宽不削弱防脚本爆破的作用。
CODE_TTL_SECONDS = 90
_LOCK = threading.Lock()
# 已签发未消费的码：{6 位码: 过期时间戳}；校验命中即删除（一次性）
_CODES: dict = {}


def issue(username: str = ""):
    """签发一个新动态码，返回 (6 位码, 有效秒数)。

    username 仅保留参数兼容（调用方仍可传入，便于将来扩展按账号留痕），
    不再作为绑定/查找键——页面一打开就要能取码，此时用户名还没输入。
    签发前顺带清理已过期的旧码，避免无限增长。
    """
    with _LOCK:
        now = time.time()
        for k, exp in list(_CODES.items()):
            if now > exp:
                _CODES.pop(k, None)
        if len(_CODES) >= 500:  # 防恶意狂刷：超上限时丢弃最早签发的码
            oldest = min(_CODES, key=_CODES.get)
            _CODES.pop(oldest, None)
        code = f"{random.randrange(10 ** 6):06d}"
        while code in _CODES:  # 极小概率碰撞时重取
            code = f"{random.randrange(10 ** 6):06d}"
        _CODES[code] = now + CODE_TTL_SECONDS
    return code, CODE_TTL_SECONDS


def verify(code: str) -> bool:
    """校验动态码：存在且未过期即作废并返回 True；否则 False。

    无论是否过期，只要提交的码在签发表里命中，就立即作废（一次性），
    保证每次登录尝试都必须重新取码。
    """
    with _LOCK:
        exp = _CODES.pop(str(code), None)
        if exp is None:
            return False
        return time.time() <= exp
