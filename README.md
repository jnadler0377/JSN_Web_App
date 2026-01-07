# JSN Web App V3.0 Implementation
## Case Claiming, Data Masking, Document Locking, Billing & Stripe Integration

**Version:** 3.0.0 (Phases 1-6 Complete)  
**Date:** January 5, 2026  
**Status:** Ready for Integration

---

## 📁 Files Included

```
v3_implementation/
├── app/
│   ├── models.py                   # Updated with CaseClaim, Invoice, InvoiceLine
│   ├── routes/
│   │   ├── claim_routes.py         # Case claim/release API endpoints
│   │   ├── billing_routes.py       # Invoice and billing API endpoints
│   │   ├── document_routes.py      # Updated with ownership checks
│   │   ├── webhook_routes.py       # Phase 5: Stripe webhooks
│   │   └── admin_v3_routes.py      # Phase 6: Admin billing/claims pages
│   ├── services/
│   │   ├── permission_service.py   # Access control logic
│   │   ├── claim_service.py        # Claim/release business logic
│   │   ├── pricing_service.py      # Tiered pricing
│   │   ├── billing_service.py      # Invoice generation
│   │   ├── masking_service.py      # Data masking utilities
│   │   └── stripe_service.py       # Phase 5: Stripe integration
│   └── jobs/
│       └── daily_billing_job.py    # Phase 5: Automated billing
├── migrations/
│   └── v3_migration.py             # Database migration script
├── templates/
│   ├── cases_list.html             # Updated with claim buttons
│   ├── case_detail.html            # Updated with claim/release UI
│   ├── billing.html                # User billing page
│   ├── admin_billing.html          # Phase 6: Admin billing dashboard
│   ├── admin_claims.html           # Phase 6: Admin claims management
│   └── cases/
│       ├── _case_overview_tab.html # Updated with masking
│       └── _case_documents_tab.html# Updated with document locking
├── main.py                         # Fully updated with V3 features
└── README.md                       # This file
```

---

## 🚀 Quick Start Installation

### Step 1: Backup Your Database
```bash
cp foreclosures.db foreclosures.db.backup
```

### Step 2: Install Dependencies
```bash
pip install stripe  # For Phase 5 Stripe integration
```

### Step 3: Add Environment Variables
```bash
# Add to your .env file (Phase 5 - Stripe)
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
```

### Step 4: Copy Files
```bash
# Replace main.py
cp main.py /path/to/your/app/

# Replace models.py
cp app/models.py /path/to/your/app/

# Copy all services
cp app/services/*.py /path/to/your/app/services/

# Copy all routes
cp app/routes/*.py /path/to/your/app/routes/

# Copy jobs
mkdir -p /path/to/your/app/jobs
cp app/jobs/*.py /path/to/your/app/jobs/

# Copy templates
cp templates/*.html /path/to/your/templates/
cp -r templates/cases/ /path/to/your/templates/
```

### Step 5: Restart Application
The migration runs automatically on startup:
```bash
uvicorn app.main:app --reload
```

---

## 📋 Phase Summary

### Phase 1 & 2: Case Claiming ✅
- Claim/release cases with transactional locking
- Bulk claim/release functionality
- Claims counter and filtering
- Score-based pricing frozen at claim time

### Phase 3: Data Masking ✅
- Address masking for non-owners
- Skip trace data locked until claimed
- Document download protection
- Visual indicators for locked content

### Phase 4: Billing Ledger ✅
- Invoice model with line items
- Daily invoice generation
- User billing summary page
- Admin billing dashboard

### Phase 5: Stripe Integration ✅
- Stripe customer creation
- Automatic invoice sync to Stripe
- Webhook handling for payment events
- Billing portal integration
- Daily billing job automation

### Phase 6: Admin Tools ✅
- Admin billing dashboard
- Admin claims management
- Bulk release operations
- User claim limit management
- Audit logging
- System health monitoring

---

## 📋 API Endpoints

