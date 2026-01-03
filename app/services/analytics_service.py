# app/services/analytics_service.py
"""
Analytics Service - Dashboard metrics and reporting
✅ WITH COMPREHENSIVE INPUT VALIDATION (Fixed Version)
✅ SQL INJECTION PREVENTION
"""

from __future__ import annotations

import logging
from typing import Dict, Any, List, Union
from datetime import datetime, timedelta
from sqlalchemy import text, func
from app.database import engine, SessionLocal
from app.models import Case, Note

logger = logging.getLogger("pascowebapp.analytics")


# ========================================
# VALIDATION FUNCTIONS (NEW)
# ========================================

def validate_months(months: Union[int, str, float]) -> int:
    """
    Validate months parameter for time-based queries
    
    Args:
        months: Number of months (1-120)
    
    Returns:
        Validated integer months value
    
    Raises:
        ValueError: If months is invalid
    """
    # Type conversion
    try:
        months_int = int(months)
    except (TypeError, ValueError) as e:
        raise ValueError(f"months must be a number, got: {months}") from e
    
    # Range validation
    if months_int < 1:
        raise ValueError(f"months must be at least 1, got: {months_int}")
    
    if months_int > 120:  # Max 10 years
        raise ValueError(f"months cannot exceed 120 (10 years), got: {months_int}")
    
    return months_int


def validate_days(days: Union[int, str, float]) -> int:
    """
    Validate days parameter for time-based queries
    
    Args:
        days: Number of days (1-365)
    
    Returns:
        Validated integer days value
    
    Raises:
        ValueError: If days is invalid
    """
    # Type conversion
    try:
        days_int = int(days)
    except (TypeError, ValueError) as e:
        raise ValueError(f"days must be a number, got: {days}") from e
    
    # Range validation
    if days_int < 1:
        raise ValueError(f"days must be at least 1, got: {days_int}")
    
    if days_int > 365:  # Max 1 year
        raise ValueError(f"days cannot exceed 365 (1 year), got: {days_int}")
    
    return days_int


def validate_limit(limit: Union[int, str, float], max_limit: int = 1000) -> int:
    """
    Validate limit parameter for query result limits
    
    Args:
        limit: Maximum number of results (1-1000)
        max_limit: Maximum allowed limit
    
    Returns:
        Validated integer limit value
    
    Raises:
        ValueError: If limit is invalid
    """
    # Type conversion
    try:
        limit_int = int(limit)
    except (TypeError, ValueError) as e:
        raise ValueError(f"limit must be a number, got: {limit}") from e
    
    # Range validation
    if limit_int < 1:
        raise ValueError(f"limit must be at least 1, got: {limit_int}")
    
    if limit_int > max_limit:
        logger.warning(f"limit {limit_int} exceeds max {max_limit}, capping")
        limit_int = max_limit
    
    return limit_int


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """
    Safely divide two numbers, avoiding division by zero
    
    Args:
        numerator: Number to divide
        denominator: Number to divide by
        default: Value to return if denominator is 0
    
    Returns:
        Result of division or default
    """
    if denominator == 0:
        return default
    return numerator / denominator


# ========================================
# ANALYTICS FUNCTIONS (WITH VALIDATION)
# ========================================

