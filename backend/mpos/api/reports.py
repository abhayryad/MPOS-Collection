"""Reports tab: Electronic General summary, paged preview and Excel download."""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from ..auth.deps import require
from ..auth.locations import stores_for
from ..auth.roles import REPORTS
from ..auth.store import User
from ..db import connect
from ..reports import electronic_journal
from .common import check_date

report_user = require(REPORTS)
router = APIRouter(prefix="/api/reports", dependencies=[Depends(report_user)])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/electronic-journal/summary")
def electronic_journal_summary(start: str, end: str, store: str = "", user: User = Depends(report_user)):
    start, end = check_date(start, "start"), check_date(end, "end")
    with connect() as conn:
        return electronic_journal.summary(electronic_journal.load(conn, start, end, stores_for(user, store)))


@router.get("/electronic-journal/rows")
def electronic_journal_rows(start: str, end: str, store: str = "",
                            page: int = Query(1, ge=1), size: int = Query(100, ge=1, le=500),
                            user: User = Depends(report_user)):
    start, end = check_date(start, "start"), check_date(end, "end")
    with connect() as conn:
        data = electronic_journal.load_page(conn, start, end, (page - 1) * size, size, stores_for(user, store))
    return JSONResponse({**data, "page": page, "size": size})


@router.get("/electronic-journal.xlsx")
def electronic_journal_xlsx(start: str, end: str, store: str = "", user: User = Depends(report_user)):
    start, end = check_date(start, "start"), check_date(end, "end")
    with connect() as conn:
        df = electronic_journal.load(conn, start, end, stores_for(user, store))
    if df.empty:
        raise HTTPException(404, "No transactions for that filter")
    name = (f"electronic_journal_{store or 'ALL'}_{start}"
            + (f"_to_{end}" if end != start else "") + ".xlsx")
    return Response(electronic_journal.to_xlsx(df), media_type=XLSX,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
