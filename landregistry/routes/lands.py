from decimal import Decimal

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, send_file, url_for
from mysql.connector import IntegrityError

from ..auth import filter_query, page_window, roles_required
from ..blockchain import add_block, land_record_hash, now_stamp
from ..db import db_cursor, log_activity, query_all, query_one
from ..records import (
    chain_record_hash,
    compare_land,
    discard_upload,
    ensure_active_ownership,
    hash_from_cleaned,
    insert_document,
    issue_identifiers,
    insert_transaction,
    latest_seal,
    persist_upload,
    read_upload,
    stored_path,
)
from ..reports import build_certificate
from ..validators import collect_application, collect_land, collect_legal, validate_notes

bp = Blueprint("lands", __name__)


@bp.get("/lands")
@roles_required("admin", "official", "landowner")
def list_lands():
    clauses, params = [], []
    if g.user["role"] == "landowner":
        clauses.append("l.owner_id = %s")
        params.append(g.user["id"])
    district = (request.args.get("district") or "").strip()
    status = (request.args.get("status") or "").strip()
    land_use = (request.args.get("land_use") or "").strip()
    query = (request.args.get("q") or "").strip()[:80]
    if district:
        clauses.append("l.district = %s")
        params.append(district)
    if status in {"registered", "verified", "pending_update", "disputed", "pending_approval", "rejected"}:
        clauses.append("l.status = %s")
        params.append(status)
    if land_use:
        clauses.append("l.land_use = %s")
        params.append(land_use)
    if query:
        clauses.append("(l.upi LIKE %s OR l.title_number LIKE %s OR u.full_name LIKE %s)")
        like = f"%{query.replace('%', '').replace('_', '')}%"
        params.extend([like, like, like])
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    total = query_one(
        f"SELECT COUNT(*) AS total FROM land_parcels l JOIN users u ON u.id = l.owner_id {where}",
        params,
    )["total"]
    page, pages, offset = page_window(total)
    lands = query_all(
        f"""
        SELECT l.id, l.upi, l.title_number, l.district, l.sector, l.land_use, l.size_sqm, l.status,
               u.full_name AS owner_name
        FROM land_parcels l
        JOIN users u ON u.id = l.owner_id
        {where}
        ORDER BY l.updated_at DESC, l.id DESC
        LIMIT %s OFFSET %s
        """,
        [*params, 10, offset],
    )
    return render_template(
        "lands/list.html",
        lands=lands,
        page=page,
        pages=pages,
        total=total,
        filter_query=filter_query(),
        filters={"q": query, "district": district, "status": status, "land_use": land_use},
    )


@bp.route("/lands/new", methods=["GET", "POST"])
@roles_required("landowner")
def new_land():
    errors = {}
    form = {
        "province": g.user["province"] or "",
        "district": g.user["district"] or "",
    }
    if request.method == "POST":
        form = request.form
        errors, cleaned = collect_application(form)
        upload_error, upload = read_upload(request.files.get("document"), required=False)
        if upload_error:
            errors["document"] = upload_error
        if not errors:
            land_id = _submit_application(cleaned, upload)
            flash("Your land is submitted. An official will approve it and issue the UPI and title.", "success")
            return redirect(url_for("lands.detail", land_id=land_id))
    return render_template("lands/apply.html", errors=errors, form=form)


@bp.get("/lands/approvals")
@roles_required("official", "admin")
def approvals():
    rows = query_all(
        """
        SELECT l.id, l.district, l.sector, l.village, l.size_sqm, l.land_use, l.created_at,
               u.full_name AS owner_name
        FROM land_parcels l
        JOIN users u ON u.id = l.owner_id
        WHERE l.status = 'pending_approval'
        ORDER BY l.created_at ASC, l.id ASC
        """
    )
    return render_template("lands/approvals.html", rows=rows)


@bp.post("/lands/<int:land_id>/approve")
@roles_required("official")
def approve_land(land_id):
    land = _land_or_404(land_id)
    if land["status"] != "pending_approval":
        flash("This application is no longer waiting for approval.", "error")
        return redirect(url_for("lands.detail", land_id=land["id"]))
    try:
        upi, title = _approve_application(land)
    except IntegrityError:
        flash("The UPI or title could not be issued. Try the approval again.", "error")
        return redirect(url_for("lands.detail", land_id=land["id"]))
    flash(f"Approved. UPI {upi} and title {title} are now on the register.", "success")
    return redirect(url_for("lands.detail", land_id=land["id"]))


