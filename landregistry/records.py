import hashlib
import json
import secrets
from decimal import Decimal
from pathlib import Path

from werkzeug.utils import secure_filename

from .blockchain import land_record_hash
from .db import query_one
from .locations import PROVINCE_CODES, PROVINCES

UPLOAD_DIR = Path(__file__).resolve().parent / "uploads"
MAX_BYTES = 5 * 1024 * 1024
ALLOWED = {".pdf", ".png", ".jpg", ".jpeg"}


def record_hash_for(values):
    return land_record_hash(
        values["upi"],
        values["title_number"],
        values["owner_id"],
        values["province"],
        values["district"],
        values["sector"],
        values["cell"],
        values["village"],
        values["size_sqm"],
        values["land_use"],
    )


def hash_from_land(land):
    return record_hash_for(
        {
            "upi": land["upi"],
            "title_number": land["title_number"],
            "owner_id": land["owner_id"],
            "province": land["province"],
            "district": land["district"],
            "sector": land["sector"],
            "cell": land["cell"],
            "village": land["village"],
            "size_sqm": f"{Decimal(land['size_sqm']):.2f}",
            "land_use": land["land_use"],
        }
    )


def hash_from_cleaned(cleaned):
    return record_hash_for(
        {
            "upi": cleaned["upi"],
            "title_number": cleaned["title_number"],
            "owner_id": cleaned["owner_id"],
            "province": cleaned["province"],
            "district": cleaned["district"],
            "sector": cleaned["sector"],
            "cell": cleaned["cell"],
            "village": cleaned["village"],
            "size_sqm": f"{cleaned['size_sqm']:.2f}",
            "land_use": cleaned["land_use"],
        }
    )


def chain_record_hash(upi):
    row = query_one(
        """
        SELECT payload FROM blocks
        WHERE record_ref = %s AND action IN ('land_registered', 'land_updated', 'land_corrected')
        ORDER BY block_index DESC
        LIMIT 1
        """,
        (upi,),
    )
    if not row:
        return None
    try:
        payload = json.loads(row["payload"])
    except (TypeError, ValueError):
        return None
    return payload.get("record_hash")


def latest_seal(upi):
    row = query_one(
        """
        SELECT block_index, action, payload, block_hash, created_at
        FROM blocks
        WHERE record_ref = %s AND action IN ('land_registered', 'land_updated', 'land_corrected')
        ORDER BY block_index DESC
        LIMIT 1
        """,
        (upi,),
    )
    if not row:
        return None
    try:
        row["body"] = json.loads(row["payload"])
    except (TypeError, ValueError):
        row["body"] = {}
    return row


def compare_land(land, chain_hash=None):
    current = hash_from_land(land)
    if chain_hash is None:
        chain_hash = chain_record_hash(land["upi"])
    reference = chain_hash or land.get("record_hash")
    return {
        "matched": bool(reference) and current == reference,
        "record_hash": current,
        "chain_hash": reference or "",
    }


def read_upload(storage, required=True):
    if storage is None or not (storage.filename or "").strip():
        if required:
            return "Upload a supporting document (PDF, PNG, or JPG, up to 5 MB).", None
        return None, None
    ext = Path(storage.filename).suffix.lower()
    if ext not in ALLOWED:
        return "Upload a PDF, PNG, or JPG.", None
    data = storage.read()
    if len(data) > MAX_BYTES:
        return "The document must be 5 MB or smaller.", None
    if not _matches(data, ext):
        return "That file is not a valid PDF, PNG, or JPG.", None
    original = secure_filename(storage.filename) or f"document{ext}"
    return None, {
        "data": data,
        "ext": ext,
        "original_name": original[:180],
        "file_hash": hashlib.sha256(data).hexdigest(),
    }


def persist_upload(upload):
    UPLOAD_DIR.mkdir(exist_ok=True)
    name = secrets.token_hex(16) + upload["ext"]
    (UPLOAD_DIR / name).write_bytes(upload["data"])
    return name


