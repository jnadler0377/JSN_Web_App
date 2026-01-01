# app/services/analytics_service.py
# Fixed version that works with existing database schema

from __future__ import annotations

import logging
from typing import Dict, Any, List
from datetime import datetime, timedelta
from sqlalchemy import text, func
from app.database import engine, SessionLocal
from app.models import Case, Note

logger = logging.getLogger("pascowebapp.analytics")


def get_dashboard_metrics() -> Dict[str, Any]:
    """
    Calculate key metrics for analytics dashboard
    Works with existing database schema
    """
    db = SessionLocal()
    
    try:
        metrics = {}
        
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
            metrics["avg_arv"] = round(float(avg_arv), 2) if avg_arv else 0
        except AttributeError:
            metrics["avg_arv"] = 0
        
        # Total potential value
        try:
            total_arv = db.query(func.sum(Case.arv)).filter(
                Case.arv > 0
            ).scalar()
            metrics["total_arv"] = round(float(total_arv), 2) if total_arv else 0
        except AttributeError:
            metrics["total_arv"] = 0
        
        # Average rehab cost
        try:
            avg_rehab = db.query(func.avg(Case.rehab)).filter(
                Case.rehab > 0
            ).scalar()
            metrics["avg_rehab"] = round(float(avg_rehab), 2) if avg_rehab else 0
        except AttributeError:
            metrics["avg_rehab"] = 0
        
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
                    metrics["total_potential_profit"] = 0
        except Exception as e:
            logger.warning(f"Could not calculate profit: {e}")
            metrics["total_potential_profit"] = 0
        
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
                # Owner occupied - escape the colon by doubling it
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
    """Get case count by month for charting"""
    try:
        with engine.connect() as conn:
            result = conn.execute(text(f"""
                SELECT 
                    strftime('%Y-%m', filing_datetime) as month,
                    COUNT(*) as count,
                    AVG(arv) as avg_arv
                FROM cases
                WHERE filing_datetime IS NOT NULL
                  AND filing_datetime != ''
                  AND date(filing_datetime) >= date('now', '-{months} months')
                GROUP BY month
                ORDER BY month DESC
            """)).fetchall()
            
            return [
                {
                    "month": row[0],
                    "count": int(row[1]),
                    "avg_arv": round(float(row[2]), 2) if row[2] else 0,
                }
                for row in result
            ]
    except Exception as e:
        logger.error(f"Error getting cases by month: {e}")
        return []


def get_cases_by_county() -> List[Dict[str, Any]]:
    """Get case distribution by county"""
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


def get_conversion_funnel() -> Dict[str, int]:
    """Calculate conversion rates through the pipeline"""
    db = SessionLocal()
    
    try:
        funnel = {}
        
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
        
        # Calculate conversion rates
        if funnel["total_leads"] > 0:
            funnel["contact_rate"] = round(
                (funnel["contacted"] / funnel["total_leads"]) * 100, 1
            )
            funnel["offer_rate"] = round(
                (funnel["offer_sent"] / funnel["total_leads"]) * 100, 1
            )
            funnel["close_rate"] = round(
                (funnel["closed_won"] / funnel["total_leads"]) * 100, 1
            )
        else:
            funnel["contact_rate"] = 0
            funnel["offer_rate"] = 0
            funnel["close_rate"] = 0
        
        return funnel
    
    finally:
        db.close()


def get_roi_analysis() -> Dict[str, Any]:
    """Calculate ROI metrics for closed deals"""
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
                "avg_purchase_price": 0,
                "avg_arv": 0,
                "avg_profit": 0,
                "avg_roi_pct": 0,
            }
        
        if not closed_cases:
            return {
                "total_deals": 0,
                "avg_purchase_price": 0,
                "avg_arv": 0,
                "avg_profit": 0,
                "avg_roi_pct": 0,
            }
        
        total_purchase = sum(float(c.close_price or 0) for c in closed_cases)
        total_arv = sum(float(c.arv or 0) for c in closed_cases)
        total_profit = 0
        
        for case in closed_cases:
            purchase = float(case.close_price or 0)
            rehab = float(case.rehab or 0)
            closing = float(case.closing_costs or 0)
            arv = float(case.arv or 0)
            
            profit = arv - (purchase + rehab + closing)
            total_profit += profit
        
        count = len(closed_cases)
        avg_purchase = total_purchase / count
        avg_arv = total_arv / count
        avg_profit = total_profit / count
        avg_roi_pct = (avg_profit / avg_purchase * 100) if avg_purchase > 0 else 0
        
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
    """Get recent activity (notes, status changes, etc.)"""
    db = SessionLocal()
    
    try:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        # Recent notes
        notes = db.query(Note).filter(
            Note.created_at >= cutoff
        ).order_by(Note.created_at.desc()).limit(50).all()
        
        activity = []
        
        for note in notes:
            activity.append({
                "timestamp": note.created_at,
                "type": "note",
                "case_id": note.case_id,
                "content": (note.content or "")[:100] + "..." if len(note.content or "") > 100 else note.content,
            })
        
        # Sort by timestamp
        activity.sort(key=lambda x: x["timestamp"], reverse=True)
        
        return activity[:20]
    
    finally:
        db.close()


def get_top_opportunities(limit: int = 10) -> List[Dict[str, Any]]:
    """Get top opportunities ranked by potential profit"""
    try:
        with engine.connect() as conn:
            # Try query with archived column
            try:
                result = conn.execute(text(f"""
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
                    LIMIT {limit}
                """)).fetchall()
            except Exception:
                # Try without archived filter
                result = conn.execute(text(f"""
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
                    LIMIT {limit}
                """)).fetchall()
            
            opportunities = []
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
