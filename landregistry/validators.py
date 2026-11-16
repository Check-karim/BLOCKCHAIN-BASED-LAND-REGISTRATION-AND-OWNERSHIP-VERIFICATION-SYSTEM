import re
from decimal import Decimal, InvalidOperation

from .locations import LAND_USES, LEGAL_BASES, PROVINCES

MIN_PASSWORD = 6
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def validate_username(username):
    raw = (username or "").strip()
    if not raw:
        return "Username is required.", ""
    if any(character.isspace() for character in raw):
        return "Username cannot contain spaces.", raw
    if not raw[0].isalpha():
        return "Username cannot start with a number or special character.", raw
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", raw):
        return "Username cannot contain special characters.", raw
    if len(raw) < 4 or len(raw) > 30:
        return "Username must be between 4 and 30 characters.", raw
    return None, raw.lower()


def validate_name(name, label="Name"):
    cleaned = " ".join((name or "").split())
    if not cleaned:
        return f"{label} is required.", ""
    if not re.fullmatch(r"[A-Za-z]+(?: [A-Za-z]+)*", cleaned):
        return f"{label} cannot contain numbers or special characters.", cleaned
    if len(cleaned) < 2 or len(cleaned) > 80:
        return f"{label} must be between 2 and 80 characters.", cleaned
    return None, cleaned


def validate_email(email):
    cleaned = (email or "").strip().lower()
    if not cleaned or not EMAIL_RE.fullmatch(cleaned) or len(cleaned) > 120:
        return "Enter a valid email address.", cleaned
    return None, cleaned


def validate_phone(phone):
    compact = re.sub(r"[\s\-()]", "", (phone or "").strip())
    if compact.startswith("+250"):
        compact = "0" + compact[4:]
    if not re.fullmatch(r"07[2389]\d{7}", compact):
        return "Enter a valid Rwandan phone number, such as 0781234567 or +250781234567.", ""
    return None, compact


def validate_password(password):
    if not password:
        return "Password is required."
    if len(password) < MIN_PASSWORD:
        return "Password must not be less than 6 characters."
    if len(password) > 72:
        return "Password must be at most 72 characters."
    return None


def validate_national_id(value, required=False):
    cleaned = re.sub(r"\s", "", (value or "").strip())
    if not cleaned:
        if required:
            return "National ID is required for landowners.", None
        return None, None
    if not re.fullmatch(r"\d{16}", cleaned):
        return "National ID must be 16 digits.", None
    return None, cleaned


def validate_place(value, label):
    cleaned = " ".join((value or "").split())
    if not cleaned:
        return f"{label} is required.", ""
    if not re.fullmatch(r"[A-Za-z]+(?: [A-Za-z]+)*", cleaned):
        return f"{label} cannot contain numbers or special characters.", cleaned
    if len(cleaned) > 60:
        return f"{label} must be 60 characters or fewer.", cleaned
    return None, cleaned


def validate_location(province, district):
    errors = {}
    province = (province or "").strip()
    district = (district or "").strip()
    if province not in PROVINCES:
        errors["province"] = "Select a province."
    elif district not in PROVINCES[province]:
        errors["district"] = "Select a district in the chosen province."
    return errors, province, district


def validate_upi(value):
    cleaned = (value or "").strip()
    if not re.fullmatch(r"\d{1,2}(?:/\d{1,5}){4}", cleaned):
        return "UPI must look like 1/03/07/04/2581.", ""
    return None, cleaned


def validate_title(value):
    cleaned = (value or "").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9-]{3,29}", cleaned):
        return "Title number must start with a letter and use only letters, numbers, and hyphens.", ""
    return None, cleaned


def validate_size(value):
    raw = (value or "").strip()
    if not raw:
        return "Size is required.", None
    try:
        size = Decimal(raw)
    except InvalidOperation:
        return "Enter the size in square metres, such as 450 or 450.5.", None
    if size <= 0 or size > Decimal("100000000"):
        return "Size must be greater than 0.", None
    return None, size.quantize(Decimal("0.01"))


def validate_notes(value, minimum=15, label="Explanation"):
    cleaned = " ".join((value or "").split())
    if len(cleaned) < minimum:
        return f"{label} must be at least {minimum} characters.", ""
    if len(cleaned) > 500:
        return f"{label} must be 500 characters or fewer.", cleaned
    return None, cleaned


