import csv
import re
from datetime import datetime
from decimal import Decimal
from io import BytesIO, StringIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .blockchain import now_stamp
from .db import query_all, query_one
from .locations import LAND_USES, PROVINCES, ROLE_LABELS, STATUS_LABELS

GREEN = colors.HexColor("#0e6a43")
INK = colors.HexColor("#17211c")
MUTED = colors.HexColor("#5c6b62")
LINE = colors.HexColor("#ddd4c4")
PAPER = colors.HexColor("#f6f3ec")
ALL_DISTRICTS = {district for districts in PROVINCES.values() for district in districts}
LAND_STATUSES = {"registered", "verified", "pending_update", "disputed", "pending_approval", "rejected"}
USER_ROLES = set(ROLE_LABELS)
EXPORT_CAP = 5000

REPORTS = {
    "parcels": {
        "title": "Land parcel register",
        "roles": {"admin", "official", "landowner"},
    },
    "ownership": {
        "title": "Ownership register",
        "roles": {"admin", "official", "landowner"},
    },
    "history": {
        "title": "Record history",
        "roles": {"admin", "official", "landowner"},
    },
    "verifications": {
        "title": "Ownership verification log",
        "roles": {"admin", "official"},
    },
    "users": {
        "title": "System users",
        "roles": {"admin"},
    },
    "activity": {
        "title": "Activity log",
        "roles": {"admin"},
    },
    "minaloc": {
        "title": "Land management report",
        "roles": {"admin", "official"},
    },
}


def reports_for(role):
    return [(key, spec["title"]) for key, spec in REPORTS.items() if role in spec["roles"]]


def status_options(report_name):
    if report_name in {"parcels", "ownership", "history", "minaloc"}:
        return [
            ("registered", "Registered"),
            ("verified", "Verified"),
            ("pending_approval", "Awaiting approval"),
            ("pending_update", "Pending update"),
            ("disputed", "Disputed"),
            ("rejected", "Rejected"),
        ]
    if report_name == "verifications":
        return [("confirmed", "Confirmed"), ("rejected", "Rejected")]
    if report_name == "users":
        return [("active", "Active"), ("inactive", "Inactive")]
    return []


def applicable_filters(report_name, filters):
    cleaned = dict(filters)
    allowed = {value for value, _label in status_options(report_name)}
    if cleaned["status"] not in allowed:
        cleaned["status"] = ""
    if report_name not in {"parcels", "ownership", "minaloc"}:
        cleaned["land_use"] = ""
    if report_name != "users":
        cleaned["role"] = ""
    if report_name == "activity":
        cleaned["province"] = ""
        cleaned["district"] = ""
    return cleaned


def clean_filters(args):
    province = (args.get("province") or "").strip()
    district = (args.get("district") or "").strip()
    status = (args.get("status") or "").strip()
    land_use = (args.get("land_use") or "").strip()
    role = (args.get("role") or "").strip()
    if province not in PROVINCES:
        province = ""
    if district not in ALL_DISTRICTS:
        district = ""
    if status not in LAND_STATUSES and status not in {"active", "inactive", "confirmed", "rejected"}:
        status = ""
    if land_use not in LAND_USES:
        land_use = ""
    if role not in USER_ROLES:
        role = ""
    query = re.sub(r"[%_]", "", (args.get("q") or "").strip())[:80]
    return {
        "q": query,
        "province": province,
        "district": district,
        "status": status,
        "land_use": land_use,
        "role": role,
        "date_from": _date(args.get("date_from")),
        "date_to": _date(args.get("date_to")),
    }


def _date(value):
    value = (value or "").strip()
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _cell(value):
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y %H:%M")
    if isinstance(value, Decimal):
        return f"{value:,.2f}"
    if value is None:
        return ""
    key = str(value)
    if key in STATUS_LABELS or key in ROLE_LABELS:
        return STATUS_LABELS.get(key) or ROLE_LABELS.get(key)
    if key in {"registration", "transfer", "boundary", "use_change", "correction", "confirmed", "rejected", "land_corrected"}:
        return key.replace("_", " ").title()
    return str(value)


def _where(clauses):
    return ("WHERE " + " AND ".join(clauses)) if clauses else ""


def _dates(clauses, params, column, filters):
    if filters["date_from"]:
        clauses.append(f"{column} >= %s")
        params.append(filters["date_from"] + " 00:00:00")
    if filters["date_to"]:
        clauses.append(f"{column} <= %s")
        params.append(filters["date_to"] + " 23:59:59")


