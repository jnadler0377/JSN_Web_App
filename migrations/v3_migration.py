#!/usr/bin/env python3
"""
V3 Database Migration Script - Phase 1-4
Run this script to create the necessary tables and columns for V3 features.

Usage:
    python migrations/v3_migration.py

Or from Python:
    from migrations.v3_migration import run_migration
    run_migration()
"""

import sys
import os

# Add app directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_migration():
    """Run all V3 migrations."""
    from app.database import engine
    from sqlalchemy import text, inspect
    
    print("=" * 60)
    print("JSN Web App V3 Migration (Phase 1-4)")
    print("=" * 60)
    
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()
    
    # ============================================================
    # Migration 1: Add assigned_at column to cases table
    # ============================================================
    print("\n[1/7] Checking cases.assigned_at column...")
    
    try:
        # Check if column exists
        columns = [col['name'] for col in inspector.get_columns('cases')]
        
        if 'assigned_at' not in columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE cases ADD COLUMN assigned_at DATETIME NULL"))
            print("  ✓ Added assigned_at column to cases table")
        else:
            print("  - Column already exists, skipping")
    except Exception as e:
        print(f"  ✗ Error: {e}")
    
    # ============================================================
    # Migration 2: Create case_claims table
    # ============================================================
    print("\n[2/7] Creating case_claims table...")
    
    try:
        if 'case_claims' not in existing_tables:
            with engine.begin() as conn:
                conn.execute(text('''
                    CREATE TABLE case_claims (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        case_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        claimed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        released_at DATETIME NULL,
                        score_at_claim INTEGER DEFAULT 0,
                        price_cents INTEGER DEFAULT 0,
                        is_active BOOLEAN DEFAULT 1,
                        
                        FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE,
                        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                    )
                '''))
            print("  ✓ Created case_claims table")
        else:
            print("  - Table already exists, skipping")
    except Exception as e:
        print(f"  ✗ Error: {e}")
    
    # ============================================================
    # Migration 3: Create indexes on case_claims
    # ============================================================
    print("\n[3/7] Creating indexes...")
    
    indexes = [
        ('idx_claims_case_id', 'CREATE INDEX IF NOT EXISTS idx_claims_case_id ON case_claims(case_id)'),
        ('idx_claims_user_id', 'CREATE INDEX IF NOT EXISTS idx_claims_user_id ON case_claims(user_id)'),
        ('idx_claims_active', 'CREATE INDEX IF NOT EXISTS idx_claims_active ON case_claims(is_active)'),
        ('idx_claims_date', 'CREATE INDEX IF NOT EXISTS idx_claims_date ON case_claims(claimed_at)'),
        ('idx_cases_assigned_at', 'CREATE INDEX IF NOT EXISTS idx_cases_assigned_at ON cases(assigned_at)'),
    ]
    
    for idx_name, idx_sql in indexes:
        try:
            with engine.begin() as conn:
                conn.execute(text(idx_sql))
            print(f"  ✓ Created index: {idx_name}")
        except Exception as e:
            print(f"  - Index {idx_name}: {e}")
    
    # ============================================================
    # Migration 4: Add V3 columns to users table
    # ============================================================
    print("\n[4/7] Checking users table for V3 columns...")
    
    try:
        columns = [col['name'] for col in inspector.get_columns('users')]
        
        v3_user_columns = [
            ('stripe_customer_id', 'ALTER TABLE users ADD COLUMN stripe_customer_id TEXT NULL'),
            ('max_claims', 'ALTER TABLE users ADD COLUMN max_claims INTEGER DEFAULT 50'),
            ('is_billing_active', 'ALTER TABLE users ADD COLUMN is_billing_active BOOLEAN DEFAULT 1'),
        ]
        
        for col_name, col_sql in v3_user_columns:
            if col_name not in columns:
                try:
                    with engine.begin() as conn:
                        conn.execute(text(col_sql))
                    print(f"  ✓ Added column: users.{col_name}")
                except Exception as e:
                    print(f"  - Column users.{col_name}: {e}")
            else:
                print(f"  - Column users.{col_name} already exists")
    except Exception as e:
        print(f"  ✗ Error checking users table: {e}")
    
    # ============================================================
    # Migration 5: Create invoices table (Phase 4)
    # ============================================================
    print("\n[5/7] Creating invoices table (Phase 4)...")
    
    try:
        if 'invoices' not in existing_tables:
            with engine.begin() as conn:
                conn.execute(text('''
                    CREATE TABLE invoices (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        invoice_number TEXT UNIQUE NOT NULL,
                        invoice_date DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        due_date DATETIME NULL,
                        subtotal_cents INTEGER DEFAULT 0,
                        tax_cents INTEGER DEFAULT 0,
                        total_cents INTEGER DEFAULT 0,
                        status TEXT DEFAULT 'pending',
                        stripe_invoice_id TEXT NULL,
                        stripe_payment_intent TEXT NULL,
                        stripe_hosted_url TEXT NULL,
                        paid_at DATETIME NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                    )
                '''))
                conn.execute(text('CREATE INDEX IF NOT EXISTS idx_invoices_user_id ON invoices(user_id)'))
                conn.execute(text('CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status)'))
                conn.execute(text('CREATE INDEX IF NOT EXISTS idx_invoices_date ON invoices(invoice_date)'))
                conn.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS idx_invoices_number ON invoices(invoice_number)'))
            print("  ✓ Created invoices table with indexes")
        else:
            print("  - Table already exists, skipping")
    except Exception as e:
        print(f"  ✗ Error: {e}")
    
    # ============================================================
    # Migration 6: Create invoice_lines table (Phase 4)
    # ============================================================
    print("\n[6/7] Creating invoice_lines table (Phase 4)...")
    
    try:
        if 'invoice_lines' not in existing_tables:
            with engine.begin() as conn:
                conn.execute(text('''
                    CREATE TABLE invoice_lines (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        invoice_id INTEGER NOT NULL,
                        claim_id INTEGER NULL,
                        case_id INTEGER NULL,
                        description TEXT NOT NULL,
                        quantity INTEGER DEFAULT 1,
                        unit_price_cents INTEGER DEFAULT 0,
                        amount_cents INTEGER DEFAULT 0,
                        case_number TEXT NULL,
                        score_at_invoice INTEGER DEFAULT 0,
                        service_date DATETIME NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE,
                        FOREIGN KEY (claim_id) REFERENCES case_claims(id) ON DELETE SET NULL,
                        FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE SET NULL
                    )
                '''))
                conn.execute(text('CREATE INDEX IF NOT EXISTS idx_invoice_lines_invoice ON invoice_lines(invoice_id)'))
                conn.execute(text('CREATE INDEX IF NOT EXISTS idx_invoice_lines_claim ON invoice_lines(claim_id)'))
            print("  ✓ Created invoice_lines table with indexes")
        else:
            print("  - Table already exists, skipping")
    except Exception as e:
        print(f"  ✗ Error: {e}")
    
    # ============================================================
    # Migration 7: Create webhook_logs table (Phase 5)
    # ============================================================
    print("\n[7/8] Creating webhook_logs table (Phase 5)...")
    
    try:
        if 'webhook_logs' not in existing_tables:
            with engine.begin() as conn:
                conn.execute(text('''
                    CREATE TABLE webhook_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_type TEXT NOT NULL,
                        event_id TEXT NOT NULL,
                        result TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                '''))
                conn.execute(text('CREATE INDEX IF NOT EXISTS idx_webhook_logs_type ON webhook_logs(event_type)'))
                conn.execute(text('CREATE INDEX IF NOT EXISTS idx_webhook_logs_date ON webhook_logs(created_at)'))
            print("  ✓ Created webhook_logs table")
        else:
            print("  - Table already exists, skipping")
    except Exception as e:
        print(f"  ✗ Error: {e}")
    
    # ============================================================
    # Verification
    # ============================================================
    print("\n[8/8] Verification...")
    print("=" * 60)
    
    # Refresh inspector
    inspector = inspect(engine)
    
    # Check all tables
    tables_to_check = ['case_claims', 'invoices', 'invoice_lines']
    for table_name in tables_to_check:
        if table_name in inspector.get_table_names():
            columns = [col['name'] for col in inspector.get_columns(table_name)]
            print(f"\n✓ {table_name} table exists with {len(columns)} columns")
        else:
            print(f"\n✗ {table_name} table NOT found")
    
    # Check cases.assigned_at
    cases_columns = [col['name'] for col in inspector.get_columns('cases')]
    if 'assigned_at' in cases_columns:
        print("\n✓ cases.assigned_at column exists")
    else:
        print("\n✗ cases.assigned_at column NOT found")
    
    print("\n" + "=" * 60)
    print("V3 Migration Complete!")
    print("=" * 60)


if __name__ == "__main__":
    run_migration()
