# test_config.py
try:
    from app.config import settings
    print("✓ Config imported successfully")
    print(f"  enable_multi_user: {settings.enable_multi_user}")
    print(f"  enable_analytics: {settings.enable_analytics}")
    print(f"  database_url: {settings.database_url}")
except ImportError as e:
    print(f"✗ Import error: {e}")
    print("  → Make sure app/config.py exists")
except AttributeError as e:
    print(f"✗ Attribute error: {e}")
    print("  → Check that config.py has all required fields")
except Exception as e:
    print(f"✗ Error: {e}")