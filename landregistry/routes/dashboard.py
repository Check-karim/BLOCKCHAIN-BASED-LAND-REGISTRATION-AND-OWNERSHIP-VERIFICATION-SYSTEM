from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from ..auth import login_required
from ..blockchain import verify_chain
from ..db import execute, log_activity, query_all, query_one
from ..routes.public import _conflicts
from ..validators import (
    validate_email,
    validate_name,
    validate_national_id,
    validate_password,
    validate_phone,
    validate_location,
)

bp = Blueprint("dashboard", __name__)


@bp.get("/dashboard")
@login_required
def home():
    user = g.user
    stats = {}
    recent_lands = []
    recent_activity = []
    if user["role"] == "landowner":
        stats = {
            "parcels": _count("SELECT COUNT(*) AS n FROM land_parcels WHERE owner_id = %s", (user["id"],)),
            "verified": _count("SELECT COUNT(*) AS n FROM land_parcels WHERE owner_id = %s AND status = 'verified'", (user["id"],)),
            "pending": _count("SELECT COUNT(*) AS n FROM land_parcels WHERE owner_id = %s AND status = 'pending_approval'", (user["id"],)),
            "titled": _count("SELECT COUNT(*) AS n FROM land_parcels WHERE owner_id = %s AND upi IS NOT NULL", (user["id"],)),
            "area": _count("SELECT COALESCE(SUM(size_sqm), 0) AS n FROM land_parcels WHERE owner_id = %s", (user["id"],)),
        }
        recent_lands = query_all(
            """
            SELECT id, upi, title_number, district, land_use, size_sqm, status
            FROM land_parcels WHERE owner_id = %s
            ORDER BY updated_at DESC LIMIT 6
            """,
            (user["id"],),
        )
    else:
        stats = {
            "parcels": _count("SELECT COUNT(*) AS n FROM land_parcels"),
            "owners": _count("SELECT COUNT(*) AS n FROM users WHERE role = 'landowner'"),
            "pending": _count("SELECT COUNT(*) AS n FROM land_parcels WHERE status = 'pending_approval'"),
            "verified": _count("SELECT COUNT(*) AS n FROM land_parcels WHERE status = 'verified'"),
            "mismatches": _count("SELECT COUNT(*) AS n FROM verification_requests WHERE outcome = 'mismatch'"),
        }
        recent_lands = query_all(
            """
            SELECT l.id, l.upi, l.title_number, l.district, l.land_use, l.size_sqm, l.status, u.full_name AS owner_name
            FROM land_parcels l
            JOIN users u ON u.id = l.owner_id
            ORDER BY l.updated_at DESC LIMIT 6
            """
        )
        if user["role"] == "admin":
            stats["users"] = _count("SELECT COUNT(*) AS n FROM users")
            stats["blocks"] = _count("SELECT COUNT(*) AS n FROM blocks")
            recent_activity = query_all(
                """
                SELECT a.created_at, a.action, a.details, u.full_name AS person
                FROM activity_logs a
                LEFT JOIN users u ON u.id = a.user_id
                ORDER BY a.created_at DESC, a.id DESC LIMIT 8
                """
            )
    chain_ok = True
    if user["role"] == "admin":
        blocks = query_all("SELECT block_index, created_at, action, payload, previous_hash, block_hash FROM blocks ORDER BY block_index ASC")
        chain_ok, _broken = verify_chain(blocks)
    return render_template(
        "dashboard.html",
        stats=stats,
        recent_lands=recent_lands,
        recent_activity=recent_activity,
        chain_ok=chain_ok,
    )


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    errors = {}
    form = _profile_form(g.user)
    if request.method == "POST":
        form = request.form
        errors, cleaned = _profile_errors(form, g.user)
        if not errors:
            execute(
                """
                UPDATE users
                SET full_name = %s, email = %s, phone = %s, national_id = %s, province = %s, district = %s
                WHERE id = %s
                """,
                (
                    cleaned["full_name"],
                    cleaned["email"],
                    cleaned["phone"],
                    cleaned["national_id"],
                    cleaned["province"],
                    cleaned["district"],
                    g.user["id"],
                ),
            )
            if g.user["role"] == "landowner":
                execute(
                    """
                    UPDATE landowners
                    SET national_id = %s, phone = %s, address = %s
                    WHERE user_id = %s
                    """,
                    (
                        cleaned["national_id"],
                        cleaned["phone"],
                        f"{cleaned['district']}, {cleaned['province']}",
                        g.user["id"],
                    ),
                )
            log_activity(
                "UPDATE_PROFILE",
                f"{g.user['username']} updated their profile.",
                g.user["id"],
                entity_type="user",
                entity_id=g.user["id"],
            )
            flash("Profile updated.", "success")
            return redirect(url_for("dashboard.profile"))
    return render_template("profile.html", errors=errors, form=form)


@bp.post("/profile/password")
@login_required
def profile_password():
    if g.user["username"] == "admin":
        flash("The predefined administrator password stays admin.", "error")
        return redirect(url_for("dashboard.profile"))
    errors = {}
    current = request.form.get("current_password") or ""
    new_password = request.form.get("password") or ""
    confirm = request.form.get("confirm_password") or ""
    if not check_password_hash(g.user["password_hash"], current):
        errors["current_password"] = "Current password is incorrect."
    password_error = validate_password(new_password)
    if password_error:
        errors["password"] = password_error
    elif new_password != confirm:
        errors["confirm_password"] = "Passwords do not match."
    if errors:
        flash("The password was not changed.", "error")
        return render_template("profile.html", errors=errors, form=_profile_form(g.user), password_errors=errors)
    execute(
        "UPDATE users SET password_hash = %s WHERE id = %s",
        (generate_password_hash(new_password), g.user["id"]),
    )
    log_activity("RESET_PASSWORD", f"{g.user['username']} changed their password.", g.user["id"])
    flash("Password updated.", "success")
    return redirect(url_for("dashboard.profile"))


def _count(sql, params=()):
    return query_one(sql, params)["n"]


def _profile_form(user):
    return {
        "full_name": user["full_name"],
        "email": user["email"],
        "phone": user["phone"],
        "national_id": user["national_id"] or "",
        "province": user["province"] or "",
        "district": user["district"] or "",
        "username": user["username"],
    }


def _profile_errors(form, user):
    errors = {}
    cleaned = {}
    error, full_name = validate_name(form.get("full_name"), "Name")
    if error:
        errors["full_name"] = error
    else:
        cleaned["full_name"] = full_name
    error, email = validate_email(form.get("email"))
    if error:
        errors["email"] = error
    else:
        cleaned["email"] = email
    error, phone = validate_phone(form.get("phone"))
    if error:
        errors["phone"] = error
    else:
        cleaned["phone"] = phone
    error, national_id = validate_national_id(form.get("national_id"), required=user["role"] == "landowner")
    if error:
        errors["national_id"] = error
    else:
        cleaned["national_id"] = national_id
    location_errors, province, district = validate_location(form.get("province"), form.get("district"))
    errors.update(location_errors)
    cleaned["province"] = province
    cleaned["district"] = district
    if not errors:
        errors.update(
            _conflicts(
                {"email": cleaned["email"], "phone": cleaned["phone"], "national_id": cleaned["national_id"]},
                exclude_id=user["id"],
            )
        )
    return errors, cleaned