def _search(clauses, params, expression, query):
    if query:
        clauses.append(expression)
        like = f"%{query}%"
        params.extend([like] * expression.count("%s"))


def fetch_report(report_name, filters, user, limit=None):
    spec = REPORTS[report_name]
    headers, sql, params = _query(report_name, filters, user)
    count_sql = f"SELECT COUNT(*) AS total FROM ({sql}) AS report_rows"
    total = query_one(count_sql, params)["total"]
    cap = EXPORT_CAP if limit is None else limit
    rows = query_all(sql + " LIMIT %s", [*params, cap])
    materialised = [[_cell(row[header]) for header in _keys(report_name)] for row in rows]
    return {
        "title": spec["title"],
        "headers": headers,
        "rows": materialised,
        "total": total,
        "shown": len(materialised),
        "truncated": total > len(materialised),
    }


def _keys(report_name):
    if report_name == "minaloc":
        report_name = "parcels"
    return {
        "parcels": ["upi", "title_number", "owner_name", "national_id", "province", "district", "sector", "cell", "village", "land_use", "size_sqm", "status", "created_at"],
        "ownership": ["upi", "title_number", "owner_name", "national_id", "phone", "email", "district", "sector", "status"],
        "history": ["created_at", "upi", "event_type", "previous_owner", "new_owner", "legal_basis", "legal_reference", "officer", "block_hash"],
        "verifications": ["created_at", "upi", "owner_name", "result", "officer", "notes", "block_hash"],
        "users": ["full_name", "username", "role", "email", "phone", "national_id", "province", "district", "status", "created_at"],
        "activity": ["created_at", "person", "action", "details", "ip_address"],
    }[report_name]


def _query(report_name, filters, user):
    if report_name in {"parcels", "minaloc"}:
        return _parcel_query(filters, user, ownership=False)
    if report_name == "ownership":
        return _parcel_query(filters, user, ownership=True)
    if report_name == "history":
        return _history_query(filters, user)
    if report_name == "verifications":
        return _verification_query(filters, user)
    if report_name == "users":
        return _user_query(filters)
    return _activity_query(filters)


def _parcel_query(filters, user, ownership):
    clauses, params = [], []
    if user["role"] == "landowner":
        clauses.append("l.owner_id = %s")
        params.append(user["id"])
    if filters["province"]:
        clauses.append("l.province = %s")
        params.append(filters["province"])
    if filters["district"]:
        clauses.append("l.district = %s")
        params.append(filters["district"])
    if filters["status"] in LAND_STATUSES:
        clauses.append("l.status = %s")
        params.append(filters["status"])
    if filters["land_use"]:
        clauses.append("l.land_use = %s")
        params.append(filters["land_use"])
    _dates(clauses, params, "l.created_at", filters)
    _search(
        clauses,
        params,
        "(l.upi LIKE %s OR l.title_number LIKE %s OR u.full_name LIKE %s)",
        filters["q"],
    )
    if ownership:
        headers = ["UPI", "Title", "Owner", "National ID", "Phone", "Email", "District", "Sector", "Status"]
        columns = """
            COALESCE(l.upi, 'Awaiting UPI') AS upi,
            COALESCE(l.title_number, 'Awaiting title') AS title_number,
            u.full_name AS owner_name, u.national_id,
            u.phone, u.email, l.district, l.sector, l.status
        """
    else:
        headers = ["UPI", "Title", "Owner", "National ID", "Province", "District", "Sector", "Cell", "Village", "Use", "Size (sqm)", "Status", "Registered"]
        columns = """
            COALESCE(l.upi, 'Awaiting UPI') AS upi,
            COALESCE(l.title_number, 'Awaiting title') AS title_number,
            u.full_name AS owner_name, u.national_id,
            l.province, l.district, l.sector, l.cell, l.village, l.land_use,
            l.size_sqm, l.status, l.created_at
        """
    sql = f"""
        SELECT {columns}
        FROM land_parcels l
        JOIN users u ON u.id = l.owner_id
        {_where(clauses)}
        ORDER BY l.created_at DESC, l.id DESC
    """
    return headers, sql, params


