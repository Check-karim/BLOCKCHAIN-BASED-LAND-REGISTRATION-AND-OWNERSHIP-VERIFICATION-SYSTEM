from urllib.parse import urlencode

from flask import Blueprint, Response, abort, g, render_template, request

from ..auth import login_required
from ..blockchain import now_stamp
from ..db import execute, log_activity
from ..reports import (
    REPORTS,
    applicable_filters,
    build_csv,
    build_pdf,
    clean_filters,
    fetch_report,
    reports_for,
    status_options,
)

bp = Blueprint("reports", __name__)


@bp.get("/reports")
@login_required
def index():
    choices = reports_for(g.user["role"])
    allowed = {key for key, _title in choices}
    selected = request.args.get("report") or choices[0][0]
    if selected not in allowed:
        selected = choices[0][0]
    filters = applicable_filters(selected, clean_filters(request.args))
    report = fetch_report(selected, filters, g.user, limit=20)
    return render_template(
        "reports/index.html",
        choices=choices,
        selected=selected,
        filters=filters,
        report=report,
        statuses=status_options(selected),
        download_query=urlencode({key: value for key, value in filters.items() if value}),
    )


@bp.get("/reports/export/<report_name>/<fmt>")
@login_required
def export(report_name, fmt):
    if fmt not in {"pdf", "csv"} or report_name not in REPORTS:
        abort(404)
    if g.user["role"] not in REPORTS[report_name]["roles"]:
        abort(403)
    filters = applicable_filters(report_name, clean_filters(request.args))
    report = fetch_report(report_name, filters, g.user)
    stamp = now_stamp()
    subtitle = f"MINALOC case study  ·  generated {stamp} (Kigali)  ·  {report['total']} record(s)"
    if fmt == "pdf":
        payload = build_pdf(report["title"], report["headers"], report["rows"], subtitle)
        mime = "application/pdf"
    else:
        payload = build_csv(report["headers"], report["rows"])
        mime = "text/csv; charset=utf-8"
    filename = f"minaloc-{report_name}-{stamp[:10]}.{fmt}"
    execute(
        """
        INSERT INTO reports (generated_by, report_type, generated_at, file_reference)
        VALUES (%s, %s, %s, %s)
        """,
        (g.user["id"], report_name, stamp, filename),
    )
    log_activity(
        "DOWNLOAD_REPORT",
        f"Downloaded {report_name} as {fmt}.",
        g.user["id"],
        entity_type="report",
        entity_id=None,
    )
    return Response(
        payload,
        mimetype=mime,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
