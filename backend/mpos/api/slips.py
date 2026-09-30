"""Slip preview / download: one slip as .txt, or every slip in a filter as one ZIP.
Today's slips can be previewed but not downloaded (see slips.service.downloadable)."""
import io
import zipfile

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from ..slips.service import KINDS, TODAY_BLOCKED, build_all, downloadable, load
from .common import check_date

router = APIRouter(prefix="/api")


@router.get("/slip/{kind}/{store}/{day}")
def slip(kind: str, store: str, day: str, download: bool = False):
    kind = kind.upper()
    if kind not in KINDS:
        raise HTTPException(404, "Unknown slip type")
    day = check_date(day, "date")
    if download and not downloadable(day):
        raise HTTPException(403, TODAY_BLOCKED)
    text = build_all(*load(day, day, store))[kind].get((store, day))
    if text is None:
        raise HTTPException(404, f"No {KINDS[kind].lower()} slip for {store} on {day}")
    headers = {"Content-Disposition": f'attachment; filename="{store}_{day}_{kind}.txt"'} if download else {}
    return Response(text, media_type="text/plain; charset=utf-8", headers=headers)


@router.get("/slips.zip")
def slips_zip(start: str, end: str, store: str = "", kinds: str = Query("MSRC")):
    start, end = check_date(start, "start"), check_date(end, "end")
    wanted = [k for k in kinds.upper() if k in KINDS]
    slips = build_all(*load(start, end, store or None))
    buf = io.BytesIO()
    count = skipped = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for kind in wanted:
            for (s, d), text in sorted(slips[kind].items()):
                if not downloadable(d):
                    skipped += 1
                    continue
                zf.writestr(f"{s}_{d}_{kind}.txt", text)
                count += 1
    if not count:
        raise HTTPException(403 if skipped else 404, TODAY_BLOCKED if skipped else "No slips for that filter")
    name = f"slips_{store or 'ALL'}_{start}_to_{end}.zip"
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
