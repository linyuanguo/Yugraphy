"""后台任务注册表：让「删除任务」能真正打断正在运行的协程。

仅靠 DELETE /tasks/{id} 删行 + 清 MinIO 无法中止后台识别/整理协程——协程会继续把
整本页/块调完 206（白算）并一直占用任务级并发闸，导致重传的新任务排队等不到槽。
本模块在后台任务入口登记其 asyncio.Task，删除接口通过 cancel_task() 发起取消：
协程会在下一个 await 点被中断，滑动窗口中的子任务与 asyncio 并发闸随之释放
（206 并发槽、任务闸空出），实现「勾选删除 = 真打断」。
"""
import asyncio
import logging
from typing import Dict

logger = logging.getLogger("genealogy.task_registry")

_RUNNING: Dict[str, asyncio.Task] = {}


def register(task_id: str) -> None:
    """登记当前正在运行的后台任务（取调用处所在 asyncio.Task）。"""
    task = asyncio.current_task()
    if task is None:
        return
    _RUNNING[task_id] = task


def unregister(task_id: str) -> None:
    """任务收尾时注销（幂等）。"""
    _RUNNING.pop(task_id, None)


def cancel_task(task_id: str) -> bool:
    """取消运行中的后台协程；返回 True=已发起取消。无任务/已完成返回 False。"""
    task = _RUNNING.get(task_id)
    if task is None or task.done():
        _RUNNING.pop(task_id, None)
        return False
    cancelled = task.cancel()
    if cancelled:
        logger.info("已向任务 %s 发起取消（将释放 206 并发槽与任务闸）", task_id)
    return cancelled


def is_running(task_id: str) -> bool:
    task = _RUNNING.get(task_id)
    return bool(task is not None and not task.done())
