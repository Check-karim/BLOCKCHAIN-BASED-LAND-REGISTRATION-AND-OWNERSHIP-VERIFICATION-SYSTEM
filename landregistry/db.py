from contextlib import contextmanager
from pathlib import Path

import mysql.connector
from flask import g, request
from werkzeug.security import generate_password_hash

from .blockchain import add_block, land_record_hash, now_stamp
from .config import DATABASE_NAME, DB_CONFIG
from .locations import DEMO_MISMATCH_UPI

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


def connect(with_database=True):
    config = {
        "host": DB_CONFIG["host"],
        "port": DB_CONFIG["port"],
        "user": DB_CONFIG["user"],
        "password": DB_CONFIG["password"],
        "charset": DB_CONFIG["charset"],
        "autocommit": False,
    }
    if with_database:
        config["database"] = DB_CONFIG["database"]
    return mysql.connector.connect(**config)


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


def query_one(sql, params=()):
    cursor = get_db().cursor(dictionary=True, buffered=True)
    cursor.execute(sql, params)
    row = cursor.fetchone()
    cursor.close()
    return row


def query_all(sql, params=()):
    cursor = get_db().cursor(dictionary=True, buffered=True)
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    cursor.close()
    return rows


def execute(sql, params=()):
    connection = get_db()
    cursor = connection.cursor(buffered=True)
    cursor.execute(sql, params)
    connection.commit()
    last_id = cursor.lastrowid
    cursor.close()
    return last_id


@contextmanager
def db_cursor():
    connection = get_db()
    cursor = connection.cursor(dictionary=True, buffered=True)
    try:
        yield cursor
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


