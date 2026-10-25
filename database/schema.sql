
-- =====================================================
-- SMART MEDICINE CABINET
-- DATABASE: Med
-- MySQL / MariaDB - XAMPP
-- Total: 15 tables
-- =====================================================

USE `Med`;

SET NAMES utf8mb4;


-- =====================================================
-- 1. USERS
-- Quan ly tai khoan nguoi dung
-- =====================================================

CREATE TABLE IF NOT EXISTS users (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,

    full_name VARCHAR(150) NOT NULL,
    email VARCHAR(190) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    phone VARCHAR(25) DEFAULT NULL,

    role ENUM(
        'patient',
        'caregiver',
        'admin'
    ) NOT NULL DEFAULT 'patient',

    account_status ENUM(
        'active',
        'disabled'
    ) NOT NULL DEFAULT 'active',

    last_login_at DATETIME DEFAULT NULL,

    created_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uq_users_email (email),
    INDEX idx_users_role (role)

) ENGINE=InnoDB
DEFAULT CHARSET=utf8mb4
COLLATE=utf8mb4_unicode_ci;


-- =====================================================
-- 2. DEVICES
-- Quan ly tu thuoc va ESP32
-- =====================================================

CREATE TABLE IF NOT EXISTS devices (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,

    owner_user_id BIGINT UNSIGNED NOT NULL,

    device_code VARCHAR(50) NOT NULL,
    device_name VARCHAR(150) NOT NULL,

    hardware_model VARCHAR(100)
        DEFAULT 'ESP32-CAM + Arduino Nano',

    esp_firmware_version VARCHAR(30) DEFAULT NULL,
    nano_firmware_version VARCHAR(30) DEFAULT NULL,

    api_token_hash CHAR(64) DEFAULT NULL,

    timezone VARCHAR(64)
        NOT NULL DEFAULT 'Asia/Ho_Chi_Minh',

    operational_status ENUM(
        'active',
        'disabled'
    ) NOT NULL DEFAULT 'active',

    last_seen_at DATETIME DEFAULT NULL,
    last_sync_at DATETIME DEFAULT NULL,

    schedule_revision INT UNSIGNED
        NOT NULL DEFAULT 1,

    notes TEXT DEFAULT NULL,

    created_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uq_device_code (device_code),
    UNIQUE KEY uq_device_token (api_token_hash),

    INDEX idx_devices_owner (owner_user_id),
    INDEX idx_devices_seen (last_seen_at),

    CONSTRAINT fk_devices_owner
        FOREIGN KEY (owner_user_id)
        REFERENCES users(id)
        ON DELETE RESTRICT

) ENGINE=InnoDB
DEFAULT CHARSET=utf8mb4
COLLATE=utf8mb4_unicode_ci;


-- =====================================================
-- 3. DEVICE_COMPARTMENTS
-- Quan ly 4 ngan thuoc
-- =====================================================

CREATE TABLE IF NOT EXISTS device_compartments (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,

    device_id BIGINT UNSIGNED NOT NULL,

    compartment_number TINYINT UNSIGNED NOT NULL,
    compartment_name VARCHAR(100) DEFAULT NULL,

    is_enabled TINYINT(1)
        NOT NULL DEFAULT 1,

    notes TEXT DEFAULT NULL,

    created_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    UNIQUE KEY uq_device_compartment (
        device_id,
        compartment_number
    ),

    CONSTRAINT chk_compartment_number
        CHECK (compartment_number BETWEEN 1 AND 4),

    CONSTRAINT fk_compartment_device
        FOREIGN KEY (device_id)
        REFERENCES devices(id)
        ON DELETE CASCADE

) ENGINE=InnoDB
DEFAULT CHARSET=utf8mb4
COLLATE=utf8mb4_unicode_ci;


-- =====================================================
-- 4. DEVICE_ACCESS
-- Phan quyen nguoi than / nguoi cham soc
-- =====================================================

