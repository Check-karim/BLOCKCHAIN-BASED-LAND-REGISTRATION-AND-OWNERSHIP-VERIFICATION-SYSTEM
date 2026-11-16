-- MINALOC blockchain land registry (MariaDB / MySQL, utf8mb4)

CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(30) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(80) NOT NULL,
    email VARCHAR(120) NOT NULL UNIQUE,
    phone CHAR(10) NOT NULL UNIQUE,
    national_id CHAR(16) NULL UNIQUE,
    role ENUM('admin', 'official', 'landowner') NOT NULL,
    status ENUM('active', 'inactive') NOT NULL DEFAULT 'active',
    province VARCHAR(40) NULL,
    district VARCHAR(40) NULL,
    created_at DATETIME NOT NULL,
    CONSTRAINT chk_username CHECK (username REGEXP '^[A-Za-z][A-Za-z0-9_]+$'),
    CONSTRAINT chk_full_name CHECK (full_name REGEXP '^[A-Za-z]+( [A-Za-z]+)*$'),
    CONSTRAINT chk_email CHECK (email LIKE '%_@_%._%' AND email NOT LIKE '% %'),
    CONSTRAINT chk_phone CHECK (phone REGEXP '^07[2389][0-9]{7}$'),
    CONSTRAINT chk_nid CHECK (national_id IS NULL OR national_id REGEXP '^[0-9]{16}$')
);

CREATE TABLE IF NOT EXISTS land_parcels (
    id INT AUTO_INCREMENT PRIMARY KEY,
    upi VARCHAR(30) NULL UNIQUE,
    title_number VARCHAR(30) NULL UNIQUE,
    owner_id INT NOT NULL,
    province VARCHAR(40) NOT NULL,
    district VARCHAR(40) NOT NULL,
    sector VARCHAR(60) NOT NULL,
    cell VARCHAR(60) NOT NULL,
    village VARCHAR(60) NOT NULL,
    size_sqm DECIMAL(12,2) NOT NULL,
    land_use VARCHAR(30) NOT NULL,
    description VARCHAR(800) NULL,
    status ENUM('registered', 'verified', 'pending_update', 'disputed', 'pending_approval', 'rejected') NOT NULL DEFAULT 'registered',
    registered_by INT NOT NULL,
    approved_by INT NULL,
    rejection_note VARCHAR(500) NULL,
    application_file VARCHAR(255) NULL,
    application_file_hash CHAR(64) NULL,
    application_file_name VARCHAR(180) NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT chk_size CHECK (size_sqm > 0),
    CONSTRAINT fk_land_owner FOREIGN KEY (owner_id) REFERENCES users(id),
    CONSTRAINT fk_land_officer FOREIGN KEY (registered_by) REFERENCES users(id),
    INDEX idx_land_owner (owner_id),
    INDEX idx_land_district (district),
    INDEX idx_land_status (status)
);

CREATE TABLE IF NOT EXISTS blocks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    block_index INT NOT NULL UNIQUE,
    created_at DATETIME NOT NULL,
    action VARCHAR(40) NOT NULL,
    record_ref VARCHAR(40) NULL,
    payload TEXT NOT NULL,
    previous_hash CHAR(64) NOT NULL,
    block_hash CHAR(64) NOT NULL,
    actor_id INT NULL,
    CONSTRAINT fk_block_actor FOREIGN KEY (actor_id) REFERENCES users(id),
    INDEX idx_blocks_ref (record_ref)
);

