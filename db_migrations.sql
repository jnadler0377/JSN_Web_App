-- ===============================================
-- Database Optimizations & New Tables
-- Run these migrations to add indexes and new features
-- ===============================================

-- ========== INDEXES FOR PERFORMANCE (Feature 15) ==========

-- Cases table indexes
CREATE INDEX IF NOT EXISTS idx_cases_filing_date ON cases(filing_datetime);
CREATE INDEX IF NOT EXISTS idx_cases_archived ON cases(archived);
CREATE INDEX IF NOT EXISTS idx_cases_arv ON cases(arv) WHERE arv IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cases_parcel ON cases(parcel_id);
CREATE INDEX IF NOT EXISTS idx_cases_case_number ON cases(case_number);

-- Notes indexes
CREATE INDEX IF NOT EXISTS idx_notes_case_id ON notes(case_id);
CREATE INDEX IF NOT EXISTS idx_notes_created ON notes(created_at);

-- Defendants indexes
CREATE INDEX IF NOT EXISTS idx_defendants_case_id ON defendants(case_id);

-- Skip trace indexes
CREATE INDEX IF NOT EXISTS idx_skiptrace_case_id ON case_skiptrace(case_id);
CREATE INDEX IF NOT EXISTS idx_skiptrace_phone_case_id ON case_skiptrace_phone(case_id);
CREATE INDEX IF NOT EXISTS idx_skiptrace_email_case_id ON case_skiptrace_email(case_id);

-- Property lookup indexes
CREATE INDEX IF NOT EXISTS idx_property_case_id ON case_property(case_id);