def get_dashboard_metrics() -> Dict[str, Any]:
    """
    Calculate key metrics for analytics dashboard
    Works with existing database schema
    
    Returns:
        Dict with various metrics
    """
    db = SessionLocal()
    
    try:
        metrics: Dict[str, Any] = {}
        
        # === CASE METRICS ===
        
        # Total cases
        metrics["total_cases"] = db.query(Case).count()
        
        # Active cases (safely check if archived column exists)
        try:
            metrics["active_cases"] = db.query(Case).filter(
                (Case.archived == 0) | (Case.archived == None)
            ).count()
        except AttributeError:
            # archived column doesn't exist, assume all are active
            metrics["active_cases"] = metrics["total_cases"]
        
        # Cases by status (safely check if status column exists)
        try:
            status_query = db.query(
                Case.status,
                func.count(Case.id).label("count")
            ).group_by(Case.status).all()
            
            metrics["cases_by_status"] = {
                status or "unknown": count for status, count in status_query
            }
        except AttributeError:
            # status column doesn't exist
            metrics["cases_by_status"] = {"active": metrics["total_cases"]}
        
        # === FINANCIAL METRICS ===
        
        # Average ARV (safely handle if column exists)
        try:
            avg_arv = db.query(func.avg(Case.arv)).filter(
                Case.arv > 0
            ).scalar()
            metrics["avg_arv"] = round(float(avg_arv), 2) if avg_arv else 0.0
        except AttributeError:
            metrics["avg_arv"] = 0.0
        
        # Total potential value
        try:
            total_arv = db.query(func.sum(Case.arv)).filter(
                Case.arv > 0
            ).scalar()
            metrics["total_arv"] = round(float(total_arv), 2) if total_arv else 0.0
        except AttributeError:
            metrics["total_arv"] = 0.0
        
        # Average rehab cost
        try:
            avg_rehab = db.query(func.avg(Case.rehab)).filter(
                Case.rehab > 0
            ).scalar()
            metrics["avg_rehab"] = round(float(avg_rehab), 2) if avg_rehab else 0.0
        except AttributeError:
            metrics["avg_rehab"] = 0.0
        
        # Calculate total potential profit
        try:
            with engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT SUM((arv * 0.65) - COALESCE(rehab, 0) - COALESCE(closing_costs, 0)) as total_profit
                    FROM cases
                    WHERE arv > 0
                """)).fetchone()
                
                if result and result[0]:
                    metrics["total_potential_profit"] = round(float(result[0]), 2)
                else:
                    metrics["total_potential_profit"] = 0.0
        except Exception as e:
            logger.warning(f"Could not calculate profit: {e}")
            metrics["total_potential_profit"] = 0.0
        
        # === PIPELINE METRICS ===
        
        # New cases (last 30 days)
        try:
            thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
            metrics["new_cases_30d"] = db.query(Case).filter(
                Case.filing_datetime >= thirty_days_ago
            ).count()
        except Exception:
            metrics["new_cases_30d"] = 0
        
        # Short sales (try to calculate, default to 0)
        try:
            with engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT COUNT(*) as short_sale_count
                    FROM cases
                    WHERE arv > 0 
                      AND outstanding_liens IS NOT NULL
                      AND outstanding_liens != '[]'
                """)).fetchone()
                
                metrics["short_sale_count"] = int(result[0]) if result else 0
        except Exception:
            metrics["short_sale_count"] = 0
        
        # High value properties (ARV > $500k)
        try:
            metrics["high_value_count"] = db.query(Case).filter(
                Case.arv > 500000
            ).count()
        except AttributeError:
            metrics["high_value_count"] = 0
        
        # === PROPERTY FLAGS (from case_property table if it exists) ===
        
        try:
            with engine.connect() as conn:
                # Owner occupied
                result = conn.execute(text("""
                    SELECT COUNT(DISTINCT case_id)
                    FROM case_property
                    WHERE raw_json LIKE '%"ownerOccupied"::true%'
                        OR raw_json LIKE '%"ownerOccupied":: true%'
                """)).fetchone()
                metrics["owner_occupied_count"] = int(result[0]) if result else 0
        
                # High equity
                result = conn.execute(text("""
                    SELECT COUNT(DISTINCT case_id)
                    FROM case_property
                    WHERE raw_json LIKE '%"highEquity"::true%'
                       OR raw_json LIKE '%"highEquity":: true%'
                """)).fetchone()
                metrics["high_equity_count"] = int(result[0]) if result else 0
        
                # Free and clear
                result = conn.execute(text("""
                    SELECT COUNT(DISTINCT case_id)
                    FROM case_property
                    WHERE raw_json LIKE '%"freeAndClear"::true%'
                    OR raw_json LIKE '%"freeAndClear":: true%'
                """)).fetchone()
                metrics["free_clear_count"] = int(result[0]) if result else 0
        except Exception as e:
            logger.warning(f"Could not get property flags: {e}")
            metrics["owner_occupied_count"] = 0
            metrics["high_equity_count"] = 0
            metrics["free_clear_count"] = 0
        
        return metrics
    
    finally:
        db.close()


def get_cases_by_month(months: int = 12) -> List[Dict[str, Any]]:
    """
    Get case count by month for charting
    
    Args:
        months: Number of months to include (1-120), default 12
    
    Returns:
        List of dicts with month, count, avg_arv
    
    Raises:
        ValueError: If months parameter is invalid
    """
    # ✅ VALIDATE INPUT
    try:
        months_validated = validate_months(months)
    except ValueError as e:
        logger.error(f"Invalid months parameter: {e}")
        raise
    
    try:
        with engine.connect() as conn:
            # ✅ USE PARAMETERIZED QUERY (prevent SQL injection)
            result = conn.execute(
                text("""
                    SELECT 
                        strftime('%Y-%m', filing_datetime) as month,
                        COUNT(*) as count,
                        AVG(arv) as avg_arv
                    FROM cases
                    WHERE filing_datetime IS NOT NULL
                      AND filing_datetime != ''
                      AND date(filing_datetime) >= date('now', '-' || :months || ' months')
                    GROUP BY month
                    ORDER BY month DESC
                """),
                {"months": months_validated}  # ✅ Safe parameter binding
            ).fetchall()
            
            return [
                {
                    "month": row[0],
                    "count": int(row[1]),
                    "avg_arv": round(float(row[2]), 2) if row[2] else 0.0,
                }
                for row in result
            ]
    except Exception as e:
        logger.error(f"Error getting cases by month: {e}")
        return []


