"""把源 PDF 重新转图并覆盖指定任务的 MinIO 页面图（deskew=False，方向保真）。

用法: python reupload_minio_pages.py <pdf_path> <task_id> [workers]
"""
import concurrent.futures
import os
import shutil
import sys
import tempfile

from minio import Minio

from app.utils import image_utils

PDF = sys.argv[1]
TASK = sys.argv[2]
WORKERS = int(sys.argv[3]) if len(sys.argv) > 3 else 8
ML = int(os.environ.get("MAX_IMAGE_LONG_EDGE", "1600"))
mc = Minio(
    os.environ["MINIO_ENDPOINT"],
    access_key=os.environ["MINIO_USER"],
    secret_key=os.environ["MINIO_PASSWORD"],
    secure=False,
)
BUCKET = os.environ.get("MINIO_BUCKET", "genealogy")

tmp = tempfile.mkdtemp(prefix="reup_")
raw_dir = os.path.join(tmp, "raw")
work_dir = os.path.join(tmp, "work")
os.makedirs(raw_dir)
os.makedirs(work_dir)
try:
    # 1) 与生产一致：多 worker 分片转换（方向按页面 rect，无 deskew）
    image_utils.pdf_to_images(PDF, raw_dir, ML, workers=WORKERS)
    files = sorted(os.listdir(raw_dir))
    print("converted:", len(files))

    # 2) 预处理（仅 CLAHE 增强，不 deskew）+ 覆盖 MinIO
    def up(f):
        no = int(f.split("_")[1].split(".")[0])
        raw = os.path.join(raw_dir, f)
        work = os.path.join(work_dir, f"w_{no:03d}.png")
        image_utils.preprocess_image(
            raw, work, deskew=False, denoise=False, enhance=True, binarize=False
        )
        obj = f"scans/{TASK}/page_{no:03d}.png"
        mc.fput_object(BUCKET, obj, work, content_type="image/png")
        return obj

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, WORKERS * 2)) as ex:
        done = 0
        for _ in ex.map(up, files):
            done += 1
            if done % 100 == 0:
                print("uploaded:", done)
    print("DONE", done)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
