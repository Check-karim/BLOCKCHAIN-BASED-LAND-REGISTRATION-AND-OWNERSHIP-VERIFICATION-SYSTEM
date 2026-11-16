from functools import wraps
from urllib.parse import urlencode, urlparse

from flask import abort, g, redirect, request, url_for


def safe_next(target):
    if not target or len(target) > 200:
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return None
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        return None
    return target


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not getattr(g, "user", None):
            return redirect(url_for("public.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not getattr(g, "user", None):
                return redirect(url_for("public.login", next=request.path))
            if g.user["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def page_window(total, per_page=10):
    try:
        page = int(request.args.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    page = max(page, 1)
    pages = max(1, (total + per_page - 1) // per_page)
    if page > pages:
        page = pages
    return page, pages, (page - 1) * per_page


def filter_query():
    raw = request.args.to_dict(flat=True)
    raw.pop("page", None)
    return urlencode(raw)
