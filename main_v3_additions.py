# ============================================================
# V3 ADDITIONS FOR main.py
# Copy these sections into your main.py file
# ============================================================

# ============================================================
# 1. ADD IMPORTS (at top of file)
# ============================================================

# V3 Claim Routes
from app.routes.claim_routes import router as claim_router

# V3 Services
from app.services.permission_service import can_view_sensitive, get_case_visibility
from app.services.claim_service import get_claim_for_case, get_user_claim_count


# ============================================================
# 2. REGISTER V3 ROUTER (after other router includes)
# ============================================================

# Add this after the other app.include_router() calls:
app.include_router(claim_router)


# ============================================================
# 3. UPDATE /cases ROUTE to include claim info
# ============================================================

# In your existing /cases route, add claim_filter handling and pass claim data to template:

@app.get("/cases", response_class=HTMLResponse)
async def cases_list(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=10, le=100),
    sort_by: str = Query("case_number"),
    sort_order: str = Query("desc"),
    case: str = Query(""),  # search query
    tag: str = Query(""),
    show_archived: int = Query(0),
    show_new: int = Query(0),
    claim_filter: str = Query(""),  # V3: Filter by claim status
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    user_id = user.get("id") if isinstance(user, dict) else user.id
    
    db = SessionLocal()
    try:
        query = db.query(Case)
        
        # V3: Apply claim filter
        if claim_filter == "mine":
            query = query.filter(Case.assigned_to == user_id)
        elif claim_filter == "available":
            query = query.filter(Case.assigned_to.is_(None))
        elif claim_filter == "claimed":
            query = query.filter(Case.assigned_to.isnot(None))
        
        # ... rest of existing filtering logic ...
        
        # Pass claim_filter to template
        return templates.TemplateResponse("cases_list.html", {
            # ... existing context ...
            "claim_filter": claim_filter,
            "current_user": user,  # Make sure this is passed
        })
    finally:
        db.close()


# ============================================================
# 4. UPDATE /cases/{case_id} ROUTE to include claim info
# ============================================================

# In your existing case detail route, add claim information:

@app.get("/cases/{case_id}", response_class=HTMLResponse)
async def case_detail(request: Request, case_id: int):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    db = SessionLocal()
    try:
        case = db.query(Case).filter(Case.id == case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail="Case not found")
        
        # V3: Get claim information
        claim_info = None
        active_claim = get_claim_for_case(db, case_id)
        if active_claim:
            claim_info = {
                "id": active_claim.id,
                "claimed_at": active_claim.claimed_at,
                "score_at_claim": active_claim.score_at_claim,
                "price_cents": active_claim.price_cents,
                "price_display": active_claim.price_display,
            }
        
        # V3: Check visibility
        user_obj = db.query(User).filter(User.id == user.get("id")).first() if user else None
        visibility = get_case_visibility(case, user_obj)
        
        # ... rest of existing case detail logic ...
        
        return templates.TemplateResponse("case_detail.html", {
            # ... existing context ...
            "claim_info": claim_info,  # V3
            "visibility": visibility,  # V3
            "current_user": user,
        })
    finally:
        db.close()


# ============================================================
# 5. ADD CaseClaim MODEL IMPORT
# ============================================================

# In your models.py imports in main.py, add:
from .models import Case, Defendant, Docket, Note, CaseClaim


# ============================================================
# 6. EXAMPLE: FULL CASE DETAIL ROUTE WITH V3 FEATURES
# ============================================================

"""
Here's a complete example of the case detail route with V3 features:
"""

@app.get("/cases/{case_id}", response_class=HTMLResponse)
async def case_detail_v3(request: Request, case_id: int):
    """Case detail page with V3 claim information."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    user_id = user.get("id") if isinstance(user, dict) else user.id
    
    db = SessionLocal()
    try:
        case = db.query(Case).filter(Case.id == case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail="Case not found")
        
        # V3: Get active claim for this case
        from app.services.claim_service import get_claim_for_case
        active_claim = get_claim_for_case(db, case_id)
        
        claim_info = None
        if active_claim:
            claim_info = {
                "id": active_claim.id,
                "user_id": active_claim.user_id,
                "claimed_at": active_claim.claimed_at.isoformat() if active_claim.claimed_at else None,
                "score_at_claim": active_claim.score_at_claim,
                "price_cents": active_claim.price_cents,
                "price_display": f"${active_claim.price_cents / 100:.2f}",
            }
        
        # V3: Check if current user can view sensitive data
        from app.services.permission_service import can_view_sensitive, get_case_visibility
        from app.models import User
        
        user_obj = db.query(User).filter(User.id == user_id).first()
        can_view = can_view_sensitive(case, user_obj)
        visibility = get_case_visibility(case, user_obj)
        
        # Get related data
        defendants = case.defendants or []
        notes = case.notes or []
        
        # Load property data
        property_data = None
        has_property_data = False
        try:
            from app.services.skiptrace_service import load_property_for_case
            property_payload = load_property_for_case(case_id)
            if property_payload:
                property_data = parse_property_data(property_payload)
                has_property_data = True
        except:
            pass
        
        # Load skip trace data
        skip_trace = None
        try:
            from app.services.skiptrace_service import load_skiptrace_for_case
            skip_trace = load_skiptrace_for_case(case_id)
        except:
            pass
        
        # V3: Mask sensitive data if user can't view
        if not can_view:
            from app.services.masking_service import mask_case_data
            # This would mask address, property, skip trace data
            # For now, we just pass the visibility flag to templates
            pass
        
        # Calculate offers
        arv = getattr(case, 'arv', None) or 0
        rehab = getattr(case, 'rehab', None) or 0
        closing = getattr(case, 'closing_costs', None) or 0
        
        wholesale_offer = compute_offer_70(arv, rehab, closing) if arv else 0
        flip_offer = compute_offer_80(arv, rehab, closing) if arv else 0
        
        return templates.TemplateResponse("case_detail.html", {
            "request": request,
            "case": case,
            "defendants": defendants,
            "notes": notes,
            "property_data": property_data,
            "has_property_data": has_property_data,
            "skip_trace": skip_trace,
            "arv": arv,
            "rehab": rehab,
            "closing_input": closing,
            "rehab_condition": getattr(case, 'rehab_condition', 'Good'),
            "wholesale_offer": wholesale_offer,
            "flip_offer": flip_offer,
            # V3 additions
            "claim_info": claim_info,
            "visibility": visibility,
            "can_view_sensitive": can_view,
            "current_user": user,
        })
    finally:
        db.close()