def validate_reference(value):
    cleaned = (value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/-]{2,39}", cleaned):
        return "Enter the legal document reference using letters, numbers, / or -.", ""
    return None, cleaned


def validate_description(value):
    cleaned = " ".join((value or "").split())
    if len(cleaned) > 800:
        return "Description must be 800 characters or fewer.", cleaned[:800]
    return None, cleaned or None


def collect_person(form, *, require_national_id=False, require_password=True):
    errors = {}
    cleaned = {}

    error, username = validate_username(form.get("username"))
    if error:
        errors["username"] = error
    else:
        cleaned["username"] = username

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

    error, national_id = validate_national_id(form.get("national_id"), required=require_national_id)
    if error:
        errors["national_id"] = error
    else:
        cleaned["national_id"] = national_id

    location_errors, province, district = validate_location(form.get("province"), form.get("district"))
    errors.update(location_errors)
    cleaned["province"] = province
    cleaned["district"] = district

    if require_password:
        password = form.get("password") or ""
        confirm = form.get("confirm_password") or ""
        password_error = validate_password(password)
        if password_error:
            errors["password"] = password_error
        elif password != confirm:
            errors["confirm_password"] = "Passwords do not match."
        else:
            cleaned["password"] = password

    return errors, cleaned


def collect_land(form, *, title_required=True):
    errors = {}
    cleaned = {}

    error, upi = validate_upi(form.get("upi"))
    if error:
        errors["upi"] = error
    else:
        cleaned["upi"] = upi

    if title_required:
        error, title_number = validate_title(form.get("title_number"))
        if error:
            errors["title_number"] = error
        else:
            cleaned["title_number"] = title_number

    location_errors, province, district = validate_location(form.get("province"), form.get("district"))
    errors.update(location_errors)
    cleaned["province"] = province
    cleaned["district"] = district

    for field, label in (("sector", "Sector"), ("cell", "Cell"), ("village", "Village")):
        error, value = validate_place(form.get(field), label)
        if error:
            errors[field] = error
        else:
            cleaned[field] = value

    error, size_sqm = validate_size(form.get("size_sqm"))
    if error:
        errors["size_sqm"] = error
    else:
        cleaned["size_sqm"] = size_sqm

    land_use = (form.get("land_use") or "").strip()
    if land_use not in LAND_USES:
        errors["land_use"] = "Select a land use."
    else:
        cleaned["land_use"] = land_use

    error, description = validate_description(form.get("description"))
    if error:
        errors["description"] = error
    else:
        cleaned["description"] = description

    owner_raw = (form.get("owner_id") or "").strip()
    if not owner_raw.isdigit():
        errors["owner_id"] = "Select the landowner."
    else:
        cleaned["owner_id"] = int(owner_raw)

    return errors, cleaned


def collect_application(form):
    errors = {}
    cleaned = {}
    location_errors, province, district = validate_location(form.get("province"), form.get("district"))
    errors.update(location_errors)
    cleaned["province"] = province
    cleaned["district"] = district
    for field, label in (("sector", "Sector"), ("cell", "Cell"), ("village", "Village")):
        error, value = validate_place(form.get(field), label)
        if error:
            errors[field] = error
        else:
            cleaned[field] = value
    error, size_sqm = validate_size(form.get("size_sqm"))
    if error:
        errors["size_sqm"] = error
    else:
        cleaned["size_sqm"] = size_sqm
    land_use = (form.get("land_use") or "").strip()
    if land_use not in LAND_USES:
        errors["land_use"] = "Select a land use."
    else:
        cleaned["land_use"] = land_use
    error, description = validate_description(form.get("description"))
    if error:
        errors["description"] = error
    else:
        cleaned["description"] = description
    return errors, cleaned


def collect_legal(form):
    errors = {}
    cleaned = {}
    basis = (form.get("legal_basis") or "").strip()
    if basis not in LEGAL_BASES:
        errors["legal_basis"] = "Select the legal basis for this update."
    else:
        cleaned["legal_basis"] = basis

    error, reference = validate_reference(form.get("legal_reference"))
    if error:
        errors["legal_reference"] = error
    else:
        cleaned["legal_reference"] = reference

    error, notes = validate_notes(form.get("notes"), minimum=15, label="The legal explanation")
    if error:
        errors["notes"] = error
    else:
        cleaned["notes"] = notes
    return errors, cleaned
