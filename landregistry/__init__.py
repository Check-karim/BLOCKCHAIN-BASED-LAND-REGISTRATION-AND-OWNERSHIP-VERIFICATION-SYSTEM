import json
import secrets
from datetime import timedelta
from decimal import Decimal

import mysql.connector
from flask import Flask, abort, current_app, g, render_template, request, session

from .blockchain import verify_chain
from .config import SECRET_KEY
from .db import connect, init_db, query_one
from .locations import ACTION_LABELS, LAND_USES, LEGAL_BASES, PROVINCES, ROLE_LABELS, STATUS_LABELS


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = SECRET_KEY
    app.permanent_session_lifetime = timedelta(hours=8)
    app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024
    app.config["DB_ERROR"] = None
    try:
        init_db()
    except Exception as exc:
        app.config["DB_ERROR"] = str(exc)

    @app.before_request
    def prepare_request():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_hex(16)
        if request.endpoint == "static":
            return None
        if current_app.config.get("DB_ERROR"):
            return render_template("db_error.html", error=current_app.config["DB_ERROR"]), 500
        try:
            g.db = connect()
        except mysql.connector.Error as exc:
            return render_template("db_error.html", error=str(exc)), 500
        g.user = None
        user_id = session.get("user_id")
        if user_id:
            user = query_one("SELECT * FROM users WHERE id = %s", (user_id,))
            if user and user["status"] == "active":
                g.user = user
            else:
                session.pop("user_id", None)
        if request.method == "POST" and request.form.get("csrf_token") != session.get("csrf_token"):
            abort(400)
        return None

    @app.teardown_request
    def close_db(_exc):
        connection = g.pop("db", None)
        if connection is not None:
            connection.close()

    @app.context_processor
    def inject_globals():
        return {
            "csrf_token": session.get("csrf_token", ""),
            "current_user": getattr(g, "user", None),
            "locations": PROVINCES,
            "land_uses": LAND_USES,
            "legal_bases": LEGAL_BASES,
            "role_labels": ROLE_LABELS,
        }

    @app.template_filter("label")
    def label(value):
        if value is None:
            return ""
        key = str(value)
        named = STATUS_LABELS.get(key) or ROLE_LABELS.get(key) or ACTION_LABELS.get(key)
        if named:
            return named
        return key.replace("_", " ").title()

    @app.template_filter("when")
    def when(value):
        if not value:
            return "—"
        if hasattr(value, "strftime"):
            return value.strftime("%d %b %Y, %H:%M")
        return str(value)

    @app.template_filter("sqm")
    def sqm(value):
        return f"{Decimal(value):,.2f} m²"

    @app.template_filter("short_hash")
    def short_hash(value):
        text = value or ""
        if len(text) < 18:
            return text
        return f"{text[:10]}…{text[-6:]}"

    @app.template_filter("pretty_json")
    def pretty_json(value):
        try:
            return json.dumps(json.loads(value), indent=2)
        except (TypeError, ValueError):
            return value or ""

    @app.errorhandler(400)
    def bad_request(_exc):
        return render_template("errors/400.html"), 400

    @app.errorhandler(413)
    def too_large(_exc):
        return render_template("errors/413.html"), 413

    @app.errorhandler(403)
    def forbidden(_exc):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def missing(_exc):
        return render_template("errors/404.html"), 404

    from .routes.admin import bp as admin_bp
    from .routes.dashboard import bp as dashboard_bp
    from .routes.lands import bp as lands_bp
    from .routes.public import bp as public_bp
    from .routes.reports import bp as reports_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(lands_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(reports_bp)

    @app.get("/health")
    def health():
        blocks = []
        try:
            from .db import query_all

            blocks = query_all(
                "SELECT block_index, created_at, action, payload, previous_hash, block_hash FROM blocks ORDER BY block_index ASC"
            )
        except Exception:
            pass
        valid, broken = verify_chain(blocks) if blocks else (False, None)
        return {"ok": current_app.config.get("DB_ERROR") is None, "chain": valid, "broken_at": broken}

    return app
