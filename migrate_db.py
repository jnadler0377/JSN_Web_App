# migrate_db.py
"""
Database migration script to apply new features
Run this to upgrade your existing database
"""

import sqlite3
import sys
from pathlib import Path

def run_migration(db_path: str = "foreclosures.db"):
    """Apply database migrations"""
    
    if not Path(db_path).exists():
        print(f"❌ Database not found: {db_path}")
        print("Creating new database...")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("🔄 Running database migrations...")
    
    # Read migration SQL
    migrations_sql = """
    -- ========== INDEXES FOR PERFORMANCE ==========
    CREATE INDEX IF NOT EXISTS idx_cases_filing_date ON cases(filing_datetime);
    CREATE INDEX IF NOT EXISTS idx_cases_archived ON cases(archived);
    CREATE INDEX IF NOT EXISTS idx_cases_arv ON cases(arv) WHERE arv IS NOT NULL;
    CREATE INDEX IF NOT EXISTS idx_cases_parcel ON cases(parcel_id);
    CREATE INDEX IF NOT EXISTS idx_cases_case_number ON cases(case_number);
    CREATE INDEX IF NOT EXISTS idx_notes_case_id ON notes(case_id);
    CREATE INDEX IF NOT EXISTS idx_notes_created ON notes(created_at);
    CREATE INDEX IF NOT EXISTS idx_defendants_case_id ON defendants(case_id);
    CREATE INDEX IF NOT EXISTS idx_skiptrace_case_id ON case_skiptrace(case_id);
    CREATE INDEX IF NOT EXISTS idx_skiptrace_phone_case_id ON case_skiptrace_phone(case_id);
    CREATE INDEX IF NOT EXISTS idx_skiptrace_email_case_id ON case_skiptrace_email(case_id);
    CREATE INDEX IF NOT EXISTS idx_property_case_id ON case_property(case_id);
    
    -- ========== MULTI-USER SYSTEM ==========
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        hashed_password TEXT NOT NULL,
        full_name TEXT,
        role TEXT DEFAULT 'analyst',
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
        assigned_by INTEGER,
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
        contact_method TEXT,
        outcome TEXT,
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
        category TEXT,
        created_by INTEGER,
        created_at TEXT,
        updated_at TEXT,
        FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL
    );
    
    -- ========== SAVED SEARCHES ==========
    CREATE TABLE IF NOT EXISTS saved_searches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        filters_json TEXT NOT NULL,
        is_default INTEGER DEFAULT 0,
        created_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    
    CREATE INDEX IF NOT EXISTS idx_saved_searches_user ON saved_searches(user_id);
    
    -- ========== COMPARABLES ==========
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
        source TEXT,
        fetched_at TEXT,
        FOREIGN KEY(case_id) REFERENCES cases(id) ON DELETE CASCADE
    );
    
    CREATE INDEX IF NOT EXISTS idx_comps_case ON property_comparables(case_id);
    CREATE INDEX IF NOT EXISTS idx_comps_sale_date ON property_comparables(sale_date);
    
    -- ========== AUDIT LOG ==========
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id INTEGER,
        changes_json TEXT,
        ip_address TEXT,
        user_agent TEXT,
        timestamp TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
    );
    
    CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp);
    CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs(user_id);
    CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_logs(entity_type, entity_id);
    
    -- ========== DOCUMENT METADATA ==========
    CREATE TABLE IF NOT EXISTS document_metadata (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id INTEGER NOT NULL,
        document_type TEXT,
        file_path TEXT,
        file_size_bytes INTEGER,
        uploaded_at TEXT,
        uploaded_by INTEGER,
        ocr_completed INTEGER DEFAULT 0,
        ocr_text TEXT,
        extracted_data_json TEXT,
        FOREIGN KEY(case_id) REFERENCES cases(id) ON DELETE CASCADE,
        FOREIGN KEY(uploaded_by) REFERENCES users(id) ON DELETE SET NULL
    );
    
    CREATE INDEX IF NOT EXISTS idx_doc_meta_case ON document_metadata(case_id);
    CREATE INDEX IF NOT EXISTS idx_doc_meta_type ON document_metadata(document_type);
    CREATE INDEX IF NOT EXISTS idx_doc_meta_ocr ON document_metadata(ocr_completed);
    
    -- ========== NOTIFICATIONS ==========
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        title TEXT NOT NULL,
        message TEXT,
        type TEXT,
        link TEXT,
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
    
    INSERT OR IGNORE INTO system_settings (key, value, updated_at) VALUES
        ('scraper_last_run', NULL, datetime('now')),
        ('default_closing_cost_pct', '0.045', datetime('now')),
        ('default_wholesale_pct', '0.65', datetime('now')),
        ('default_flip_pct_under_350k', '0.80', datetime('now')),
        ('default_flip_pct_over_350k', '0.85', datetime('now'));
    """
    
    try:
        # Execute migrations
        cursor.executescript(migrations_sql)
        
        # Add new columns to cases table (if they don't exist)
        print("  ➜ Adding new columns to cases table...")
        
        new_columns = [
            ("status", "TEXT DEFAULT 'new'"),
            ("assigned_to", "INTEGER REFERENCES users(id)"),
            ("priority", "INTEGER DEFAULT 0"),
            ("last_contact_date", "TEXT"),
            ("next_followup_date", "TEXT"),
            ("estimated_close_date", "TEXT"),
            ("actual_close_date", "TEXT"),
            ("close_price", "REAL"),
        ]
        
        for col_name, col_type in new_columns:
            try:
                cursor.execute(f"ALTER TABLE cases ADD COLUMN {col_name} {col_type}")
                print(f"    ✓ Added column: {col_name}")
            except sqlite3.OperationalError as e:
                if "duplicate column" in str(e).lower():
                    print(f"    ⊙ Column exists: {col_name}")
                else:
                    raise
        
        # Create indexes on new columns
        print("  ➜ Creating indexes on new columns...")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_cases_assigned_to ON cases(assigned_to)")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_cases_next_followup ON cases(next_followup_date) 
            WHERE next_followup_date IS NOT NULL
        """)
        
        # Create full-text search table
        print("  ➜ Creating full-text search index...")
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS cases_fts USING fts5(
                case_number,
                style,
                address,
                address_override,
                parcel_id,
                content=cases,
                content_rowid=id
            )
        """)
        
        # Populate FTS table with existing data
        cursor.execute("""
            INSERT INTO cases_fts(rowid, case_number, style, address, address_override, parcel_id)
            SELECT id, case_number, style, address, address_override, parcel_id
            FROM cases
            WHERE id NOT IN (SELECT rowid FROM cases_fts)
        """)
        
        # Create FTS triggers
        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS cases_fts_insert AFTER INSERT ON cases BEGIN
                INSERT INTO cases_fts(rowid, case_number, style, address, address_override, parcel_id)
                VALUES (new.id, new.case_number, new.style, new.address, new.address_override, new.parcel_id);
            END
        """)
        
        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS cases_fts_update AFTER UPDATE ON cases BEGIN
                UPDATE cases_fts 
                SET case_number = new.case_number,
                    style = new.style,
                    address = new.address,
                    address_override = new.address_override,
                    parcel_id = new.parcel_id
                WHERE rowid = new.id;
            END
        """)
        
        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS cases_fts_delete AFTER DELETE ON cases BEGIN
                DELETE FROM cases_fts WHERE rowid = old.id;
            END
        """)
        
        conn.commit()
        print("\n✅ Migration completed successfully!")
        print(f"   Database: {db_path}")
        
        # Show table counts
        print("\n📊 Table Summary:")
        tables = [
            'cases', 'users', 'sessions', 'contact_attempts', 
            'property_comparables', 'audit_logs', 'notifications'
        ]
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]
            print(f"   {table:25} {count:6} records")
        
    except Exception as e:
        print(f"\n❌ Migration failed: {e}")
        conn.rollback()
        raise
    
    finally:
        conn.close()


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else "foreclosures.db"
    run_migration(db_path)