CREATE TABLE IF NOT EXISTS ownership_events (
    id INT AUTO_INCREMENT PRIMARY KEY,
    land_id INT NOT NULL,
    event_type VARCHAR(40) NOT NULL,
    old_owner_id INT NULL,
    new_owner_id INT NULL,
    legal_basis VARCHAR(80) NULL,
    legal_reference VARCHAR(80) NULL,
    notes TEXT NULL,
    actor_id INT NOT NULL,
    block_hash CHAR(64) NOT NULL,
    created_at DATETIME NOT NULL,
    CONSTRAINT fk_event_land FOREIGN KEY (land_id) REFERENCES land_parcels(id),
    CONSTRAINT fk_event_old_owner FOREIGN KEY (old_owner_id) REFERENCES users(id),
    CONSTRAINT fk_event_new_owner FOREIGN KEY (new_owner_id) REFERENCES users(id),
    CONSTRAINT fk_event_actor FOREIGN KEY (actor_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS verifications (
    id INT AUTO_INCREMENT PRIMARY KEY,
    land_id INT NOT NULL,
    official_id INT NOT NULL,
    result VARCHAR(20) NOT NULL,
    notes VARCHAR(500) NOT NULL,
    block_hash CHAR(64) NOT NULL,
    created_at DATETIME NOT NULL,
    CONSTRAINT fk_ver_land FOREIGN KEY (land_id) REFERENCES land_parcels(id),
    CONSTRAINT fk_ver_official FOREIGN KEY (official_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS activity_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NULL,
    action VARCHAR(40) NOT NULL,
    details VARCHAR(500) NOT NULL,
    ip_address VARCHAR(45) NULL,
    created_at DATETIME NOT NULL,
    entity_type VARCHAR(40) NULL,
    entity_id INT NULL,
    CONSTRAINT fk_log_user FOREIGN KEY (user_id) REFERENCES users(id),
    INDEX idx_activity_created (created_at)
);

CREATE TABLE IF NOT EXISTS roles (
    id INT AUTO_INCREMENT PRIMARY KEY,
    role_name VARCHAR(40) NOT NULL UNIQUE,
    description VARCHAR(255) NOT NULL
);

CREATE TABLE IF NOT EXISTS landowners (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL UNIQUE,
    national_id CHAR(16) NOT NULL,
    phone CHAR(10) NOT NULL,
    address VARCHAR(160) NOT NULL,
    CONSTRAINT fk_owner_user FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS ownerships (
    id INT AUTO_INCREMENT PRIMARY KEY,
    parcel_id INT NOT NULL,
    owner_user_id INT NOT NULL,
    start_date DATETIME NOT NULL,
    end_date DATETIME NULL,
    ownership_status ENUM('active', 'ended', 'disputed') NOT NULL DEFAULT 'active',
    CONSTRAINT fk_own_parcel FOREIGN KEY (parcel_id) REFERENCES land_parcels(id),
    CONSTRAINT fk_own_owner FOREIGN KEY (owner_user_id) REFERENCES users(id),
    INDEX idx_own_parcel (parcel_id)
);

CREATE TABLE IF NOT EXISTS land_transactions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    parcel_id INT NOT NULL,
    official_user_id INT NOT NULL,
    transaction_type VARCHAR(40) NOT NULL,
    approval_reference VARCHAR(80) NULL,
    created_at DATETIME NOT NULL,
    blockchain_tx_hash CHAR(64) NOT NULL,
    CONSTRAINT fk_tx_parcel FOREIGN KEY (parcel_id) REFERENCES land_parcels(id),
    CONSTRAINT fk_tx_official FOREIGN KEY (official_user_id) REFERENCES users(id),
    INDEX idx_tx_parcel (parcel_id),
    INDEX idx_tx_hash (blockchain_tx_hash)
);

CREATE TABLE IF NOT EXISTS documents (
    id INT AUTO_INCREMENT PRIMARY KEY,
    transaction_id INT NOT NULL,
    document_type VARCHAR(40) NOT NULL,
    storage_reference VARCHAR(255) NOT NULL,
    file_hash CHAR(64) NOT NULL,
    original_name VARCHAR(180) NOT NULL,
    uploaded_at DATETIME NOT NULL,
    CONSTRAINT fk_doc_tx FOREIGN KEY (transaction_id) REFERENCES land_transactions(id),
    INDEX idx_doc_tx (transaction_id)
);

CREATE TABLE IF NOT EXISTS verification_requests (
    id INT AUTO_INCREMENT PRIMARY KEY,
    parcel_id INT NULL,
    requested_by INT NOT NULL,
    parcel_number VARCHAR(30) NOT NULL,
    outcome ENUM('verified', 'mismatch', 'not_found', 'resolved') NOT NULL,
    record_hash CHAR(64) NULL,
    chain_hash CHAR(64) NULL,
    notes VARCHAR(500) NULL,
    reviewed_by INT NULL,
    created_at DATETIME NOT NULL,
    reviewed_at DATETIME NULL,
    CONSTRAINT fk_vr_parcel FOREIGN KEY (parcel_id) REFERENCES land_parcels(id),
    CONSTRAINT fk_vr_user FOREIGN KEY (requested_by) REFERENCES users(id),
    CONSTRAINT fk_vr_reviewer FOREIGN KEY (reviewed_by) REFERENCES users(id),
    INDEX idx_vr_outcome (outcome),
    INDEX idx_vr_user (requested_by)
);

CREATE TABLE IF NOT EXISTS policies (
    id INT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(120) NOT NULL,
    reference_code VARCHAR(40) NOT NULL UNIQUE,
    body VARCHAR(1000) NOT NULL,
    recorded_by INT NOT NULL,
    created_at DATETIME NOT NULL,
    CONSTRAINT fk_policy_user FOREIGN KEY (recorded_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS reports (
    id INT AUTO_INCREMENT PRIMARY KEY,
    generated_by INT NOT NULL,
    report_type VARCHAR(40) NOT NULL,
    generated_at DATETIME NOT NULL,
    file_reference VARCHAR(180) NOT NULL,
    CONSTRAINT fk_report_user FOREIGN KEY (generated_by) REFERENCES users(id),
    INDEX idx_reports_created (generated_at)
);