CREATE TABLE IF NOT EXISTS device_access (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,

    device_id BIGINT UNSIGNED NOT NULL,
    user_id BIGINT UNSIGNED NOT NULL,

    access_role ENUM(
        'owner',
        'caregiver',
        'viewer'
    ) NOT NULL DEFAULT 'viewer',

    is_active TINYINT(1)
        NOT NULL DEFAULT 1,

    granted_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    UNIQUE KEY uq_device_user (
        device_id,
        user_id
    ),

    CONSTRAINT fk_access_device
        FOREIGN KEY (device_id)
        REFERENCES devices(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_access_user
        FOREIGN KEY (user_id)
        REFERENCES users(id)
        ON DELETE CASCADE

) ENGINE=InnoDB
DEFAULT CHARSET=utf8mb4
COLLATE=utf8mb4_unicode_ci;


-- =====================================================
-- 5. DRUG_SOURCES
-- Luu nguon du lieu thuoc cho crawler
-- =====================================================

CREATE TABLE IF NOT EXISTS drug_sources (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,

    source_code VARCHAR(80) NOT NULL,
    source_name VARCHAR(255) NOT NULL,

    base_url VARCHAR(500) DEFAULT NULL,

    source_type ENUM(
        'official',
        'licensed',
        'reference'
    ) NOT NULL DEFAULT 'reference',

    collection_method ENUM(
        'excel',
        'csv',
        'api',
        'html',
        'manual'
    ) NOT NULL DEFAULT 'manual',

    usage_notes TEXT DEFAULT NULL,

    is_enabled TINYINT(1)
        NOT NULL DEFAULT 1,

    created_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    UNIQUE KEY uq_source_code (source_code)

) ENGINE=InnoDB
DEFAULT CHARSET=utf8mb4
COLLATE=utf8mb4_unicode_ci;


-- =====================================================
-- 6. DRUG_IMPORT_BATCHES
-- Theo doi moi lan chay crawler
-- =====================================================

CREATE TABLE IF NOT EXISTS drug_import_batches (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,

    source_id BIGINT UNSIGNED NOT NULL,

    batch_code VARCHAR(100) DEFAULT NULL,

    started_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    finished_at DATETIME DEFAULT NULL,

    status ENUM(
        'running',
        'completed',
        'partial',
        'failed'
    ) NOT NULL DEFAULT 'running',

    records_fetched INT UNSIGNED
        NOT NULL DEFAULT 0,

    records_valid INT UNSIGNED
        NOT NULL DEFAULT 0,

    records_inserted INT UNSIGNED
        NOT NULL DEFAULT 0,

    records_updated INT UNSIGNED
        NOT NULL DEFAULT 0,

    records_duplicates INT UNSIGNED
        NOT NULL DEFAULT 0,

    records_failed INT UNSIGNED
        NOT NULL DEFAULT 0,

    error_message TEXT DEFAULT NULL,

    UNIQUE KEY uq_import_batch_code (batch_code),

    INDEX idx_batches_source (source_id),
    INDEX idx_batches_started (started_at),

    CONSTRAINT fk_batches_source
        FOREIGN KEY (source_id)
        REFERENCES drug_sources(id)
        ON DELETE RESTRICT

) ENGINE=InnoDB
DEFAULT CHARSET=utf8mb4
COLLATE=utf8mb4_unicode_ci;


-- =====================================================
-- 7. DRUG_CATALOG
-- DANH MUC THUOC CHINH
-- Du lieu duoc crawler xu ly, chuan hoa
-- =====================================================

CREATE TABLE IF NOT EXISTS drug_catalog (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,

    -- Dinh danh chong trung do crawler tao
    catalog_key CHAR(64) NOT NULL,

    -- Thong tin thuoc
    drug_name VARCHAR(255) NOT NULL,
    generic_name VARCHAR(255) DEFAULT NULL,

    registration_number VARCHAR(120) DEFAULT NULL,

    active_ingredients_text TEXT DEFAULT NULL,

    strength VARCHAR(255) DEFAULT NULL,

    dosage_form VARCHAR(255) DEFAULT NULL,

    route_of_administration VARCHAR(150)
        DEFAULT NULL,

    packaging TEXT DEFAULT NULL,

    -- Nha san xuat
    manufacturer VARCHAR(255) DEFAULT NULL,
    manufacturer_country VARCHAR(120)
        DEFAULT NULL,

    -- Giay phep
    registration_issued_on DATE DEFAULT NULL,
    registration_expires_on DATE DEFAULT NULL,

    registration_status ENUM(
        'unverified',
        'valid',
        'expired',
        'withdrawn',
        'suspended'
    ) NOT NULL DEFAULT 'unverified',

    review_status ENUM(
        'pending',
        'reviewed',
        'rejected'
    ) NOT NULL DEFAULT 'pending',

    -- Chi hien thi trong danh muc sau kiem duyet
    is_searchable TINYINT(1)
        NOT NULL DEFAULT 0,

    primary_source_id BIGINT UNSIGNED DEFAULT NULL,

    source_url TEXT DEFAULT NULL,

    last_verified_at DATETIME DEFAULT NULL,

    created_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uq_catalog_key (catalog_key),

    INDEX idx_drug_name (drug_name(190)),

    INDEX idx_drug_generic (generic_name(190)),

    INDEX idx_registration_number (
        registration_number
    ),

    INDEX idx_drug_searchable (
        is_searchable,
        registration_status
    ),

    INDEX idx_drug_source (
        primary_source_id
    ),

    CONSTRAINT fk_catalog_source
        FOREIGN KEY (primary_source_id)
        REFERENCES drug_sources(id)
        ON DELETE SET NULL

) ENGINE=InnoDB
DEFAULT CHARSET=utf8mb4
COLLATE=utf8mb4_unicode_ci;


-- =====================================================
-- 8. DRUG_SOURCE_RECORDS
-- Luu ban ghi goc crawler thu thap duoc
-- =====================================================

CREATE TABLE IF NOT EXISTS drug_source_records (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,

    source_id BIGINT UNSIGNED NOT NULL,

    import_batch_id BIGINT UNSIGNED DEFAULT NULL,

    -- Ma record tren website goc
    external_key VARCHAR(190) NOT NULL,

    -- Hash noi dung raw
    record_hash CHAR(64) NOT NULL,

    source_url TEXT DEFAULT NULL,

    -- JSON raw tu crawler
    raw_json LONGTEXT NOT NULL,

    -- Link den drug_catalog sau chuan hoa
    matched_drug_id BIGINT UNSIGNED DEFAULT NULL,

    processing_status ENUM(
        'new',
        'linked',
        'ignored',
        'error'
    ) NOT NULL DEFAULT 'new',

    processing_note TEXT DEFAULT NULL,

    first_seen_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    last_seen_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uq_source_record (
        source_id,
        external_key
    ),

    INDEX idx_source_record_hash (record_hash),

    INDEX idx_source_record_drug (
        matched_drug_id
    ),

    INDEX idx_source_record_batch (
        import_batch_id
    ),

    CONSTRAINT fk_records_source
        FOREIGN KEY (source_id)
        REFERENCES drug_sources(id)
        ON DELETE RESTRICT,

    CONSTRAINT fk_records_batch
        FOREIGN KEY (import_batch_id)
        REFERENCES drug_import_batches(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_records_drug
        FOREIGN KEY (matched_drug_id)
        REFERENCES drug_catalog(id)
        ON DELETE SET NULL

) ENGINE=InnoDB
DEFAULT CHARSET=utf8mb4
COLLATE=utf8mb4_unicode_ci;


-- =====================================================
-- 9. INGREDIENTS
-- Danh muc hoat chat
-- =====================================================

CREATE TABLE IF NOT EXISTS ingredients (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,

    ingredient_name VARCHAR(190) NOT NULL,

    normalized_name VARCHAR(190) DEFAULT NULL,

    description TEXT DEFAULT NULL,

    created_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    UNIQUE KEY uq_ingredient_name (
        ingredient_name
    ),

    INDEX idx_ingredient_normalized (
        normalized_name
    )

) ENGINE=InnoDB
DEFAULT CHARSET=utf8mb4
COLLATE=utf8mb4_unicode_ci;


-- =====================================================
-- 10. DRUG_CATALOG_INGREDIENTS
-- Quan he nhieu-nhieu giua thuoc va hoat chat
-- =====================================================

CREATE TABLE IF NOT EXISTS drug_catalog_ingredients (
    drug_id BIGINT UNSIGNED NOT NULL,

    ingredient_id BIGINT UNSIGNED NOT NULL,

    amount_text VARCHAR(150) DEFAULT NULL,

    PRIMARY KEY (
        drug_id,
        ingredient_id
    ),

    INDEX idx_link_ingredient (
        ingredient_id
    ),

    CONSTRAINT fk_link_drug
        FOREIGN KEY (drug_id)
        REFERENCES drug_catalog(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_link_ingredient
        FOREIGN KEY (ingredient_id)
        REFERENCES ingredients(id)
        ON DELETE RESTRICT

) ENGINE=InnoDB
DEFAULT CHARSET=utf8mb4
COLLATE=utf8mb4_unicode_ci;


-- =====================================================
-- 11. USER_MEDICINES
-- Thuoc do nguoi dung them vao tu
-- Ho tro chon tu catalog hoac nhap tay
-- =====================================================

CREATE TABLE IF NOT EXISTS user_medicines (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,

    user_id BIGINT UNSIGNED NOT NULL,

    drug_catalog_id BIGINT UNSIGNED DEFAULT NULL,

    medicine_name VARCHAR(255) NOT NULL,

    strength VARCHAR(255) DEFAULT NULL,

    dosage_form VARCHAR(255) DEFAULT NULL,

    entry_source ENUM(
        'catalog',
        'manual'
    ) NOT NULL DEFAULT 'manual',

    notes TEXT DEFAULT NULL,

    is_active TINYINT(1)
        NOT NULL DEFAULT 1,

    created_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_um_user (user_id),

    INDEX idx_um_catalog (drug_catalog_id),

    INDEX idx_um_name (medicine_name(190)),

    CONSTRAINT fk_user_medicine_user
        FOREIGN KEY (user_id)
        REFERENCES users(id)
        ON DELETE RESTRICT,

    CONSTRAINT fk_user_medicine_catalog
        FOREIGN KEY (drug_catalog_id)
        REFERENCES drug_catalog(id)
        ON DELETE SET NULL

) ENGINE=InnoDB
DEFAULT CHARSET=utf8mb4
COLLATE=utf8mb4_unicode_ci;


-- =====================================================
-- 12. MEDICINE_SCHEDULES
-- Lich uong thuoc cho ESP32
-- =====================================================

CREATE TABLE IF NOT EXISTS medicine_schedules (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,

    user_medicine_id BIGINT UNSIGNED NOT NULL,

    device_id BIGINT UNSIGNED NOT NULL,

    compartment_number TINYINT UNSIGNED NOT NULL,

    -- Lieu uong, vi du 1, 2, 0.5
    dose_quantity DECIMAL(10,3) NOT NULL,

    dose_unit VARCHAR(60) NOT NULL,

    instructions TEXT DEFAULT NULL,

    -- Gio nhac theo mui gio cua tu
    reminder_time TIME NOT NULL,

    -- Bitmask cac ngay trong tuan
    -- T2=1, T3=2, T4=4, T5=8,
    -- T6=16, T7=32, CN=64
    -- Hang ngay = 127
    weekdays_mask TINYINT UNSIGNED
        NOT NULL DEFAULT 127,

    start_date DATE NOT NULL,

    end_date DATE DEFAULT NULL,

    -- So phut cho truoc khi bao bo lo
    grace_minutes SMALLINT UNSIGNED
        NOT NULL DEFAULT 15,

    status ENUM(
        'active',
        'paused',
        'completed'
    ) NOT NULL DEFAULT 'active',

    revision INT UNSIGNED
        NOT NULL DEFAULT 1,

    created_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT chk_schedule_slot
        CHECK (
            compartment_number BETWEEN 1 AND 4
        ),

    CONSTRAINT chk_schedule_dose
        CHECK (dose_quantity > 0),

    CONSTRAINT chk_schedule_days
        CHECK (
            weekdays_mask BETWEEN 1 AND 127
        ),

    CONSTRAINT chk_schedule_dates
        CHECK (
            end_date IS NULL
            OR end_date >= start_date
        ),

    INDEX idx_schedule_device_time (
        device_id,
        status,
        reminder_time
    ),

    INDEX idx_schedule_medicine (
        user_medicine_id
    ),

    CONSTRAINT fk_schedule_medicine
        FOREIGN KEY (user_medicine_id)
        REFERENCES user_medicines(id)
        ON DELETE RESTRICT,

    CONSTRAINT fk_schedule_device
        FOREIGN KEY (device_id)
        REFERENCES devices(id)
        ON DELETE RESTRICT

) ENGINE=InnoDB
DEFAULT CHARSET=utf8mb4
COLLATE=utf8mb4_unicode_ci;


-- =====================================================
-- 13. DOSE_EVENTS
-- Ghi nhan tung lan nhac thuoc cu the
-- =====================================================

CREATE TABLE IF NOT EXISTS dose_events (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,

    schedule_id BIGINT UNSIGNED NOT NULL,

    device_id BIGINT UNSIGNED NOT NULL,

    -- Thoi gian du kien theo UTC
    scheduled_at DATETIME NOT NULL,

    compartment_number TINYINT UNSIGNED NOT NULL,

    -- Snapshot thong tin thuoc khi tao su kien
    medicine_name_snapshot VARCHAR(255) NOT NULL,

    strength_snapshot VARCHAR(255) DEFAULT NULL,

    dose_quantity_snapshot DECIMAL(10,3) NOT NULL,

    dose_unit_snapshot VARCHAR(60) NOT NULL,

    status ENUM(
        'pending',
        'reminded',
        'retrieved',
        'missed',
        'cancelled'
    ) NOT NULL DEFAULT 'pending',

    reminder_sent_at DATETIME DEFAULT NULL,

    retrieved_at DATETIME DEFAULT NULL,

    missed_at DATETIME DEFAULT NULL,

    notes TEXT DEFAULT NULL,

    created_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uq_dose_schedule_datetime (
        schedule_id,
        scheduled_at
    ),

    INDEX idx_dose_device_datetime (
        device_id,
        scheduled_at
    ),

    INDEX idx_dose_status (
        status
    ),

    CONSTRAINT chk_dose_event_slot
        CHECK (
            compartment_number BETWEEN 1 AND 4
        ),

    CONSTRAINT fk_dose_schedule
        FOREIGN KEY (schedule_id)
        REFERENCES medicine_schedules(id)
        ON DELETE RESTRICT,

    CONSTRAINT fk_dose_device
        FOREIGN KEY (device_id)
        REFERENCES devices(id)
        ON DELETE RESTRICT

) ENGINE=InnoDB
DEFAULT CHARSET=utf8mb4
COLLATE=utf8mb4_unicode_ci;


-- =====================================================
-- 14. DEVICE_EVENTS
-- Luu su kien ESP32 / ARDUINO NANO
-- =====================================================

CREATE TABLE IF NOT EXISTS device_events (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,

    device_id BIGINT UNSIGNED NOT NULL,

    dose_event_id BIGINT UNSIGNED DEFAULT NULL,

    -- ID su kien duy nhat tranh gui trung
    event_uid VARCHAR(64) NOT NULL,

    event_type ENUM(
        'boot',
        'heartbeat',
        'uart_ack',
        'ir_triggered',
        'medicine_retrieved',
        'wrong_compartment',
        'missed_dose',
        'sync_success',
        'sync_failed',
        'other'
    ) NOT NULL,

    compartment_number TINYINT UNSIGNED
        DEFAULT NULL,

    raw_uart_message VARCHAR(255) DEFAULT NULL,

    payload_json LONGTEXT DEFAULT NULL,

    occurred_at DATETIME DEFAULT NULL,

    received_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    UNIQUE KEY uq_device_event_uid (
        event_uid
    ),

    INDEX idx_event_device_time (
        device_id,
        received_at
    ),

    INDEX idx_event_dose (
        dose_event_id
    ),

    INDEX idx_event_type (
        event_type
    ),

    CONSTRAINT fk_event_device
        FOREIGN KEY (device_id)
        REFERENCES devices(id)
        ON DELETE RESTRICT,

    CONSTRAINT fk_event_dose
        FOREIGN KEY (dose_event_id)
        REFERENCES dose_events(id)
        ON DELETE SET NULL

) ENGINE=InnoDB
DEFAULT CHARSET=utf8mb4
COLLATE=utf8mb4_unicode_ci;


-- =====================================================
-- 15. NOTIFICATIONS
-- Thong bao tren Web Dashboard
-- =====================================================

CREATE TABLE IF NOT EXISTS notifications (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,

    user_id BIGINT UNSIGNED NOT NULL,

    device_id BIGINT UNSIGNED DEFAULT NULL,

    dose_event_id BIGINT UNSIGNED DEFAULT NULL,

    notification_type ENUM(
        'upcoming',
        'retrieved',
        'missed',
        'wrong_compartment',
        'device_offline',
        'sync_error',
        'system'
    ) NOT NULL DEFAULT 'system',

    title VARCHAR(200) NOT NULL,

    message TEXT NOT NULL,

    is_read TINYINT(1)
        NOT NULL DEFAULT 0,

    read_at DATETIME DEFAULT NULL,

    created_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_notification_user (
        user_id,
        is_read,
        created_at
    ),

    CONSTRAINT fk_notification_user
        FOREIGN KEY (user_id)
        REFERENCES users(id)
        ON DELETE RESTRICT,

    CONSTRAINT fk_notification_device
        FOREIGN KEY (device_id)
        REFERENCES devices(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_notification_dose
        FOREIGN KEY (dose_event_id)
        REFERENCES dose_events(id)
        ON DELETE SET NULL

) ENGINE=InnoDB
DEFAULT CHARSET=utf8mb4
COLLATE=utf8mb4_unicode_ci;


-- =====================================================
-- THEM NGUON DU LIEU THUOC BAN DAU
-- =====================================================

INSERT IGNORE INTO drug_sources (
    source_code,
    source_name,
    base_url,
    source_type,
    collection_method,
    usage_notes
)
VALUES (
    'DAV_VN',
    'Cuc Quan ly Duoc - Bo Y te',
    'https://dichvucong.dav.gov.vn/congbothuoc/index',
    'official',
    'excel',
    'Nguon tham chieu chinh thuc. Can doi chieu tinh trang dang ky, gia han va thu hoi truoc khi xac minh.'
);


-- =====================================================
-- KIEM TRA KET QUA
-- KHONG SU DUNG INFORMATION_SCHEMA
-- =====================================================

SHOW TABLES;

SELECT
    id,
    source_code,
    source_name,
    source_type
FROM drug_sources;





-- Update database để thêm được thuốc cho từng ngăn
USE `Med`;

-- Lay dung kieu du lieu ID cua bang user_medicines.
SET @medicine_id_type = (
    SELECT COLUMN_TYPE
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_medicines'
      AND COLUMN_NAME = 'id'
    LIMIT 1
);

-- Them cot gan thuoc vao bang 4 ngan.
SET @sql = CONCAT(
    'ALTER TABLE device_compartments ',
    'ADD COLUMN user_medicine_id ',
    @medicine_id_type,
    ' NULL, ',
    'ADD INDEX idx_dc_user_medicine (user_medicine_id), ',
    'ADD CONSTRAINT fk_dc_user_medicine ',
    'FOREIGN KEY (user_medicine_id) ',
    'REFERENCES user_medicines(id) ',
    'ON DELETE RESTRICT ON UPDATE RESTRICT'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Luu lai lich su thay doi thuoc trong cac ngan.
CREATE TABLE IF NOT EXISTS compartment_assignment_history (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    device_id BIGINT UNSIGNED NOT NULL,
    compartment_number TINYINT UNSIGNED NOT NULL,
    previous_user_medicine_id BIGINT UNSIGNED NULL,
    new_user_medicine_id BIGINT UNSIGNED NULL,
    previous_medicine_name VARCHAR(255) NULL,
    new_medicine_name VARCHAR(255) NULL,
    changed_by_user_id BIGINT UNSIGNED NOT NULL,
    changed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_assignment_device (device_id, changed_at),
    INDEX idx_assignment_user (changed_by_user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
