"""MinIO 文件存储服务。"""
import io
import json
import logging
import uuid
from typing import Optional

from fastapi.concurrency import run_in_threadpool
from minio import Minio
from minio.error import S3Error

from app.core.config import settings

logger = logging.getLogger("genealogy.files")


def _client() -> Minio:
    return Minio(
        settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_USER,
        secret_key=settings.MINIO_PASSWORD,
        secure=False,
    )


def _ensure_bucket(client: Minio) -> None:
    if not client.bucket_exists(settings.MINIO_BUCKET):
        client.make_bucket(settings.MINIO_BUCKET)
        logger.info("MinIO bucket %s created", settings.MINIO_BUCKET)


def public_url(object_name: str) -> str:
    """经 Nginx 代理的访问 URL。"""
    return f"/files/{object_name}"


def _public_read_policy() -> str:
    """允许匿名 GetObject 的桶策略。

    Nginx 直接代理 MinIO（/files/...）供 <img>/预览展示，无法携带登录态，
    因此需开放匿名只读；写入/删除仍受 MinIO 凭证保护。
    对象仅含扫描件页面图与已登录页面展示的文件。
    """
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": ["*"]},
                    "Action": ["s3:GetObject"],
                    "Resource": [f"arn:aws:s3:::{settings.MINIO_BUCKET}/*"],
                }
            ],
        }
    )


def ensure_public_read() -> None:
    """幂等设置桶为匿名只读；失败抛出由启动流程告警（不阻断启动）。"""
    _client().set_bucket_policy(settings.MINIO_BUCKET, _public_read_policy())


async def upload_bytes(
    object_name: str, data: bytes, content_type: str = "application/octet-stream"
) -> str:
    def _upload():
        client = _client()
        _ensure_bucket(client)
        client.put_object(
            settings.MINIO_BUCKET,
            object_name,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )

    await run_in_threadpool(_upload)
    return object_name


async def upload_file(
    object_name: str, file_path: str, content_type: Optional[str] = None
) -> str:
    def _upload():
        client = _client()
        _ensure_bucket(client)
        client.fput_object(
            settings.MINIO_BUCKET, object_name, file_path, content_type=content_type
        )

    await run_in_threadpool(_upload)
    return object_name


async def remove_prefix(prefix: str) -> int:
    """删除指定前缀下的所有对象，返回删除数量。"""

    def _remove() -> int:
        client = _client()
        objects = list(
            client.list_objects(settings.MINIO_BUCKET, prefix=prefix, recursive=True)
        )
        for obj in objects:
            client.remove_object(settings.MINIO_BUCKET, obj.object_name)
        if objects:
            logger.info("清理 MinIO 前缀 %s：%d 个对象", prefix, len(objects))
        return len(objects)

    return await run_in_threadpool(_remove)


async def object_exists(object_name: str) -> bool:
    """对象是否真实存在于 MinIO。

    用于「删除原始扫描件前的核对」：upload_file 返回成功并不代表对象一定落盘
    （连接抖动/中断窗口期），必须逐个 stat 核对后才敢删原件。
    """

    def _stat() -> bool:
        try:
            _client().stat_object(settings.MINIO_BUCKET, object_name)
            return True
        except Exception:  # noqa: BLE001
            return False

    return await run_in_threadpool(_stat)


async def count_objects(prefix: str) -> int:
    """统计前缀下对象数量（-1 = 查询失败）。用于判断页面图是否齐全。"""

    def _count() -> int:
        try:
            return len(
                list(
                    _client().list_objects(
                        settings.MINIO_BUCKET, prefix=prefix, recursive=True
                    )
                )
            )
        except Exception:  # noqa: BLE001
            return -1

    return await run_in_threadpool(_count)


async def remove_object(object_name: str) -> None:
    def _remove():
        client = _client()
        try:
            client.remove_object(settings.MINIO_BUCKET, object_name)
        except S3Error as exc:
            logger.warning("remove %s failed: %s", object_name, exc)

    await run_in_threadpool(_remove)


def new_doc_id(prefix: str = "doc") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
