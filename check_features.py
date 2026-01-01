# check_features.py
import os
from pathlib import Path

print("=== CHECKING FEATURE IMPLEMENTATION ===\n")

# Check config
if Path("app/config.py").exists():
    print("✓ app/config.py exists")
else:
    print("✗ app/config.py MISSING")

# Check services
services = [
    "app/services/auth_service.py",
    "app/services/analytics_service.py",
    "app/services/comparables_service.py",
    "app/services/ocr_service.py"
]

for service in services:
    if Path(service).exists():
        print(f"✓ {service} exists")
    else:
        print(f"✗ {service} MISSING")

# Check templates
templates = [
    "app/templates/dashboard.html",
    "app/templates/auth/login.html",
    "app/templates/cases/comparables.html"
]

for template in templates:
    if Path(template).exists():
        print(f"✓ {template} exists")
    else:
        print(f"✗ {template} MISSING")

# Check CSS
if Path("app/static/style_mobile.css").exists():
    print("✓ app/static/style_mobile.css exists")
else:
    print("✗ app/static/style_mobile.css MISSING")

# Check if new routes are in main.py
main_py = Path("app/main.py").read_text()
routes_to_check = [
    ("/dashboard", "Analytics Dashboard"),
    ("/cases/{case_id}/comparables", "Comparables"),
    ("/login", "Authentication"),
]

print("\n=== CHECKING ROUTES IN main.py ===\n")
for route, name in routes_to_check:
    if route in main_py:
        print(f"✓ {name} route found")
    else:
        print(f"✗ {name} route MISSING")

print("\n=== END ===")