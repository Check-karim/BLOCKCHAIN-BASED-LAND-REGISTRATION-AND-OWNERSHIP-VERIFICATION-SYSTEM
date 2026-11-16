from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from ..auth import page_window, roles_required
from ..db import query_all, query_one
from ..records import read_upload
from ..routes.lands import _bundle, _changes, _form_from_land, _land_or_404, _owners, apply_legal_update
from ..validators import collect_land, collect_legal

bp = Blueprint("ownership", __name__)


@bp.get("/ownership/reviews")
@roles_required("official", "admin")
def reviews():
    total = query_one(
        "SELECT COUNT(*) AS total FROM verification_requests WHERE outcome = 'mismatch'"
    )["total"]
    page, pages, offset = page_window(total)
    rows = query_all(
        """
        SELECT v.id, v.parcel_number, v.created_at, v.record_hash, v.chain_hash, u.full_name AS requester
        FROM verification_requests v
        JOIN users u ON u.id = v.requested_by
        WHERE v.outcome = 'mismatch'
        ORDER BY v.created_at ASC, v.id ASC
        LIMIT %s OFFSET %s
        """,
        (10, offset),
    )
    return render_template("ownership/reviews.html", rows=rows, page=page, pages=pages, total=total)


@bp.route("/ownership/reviews/<int:request_id>", methods=["GET", "POST"])
@roles_required("official", "admin")
def review(request_id):
    if request.method == "POST" and g.user["role"] != "official":
        abort(403)
    item = _request_or_404(request_id)
    if not item["parcel_id"]:
        abort(404)
    land = _land_or_404(item["parcel_id"])
    errors = {}
    form = _form_from_land(land)
    owners = _owners(land["owner_id"])
    if request.method == "POST":
        form = request.form
        errors, cleaned = collect_land(form)
        legal_errors, legal = collect_legal(form)
        errors.update(legal_errors)
        upload_error, upload = read_upload(request.files.get("document"), required=True)
        if upload_error:
            errors["document"] = upload_error
        if not errors and cleaned.get("upi") != land["upi"]:
            errors["upi"] = "The UPI cannot be changed."
        owner = None
        if "owner_id" not in errors:
            owner = query_one(
                """
                SELECT id, full_name FROM users
                WHERE id = %s AND role = 'landowner' AND (status = 'active' OR id = %s)
                """,
                (cleaned.get("owner_id"), land["owner_id"]),
            )
            if not owner:
                errors["owner_id"] = "Select an active landowner."
        if not errors and query_one(
            "SELECT id FROM land_parcels WHERE title_number = %s AND id <> %s",
            (cleaned["title_number"], land["id"]),
        ):
            errors["title_number"] = "That title number is already on the register."
        changes = _changes(land, cleaned) if not errors else {}
        if not errors:
            apply_legal_update(land, cleaned, legal, changes, owner, upload, correction=True)
            flash("The corrected record is sealed. The earlier block stays in the ledger.", "success")
            return redirect(url_for("lands.detail", land_id=land["id"]))
    return render_template(
        "ownership/review.html",
        item=item,
        land=land,
        errors=errors,
        form=form,
        owners=owners,
        bundle=_bundle(land),
    )


def _request_or_404(request_id):
    row = query_one(
        """
        SELECT v.*, u.full_name AS requester_name
        FROM verification_requests v
        JOIN users u ON u.id = v.requested_by
        WHERE v.id = %s
        """,
        (request_id,),
    )
    if not row:
        abort(404)
    return row

