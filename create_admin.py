#!/usr/bin/env python3
"""
Initialize authentication system and create the first admin user.

Run this script once to set up user authentication:
    python create_admin.py
"""
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import engine, SessionLocal
from app.auth import User, get_password_hash
from sqlalchemy import inspect

def create_users_table():
    """Create the users table if it doesn't exist"""
    from sqlalchemy import text, inspect as sqlalchemy_inspect
    
    print("Checking users table...")
    
    # Check if table exists
    inspector = sqlalchemy_inspect(engine)
    if 'users' in inspector.get_table_names():
        # Verify schema
        columns = {col['name'] for col in inspector.get_columns('users')}
        required_columns = {'id', 'username', 'email', 'hashed_password', 'full_name',
                          'role', 'is_active', 'is_admin', 'created_at', 'last_login'}
        
        if not required_columns.issubset(columns):
            print("\n✗ ERROR: Users table exists but has incorrect schema!")
            print(f"  Found columns: {', '.join(sorted(columns))}")
            print(f"  Required columns: {', '.join(sorted(required_columns))}")
            print("\nPlease run: python fix_users_table.py")
            sys.exit(1)
        
        print("✓ Users table exists with correct schema")
    else:
        print("Creating users table...")
        User.__table__.create(engine, checkfirst=True)
        print("✓ Users table created")

def create_admin_user():
    """Create the initial admin user"""
    db = SessionLocal()
    try:
        # Check if admin already exists
        existing_admin = db.query(User).filter(User.username == "admin").first()
        if existing_admin:
            print("⚠ Admin user already exists!")
            print(f"  Username: admin")
            return
        
        # Get admin credentials
        print("\n" + "="*50)
        print("CREATE ADMINISTRATOR ACCOUNT")
        print("="*50)
        
        username = input("Admin username (default: admin): ").strip() or "admin"
        email = input("Admin email: ").strip()
        while not email:
            print("  Error: Email is required")
            email = input("Admin email: ").strip()
        
        full_name = input("Full name (optional): ").strip()
        
        import getpass
        while True:
            password = getpass.getpass("Admin password (min 8 characters): ")
            if len(password) < 8:
                print("  Error: Password must be at least 8 characters")
                continue
            
            confirm = getpass.getpass("Confirm password: ")
            if password != confirm:
                print("  Error: Passwords do not match")
                continue
            
            break
        
        # Create admin user
        admin = User(
            username=username,
            email=email,
            full_name=full_name,
            hashed_password=get_password_hash(password),
            is_admin=True,
            is_active=True
        )
        
        db.add(admin)
        db.commit()
        
        print("\n✓ Administrator account created successfully!")
        print(f"  Username: {username}")
        print(f"  Email: {email}")
        print("\nYou can now login at: http://localhost:8000/login")
        
    except KeyboardInterrupt:
        print("\n\nSetup cancelled")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Error creating admin user: {e}")
        db.rollback()
        raise
    finally:
        db.close()

def main():
    """Main setup function"""
    print("JSN Holdings Foreclosure Manager - Authentication Setup")
    print("="*60)
    
    try:
        create_users_table()
        create_admin_user()
        
        print("\n" + "="*60)
        print("NEXT STEPS:")
        print("="*60)
        print("1. Restart your application server")
        print("2. Visit http://localhost:8000/login")
        print("3. Login with your admin credentials")
        print("4. Create additional users from Users menu")
        print("="*60)
        
    except Exception as e:
        print(f"\n✗ Setup failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()