def get_cases_by_county() -> List[Dict[str, Any]]:
    """
    Get case distribution by county
    
    Returns:
        List of dicts with county name and count
    """
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT 
                    CASE 
                        WHEN parcel_id LIKE '__-__-__-____-_____-____' THEN 'Pasco'
                        WHEN parcel_id LIKE '__-__-__-_____-___-____' THEN 'Pinellas'
                        ELSE 'Unknown'
                    END as county,
                    COUNT(*) as count
                FROM cases
                WHERE parcel_id IS NOT NULL AND parcel_id != ''
                GROUP BY county
            """)).fetchall()
            
            return [
                {"county": row[0], "count": int(row[1])}
                for row in result
            ]
    except Exception as e:
        logger.error(f"Error getting cases by county: {e}")
        return []


def get_conversion_funnel() -> Dict[str, Any]:
    """
    Calculate conversion rates through the pipeline
    
    Returns:
        Dict with funnel metrics and conversion rates
    """
    db = SessionLocal()
    
    try:
        funnel: Dict[str, Any] = {}
        
        # Total leads (try with archived filter, fallback to all)
        try:
            funnel["total_leads"] = db.query(Case).filter(
                (Case.archived == 0) | (Case.archived == None)
            ).count()
        except AttributeError:
            funnel["total_leads"] = db.query(Case).count()
        
        # Try to get status-based counts if status column exists
        try:
            funnel["contacted"] = db.query(Case).filter(
                Case.status.in_(["contacted", "offer_sent", "offer_accepted", "due_diligence", "closing", "closed_won"])
            ).count()
            
            funnel["offer_sent"] = db.query(Case).filter(
                Case.status.in_(["offer_sent", "offer_accepted", "due_diligence", "closing", "closed_won"])
            ).count()
            
            funnel["offer_accepted"] = db.query(Case).filter(
                Case.status.in_(["offer_accepted", "due_diligence", "closing", "closed_won"])
            ).count()
            
            funnel["closed_won"] = db.query(Case).filter(
                Case.status == "closed_won"
            ).count()
        except AttributeError:
            # status column doesn't exist, use placeholder values
            funnel["contacted"] = 0
            funnel["offer_sent"] = 0
            funnel["offer_accepted"] = 0
            funnel["closed_won"] = 0
        
        # ✅ SAFE DIVISION - Calculate conversion rates
        total_leads = funnel["total_leads"]
        
        funnel["contact_rate"] = round(
            safe_divide(funnel["contacted"], total_leads) * 100, 1
        )
        funnel["offer_rate"] = round(
            safe_divide(funnel["offer_sent"], total_leads) * 100, 1
        )
        funnel["close_rate"] = round(
            safe_divide(funnel["closed_won"], total_leads) * 100, 1
        )
        
        return funnel
    
    finally:
        db.close()


def get_roi_analysis() -> Dict[str, Any]:
    """
    Calculate ROI metrics for closed deals
    
    Returns:
        Dict with ROI analysis metrics
    """
    db = SessionLocal()
    
    try:
        # Try to get closed cases if status and close_price columns exist
        try:
            closed_cases = db.query(Case).filter(
                Case.status == "closed_won",
                Case.close_price.isnot(None),
                Case.close_price > 0
            ).all()
        except AttributeError:
            # Columns don't exist, return empty
            return {
                "total_deals": 0,
                "avg_purchase_price": 0.0,
                "avg_arv": 0.0,
                "avg_profit": 0.0,
                "avg_roi_pct": 0.0,
            }
        
        if not closed_cases:
            return {
                "total_deals": 0,
                "avg_purchase_price": 0.0,
                "avg_arv": 0.0,
                "avg_profit": 0.0,
                "avg_roi_pct": 0.0,
            }
        
        total_purchase = sum(float(c.close_price or 0) for c in closed_cases)
        total_arv = sum(float(c.arv or 0) for c in closed_cases)
        total_profit = 0.0
        
        for case in closed_cases:
            purchase = float(case.close_price or 0)
            rehab = float(case.rehab or 0)
            closing = float(case.closing_costs or 0)
            arv = float(case.arv or 0)
            
            profit = arv - (purchase + rehab + closing)
            total_profit += profit
        
        count = len(closed_cases)
        
        # ✅ SAFE DIVISION - Avoid division by zero
        avg_purchase = safe_divide(total_purchase, count)
        avg_arv = safe_divide(total_arv, count)
        avg_profit = safe_divide(total_profit, count)
        avg_roi_pct = safe_divide(avg_profit, avg_purchase) * 100 if avg_purchase > 0 else 0.0
        
        return {
            "total_deals": count,
            "avg_purchase_price": round(avg_purchase, 2),
            "avg_arv": round(avg_arv, 2),
            "avg_profit": round(avg_profit, 2),
            "avg_roi_pct": round(avg_roi_pct, 1),
        }
    
    finally:
        db.close()


def get_activity_timeline(days: int = 30) -> List[Dict[str, Any]]:
    """
    Get recent activity (notes, status changes, etc.)
    
    Args:
        days: Number of days to include (1-365), default 30
    
    Returns:
        List of activity items with timestamp, type, case_id, content
    
    Raises:
        ValueError: If days parameter is invalid
    """
    # ✅ VALIDATE INPUT
    try:
        days_validated = validate_days(days)
    except ValueError as e:
        logger.error(f"Invalid days parameter: {e}")
        raise
    
    db = SessionLocal()
    
    try:
        cutoff = (datetime.now() - timedelta(days=days_validated)).strftime("%Y-%m-%d")
        
        # Recent notes
        notes = db.query(Note).filter(
            Note.created_at >= cutoff
        ).order_by(Note.created_at.desc()).limit(50).all()
        
        activity: List[Dict[str, Any]] = []
        
        for note in notes:
            content = note.content or ""
            # Truncate long content
            truncated_content = content[:100] + "..." if len(content) > 100 else content
            
            activity.append({
                "timestamp": note.created_at,
                "type": "note",
                "case_id": note.case_id,
                "content": truncated_content,
            })
        
        # Sort by timestamp
        activity.sort(key=lambda x: x["timestamp"], reverse=True)
        
        return activity[:20]
    
    finally:
        db.close()


def get_top_opportunities(limit: int = 10) -> List[Dict[str, Any]]:
    """
    Get top opportunities ranked by potential profit
    
    Args:
        limit: Maximum number of opportunities to return (1-1000), default 10
    
    Returns:
        List of top opportunities with profit calculations
    
    Raises:
        ValueError: If limit parameter is invalid
    """
    # ✅ VALIDATE INPUT
    try:
        limit_validated = validate_limit(limit, max_limit=1000)
    except ValueError as e:
        logger.error(f"Invalid limit parameter: {e}")
        raise
    
    try:
        with engine.connect() as conn:
            # ✅ USE PARAMETERIZED QUERY (prevent SQL injection)
            # Try query with archived column
            try:
                result = conn.execute(
                    text("""
                        SELECT 
                            id,
                            case_number,
                            address_override,
                            address,
                            arv,
                            rehab,
                            closing_costs,
                            ((arv * 0.65) - COALESCE(rehab, 0) - COALESCE(closing_costs, 0)) as potential_profit,
                            status
                        FROM cases
                        WHERE arv > 0 
                          AND (archived = 0 OR archived IS NULL)
                        ORDER BY potential_profit DESC
                        LIMIT :limit
                    """),
                    {"limit": limit_validated}  # ✅ Safe parameter binding
                ).fetchall()
            except Exception:
                # Try without archived filter
                result = conn.execute(
                    text("""
                        SELECT 
                            id,
                            case_number,
                            address_override,
                            address,
                            arv,
                            COALESCE(rehab, 0) as rehab,
                            COALESCE(closing_costs, 0) as closing_costs,
                            ((arv * 0.65) - COALESCE(rehab, 0) - COALESCE(closing_costs, 0)) as potential_profit,
                            'new' as status
                        FROM cases
                        WHERE arv > 0
                        ORDER BY potential_profit DESC
                        LIMIT :limit
                    """),
                    {"limit": limit_validated}  # ✅ Safe parameter binding
                ).fetchall()
            
            opportunities: List[Dict[str, Any]] = []
            for row in result:
                opportunities.append({
                    "id": row[0],
                    "case_number": row[1],
                    "address": row[2] or row[3] or "N/A",
                    "arv": round(float(row[4]), 2),
                    "rehab": round(float(row[5] or 0), 2),
                    "closing_costs": round(float(row[6] or 0), 2),
                    "potential_profit": round(float(row[7]), 2),
                    "status": row[8] or "new",
                })
            
            return opportunities
    
    except Exception as e:
        logger.error(f"Error getting top opportunities: {e}")
        return []