def discard_upload(name):
    if not name:
        return
    path = UPLOAD_DIR / name
    if path.is_file():
        path.unlink()


def stored_path(name):
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    root = UPLOAD_DIR.resolve()
    path = (UPLOAD_DIR / name).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    if not path.is_file():
        return None
    return path


def insert_landowner(cursor, user_id, national_id, phone, address):
    cursor.execute(
        """
        INSERT INTO landowners (user_id, national_id, phone, address)
        VALUES (%s, %s, %s, %s)
        """,
        (user_id, national_id, phone, address[:160]),
    )


def insert_transaction(cursor, parcel_id, official_user_id, transaction_type, approval_reference, created_at, blockchain_tx_hash):
    cursor.execute(
        """
        INSERT INTO land_transactions
            (parcel_id, official_user_id, transaction_type, approval_reference, created_at, blockchain_tx_hash)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (parcel_id, official_user_id, transaction_type, approval_reference, created_at, blockchain_tx_hash),
    )
    return cursor.lastrowid


def insert_document(cursor, transaction_id, document_type, storage_reference, file_hash, original_name, uploaded_at):
    cursor.execute(
        """
        INSERT INTO documents
            (transaction_id, document_type, storage_reference, file_hash, original_name, uploaded_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (transaction_id, document_type, storage_reference, file_hash, original_name, uploaded_at),
    )


def ensure_active_ownership(cursor, parcel_id, owner_id, stamp, previous_owner_id=None):
    if previous_owner_id is not None and previous_owner_id != owner_id:
        cursor.execute(
            """
            UPDATE ownerships
            SET end_date = %s, ownership_status = 'ended'
            WHERE parcel_id = %s AND ownership_status = 'active'
            """,
            (stamp, parcel_id),
        )
        cursor.execute(
            """
            INSERT INTO ownerships (parcel_id, owner_user_id, start_date, end_date, ownership_status)
            VALUES (%s, %s, %s, NULL, 'active')
            """,
            (parcel_id, owner_id, stamp),
        )
        return
    cursor.execute(
        "SELECT id FROM ownerships WHERE parcel_id = %s AND ownership_status = 'active'",
        (parcel_id,),
    )
    if cursor.fetchone() is None:
        cursor.execute(
            """
            INSERT INTO ownerships (parcel_id, owner_user_id, start_date, end_date, ownership_status)
            VALUES (%s, %s, %s, NULL, 'active')
            """,
            (parcel_id, owner_id, stamp),
        )


def issue_identifiers(cursor, province, district, sector, cell, year):
    province_code = PROVINCE_CODES[province]
    district_code = PROVINCES[province].index(district) + 1
    sector_code = _name_code(sector)
    cell_code = _name_code(cell)
    cursor.execute("SELECT upi, title_number FROM land_parcels")
    rows = cursor.fetchall()
    upis = {row["upi"] for row in rows if row["upi"]}
    titles = {row["title_number"] for row in rows if row["title_number"]}
    serial = 1000
    for existing in upis:
        tail = existing.rsplit("/", 1)[-1]
        if tail.isdigit():
            serial = max(serial, int(tail))
    serial += 1
    while True:
        upi = f"{province_code}/{district_code:02d}/{sector_code:02d}/{cell_code:02d}/{serial}"
        if upi not in upis:
            break
        serial += 1
    prefix = f"LR-{year}-"
    number = 1
    for existing in titles:
        if existing.startswith(prefix) and existing[len(prefix):].isdigit():
            number = max(number, int(existing[len(prefix):]) + 1)
    while f"{prefix}{number:04d}" in titles:
        number += 1
    return upi, f"{prefix}{number:04d}"


def _name_code(name):
    total = sum(ord(character) for character in name.lower())
    return (total % 90) + 10


def _matches(data, ext):
    if ext == ".pdf":
        return data.startswith(b"%PDF")
    if ext == ".png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in {".jpg", ".jpeg"}:
        return data.startswith(b"\xff\xd8\xff")
    return False
