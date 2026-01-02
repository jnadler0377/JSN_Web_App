#!/usr/bin/env python3
"""
Fix the users table schema by dropping and recreating it.

Run this script to fix the authentication system:
    python fix_users_table.py
"""
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from app.database import engine
from sqlalchemy import text


def fix_users_table():
    """Drop and recreate the users table with correct schema"""
    print("JSN Holdings - Fix Users Table")
    print("=" * 60)

    with engine.begin() as conn:
        # Check if users table exists
        result = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        ))
        table_exists = result.fetchone() is not None

        if table_exists:
            print("Found existing users table...")

            # Check what columns it has
            result = conn.execute(text("PRAGMA table_info(users)"))
            existing_columns = [row[1] for row in result.fetchall()]
            print(f"  Existing columns: {', '.join(existing_columns)}")

            # Check if there are any users
            result = conn.execute(text("SELECT COUNT(*) FROM users"))
            user_count = result.fetchone()[0]

            if user_count > 0:
                print(f"
WARNING: Table has {user_count} existing user(s)")
                response = input("Drop table and lose existing users? (yes/no): ").strip().lower()
                if response != 'yes':
                    print("Cancelled. No changes made.")
                    return False

            # Drop the table
            print("
Dropping existing users table...")
            conn.execute(text("DROP TABLE users"))
            print("OK: Table dropped")

        # Create the table with correct schema
        print("
Creating users table with correct schema...")
        conn.execute(text("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username VARCHAR NOT NULL UNIQUE,
                email VARCHAR NOT NULL UNIQUE,
                hashed_password VARCHAR NOT NULL,
                full_name VARCHAR DEFAULT '',
                role TEXT DEFAULT 'analyst',
                is_active BOOLEAN DEFAULT 1,
                is_admin BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP
            )
        """))
        print("OK: Users table created successfully")

        # Create indexes
        print("
Creating indexes...")
        conn.execute(text("CREATE INDEX ix_users_username ON users (username)"))
        conn.execute(text("CREATE INDEX ix_users_email ON users (email)"))
        print("OK: Indexes created")

    print("
" + "=" * 60)
    print("SUCCESS! Users table is now properly configured.")
    print("=" * 60)
    print("
Next step: Run 'python create_admin.py' to create your admin user")
    return True


if __name__ == "__main__":
    try:
        success = fix_users_table()
        if success:
            sys.exit(0)
        else:
            sys.exit(1)
    except Exception as e:
        print(f"
ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