@bp.post("/lands/<int:land_id>/reject")
@roles_required("official")
def reject_land(land_id):
    land = _land_or_404(land_id)
    if land["status"] != "pending_approval":
        flash("This application is no longer waiting for approval.", "error")
        return redirect(url_for("lands.detail", land_id=land["id"]))
    note_error, notes = validate_notes(request.form.get("notes"), minimum=10, label="The reason")
    if note_error:
        flash(note_error, "error")
        return redirect(url_for("lands.detail", land_id=land["id"]))
    stamp = now_stamp()
    with db_cursor() as cursor:
        cursor.execute(
            """
            UPDATE land_parcels
            SET status = 'rejected', rejection_note = %s, updated_at = %s
            WHERE id = %s AND status = 'pending_approval'
            """,
            (notes, stamp, land["id"]),
        )
        if cursor.rowcount != 1:
            flash("This application is no longer waiting for approval.", "error")
            return redirect(url_for("lands.detail", land_id=land["id"]))
        log_activity(
            "REJECT_LAND",
            f"Rejected an application from {land['owner_name']}.",
            g.user["id"],
            cursor,
            entity_type="land_parcel",
            entity_id=land["id"],
        )
    flash("The application was rejected. No UPI or title was issued.", "success")
    return redirect(url_for("lands.approvals"))


@bp.get("/lands/<int:land_id>")
@roles_required("admin", "official", "landowner")
def detail(land_id):
    land = _land_or_404(land_id)
    _require_view(land)
    bundle = _bundle(land)
    return render_template("lands/detail.html", land=land, errors={}, **bundle)


@bp.route("/lands/<int:land_id>/update", methods=["GET", "POST"])
@roles_required("official")
def update_land(land_id):
    land = _land_or_404(land_id)
    if not land["upi"]:
        flash("Approve the application before updating the record. That approval issues the UPI and title.", "error")
        return redirect(url_for("lands.detail", land_id=land["id"]))
    errors = {}
    form = _form_from_land(land)
    owners = _owners(land["owner_id"])
    if request.method == "POST":
        form = request.form
        errors, cleaned = collect_land(form)
        legal_errors, legal = collect_legal(form)
        errors.update(legal_errors)
        if not errors and cleaned["upi"] != land["upi"]:
            errors["upi"] = "The UPI cannot be changed."
        owner = _owner_for_update(cleaned.get("owner_id"), land["owner_id"])
        if "owner_id" not in errors and not owner:
            errors["owner_id"] = "Select an active landowner."
        if not errors and _title_taken(cleaned["title_number"], land["id"]):
            errors["title_number"] = "That title number is already on the register."
        upload_error, upload = read_upload(request.files.get("document"), required=True)
        if upload_error:
            errors["document"] = upload_error
        changes = _changes(land, cleaned) if not errors else {}
        if not errors and not changes:
            errors["form"] = "Change at least one field. A legal update has to alter the record."
        if not errors:
            try:
                apply_legal_update(land, cleaned, legal, changes, owner, upload, correction=False)
            except IntegrityError:
                flash("That title number is already on the register.", "error")
            else:
                flash("The update is recorded. The earlier version stays in the ledger.", "success")
                return redirect(url_for("lands.detail", land_id=land["id"]))
    return render_template("lands/form.html", mode="update", errors=errors, form=form, owners=owners, land=land)