def _history_query(filters, user):
    clauses, params = [], []
    if user["role"] == "landowner":
        clauses.append("l.owner_id = %s")
        params.append(user["id"])
    if filters["province"]:
        clauses.append("l.province = %s")
        params.append(filters["province"])
    if filters["district"]:
        clauses.append("l.district = %s")
        params.append(filters["district"])
    if filters["status"] in LAND_STATUSES:
        clauses.append("l.status = %s")
        params.append(filters["status"])
    _dates(clauses, params, "e.created_at", filters)
    _search(
        clauses,
        params,
        "(l.upi LIKE %s OR e.legal_reference LIKE %s OR e.event_type LIKE %s)",
        filters["q"],
    )
    headers = ["Date", "UPI", "Event", "Previous owner", "New owner", "Legal basis", "Reference", "Officer", "Block"]
    sql = f"""
        SELECT e.created_at, COALESCE(l.upi, 'Awaiting UPI') AS upi, e.event_type,
               po.full_name AS previous_owner, no.full_name AS new_owner,
               e.legal_basis, e.legal_reference, a.full_name AS officer, e.block_hash
        FROM ownership_events e
        JOIN land_parcels l ON l.id = e.land_id
        LEFT JOIN users po ON po.id = e.old_owner_id
        LEFT JOIN users no ON no.id = e.new_owner_id
        JOIN users a ON a.id = e.actor_id
        {_where(clauses)}
        ORDER BY e.created_at DESC, e.id DESC
    """
    return headers, sql, params


def _verification_query(filters, user):
    clauses, params = [], []
    if user["role"] == "landowner":
        clauses.append("l.owner_id = %s")
        params.append(user["id"])
    if filters["province"]:
        clauses.append("l.province = %s")
        params.append(filters["province"])
    if filters["district"]:
        clauses.append("l.district = %s")
        params.append(filters["district"])
    if filters["status"] in {"confirmed", "rejected"}:
        clauses.append("v.result = %s")
        params.append(filters["status"])
    _dates(clauses, params, "v.created_at", filters)
    _search(clauses, params, "(l.upi LIKE %s OR u.full_name LIKE %s OR v.notes LIKE %s)", filters["q"])
    headers = ["Date", "UPI", "Owner", "Result", "Officer", "Notes", "Block"]
    sql = f"""
        SELECT v.created_at, l.upi, u.full_name AS owner_name, v.result,
               o.full_name AS officer, v.notes, v.block_hash
        FROM verifications v
        JOIN land_parcels l ON l.id = v.land_id
        JOIN users u ON u.id = l.owner_id
        JOIN users o ON o.id = v.official_id
        {_where(clauses)}
        ORDER BY v.created_at DESC, v.id DESC
    """
    return headers, sql, params


def _user_query(filters):
    clauses, params = [], []
    if filters["role"]:
        clauses.append("u.role = %s")
        params.append(filters["role"])
    if filters["province"]:
        clauses.append("u.province = %s")
        params.append(filters["province"])
    if filters["district"]:
        clauses.append("u.district = %s")
        params.append(filters["district"])
    if filters["status"] in {"active", "inactive"}:
        clauses.append("u.status = %s")
        params.append(filters["status"])
    _dates(clauses, params, "u.created_at", filters)
    _search(
        clauses,
        params,
        "(u.full_name LIKE %s OR u.username LIKE %s OR u.email LIKE %s)",
        filters["q"],
    )
    headers = ["Name", "Username", "Role", "Email", "Phone", "National ID", "Province", "District", "Status", "Created"]
    sql = f"""
        SELECT u.full_name, u.username, u.role, u.email, u.phone, u.national_id,
               u.province, u.district, u.status, u.created_at
        FROM users u
        {_where(clauses)}
        ORDER BY u.created_at DESC, u.id DESC
    """
    return headers, sql, params


def _activity_query(filters):
    clauses, params = [], []
    _dates(clauses, params, "a.created_at", filters)
    _search(
        clauses,
        params,
        "(a.action LIKE %s OR a.details LIKE %s OR u.full_name LIKE %s OR u.username LIKE %s)",
        filters["q"],
    )
    headers = ["Time", "Person", "Action", "Details", "IP address"]
    sql = f"""
        SELECT a.created_at,
               COALESCE(u.full_name, 'System') AS person,
               a.action, a.details, a.ip_address
        FROM activity_logs a
        LEFT JOIN users u ON u.id = a.user_id
        {_where(clauses)}
        ORDER BY a.created_at DESC, a.id DESC
    """
    return headers, sql, params


def build_csv(headers, rows):
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_csv_cell(value) for value in row])
    return buffer.getvalue().encode("utf-8-sig")


def _csv_cell(value):
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@")) or re.fullmatch(r"\d{10,}", text) or re.fullmatch(r"0\d+", text):
        return "'" + text
    return text


