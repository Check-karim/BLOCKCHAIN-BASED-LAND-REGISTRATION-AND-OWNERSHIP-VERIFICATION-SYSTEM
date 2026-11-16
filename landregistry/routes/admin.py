import re

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from mysql.connector import IntegrityError
from werkzeug.security import generate_password_hash

from ..auth import filter_query, page_window, roles_required
from ..blockchain import now_stamp, verify_chain
from ..db import execute, log_activity, query_all, query_one
from ..locations import ADMIN_DUTIES, LANDOWNER_DUTIES, OFFICIAL_DUTIES
from ..routes.public import _conflicts
from ..validators import collect_person, validate_password

bp = Blueprint("admin", __name__)


@bp.get("/users")
@roles_required("admin")
def users():
    clauses, params = [], []
    role = (request.args.get("role") or "").strip()
    query = (request.args.get("q") or "").strip()[:80]
    if role in {"admin", "official", "landowner"}:
        clauses.append("role = %s")
        params.append(role)
    if query:
        like = f"%{query.replace('%', '').replace('_', '')}%"
        clauses.append("(full_name LIKE %s OR username LIKE %s OR email LIKE %s)")
        params.extend([like, like, like])
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    total = query_one(f"SELECT COUNT(*) AS total FROM users {where}", params)["total"]
    page, pages, offset = page_window(total)
    rows = query_all(
        f"""
        SELECT id, full_name, username, role, email, phone, district, status, created_at
        FROM users {where}
        ORDER BY created_at DESC, id DESC
        LIMIT %s OFFSET %s
        """,
        [*params, 10, offset],
    )
    return render_template(
        "admin/users.html",
        users=rows,
        page=page,
        pages=pages,
        total=total,
        filter_query=filter_query(),
        filters={"q": query, "role": role},
    )


@bp.route("/users/new", methods=["GET", "POST"])
@roles_required("admin")
def new_user():
    errors = {}
    form = {}
    if request.method == "POST":
        form = request.form
        role = "official"
        field_errors, cleaned = collect_person(
            form,
            require_national_id=False,
            require_password=True,
        )
        errors.update(field_errors)
        if cleaned.get("username") == "admin":
            errors["username"] = "That username is reserved."
        if not errors:
            errors.update(_conflicts(cleaned))
        if not errors:
            role_id = query_one("SELECT id FROM roles WHERE role_name = 'official'")["id"]
            user_id = execute(
                """
                INSERT INTO users
                    (username, password_hash, full_name, email, phone, national_id, role, role_id, status,
                     province, district, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s)
                """,
                (
                    cleaned["username"],
                    generate_password_hash(cleaned["password"]),
                    cleaned["full_name"],
                    cleaned["email"],
                    cleaned["phone"],
                    cleaned["national_id"],
                    role,
                    role_id,
                    cleaned["province"],
                    cleaned["district"],
                    now_stamp(),
                ),
            )
            log_activity(
                "CREATE_USER",
                f"Created official account {cleaned['username']}.",
                g.user["id"],
                entity_type="user",
                entity_id=user_id,
            )
            flash("The official account has been created.", "success")
            return redirect(url_for("admin.users"))
    return render_template("admin/user_form.html", errors=errors, form=form, duties=OFFICIAL_DUTIES)


@bp.get("/users/<int:user_id>")
@roles_required("admin")
def user_detail(user_id):
    account = _account_or_404(user_id)
    return render_template(
        "admin/user_detail.html",
        account=account,
        errors={},
        duties=_duties(account["role"]),
        parcels=_parcels_for(account),
    )


@bp.post("/users/<int:user_id>/status")
@roles_required("admin")
def user_status(user_id):
    account = _account_or_404(user_id)
    if account["username"] == "admin" or account["id"] == g.user["id"]:
        flash("The predefined administrator account stays active.", "error")
        return redirect(url_for("admin.user_detail", user_id=account["id"]))
    status = "inactive" if account["status"] == "active" else "active"
    execute("UPDATE users SET status = %s WHERE id = %s", (status, account["id"]))
    log_activity("UPDATE_USER", f"Set {account['username']} to {status}.", g.user["id"])
    flash(f"{account['full_name']} is now {status}.", "success")
    return redirect(url_for("admin.user_detail", user_id=account["id"]))


@bp.post("/users/<int:user_id>/password")
@roles_required("admin")
def user_password(user_id):
    account = _account_or_404(user_id)
    if account["username"] == "admin":
        flash("The predefined administrator password stays admin.", "error")
        return redirect(url_for("admin.user_detail", user_id=account["id"]))
    password = request.form.get("password") or ""
    confirm = request.form.get("confirm_password") or ""
    errors = {}
    password_error = validate_password(password)
    if password_error:
        errors["password"] = password_error
    elif password != confirm:
        errors["confirm_password"] = "Passwords do not match."
    if errors:
        return render_template(
            "admin/user_detail.html",
            account=account,
            errors=errors,
            duties=_duties(account["role"]),
            parcels=_parcels_for(account),
        )
    execute(
        "UPDATE users SET password_hash = %s WHERE id = %s",
        (generate_password_hash(password), account["id"]),
    )
    log_activity("RESET_PASSWORD", f"Reset the password for {account['username']}.", g.user["id"])
    flash("Password updated. It is at least 6 characters.", "success")
    return redirect(url_for("admin.user_detail", user_id=account["id"]))