-- ========== MULTI-USER SYSTEM (Feature 9) ==========

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    hashed_password TEXT NOT NULL,
    full_name TEXT,
    role TEXT DEFAULT 'analyst',  -- admin, analyst, closer, viewer
    is_active INTEGER DEFAULT 1,
    created_at TEXT,
    last_login TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token TEXT UNIQUE NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS case_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    assigned_at TEXT NOT NULL,
    assigned_by INTEGER,  -- user_id who made assignment
    FOREIGN KEY(case_id) REFERENCES cases(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(assigned_by) REFERENCES users(id) ON DELETE SET NULL,
    UNIQUE(case_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_assignments_case ON case_assignments(case_id);
CREATE INDEX IF NOT EXISTS idx_assignments_user ON case_assignments(user_id);


-- ========== CONTACT MANAGEMENT ==========

CREATE TABLE IF NOT EXISTS contact_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL,
    user_id INTEGER,
    attempt_date TEXT NOT NULL,
    contact_method TEXT,  -- phone, email, mail, in_person
    outcome TEXT,  -- answered, voicemail, busy, no_answer, bounced, delivered
    notes TEXT,
    next_followup_date TEXT,
    created_at TEXT,
    FOREIGN KEY(case_id) REFERENCES cases(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_contact_attempts_case ON contact_attempts(case_id);
CREATE INDEX IF NOT EXISTS idx_contact_attempts_user ON contact_attempts(user_id);
CREATE INDEX IF NOT EXISTS idx_contact_attempts_followup ON contact_attempts(next_followup_date) 
    WHERE next_followup_date IS NOT NULL;

CREATE TABLE IF NOT EXISTS email_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    subject TEXT,
    body TEXT,
    category TEXT,  -- initial_contact, followup, offer, closing
    created_by INTEGER,
    created_at TEXT,
    updated_at TEXT,
    FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL
);


-- ========== SAVED SEARCHES & FILTERS ==========

CREATE TABLE IF NOT EXISTS saved_searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    filters_json TEXT NOT NULL,  -- Store filter state as JSON
    is_default INTEGER DEFAULT 0,
    created_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_saved_searches_user ON saved_searches(user_id);


-- ========== COMPARABLES DATA (Feature 7) ==========

CREATE TABLE IF NOT EXISTS property_comparables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL,
    comp_address TEXT,
    comp_city TEXT,
    comp_state TEXT,
    comp_zip TEXT,
    sale_date TEXT,
    sale_price REAL,
    bedrooms INTEGER,
    bathrooms REAL,
    sqft INTEGER,
    year_built INTEGER,
    distance_miles REAL,
    price_per_sqft REAL,
    source TEXT,  -- zillow, batchdata, mls
    fetched_at TEXT,
    FOREIGN KEY(case_id) REFERENCES cases(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_comps_case ON property_comparables(case_id);
CREATE INDEX IF NOT EXISTS idx_comps_sale_date ON property_comparables(sale_date);


-- ========== AUDIT LOG (Compliance) ==========

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    action TEXT NOT NULL,  -- viewed, created, updated, deleted, exported
    entity_type TEXT NOT NULL,  -- case, document, contact, user
    entity_id INTEGER,
    changes_json TEXT,  -- Before/after state
    ip_address TEXT,
    user_agent TEXT,
    timestamp TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_logs(entity_type, entity_id);


-- ========== CASE STATUS & WORKFLOW ==========

-- Add status column to cases table
ALTER TABLE cases ADD COLUMN status TEXT DEFAULT 'new';
-- Possible values: new, contacted, offer_sent, offer_accepted, 
--                  due_diligence, closing, closed_won, closed_lost, archived

ALTER TABLE cases ADD COLUMN assigned_to INTEGER REFERENCES users(id);
ALTER TABLE cases ADD COLUMN priority INTEGER DEFAULT 0;  -- 0=normal, 1=high, 2=urgent
ALTER TABLE cases ADD COLUMN last_contact_date TEXT;
ALTER TABLE cases ADD COLUMN next_followup_date TEXT;
ALTER TABLE cases ADD COLUMN estimated_close_date TEXT;
ALTER TABLE cases ADD COLUMN actual_close_date TEXT;
ALTER TABLE cases ADD COLUMN close_price REAL;  -- Actual purchase price

CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);
CREATE INDEX IF NOT EXISTS idx_cases_assigned_to ON cases(assigned_to);
CREATE INDEX IF NOT EXISTS idx_cases_next_followup ON cases(next_followup_date) 
    WHERE next_followup_date IS NOT NULL;


-- ========== ANALYTICS MATERIALIZED VIEW (Feature 10) ==========

-- Create a view for analytics dashboard
CREATE VIEW IF NOT EXISTS analytics_summary AS
SELECT 
    COUNT(*) as total_cases,
    COUNT(CASE WHEN archived = 0 THEN 1 END) as active_cases,
    COUNT(CASE WHEN status = 'new' THEN 1 END) as new_cases,
    COUNT(CASE WHEN status LIKE 'closed_won%' THEN 1 END) as won_cases,
    AVG(arv) as avg_arv,
    AVG(rehab) as avg_rehab,
    SUM(CASE WHEN arv > 0 THEN arv - rehab - closing_costs ELSE 0 END) as total_potential_profit,
    strftime('%Y-%m', filing_datetime) as filing_month
FROM cases
GROUP BY filing_month
ORDER BY filing_month DESC;


-- ========== DOCUMENT METADATA (Feature 11) ==========

CREATE TABLE IF NOT EXISTS document_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL,
    document_type TEXT,  -- verified_complaint, mortgage, deed_current, etc.
    file_path TEXT,
    file_size_bytes INTEGER,
    uploaded_at TEXT,
    uploaded_by INTEGER,
    ocr_completed INTEGER DEFAULT 0,
    ocr_text TEXT,  -- Full extracted text
    extracted_data_json TEXT,  -- Structured data extracted by OCR
    FOREIGN KEY(case_id) REFERENCES cases(id) ON DELETE CASCADE,
    FOREIGN KEY(uploaded_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_doc_meta_case ON document_metadata(case_id);
CREATE INDEX IF NOT EXISTS idx_doc_meta_type ON document_metadata(document_type);
CREATE INDEX IF NOT EXISTS idx_doc_meta_ocr ON document_metadata(ocr_completed);


-- ========== FULL-TEXT SEARCH (SQLite FTS5) ==========

-- Create virtual table for full-text search
CREATE VIRTUAL TABLE IF NOT EXISTS cases_fts USING fts5(
    case_number,
    style,
    address,
    address_override,
    parcel_id,
    content=cases,
    content_rowid=id
);

-- Triggers to keep FTS table in sync
CREATE TRIGGER IF NOT EXISTS cases_fts_insert AFTER INSERT ON cases BEGIN
    INSERT INTO cases_fts(rowid, case_number, style, address, address_override, parcel_id)
    VALUES (new.id, new.case_number, new.style, new.address, new.address_override, new.parcel_id);
END;

CREATE TRIGGER IF NOT EXISTS cases_fts_update AFTER UPDATE ON cases BEGIN
    UPDATE cases_fts 
    SET case_number = new.case_number,
        style = new.style,
        address = new.address,
        address_override = new.address_override,
        parcel_id = new.parcel_id
    WHERE rowid = new.id;
END;

CREATE TRIGGER IF NOT EXISTS cases_fts_delete AFTER DELETE ON cases BEGIN
    DELETE FROM cases_fts WHERE rowid = old.id;
END;


-- ========== NOTIFICATIONS ==========

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    title TEXT NOT NULL,
    message TEXT,
    type TEXT,  -- info, success, warning, error
    link TEXT,  -- URL to related resource
    is_read INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_unread ON notifications(user_id, is_read) 
    WHERE is_read = 0;


-- ========== SYSTEM SETTINGS ==========

CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT,
    updated_by INTEGER,
    FOREIGN KEY(updated_by) REFERENCES users(id) ON DELETE SET NULL
);

-- Insert default settings
INSERT OR IGNORE INTO system_settings (key, value, updated_at) VALUES
    ('scraper_last_run', NULL, datetime('now')),
    ('default_closing_cost_pct', '0.045', datetime('now')),
    ('default_wholesale_pct', '0.65', datetime('now')),
    ('default_flip_pct_under_350k', '0.80', datetime('now')),
    ('default_flip_pct_over_350k', '0.85', datetime('now'));
