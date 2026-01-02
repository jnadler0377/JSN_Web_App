#!/usr/bin/env python3
"""
Fix bcrypt/passlib compatibility issue

This script will:
1. Uninstall incompatible bcrypt version
2. Install compatible bcrypt 3.2.2 and passlib 1.7.4

Run this if you see: "error reading bcrypt version"
"""

import subprocess
import sys

def run_command(cmd):
    """Run a command and print output"""
    print(f"\n→ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    return result.returncode == 0

def fix_bcrypt():
    print("="*60)
    print("Fixing bcrypt/passlib compatibility")
    print("="*60)
    
    # Uninstall current versions
    print("\n[Step 1/3] Uninstalling current bcrypt and passlib...")
    run_command([sys.executable, "-m", "pip", "uninstall", "-y", "bcrypt", "passlib"])
    
    # Install compatible versions
    print("\n[Step 2/3] Installing compatible bcrypt 3.2.2...")
    if not run_command([sys.executable, "-m", "pip", "install", "bcrypt==3.2.2", "--break-system-packages"]):
        print("\n✗ Failed to install bcrypt")
        print("Try manually: pip install bcrypt==3.2.2 --break-system-packages")
        return False
    
    print("\n[Step 3/3] Installing passlib 1.7.4...")
    if not run_command([sys.executable, "-m", "pip", "install", "passlib==1.7.4", "--break-system-packages"]):
        print("\n✗ Failed to install passlib")
        print("Try manually: pip install passlib==1.7.4 --break-system-packages")
        return False
    
    print("\n" + "="*60)
    print("✓ SUCCESS! Compatible versions installed")
    print("="*60)
    print("\nInstalled:")
    run_command([sys.executable, "-m", "pip", "show", "bcrypt"])
    run_command([sys.executable, "-m", "pip", "show", "passlib"])
    
    print("\n" + "="*60)
    print("Next step: Run 'python fix_users_table.py'")
    print("="*60)
    return True

if __name__ == "__main__":
    try:
        fix_bcrypt()
    except Exception as e:
        print(f"\n✗ Error: {e}")
        print("\nManual fix:")
        print("  pip uninstall -y bcrypt passlib")
        print("  pip install bcrypt==3.2.2 --break-system-packages")
        print("  pip install passlib==1.7.4 --break-system-packages")
        sys.exit(1)