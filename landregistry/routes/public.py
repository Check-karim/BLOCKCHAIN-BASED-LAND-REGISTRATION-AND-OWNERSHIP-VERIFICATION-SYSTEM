from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from ..auth import safe_next
from ..db import db_cursor, log_activity, query_one
from ..records import insert_landowner
from ..validators import collect_person

bp = Blueprint("public", __name__)

DUMMY_HASH = generate_password_hash("not-a-real-password")


@bp.get("/")
def home():
    stats = {
        "parcels": query_one("SELECT COUNT(*) AS n FROM land_parcels")["n"],
        "owners": query_one("SELECT COUNT(*) AS n FROM users WHERE role = 'landowner' AND status = 'active'")["n"],
        "verified": query_one("SELECT COUNT(*) AS n FROM land_parcels WHERE status = 'verified'")["n"],
        "blocks": query_one("SELECT COUNT(*) AS n FROM blocks")["n"],
    }
    latest = query_one(
        """
        SELECT b.block_index, b.action, b.record_ref, b.block_hash, b.created_at, u.full_name AS actor_name
        FROM blocks b
        LEFT JOIN users u ON u.id = b.actor_id
        WHERE b.action <> 'genesis'
        ORDER BY b.block_index DESC
        LIMIT 1
        """
    )
    return render_template("home.html", stats=stats, latest=latest)


@bp.get("/about")
def about():
    return render_template("about.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("dashboard.home"))
    next_url = safe_next(request.values.get("next"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        user = query_one("SELECT * FROM users WHERE username = %s", (username,))
        if not user:
            check_password_hash(DUMMY_HASH, password)
            flash("Invalid username or password.", "error")
        elif not check_password_hash(user["password_hash"], password):
            flash("Invalid username or password.", "error")
        elif user["status"] != "active":
            flash("This account is inactive. Contact the administrator.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            session.permanent = True
            log_activity("LOGIN", f"{user['username']} signed in.", user["id"])
            flash(f"Welcome, {user['full_name']}.", "success")
            return redirect(next_url or url_for("dashboard.home"))
    return render_template("login.html", next_url=next_url or "", form=request.form)


@bp.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("dashboard.home"))
    errors = {}
    if request.method == "POST":
        errors, cleaned = collect_person(request.form, require_national_id=True, require_password=True)
        if cleaned.get("username") == "admin":
            errors["username"] = "That username is reserved."
        if not errors:
            errors.update(_conflicts(cleaned))
        if not errors:
            with db_cursor() as cursor:
                cursor.execute("SELECT id FROM roles WHERE role_name = 'landowner'")
                role_id = cursor.fetchone()["id"]
                cursor.execute(
                    """
                    INSERT INTO users
                        (username, password_hash, full_name, email, phone, national_id, role, role_id, status,
                         province, district, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, 'landowner', %s, 'active', %s, %s, %s)
                    """,
                    (
                        cleaned["username"],
                        generate_password_hash(cleaned["password"]),
                        cleaned["full_name"],
                        cleaned["email"],
                        cleaned["phone"],
                        cleaned["national_id"],
                        role_id,
                        cleaned["province"],
                        cleaned["district"],
                        _stamp(),
                    ),
                )
                user_id = cursor.lastrowid
                insert_landowner(
                    cursor,
                    user_id,
                    cleaned["national_id"],
                    cleaned["phone"],
                    f"{cleaned['district']}, {cleaned['province']}",
                )
                log_activity(
                    "REGISTER",
                    f"Landowner {cleaned['username']} opened an account.",
                    user_id,
                    cursor,
                    entity_type="user",
                    entity_id=user_id,
                )
            flash("Account created. You can sign in.", "success")
            return redirect(url_for("public.login"))
    return render_template("register.html", errors=errors, form=request.form)


@bp.post("/logout")
def logout():
    if g.user:
        log_activity("LOGOUT", f"{g.user['username']} signed out.", g.user["id"])
    session.clear()
    flash("You have signed out.", "success")
    return redirect(url_for("public.home"))


def _conflicts(cleaned, exclude_id=None):
    errors = {}
    checks = (
        ("username", cleaned.get("username"), "That username is already in use."),
        ("email", cleaned.get("email"), "That email address is already in use."),
        ("phone", cleaned.get("phone"), "That phone number is already in use."),
        ("national_id", cleaned.get("national_id"), "That national ID is already in use."),
    )
    for field, value, message in checks:
        if not value:
            continue
        row = query_one(
            f"SELECT id FROM users WHERE {field} = %s AND id <> %s",
            (value, exclude_id or 0),
        )
        if row:
            errors[field] = message
    return errors


def _stamp():
    from ..blockchain import now_stamp

    return now_stamp()