@bp.get("/lands/<int:land_id>/certificate")
@roles_required("admin", "official", "landowner")
def certificate(land_id):
    land = _land_or_404(land_id)
    _require_view(land)
    if not land["upi"]:
        flash("The extract is available after an official issues the UPI and title.", "error")
        return redirect(url_for("lands.detail", land_id=land["id"]))
    owner = {"full_name": land["owner_name"], "national_id": land["owner_national_id"]}
    payload = build_certificate(land, owner)
    filename = "land-extract-" + land["upi"].replace("/", "-") + ".pdf"
    log_activity(
        "DOWNLOAD_REPORT",
        f"Certificate for {land['upi']}.",
        g.user["id"],
        entity_type="land_parcel",
        entity_id=land["id"],
    )
    return Response(
        payload,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@bp.get("/lands/<int:land_id>/file")
@roles_required("admin", "official", "landowner")
def application_file(land_id):
    land = _land_or_404(land_id)
    _require_view(land)
    if not land.get("application_file"):
        abort(404)
    path = stored_path(land["application_file"])
    if path is None:
        abort(404)
    return send_file(path, as_attachment=True, download_name=land.get("application_file_name") or "document")


@bp.get("/lands/<int:land_id>/print")
@roles_required("admin", "official", "landowner")
def print_record(land_id):
    land = _land_or_404(land_id)
    _require_view(land)
    if not land["upi"]:
        flash("Printing is available after an official issues the UPI and title.", "error")
        return redirect(url_for("lands.detail", land_id=land["id"]))
    log_activity(
        "DOWNLOAD_REPORT",
        f"Print view for {land['upi']}.",
        g.user["id"],
        entity_type="land_parcel",
        entity_id=land["id"],
    )
    return render_template("lands/print.html", land=land, **_bundle(land))


@bp.get("/documents/<int:document_id>")
@roles_required("admin", "official", "landowner")
def download_document(document_id):
    row = query_one(
        """
        SELECT d.*, l.owner_id, l.upi
        FROM documents d
        JOIN land_transactions t ON t.id = d.transaction_id
        JOIN land_parcels l ON l.id = t.parcel_id
        WHERE d.id = %s
        """,
        (document_id,),
    )
    if not row:
        abort(404)
    if g.user["role"] == "landowner" and row["owner_id"] != g.user["id"]:
        abort(403)
    path = stored_path(row["storage_reference"])
    if path is None:
        abort(404)
    log_activity(
        "DOWNLOAD_REPORT",
        f"Document for {row['upi']}.",
        g.user["id"],
        entity_type="document",
        entity_id=row["id"],
    )
    return send_file(path, as_attachment=True, download_name=row["original_name"])


def _bundle(land):
    events = query_all(
        """
        SELECT e.*, po.full_name AS old_owner_name, no.full_name AS new_owner_name, a.full_name AS actor_name
        FROM ownership_events e
        LEFT JOIN users po ON po.id = e.old_owner_id
        LEFT JOIN users no ON no.id = e.new_owner_id
        JOIN users a ON a.id = e.actor_id
        WHERE e.land_id = %s
        ORDER BY e.created_at ASC, e.id ASC
        """,
        (land["id"],),
    )
    blocks = query_all(
        "SELECT block_index, action, block_hash, previous_hash, created_at FROM blocks WHERE record_ref = %s ORDER BY block_index ASC",
        (land["upi"],),
    )
    documents = query_all(
        """
        SELECT d.*, t.transaction_type
        FROM documents d
        JOIN land_transactions t ON t.id = d.transaction_id
        WHERE t.parcel_id = %s
        ORDER BY d.uploaded_at DESC, d.id DESC
        """,
        (land["id"],),
    )
    transactions = query_all(
        """
        SELECT t.*, u.full_name AS official_name
        FROM land_transactions t
        JOIN users u ON u.id = t.official_user_id
        WHERE t.parcel_id = %s
        ORDER BY t.created_at ASC, t.id ASC
        """,
        (land["id"],),
    )
    ownerships = query_all(
        """
        SELECT o.*, u.full_name AS owner_name
        FROM ownerships o
        JOIN users u ON u.id = o.owner_user_id
        WHERE o.parcel_id = %s
        ORDER BY o.start_date ASC, o.id ASC
        """,
        (land["id"],),
    )
    return {
        "events": events,
        "blocks": blocks,
        "documents": documents,
        "transactions": transactions,
        "ownerships": ownerships,
        "comparison": compare_land(land, chain_record_hash(land["upi"])) if land.get("upi") else None,
        "seal": latest_seal(land["upi"]) if land.get("upi") else None,
    }


def _land_or_404(land_id):
    land = query_one(
        """
        SELECT l.*, u.full_name AS owner_name, u.email AS owner_email, u.phone AS owner_phone,
               u.national_id AS owner_national_id, o.full_name AS officer_name,
               ap.full_name AS approver_name
        FROM land_parcels l
        JOIN users u ON u.id = l.owner_id
        JOIN users o ON o.id = l.registered_by
        LEFT JOIN users ap ON ap.id = l.approved_by
        WHERE l.id = %s
        """,
        (land_id,),
    )
    if not land:
        abort(404)
    return land


def _require_view(land):
    if g.user["role"] == "landowner" and land["owner_id"] != g.user["id"]:
        abort(403)


def _owners(current_id=None):
    return query_all(
        """
        SELECT id, full_name, district, national_id
        FROM users
        WHERE role = 'landowner' AND (status = 'active' OR id = %s)
        ORDER BY full_name
        """,
        (current_id or 0,),
    )


def _active_owner(owner_id):
    if not owner_id:
        return None
    return query_one(
        "SELECT id, full_name, role, status, province, district FROM users WHERE id = %s AND role = 'landowner' AND status = 'active'",
        (owner_id,),
    )


def _owner_for_update(owner_id, current_id):
    if not owner_id:
        return None
    return query_one(
        """
        SELECT id, full_name, role, status FROM users
        WHERE id = %s AND role = 'landowner' AND (status = 'active' OR id = %s)
        """,
        (owner_id, current_id),
    )


def _unique_land(upi, title_number):
    errors = {}
    if query_one("SELECT id FROM land_parcels WHERE upi = %s", (upi,)):
        errors["upi"] = "This UPI is already on the register."
    if query_one("SELECT id FROM land_parcels WHERE title_number = %s", (title_number,)):
        errors["title_number"] = "That title number is already on the register."
    return errors


def _title_taken(title_number, land_id):
    return query_one(
        "SELECT id FROM land_parcels WHERE title_number = %s AND id <> %s",
        (title_number, land_id),
    )


def _form_from_land(land):
    return {
        "upi": land["upi"],
        "title_number": land["title_number"],
        "owner_id": str(land["owner_id"]),
        "province": land["province"],
        "district": land["district"],
        "sector": land["sector"],
        "cell": land["cell"],
        "village": land["village"],
        "size_sqm": f"{land['size_sqm']:.2f}",
        "land_use": land["land_use"],
        "description": land["description"] or "",
    }


def _changes(land, cleaned):
    previous = {
        "title_number": land["title_number"],
        "owner_id": land["owner_id"],
        "province": land["province"],
        "district": land["district"],
        "sector": land["sector"],
        "cell": land["cell"],
        "village": land["village"],
        "size_sqm": Decimal(land["size_sqm"]).quantize(Decimal("0.01")),
        "land_use": land["land_use"],
        "description": land["description"] or None,
    }
    changes = {}
    for field, old in previous.items():
        new = cleaned[field]
        if old != new:
            changes[field] = (old, new)
    return changes


def _plain(field, value):
    if field == "size_sqm":
        return f"{Decimal(value):.2f}"
    return "" if value is None else str(value)


def _submit_application(cleaned, upload):
    stamp = now_stamp()
    stored_name = persist_upload(upload) if upload else None
    try:
        with db_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO land_parcels
                    (upi, title_number, owner_id, province, district, sector, cell, village,
                     size_sqm, land_use, description, status, registered_by, created_at, updated_at,
                     application_file, application_file_hash, application_file_name)
                VALUES (NULL, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending_approval', %s, %s, %s, %s, %s, %s)
                """,
                (
                    g.user["id"],
                    cleaned["province"],
                    cleaned["district"],
                    cleaned["sector"],
                    cleaned["cell"],
                    cleaned["village"],
                    f"{cleaned['size_sqm']:.2f}",
                    cleaned["land_use"],
                    cleaned["description"],
                    g.user["id"],
                    stamp,
                    stamp,
                    stored_name,
                    upload["file_hash"] if upload else None,
                    upload["original_name"] if upload else None,
                ),
            )
            land_id = cursor.lastrowid
            log_activity(
                "SUBMIT_LAND",
                f"Submitted land in {cleaned['district']} for approval.",
                g.user["id"],
                cursor,
                entity_type="land_parcel",
                entity_id=land_id,
            )
            return land_id
    except Exception:
        discard_upload(stored_name)
        raise


def _approve_application(land):
    stamp = now_stamp()
    year = int(stamp[:4])
    stored_name = land.get("application_file")
    with db_cursor() as cursor:
        upi, title = issue_identifiers(
            cursor, land["province"], land["district"], land["sector"], land["cell"], year
        )
        record_hash = land_record_hash(
            upi,
            title,
            land["owner_id"],
            land["province"],
            land["district"],
            land["sector"],
            land["cell"],
            land["village"],
            land["size_sqm"],
            land["land_use"],
        )
        cursor.execute(
            """
            UPDATE land_parcels
            SET upi = %s, title_number = %s, status = 'registered', record_hash = %s,
                approved_by = %s, updated_at = %s
            WHERE id = %s AND status = 'pending_approval'
            """,
            (upi, title, record_hash, g.user["id"], stamp, land["id"]),
        )
        if cursor.rowcount != 1:
            raise IntegrityError("Application was already decided")
        reference = f"REG-{land['id']}"
        digest = add_block(
            cursor,
            "land_registered",
            {
                "upi": upi,
                "title_number": title,
                "owner": land["owner_name"],
                "owner_id": land["owner_id"],
                "district": land["district"],
                "size_sqm": f"{Decimal(land['size_sqm']):.2f}",
                "land_use": land["land_use"],
                "record_hash": record_hash,
            },
            actor_id=g.user["id"],
            record_ref=upi,
        )
        cursor.execute(
            """
            INSERT INTO ownership_events
                (land_id, event_type, old_owner_id, new_owner_id, legal_basis, legal_reference,
                 notes, actor_id, block_hash, created_at)
            VALUES (%s, 'registration', NULL, %s, 'Administrative correction', %s, %s, %s, %s, %s)
            """,
            (
                land["id"],
                land["owner_id"],
                reference,
                "Approved application. UPI and title issued.",
                g.user["id"],
                digest,
                stamp,
            ),
        )
        ensure_active_ownership(cursor, land["id"], land["owner_id"], stamp)
        transaction_id = insert_transaction(
            cursor, land["id"], g.user["id"], "registration", reference, stamp, digest
        )
        if stored_name and land.get("application_file_hash"):
            insert_document(
                cursor,
                transaction_id,
                "registration",
                stored_name,
                land["application_file_hash"],
                land.get("application_file_name") or "document",
                stamp,
            )
        log_activity(
            "APPROVE_LAND",
            f"Issued {upi} and {title} for {land['owner_name']}.",
            g.user["id"],
            cursor,
            entity_type="land_parcel",
            entity_id=land["id"],
        )
        return upi, title


def _create_land(cleaned, owner, upload):
    stamp = now_stamp()
    record_hash = hash_from_cleaned(cleaned)
    stored_name = persist_upload(upload) if upload else None
    try:
        with db_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO land_parcels
                    (upi, title_number, owner_id, province, district, sector, cell, village,
                     size_sqm, land_use, description, status, registered_by, created_at, updated_at, record_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'registered', %s, %s, %s, %s)
                """,
                (
                    cleaned["upi"],
                    cleaned["title_number"],
                    cleaned["owner_id"],
                    cleaned["province"],
                    cleaned["district"],
                    cleaned["sector"],
                    cleaned["cell"],
                    cleaned["village"],
                    f"{cleaned['size_sqm']:.2f}",
                    cleaned["land_use"],
                    cleaned["description"],
                    g.user["id"],
                    stamp,
                    stamp,
                    record_hash,
                ),
            )
            land_id = cursor.lastrowid
            reference = f"REG-{land_id}"
            digest = add_block(
                cursor,
                "land_registered",
                {
                    "upi": cleaned["upi"],
                    "title_number": cleaned["title_number"],
                    "owner": owner["full_name"],
                    "owner_id": cleaned["owner_id"],
                    "district": cleaned["district"],
                    "size_sqm": f"{cleaned['size_sqm']:.2f}",
                    "land_use": cleaned["land_use"],
                    "record_hash": record_hash,
                },
                actor_id=g.user["id"],
                record_ref=cleaned["upi"],
            )
            cursor.execute(
                """
                INSERT INTO ownership_events
                    (land_id, event_type, old_owner_id, new_owner_id, legal_basis, legal_reference,
                     notes, actor_id, block_hash, created_at)
                VALUES (%s, 'registration', NULL, %s, 'Administrative correction', %s, %s, %s, %s, %s)
                """,
                (
                    land_id,
                    cleaned["owner_id"],
                    reference,
                    "Initial registration on the ledger.",
                    g.user["id"],
                    digest,
                    stamp,
                ),
            )
            ensure_active_ownership(cursor, land_id, cleaned["owner_id"], stamp)
            transaction_id = insert_transaction(
                cursor, land_id, g.user["id"], "registration", reference, stamp, digest
            )
            if upload and stored_name:
                insert_document(
                    cursor,
                    transaction_id,
                    "registration",
                    stored_name,
                    upload["file_hash"],
                    upload["original_name"],
                    stamp,
                )
            log_activity(
                "REGISTER_LAND",
                f"Registered {cleaned['upi']} for {owner['full_name']}.",
                g.user["id"],
                cursor,
                entity_type="land_parcel",
                entity_id=land_id,
            )
            return land_id
    except Exception:
        discard_upload(stored_name)
        raise


def apply_legal_update(land, cleaned, legal, changes, owner, upload, correction=False):
    if not changes:
        event_type = "correction"
    elif "owner_id" in changes:
        event_type = "transfer"
    elif any(field in changes for field in ("size_sqm", "province", "district", "sector", "cell", "village")):
        event_type = "boundary"
    elif "land_use" in changes:
        event_type = "use_change"
    else:
        event_type = "correction"
    payload_changes = {
        field: {"from": _plain(field, old), "to": _plain(field, new)} for field, (old, new) in changes.items()
    }
    record_hash = hash_from_cleaned(cleaned)
    status = "verified" if correction else "pending_update"
    action = "land_corrected" if correction else "land_updated"
    stamp = now_stamp()
    stored_name = persist_upload(upload)
    try:
        with db_cursor() as cursor:
            cursor.execute(
                """
                UPDATE land_parcels
                SET title_number = %s, owner_id = %s, province = %s, district = %s, sector = %s,
                    cell = %s, village = %s, size_sqm = %s, land_use = %s, description = %s,
                    status = %s, updated_at = %s, record_hash = %s
                WHERE id = %s
                """,
                (
                    cleaned["title_number"],
                    cleaned["owner_id"],
                    cleaned["province"],
                    cleaned["district"],
                    cleaned["sector"],
                    cleaned["cell"],
                    cleaned["village"],
                    f"{cleaned['size_sqm']:.2f}",
                    cleaned["land_use"],
                    cleaned["description"],
                    status,
                    stamp,
                    record_hash,
                    land["id"],
                ),
            )
            digest = add_block(
                cursor,
                action,
                {
                    "upi": land["upi"],
                    "event_type": event_type,
                    "changes": payload_changes,
                    "legal_basis": legal["legal_basis"],
                    "legal_reference": legal["legal_reference"],
                    "size_sqm": f"{cleaned['size_sqm']:.2f}",
                    "owner_id": cleaned["owner_id"],
                    "record_hash": record_hash,
                },
                actor_id=g.user["id"],
                record_ref=land["upi"],
            )
            cursor.execute(
                """
                INSERT INTO ownership_events
                    (land_id, event_type, old_owner_id, new_owner_id, legal_basis, legal_reference,
                     notes, actor_id, block_hash, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    land["id"],
                    event_type,
                    land["owner_id"] if cleaned["owner_id"] != land["owner_id"] else None,
                    cleaned["owner_id"],
                    legal["legal_basis"],
                    legal["legal_reference"],
                    legal["notes"],
                    g.user["id"],
                    digest,
                    stamp,
                ),
            )
            ensure_active_ownership(
                cursor,
                land["id"],
                cleaned["owner_id"],
                stamp,
                previous_owner_id=land["owner_id"],
            )
            transaction_id = insert_transaction(
                cursor,
                land["id"],
                g.user["id"],
                event_type,
                legal["legal_reference"],
                stamp,
                digest,
            )
            insert_document(
                cursor,
                transaction_id,
                "correction" if correction else "legal_update",
                stored_name,
                upload["file_hash"],
                upload["original_name"],
                stamp,
            )
            if correction:
                cursor.execute(
                    """
                    UPDATE verification_requests
                    SET outcome = 'resolved', reviewed_by = %s, reviewed_at = %s, notes = %s
                    WHERE parcel_id = %s AND outcome = 'mismatch'
                    """,
                    (g.user["id"], stamp, legal["notes"], land["id"]),
                )
            log_activity(
                "CORRECT_LAND" if correction else "UPDATE_LAND",
                f"{event_type} on {land['upi']} for {owner['full_name']} under {legal['legal_reference']}.",
                g.user["id"],
                cursor,
                entity_type="land_parcel",
                entity_id=land["id"],
            )
    except Exception:
        discard_upload(stored_name)
        raise