def build_pdf(title, headers, rows, subtitle):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=18 * mm,
        bottomMargin=14 * mm,
        title=title,
    )
    styles = _styles()
    story = [
        Paragraph(escape(title), styles["title"]),
        Spacer(1, 4),
        Paragraph(escape(subtitle), styles["sub"]),
        Spacer(1, 10),
    ]
    if not rows:
        story.append(Paragraph("No records match this report.", styles["body"]))
    else:
        header_row = [Paragraph(escape(header), styles["th"]) for header in headers]
        body = [[Paragraph(escape(str(value)).replace("\n", "<br/>"), styles["td"]) for value in row] for row in rows]
        width = landscape(A4)[0] - 24 * mm
        table = Table([header_row, *body], colWidths=[width / len(headers)] * len(headers), repeatRows=1)
        table.setStyle(_table_style())
        story.append(table)
    doc.build(story, onFirstPage=_paint, onLaterPages=_paint)
    return buffer.getvalue()


def build_certificate(land, owner):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=22 * mm,
        bottomMargin=16 * mm,
        title=f"Land extract {land['upi']}",
    )
    styles = _styles()
    latest = query_one(
        "SELECT block_index, block_hash, created_at FROM blocks WHERE record_ref = %s ORDER BY block_index DESC LIMIT 1",
        (land["upi"],),
    )
    story = [
        Paragraph("REPUBLIC OF RWANDA", styles["kicker"]),
        Paragraph("Ministry of Local Government", styles["sub"]),
        Spacer(1, 8),
        Paragraph("Land record extract", styles["title"]),
        Paragraph(
            "This extract is produced by the Ubutaka ledger for ownership verification in the MINALOC case study. It restates the current register entry and its latest block.",
            styles["body"],
        ),
        Spacer(1, 8),
    ]
    pairs = [
        ("UPI", land["upi"]),
        ("Title number", land["title_number"]),
        ("Owner", owner["full_name"]),
        ("National ID", owner["national_id"] or "Not recorded"),
        ("Status", STATUS_LABELS.get(land["status"], land["status"])),
        ("Land use", land["land_use"]),
        ("Size", f"{land['size_sqm']:,.2f} square metres"),
        ("Province", land["province"]),
        ("District", land["district"]),
        ("Sector / cell / village", f"{land['sector']} / {land['cell']} / {land['village']}"),
        ("Registered", _cell(land["created_at"])),
        ("Latest block", f"#{latest['block_index']}  {latest['block_hash']}" if latest else "Not sealed"),
    ]
    data = [[Paragraph(escape(label), styles["th"]), Paragraph(escape(value), styles["td"])] for label, value in pairs]
    table = Table(data, colWidths=[55 * mm, 115 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), GREEN),
                ("BACKGROUND", (1, 0), (1, -1), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.3, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend([table, Spacer(1, 12), Paragraph(f"Generated {now_stamp()} (Kigali).", styles["sub"])])
    doc.build(story, onFirstPage=_paint, onLaterPages=_paint)
    return buffer.getvalue()


def _styles():
    return {
        "title": ParagraphStyle("title", fontName="Times-Bold", fontSize=16, textColor=GREEN, leading=20, alignment=TA_LEFT),
        "kicker": ParagraphStyle("kicker", fontName="Times-Bold", fontSize=10, textColor=INK, leading=13),
        "sub": ParagraphStyle("sub", fontName="Times-Roman", fontSize=9, textColor=MUTED, leading=12),
        "body": ParagraphStyle("body", fontName="Times-Roman", fontSize=10, textColor=INK, leading=14),
        "th": ParagraphStyle("th", fontName="Helvetica-Bold", fontSize=8, textColor=colors.white, leading=10),
        "td": ParagraphStyle("td", fontName="Helvetica", fontSize=8, textColor=INK, leading=10),
    }


def _table_style():
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), GREEN),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BACKGROUND", (0, 1), (-1, -1), colors.white),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PAPER]),
            ("GRID", (0, 0), (-1, -1), 0.3, LINE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
    )


def _paint(canvas, doc):
    width, height = doc.pagesize
    canvas.saveState()
    canvas.setFillColor(GREEN)
    canvas.rect(0, height - 12 * mm, width, 12 * mm, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("Times-Bold", 9)
    canvas.drawString(12 * mm, height - 7.5 * mm, "MINALOC   ·   Ubutaka land registry")
    canvas.setFillColor(MUTED)
    canvas.setFont("Times-Roman", 8)
    canvas.drawString(12 * mm, 7 * mm, f"Kigali case study   ·   Page {doc.page}")
    canvas.restoreState()
