"""/api/v1: CSV export and the weekly PDF."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response

from pulse_mirror.api.deps import Db, Principal
from pulse_mirror.reports import export, pdf

router = APIRouter(tags=["export"])


@router.get("/export/all.zip", response_class=Response)
def export_all(db: Db, _: Principal) -> Response:
    return Response(
        export.all_zip(db),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="pulse-mirror-export.zip"'},
    )


@router.get("/export/{dataset}.csv", response_class=Response)
def export_csv(dataset: str, db: Db, _: Principal) -> Response:
    if dataset not in export.DATASETS:
        raise HTTPException(404, f"Unknown dataset. Use one of: {', '.join(export.DATASETS)}.")
    return Response(
        export.dataset_csv(db, dataset),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="pulse-mirror-{dataset}.csv"'},
    )


@router.get("/reports/weekly.pdf", response_class=Response)
def weekly_pdf(
    db: Db,
    _: Principal,
    week: Annotated[
        str | None, Query(pattern=r"^\d{4}-W\d{2}$", description="e.g. 2026-W47")
    ] = None,
) -> Response:
    if week:
        year, num = week.split("-W")
        try:
            monday = date.fromisocalendar(int(year), int(num), 1)
        except ValueError as e:
            raise HTTPException(422, "Invalid ISO week.") from e
    else:
        today = date.today()
        monday = today - timedelta(days=today.weekday())
    return Response(
        pdf.build_weekly_report(db, monday),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{pdf.report_filename(monday)}"'},
    )