def log_activity(action, details, user_id=None, cursor=None, entity_type=None, entity_id=None):
    details = " ".join((details or "").split())[:500]
    ip_address = ""
    try:
        ip_address = (request.remote_addr or "")[:45]
    except RuntimeError:
        ip_address = ""
    sql = """
        INSERT INTO activity_logs (user_id, action, details, ip_address, created_at, entity_type, entity_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    params = (user_id, action, details, ip_address, now_stamp(), entity_type, entity_id)
    if cursor is not None:
        cursor.execute(sql, params)
    else:
        execute(sql, params)


def _script_statements(sql_text):
    buffer = []
    statements = []
    for line in sql_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buffer.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(buffer))
            buffer = []
    return statements


def init_db():
    server = connect(with_database=False)
    cursor = server.cursor()
    cursor.execute(
        f"CREATE DATABASE IF NOT EXISTS {DATABASE_NAME} "
        "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    )
    server.commit()
    cursor.close()
    server.close()

    connection = connect()
    cursor = connection.cursor()
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    for statement in _script_statements(schema):
        cursor.execute(statement)
    connection.commit()
    cursor.close()
    _migrate(connection)
    _seed(connection)
    _align(connection)
    connection.close()


def _insert_user(cursor, username, password, full_name, email, phone, role, national_id, province, district):
    cursor.execute(
        """
        INSERT INTO users
            (username, password_hash, full_name, email, phone, national_id, role, status, province, district, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s)
        """,
        (
            username,
            generate_password_hash(password),
            full_name,
            email,
            phone,
            national_id,
            role,
            province,
            district,
            now_stamp(),
        ),
    )
    return cursor.lastrowid


def _column_exists(cursor, table, column):
    cursor.execute(
        """
        SELECT COUNT(*) AS n FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s
        """,
        (DATABASE_NAME, table, column),
    )
    return cursor.fetchone()["n"] > 0


def _constraint_exists(cursor, name):
    cursor.execute(
        """
        SELECT COUNT(*) AS n FROM information_schema.TABLE_CONSTRAINTS
        WHERE CONSTRAINT_SCHEMA = %s AND CONSTRAINT_NAME = %s
        """,
        (DATABASE_NAME, name),
    )
    return cursor.fetchone()["n"] > 0


def _add_column(cursor, table, column, definition):
    if not _column_exists(cursor, table, column):
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _migrate(connection):
    cursor = connection.cursor(dictionary=True, buffered=True)
    try:
        _add_column(cursor, "users", "role_id", "INT NULL")
        _add_column(cursor, "land_parcels", "record_hash", "CHAR(64) NULL")
        _add_column(cursor, "land_parcels", "approved_by", "INT NULL")
        _add_column(cursor, "land_parcels", "rejection_note", "VARCHAR(500) NULL")
        _add_column(cursor, "land_parcels", "application_file", "VARCHAR(255) NULL")
        _add_column(cursor, "land_parcels", "application_file_hash", "CHAR(64) NULL")
        _add_column(cursor, "land_parcels", "application_file_name", "VARCHAR(180) NULL")
        cursor.execute(
            """
            SELECT COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'land_parcels' AND COLUMN_NAME = 'upi'
            """,
            (DATABASE_NAME,),
        )
        upi_column = cursor.fetchone()
        if upi_column and upi_column["IS_NULLABLE"] == "NO":
            cursor.execute("ALTER TABLE land_parcels MODIFY upi VARCHAR(30) NULL")
            cursor.execute("ALTER TABLE land_parcels MODIFY title_number VARCHAR(30) NULL")
        cursor.execute(
            """
            SELECT COLUMN_TYPE FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'land_parcels' AND COLUMN_NAME = 'status'
            """,
            (DATABASE_NAME,),
        )
        status_column = cursor.fetchone()
        if status_column and "pending_approval" not in status_column["COLUMN_TYPE"]:
            cursor.execute(
                """
                ALTER TABLE land_parcels
                MODIFY status ENUM(
                    'registered', 'verified', 'pending_update', 'disputed', 'pending_approval', 'rejected'
                ) NOT NULL DEFAULT 'registered'
                """
            )
        _add_column(cursor, "activity_logs", "entity_type", "VARCHAR(40) NULL")
        _add_column(cursor, "activity_logs", "entity_id", "INT NULL")
        cursor.execute(
            """
            INSERT IGNORE INTO roles (role_name, description) VALUES
            ('admin', 'System administrator. Manages accounts, audit logs, and management reports.'),
            ('official', 'Authorized land official. Registers parcels, uploads documents, and seals transactions.'),
            ('landowner', 'Landowner. Views personal records and requests ownership verification.')
            """
        )
        if not _constraint_exists(cursor, "fk_user_role"):
            cursor.execute(
                "ALTER TABLE users ADD CONSTRAINT fk_user_role FOREIGN KEY (role_id) REFERENCES roles (id)"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


def _align(connection):
    cursor = connection.cursor(dictionary=True, buffered=True)
    try:
        cursor.execute(
            "UPDATE users SET password_hash = %s, status = 'active' WHERE username = 'admin'",
            (generate_password_hash("admin"),),
        )
        cursor.execute(
            """
            UPDATE users u
            JOIN roles r ON r.role_name = u.role
            SET u.role_id = r.id
            WHERE u.role_id IS NULL
            """
        )
        cursor.execute(
            """
            INSERT INTO landowners (user_id, national_id, phone, address)
            SELECT u.id, u.national_id, u.phone,
                   CONCAT(IFNULL(u.district, ''), ', ', IFNULL(u.province, ''))
            FROM users u
            LEFT JOIN landowners l ON l.user_id = u.id
            WHERE u.role = 'landowner' AND u.national_id IS NOT NULL AND l.id IS NULL
            """
        )
        _ensure_mismatch_parcel(cursor)
        cursor.execute("SELECT * FROM land_parcels WHERE record_hash IS NULL")
        for row in cursor.fetchall():
            digest = land_record_hash(
                row["upi"],
                row["title_number"],
                row["owner_id"],
                row["province"],
                row["district"],
                row["sector"],
                row["cell"],
                row["village"],
                row["size_sqm"],
                row["land_use"],
            )
            cursor.execute(
                "UPDATE land_parcels SET record_hash = %s WHERE id = %s",
                (digest, row["id"]),
            )
        cursor.execute(
            """
            INSERT INTO ownerships (parcel_id, owner_user_id, start_date, end_date, ownership_status)
            SELECT l.id, l.owner_id, l.created_at, NULL, 'active'
            FROM land_parcels l
            LEFT JOIN ownerships o ON o.parcel_id = l.id AND o.ownership_status = 'active'
            WHERE o.id IS NULL
            """
        )
        cursor.execute(
            """
            INSERT INTO land_transactions
                (parcel_id, official_user_id, transaction_type, approval_reference, created_at, blockchain_tx_hash)
            SELECT e.land_id, e.actor_id, e.event_type, e.legal_reference, e.created_at, e.block_hash
            FROM ownership_events e
            LEFT JOIN land_transactions t ON t.blockchain_tx_hash = e.block_hash
            WHERE t.id IS NULL
            """
        )
        cursor.execute(
            """
            INSERT INTO land_transactions
                (parcel_id, official_user_id, transaction_type, approval_reference, created_at, blockchain_tx_hash)
            SELECT v.land_id, v.official_id, 'ownership_verified', v.result, v.created_at, v.block_hash
            FROM verifications v
            LEFT JOIN land_transactions t ON t.blockchain_tx_hash = v.block_hash
            WHERE t.id IS NULL
            """
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


def _ensure_mismatch_parcel(cursor):
    cursor.execute("SELECT id FROM land_parcels WHERE upi = %s", (DEMO_MISMATCH_UPI,))
    if cursor.fetchone():
        return
    cursor.execute(
        "SELECT id FROM users WHERE username = 'uwase' AND role = 'landowner'"
    )
    owner = cursor.fetchone()
    if not owner:
        cursor.execute(
            "SELECT id FROM users WHERE role = 'landowner' AND status = 'active' ORDER BY id LIMIT 1"
        )
        owner = cursor.fetchone()
    cursor.execute(
        "SELECT id FROM users WHERE role = 'official' AND status = 'active' ORDER BY id LIMIT 1"
    )
    officer = cursor.fetchone()
    if not owner or not officer:
        return
    sealed = {
        "upi": DEMO_MISMATCH_UPI,
        "title_number": "LR-2024-9090",
        "owner_id": owner["id"],
        "province": "Kigali City",
        "district": "Gasabo",
        "sector": "Kimironko",
        "cell": "Kibagabaga",
        "village": "Kibagabaga",
        "size_sqm": "800.00",
        "land_use": "Residential",
    }
    sealed_hash = land_record_hash(**sealed)
    stamp = now_stamp()
    cursor.execute(
        """
        INSERT INTO land_parcels
            (upi, title_number, owner_id, province, district, sector, cell, village,
             size_sqm, land_use, description, status, registered_by, created_at, updated_at, record_hash)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'disputed', %s, %s, %s, %s)
        """,
        (
            sealed["upi"],
            sealed["title_number"],
            sealed["owner_id"],
            sealed["province"],
            sealed["district"],
            sealed["sector"],
            sealed["cell"],
            sealed["village"],
            "960.00",
            sealed["land_use"],
            "Demo parcel. The sealed size is 800 square metres and the register now says 960, so the hashes differ.",
            officer["id"],
            stamp,
            stamp,
            sealed_hash,
        ),
    )
    land_id = cursor.lastrowid
    digest = add_block(
        cursor,
        "land_registered",
        {
            "upi": sealed["upi"],
            "title_number": sealed["title_number"],
            "owner_id": sealed["owner_id"],
            "district": sealed["district"],
            "size_sqm": sealed["size_sqm"],
            "land_use": sealed["land_use"],
            "record_hash": sealed_hash,
        },
        actor_id=officer["id"],
        record_ref=sealed["upi"],
    )
    cursor.execute(
        """
        INSERT INTO ownership_events
            (land_id, event_type, old_owner_id, new_owner_id, legal_basis, legal_reference,
             notes, actor_id, block_hash, created_at)
        VALUES (%s, 'registration', NULL, %s, 'Administrative correction', 'SEED-MISMATCH',
                %s, %s, %s, %s)
        """,
        (
            land_id,
            owner["id"],
            "Seeded so the ownership check can show a hash mismatch.",
            officer["id"],
            digest,
            stamp,
        ),
    )
    cursor.execute(
        """
        INSERT INTO ownerships (parcel_id, owner_user_id, start_date, end_date, ownership_status)
        VALUES (%s, %s, %s, NULL, 'active')
        """,
        (land_id, owner["id"], stamp),
    )
    cursor.execute(
        """
        INSERT INTO land_transactions
            (parcel_id, official_user_id, transaction_type, approval_reference, created_at, blockchain_tx_hash)
        VALUES (%s, %s, 'registration', 'SEED-MISMATCH', %s, %s)
        """,
        (land_id, officer["id"], stamp, digest),
    )


def _seed(connection):
    cursor = connection.cursor(dictionary=True, buffered=True)
    cursor.execute("SELECT id FROM users WHERE username = %s", ("admin",))
    if cursor.fetchone():
        cursor.close()
        return

    try:
        admin_id = _insert_user(
            cursor,
            "admin",
            "admin",
            "System Administrator",
            "admin@minaloc.gov.rw",
            "0788000001",
            "admin",
            None,
            "Kigali City",
            "Nyarugenge",
        )
        officer_id = _insert_user(
            cursor,
            "mukamana",
            "Officer1",
            "Claire Mukamana",
            "claire.mukamana@minaloc.gov.rw",
            "0788123001",
            "official",
            "1199001234567890",
            "Kigali City",
            "Gasabo",
        )
        uwase_id = _insert_user(
            cursor,
            "uwase",
            "Uwase123",
            "Aline Uwase",
            "aline.uwase@example.com",
            "0788234002",
            "landowner",
            "1199670123456781",
            "Kigali City",
            "Gasabo",
        )
        habimana_id = _insert_user(
            cursor,
            "habimana",
            "Habimana1",
            "Eric Habimana",
            "eric.habimana@example.com",
            "0788345003",
            "landowner",
            "1198580098765432",
            "Southern",
            "Huye",
        )

        add_block(
            cursor,
            "genesis",
            {
                "system": "MINALOC Land Registry",
                "network": "ubutaka-ledger",
                "note": "Genesis block for the land registration ledger",
            },
            actor_id=None,
            record_ref=None,
        )

        samples = [
            {
                "upi": "1/03/07/04/2581",
                "title_number": "LR-2024-0142",
                "owner_id": uwase_id,
                "province": "Kigali City",
                "district": "Gasabo",
                "sector": "Kimironko",
                "cell": "Kibagabaga",
                "village": "Kibagabaga",
                "size_sqm": "450.00",
                "land_use": "Residential",
                "description": "Residential plot recorded for the MINALOC case study.",
                "status": "verified",
            },
            {
                "upi": "5/04/02/01/1108",
                "title_number": "LR-2023-0881",
                "owner_id": uwase_id,
                "province": "Southern",
                "district": "Huye",
                "sector": "Tumba",
                "cell": "Rango",
                "village": "Rango",
                "size_sqm": "12500.50",
                "land_use": "Agricultural",
                "description": "Agricultural parcel used in the sample ownership register.",
                "status": "registered",
            },
            {
                "upi": "1/01/03/02/7740",
                "title_number": "LR-2025-2210",
                "owner_id": habimana_id,
                "province": "Kigali City",
                "district": "Nyarugenge",
                "sector": "Nyamirambo",
                "cell": "Mumena",
                "village": "Buhoro",
                "size_sqm": "280.00",
                "land_use": "Commercial",
                "description": "Commercial frontage recorded by a land officer.",
                "status": "registered",
            },
        ]

        stamp = now_stamp()
        first_land_id = None
        for sample in samples:
            cursor.execute(
                """
                INSERT INTO land_parcels
                    (upi, title_number, owner_id, province, district, sector, cell, village,
                     size_sqm, land_use, description, status, registered_by, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    sample["upi"],
                    sample["title_number"],
                    sample["owner_id"],
                    sample["province"],
                    sample["district"],
                    sample["sector"],
                    sample["cell"],
                    sample["village"],
                    sample["size_sqm"],
                    sample["land_use"],
                    sample["description"],
                    sample["status"],
                    officer_id,
                    stamp,
                    stamp,
                ),
            )
            land_id = cursor.lastrowid
            digest = add_block(
                cursor,
                "land_registered",
                {
                    "upi": sample["upi"],
                    "title_number": sample["title_number"],
                    "owner_id": sample["owner_id"],
                    "district": sample["district"],
                    "size_sqm": sample["size_sqm"],
                    "land_use": sample["land_use"],
                },
                actor_id=officer_id,
                record_ref=sample["upi"],
            )
            cursor.execute(
                """
                INSERT INTO ownership_events
                    (land_id, event_type, old_owner_id, new_owner_id, legal_basis, legal_reference,
                     notes, actor_id, block_hash, created_at)
                VALUES (%s, 'registration', NULL, %s, 'Administrative correction', 'SEED-REG',
                        %s, %s, %s, %s)
                """,
                (
                    land_id,
                    sample["owner_id"],
                    "Initial registration on the ledger.",
                    officer_id,
                    digest,
                    stamp,
                ),
            )
            if first_land_id is None:
                first_land_id = land_id

        verify_hash = add_block(
            cursor,
            "ownership_verified",
            {"upi": "1/03/07/04/2581", "result": "confirmed", "land_id": first_land_id},
            actor_id=officer_id,
            record_ref="1/03/07/04/2581",
        )
        cursor.execute(
            """
            INSERT INTO verifications (land_id, official_id, result, notes, block_hash, created_at)
            VALUES (%s, %s, 'confirmed', %s, %s, %s)
            """,
            (
                first_land_id,
                officer_id,
                "Boundary and owner documents match the recorded sale agreement.",
                verify_hash,
                stamp,
            ),
        )
        cursor.execute(
            """
            INSERT INTO activity_logs (user_id, action, details, ip_address, created_at)
            VALUES (%s, 'SEED', 'Sample land records created for the MINALOC case study.', '127.0.0.1', %s)
            """,
            (admin_id, stamp),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
