"""当前请求客户端 IP 的上下文变量（供 service 层日志自动填充）。

由 ASGI 中间件在每个 HTTP 请求开始时设置；后台任务（asyncio.create_task）
会继承发起请求的 IP，因此转图/识别/整理等异步阶段也能记录到操作者 IP。
"""
from contextvars import ContextVar

# 默认空串：非 HTTP 上下文（如启动时恢复中断任务）取不到 IP
current_client_ip: ContextVar[str] = ContextVar("current_client_ip", default="")


def get_client_ip(request) -> str:
    """从请求头解析真实客户端 IP（兼容 Nginx 反向代理）。

    优先级：X-Forwarded-For（多级代理取第一个）> X-Real-IP > 直连地址。
    """
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    x_real = request.headers.get("x-real-ip", "")
    if x_real:
        return x_real.strip()
    if request.client and request.client.host:
        return request.client.host
    return ""
