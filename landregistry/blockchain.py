import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

GENESIS_PREVIOUS = "0" * 64
KIGALI = timezone(timedelta(hours=2))


def now_stamp():
    return datetime.now(KIGALI).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def land_record_hash(upi, title_number, owner_id, province, district, sector, cell, village, size_sqm, land_use):
    body = json.dumps(
        {
            "upi": str(upi),
            "title_number": str(title_number),
            "owner_id": int(owner_id),
            "province": province,
            "district": district,
            "sector": sector,
            "cell": cell,
            "village": village,
            "size_sqm": f"{Decimal(str(size_sqm)):.2f}",
            "land_use": land_use,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def compute_hash(index, timestamp, action, payload, previous_hash):
    raw = f"{index}|{timestamp}|{action}|{payload}|{previous_hash}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def add_block(cursor, action, payload, actor_id=None, record_ref=None, when=None):
    cursor.execute(
        "SELECT block_index, block_hash FROM blocks ORDER BY block_index DESC LIMIT 1"
    )
    last = cursor.fetchone()
    if last:
        index = last["block_index"] + 1
        previous = last["block_hash"]
    else:
        index = 0
        previous = GENESIS_PREVIOUS

    timestamp = when or now_stamp()
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    digest = compute_hash(index, timestamp, action, body, previous)
    cursor.execute(
        """
        INSERT INTO blocks
            (block_index, created_at, action, record_ref, payload, previous_hash, block_hash, actor_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (index, timestamp, action, record_ref, body, previous, digest, actor_id),
    )
    return digest


def verify_chain(blocks):
    previous = GENESIS_PREVIOUS
    for expected_index, block in enumerate(blocks):
        timestamp = block["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        digest = compute_hash(
            block["block_index"],
            timestamp,
            block["action"],
            block["payload"],
            block["previous_hash"],
        )
        if block["block_index"] != expected_index or block["previous_hash"] != previous:
            return False, block["block_index"]
        if digest != block["block_hash"]:
            return False, block["block_index"]
        previous = block["block_hash"]
    return True, None
