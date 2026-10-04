# -*- coding: utf-8 -*-
"""P4 世系页 V5 专项重测（只读不写库）。

跑「生产默认」整页链路（extract_page 含自动补漏复查 + normalize 后处理），
连跑 RUNS 次评估单次随机性，并把结果落 /app/compare/p4_test.json 供对比渲染。
判定要点：日期臆造（模糊格应 □）、残句（兼德/坐向辛生 等）不得成 person、
配氏不拼接、置信不整页统一 0.4、版心（共和戊午…）保留。
"""
import asyncio
import json
import os
import time

from app.core.config import settings
from app.services import file_service, vision_service

TID = "c27afaa9424d"
PAGE = 4
RUNS = int(os.environ.get("P4_RUNS", "2"))
BUDGET = int(os.environ.get("P4_BUDGET", "240"))


def _snap(res: dict) -> dict:
    return {
        "persons": res.get("persons", []),
        "entries": res.get("entries", []),
        "page_notes": res.get("page_notes", ""),
    }


async def _one(tmp: str, run: int) -> dict:
    t0 = time.time()
    res = await vision_service.extract_page(tmp, hard_timeout=BUDGET)  # 生产默认（auto_verify=True）
    norm = vision_service.normalize_extraction(res)
    return {"run": run, "sec": round(time.time() - t0, 1), "result": _snap(norm)}


async def main() -> None:
    os.makedirs("/app/compare/tmp", exist_ok=True)
    client = file_service._client()  # noqa: SLF001
    tmp = f"/app/compare/tmp/p4_{TID[:8]}.png"
    client.fget_object(settings.MINIO_BUCKET, f"scans/{TID}/page_{PAGE:03d}.png", tmp)
    out = []
    for i in range(RUNS):
        out.append(await _one(tmp, i + 1))
        print(f"  run {i + 1} done", flush=True)
    with open("/app/compare/p4_test.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    for o in out:
        r = o["result"]
        ps = r["persons"]
        confs = [p.get("confidence") for p in ps]
        print(
            f"--- run{o['run']}  {o['sec']}s  persons={len(ps)}  "
            f"conf={confs}  notes={str(r['page_notes'])[:70]}"
        )
        for p in ps:
            print(
                f"   {p['name']}|{p['confidence']}|"
                f"{str(p.get('biography'))[:70]}"
            )
        text = "".join(str(e.get("text") or "") for e in r["entries"])
        print(
            f"   entries={len(r['entries'])} chars={len(text)} "
            f"共和戊午={'共和戊午' in text} 共和={'共和' in text} □={text.count('□')}"
        )
    print("WROTE /app/compare/p4_test.json")


asyncio.run(main())