### Claim Endpoints (Phase 1-2)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v3/cases/claim` | Claim one or more cases |
| POST | `/api/v3/cases/release` | Release one or more cases |
| POST | `/api/v3/cases/{id}/claim` | Claim single case |
| POST | `/api/v3/cases/{id}/release` | Release single case |
| GET | `/api/v3/claims` | Get user's claims |
| GET | `/api/v3/claims/count` | Get claim count |

### Billing Endpoints (Phase 4)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v3/billing/invoices` | Get user's invoices |
| GET | `/api/v3/billing/invoices/{id}` | Get invoice details |
| GET | `/api/v3/billing/summary` | Get user's billing summary |
| GET | `/api/v3/billing/pricing` | Get pricing tiers |

### Webhook Endpoint (Phase 5)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/webhooks/stripe` | Stripe webhook receiver |

### Admin Endpoints (Phase 6)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/admin/billing` | Admin billing dashboard page |
| GET | `/admin/claims` | Admin claims management page |
| GET | `/api/v3/admin/billing/summary` | Overall billing stats |
| POST | `/api/v3/admin/billing/generate-daily` | Generate daily invoices |
| GET | `/api/v3/admin/billing/invoices` | List all invoices |
| POST | `/api/v3/admin/billing/invoices/{id}/mark-paid` | Mark invoice paid |
| GET | `/api/v3/admin/claims` | List all claims |
| GET | `/api/v3/admin/claims/stats` | Claims statistics |
| POST | `/api/v3/admin/users/{id}/release-all-claims` | Release all user claims |
| POST | `/api/v3/admin/users/{id}/set-claim-limit` | Set user claim limit |
| POST | `/api/v3/admin/users/{id}/toggle-billing` | Enable/disable billing |
| GET | `/api/v3/admin/system/health` | System health check |

---

## 💰 Pricing Tiers

| Tier | Score Range | Daily Price |
|------|-------------|-------------|
| Excellent | 80-100 | $15.00 |
| Good | 60-79 | $10.00 |
| Fair | 40-59 | $5.00 |
| Poor | 0-39 | $2.50 |

Prices are frozen at claim time for billing accuracy.

---

## 🔒 Stripe Setup (Phase 5)

