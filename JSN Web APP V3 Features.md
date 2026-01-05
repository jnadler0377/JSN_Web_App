# JSN Web App – Case Claiming, Locking & Daily Billing System

This document defines the **implementation task list**, **system architecture**, and **data flow** for the JSN Web App feature that allows subscribers to exclusively claim foreclosure cases, restrict access to non-owners, and bill users daily based on claimed case scores.

---

## PART I – IMPLEMENTATION TASK LIST

### Phase 1 – Authentication & Ownership Core
- Create or confirm `users` table:
  - `id`, `email`, `password_hash`, `role`, timestamps
- Implement authentication (session or JWT-based)
- Restrict application access to authenticated users
- Add ownership columns to `cases` table:
  - `assigned_to`
  - `assigned_at`
- Create backend permission helper:
  - `can_view_sensitive(case, user)`

---

### Phase 2 – Case Claiming & Locking
- Create `case_claims` table to track:
  - case_id
  - user_id
  - claim date
  - score at claim
  - price at claim
- Build `POST /cases/claim` endpoint
  - Transactional locking
  - Prevent concurrent claims
- Build `POST /cases/release` endpoint
  - Owner or admin only
- Add UI features:
  - Case selection checkboxes
  - Claim Selected / Release Selected buttons
  - Claimed / Unclaimed status badges

---

### Phase 3 – Data Masking & Document Locking
- Mask street address for non-owners
- Mask skip trace data:
  - Phone numbers
  - Emails
  - Names
- Protect document download endpoints
- Show document filenames but disable viewing/downloading
- Add UI indicators:
  - Lock icons
  - “Claim to Unlock” messaging

---

### Phase 4 – Pricing & Billing Ledger
- Define pricing model based on case score:
  - Flat
  - Banded
  - Multiplier
- Freeze billing values at claim time:
  - `case_score_at_claim`
  - `price_cents`
- Create billing tables:
  - `invoices`
  - `invoice_lines`
- Generate one invoice per user per day
- Ensure billing job is idempotent and retry-safe

---

### Phase 5 – Payments & Automation
- Integrate Stripe:
  - Customer creation
  - Payment method storage
- Create Stripe invoice items daily
- Finalize and auto-charge invoices
- Implement Stripe webhook handlers:
  - payment succeeded
  - payment failed
- Add daily background job:
  - cron / task scheduler / worker

---

### Phase 6 – Admin & Safeguards
- Admin case reassignment tools
- Claim limits per user
- Optional claim expiration logic
- Audit views:
  - claims
  - invoices
  - payments
- Dashboard metrics:
  - claimed vs available cases

---

## PART II – SYSTEM ARCHITECTURE

### High-Level Architecture
The JSN Web App follows a layered architecture:

- **Frontend**
  - Case list
  - Claim UI
  - Invoice views
- **FastAPI Backend**
  - Authentication
  - Case locking logic
  - Masking enforcement
- **Database**
  - Users
  - Cases
  - Claims
  - Invoices
- **Background Jobs**
  - Daily invoicing
  - Auto-charging
- **External Services**
  - Stripe (billing & payments)

---

### Core Design Principles
- Server-side enforcement of permissions
- Transactional case locking
- Immutable billing records
- Idempotent background jobs
- PCI compliance delegated to Stripe

---

## PART III – DATA FLOW

### Case Claim Flow
1. User selects cases in the UI
2. UI sends `POST /cases/claim`
3. Backend validates authentication
4. Database transaction locks cases
5. Ownership assigned (`assigned_to`)
6. Claim records created
7. UI updates case status

---

### Data Access Flow (Masked vs Unmasked)
1. User requests case details
2. Backend checks case ownership
3. If owner/admin:
   - Full address
   - Full skip trace
   - Document access
4. If non-owner:
   - Masked address
   - Masked skip trace
   - Locked documents
5. UI renders based on permission

---

### Daily Billing Flow
1. Scheduler runs daily billing job
2. System finds all claims for the day
3. Invoice + line items generated per user
4. Stripe invoice created and finalized
5. Automatic payment attempted
6. Webhook updates invoice status

---

## PART IV – SECURITY & DATA INTEGRITY

- Sensitive data masking enforced server-side
- Documents served only through protected endpoints
- Claims handled in DB transactions to prevent race conditions
- Billing values frozen at claim time
- Stripe handles all payment security and PCI compliance

---

## Notes
This document is designed to be:
- Implementation-ready
- Contractor-safe
- Scalable from SQLite → Postgres
- Suitable for SaaS monetization