@bp.get("/activity")
@roles_required("admin")
def activity():
    query = (request.args.get("q") or "").strip()[:80]
    clauses, params = [], []
    if query:
        like = f"%{query.replace('%', '').replace('_', '')}%"
        clauses.append("(a.action LIKE %s OR a.details LIKE %s OR u.full_name LIKE %s OR u.username LIKE %s)")
        params.extend([like, like, like, like])
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    total = query_one(
        f"SELECT COUNT(*) AS total FROM activity_logs a LEFT JOIN users u ON u.id = a.user_id {where}",
        params,
    )["total"]
    page, pages, offset = page_window(total, per_page=15)
    rows = query_all(
        f"""
        SELECT a.created_at, a.action, a.details, a.ip_address, a.entity_type, a.entity_id,
               u.full_name AS person, u.username
        FROM activity_logs a
        LEFT JOIN users u ON u.id = a.user_id
        {where}
        ORDER BY a.created_at DESC, a.id DESC
        LIMIT %s OFFSET %s
        """,
        [*params, 15, offset],
    )
    summary = query_all(
        "SELECT action, COUNT(*) AS total FROM activity_logs GROUP BY action ORDER BY total DESC, action ASC"
    )
    return render_template(
        "admin/activity.html",
        rows=rows,
        summary=summary,
        page=page,
        pages=pages,
        total=total,
        filter_query=filter_query(),
        query=query,
    )


@bp.get("/ledger")
@roles_required("admin", "official")
def ledger():
    blocks = query_all(
        """
        SELECT b.*, u.full_name AS actor_name
        FROM blocks b
        LEFT JOIN users u ON u.id = b.actor_id
        ORDER BY b.block_index ASC
        """
    )
    valid, broken = verify_chain(blocks)
    return render_template("admin/ledger.html", blocks=list(reversed(blocks)), valid=valid, broken=broken)


@bp.route("/minaloc", methods=["GET", "POST"])
@roles_required("admin", "official")
def minaloc():
    if request.method == "POST" and g.user["role"] != "admin":
        abort(403)
    errors = {}
    form = {}
    if request.method == "POST":
        form = request.form
        errors, cleaned = _policy_fields(form)
        if not errors:
            try:
                execute(
                    """
                    INSERT INTO policies (title, reference_code, body, recorded_by, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        cleaned["title"],
                        cleaned["reference_code"],
                        cleaned["body"],
                        g.user["id"],
                        now_stamp(),
                    ),
                )
            except IntegrityError:
                errors["reference_code"] = "That reference is already recorded."
            else:
                log_activity(
                    "RECORD_POLICY",
                    f"Recorded policy {cleaned['reference_code']}.",
                    g.user["id"],
                    entity_type="policy",
                )
                flash("The instruction is recorded in this demo. It is not sent to MINALOC.", "success")
                return redirect(url_for("admin.minaloc"))
    policies = query_all(
        """
        SELECT p.*, u.full_name AS recorder
        FROM policies p
        JOIN users u ON u.id = p.recorded_by
        ORDER BY p.created_at DESC, p.id DESC
        """
    )
    saved = query_all(
        """
        SELECT r.*, u.full_name AS author
        FROM reports r
        JOIN users u ON u.id = r.generated_by
        ORDER BY r.generated_at DESC, r.id DESC
        LIMIT 8
        """
    )
    return render_template("admin/minaloc.html", policies=policies, saved=saved, errors=errors, form=form)


def _policy_fields(form):
    title = " ".join((form.get("title") or "").split())
    reference = (form.get("reference_code") or "").strip().upper()
    body = " ".join((form.get("body") or "").split())
    errors = {}
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ,.'/-]{3,119}", title):
        errors["title"] = "Enter a title using letters, numbers, and simple punctuation."
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/-]{2,39}", reference):
        errors["reference_code"] = "Use letters, numbers, / or -."
    if len(body) < 20 or len(body) > 1000:
        errors["body"] = "The instruction must be between 20 and 1000 characters."
    return errors, {"title": title, "reference_code": reference, "body": body}


def _parcels_for(account):
    if account["role"] != "landowner":
        return []
    return query_all(
        """
        SELECT id, upi, title_number, district, land_use, status
        FROM land_parcels
        WHERE owner_id = %s
        ORDER BY created_at DESC, id DESC
        """,
        (account["id"],),
    )


def _duties(role):
    return {"admin": ADMIN_DUTIES, "official": OFFICIAL_DUTIES, "landowner": LANDOWNER_DUTIES}.get(role, [])


def _account_or_404(user_id):
    account = query_one("SELECT * FROM users WHERE id = %s", (user_id,))
    if not account:
        abort(404)
    return account