### 1. Create Stripe Account
Go to [stripe.com](https://stripe.com) and create an account.

### 2. Get API Keys
In your Stripe Dashboard:
- Go to Developers → API Keys
- Copy the Secret Key (starts with `sk_test_` or `sk_live_`)
- Copy the Publishable Key (starts with `pk_test_` or `pk_live_`)

### 3. Set Up Webhook
In Stripe Dashboard:
- Go to Developers → Webhooks
- Add endpoint: `https://yourdomain.com/webhooks/stripe`
- Select events:
  - `invoice.paid`
  - `invoice.payment_failed`
  - `invoice.created`
  - `invoice.finalized`
- Copy the Signing Secret (starts with `whsec_`)

### 4. Configure Environment
```bash
# .env file
STRIPE_SECRET_KEY=sk_test_your_key_here
STRIPE_PUBLISHABLE_KEY=pk_test_your_key_here
STRIPE_WEBHOOK_SECRET=whsec_your_secret_here
```

---

## ⏰ Daily Billing Job (Phase 5)

### Manual Execution
```bash
python -m app.jobs.daily_billing_job
```

### With Options
```bash
# Specific date
python -m app.jobs.daily_billing_job --date 2026-01-04

# Dry run (see what would be generated)
python -m app.jobs.daily_billing_job --dry-run

# Skip Stripe sync
python -m app.jobs.daily_billing_job --no-stripe

# Backfill from date
python -m app.jobs.daily_billing_job --backfill-from 2026-01-01
```

### Cron Job
Add to crontab (run daily at midnight):
```cron
0 0 * * * cd /path/to/app && python -m app.jobs.daily_billing_job >> /var/log/billing.log 2>&1
```

---

## 🗄️ Database Schema

### New Tables

#### case_claims
```sql
CREATE TABLE case_claims (
    id INTEGER PRIMARY KEY,
    case_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    claimed_at DATETIME NOT NULL,
    released_at DATETIME,
    score_at_claim INTEGER,
    price_cents INTEGER,
    is_active BOOLEAN DEFAULT 1
);
```

#### invoices
```sql
CREATE TABLE invoices (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    invoice_number TEXT UNIQUE NOT NULL,
    invoice_date DATETIME NOT NULL,
    due_date DATETIME,
    subtotal_cents INTEGER DEFAULT 0,
    tax_cents INTEGER DEFAULT 0,
    total_cents INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    stripe_invoice_id TEXT,
    stripe_payment_intent TEXT,
    stripe_hosted_url TEXT,
    paid_at DATETIME
);
```

#### invoice_lines
```sql
CREATE TABLE invoice_lines (
    id INTEGER PRIMARY KEY,
    invoice_id INTEGER NOT NULL,
    claim_id INTEGER,
    case_id INTEGER,
    description TEXT NOT NULL,
    quantity INTEGER DEFAULT 1,
    unit_price_cents INTEGER DEFAULT 0,
    amount_cents INTEGER DEFAULT 0,
    case_number TEXT,
    score_at_invoice INTEGER,
    service_date DATETIME
);
```

#### webhook_logs (Phase 5)
```sql
CREATE TABLE webhook_logs (
    id INTEGER PRIMARY KEY,
    event_type TEXT NOT NULL,
    event_id TEXT NOT NULL,
    result TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### Modified Tables

#### cases
- Added: `assigned_at DATETIME` - Timestamp when case was claimed

#### users
- Added: `stripe_customer_id TEXT` - For Stripe integration
- Added: `max_claims INTEGER DEFAULT 50` - Claim limit per user
- Added: `is_billing_active BOOLEAN DEFAULT 1` - Enable/disable billing

---

## 🎯 Admin Dashboard

### Access
Navigate to `/admin/billing` or `/admin/claims` (requires admin login).

### Features

#### Billing Dashboard (`/admin/billing`)
- Total billed/collected/pending/failed amounts
- Active claims and estimated daily revenue
- Invoice listing with filters
- Generate daily invoices
- Mark invoices as paid/failed
- Export invoices to CSV

#### Claims Management (`/admin/claims`)
- Total/active/released claims stats
- Daily revenue from claims
- Claims listing with filters
- Release individual claims
- Bulk release all user claims
- Reassign cases to different users
- Set user claim limits
- Export claims to CSV

---

## ⚠️ Important Notes

1. **Backup your database** before running migrations
2. **Backup templates** before overwriting
3. **Test with Stripe test keys** before using live keys
4. Migration is **non-destructive** - only adds new tables/columns
5. Existing `assigned_to` values are preserved
6. Set up webhook endpoint **before** enabling live payments

---

## 🐛 Troubleshooting

### "Stripe not configured"
Ensure `STRIPE_SECRET_KEY` is set in your environment or .env file.

### "Invoice already exists for this date"
This is normal - invoices are idempotent. Use `force: true` to regenerate.

### Webhook 400 "Invalid signature"
Check that `STRIPE_WEBHOOK_SECRET` matches the webhook signing secret in Stripe Dashboard.

### Admin pages not loading
Ensure `init_admin_v3_templates(templates)` is called in main.py after template initialization.

---

## 📜 Changelog

### v3.0.0 (Phases 1-6 Complete)
- ✅ Case claiming with transactional locking
- ✅ Score-based tiered pricing
- ✅ Bulk claim/release functionality
- ✅ Claims counter and filtering
- ✅ Data masking for non-owners (Phase 3)
- ✅ Document download protection (Phase 3)
- ✅ Invoice model and line items (Phase 4)
- ✅ Daily invoice generation (Phase 4)
- ✅ Stripe customer creation (Phase 5)
- ✅ Stripe invoice sync (Phase 5)
- ✅ Webhook handling (Phase 5)
- ✅ Daily billing job (Phase 5)
- ✅ Admin billing dashboard (Phase 6)
- ✅ Admin claims management (Phase 6)
- ✅ Bulk admin operations (Phase 6)
- ✅ Audit logging (Phase 6)
- ✅ System health monitoring (Phase 6)
