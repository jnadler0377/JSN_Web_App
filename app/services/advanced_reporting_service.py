# app/services/advanced_reporting_service.py
"""
Advanced Reporting Service for JSN Holdings
Generates comprehensive PDF reports for portfolio analysis
"""

import io
import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path

logger = logging.getLogger("pascowebapp.reporting")

# Try to import PDF libraries
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter, LETTER
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, Image, HRFlowable
    )
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False
    logger.warning("reportlab not installed - PDF reports unavailable")

# Try matplotlib for charts
try:
    import matplotlib
    matplotlib.use('Agg')  # Non-interactive backend
    import matplotlib.pyplot as plt
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    logger.warning("matplotlib not installed - charts unavailable")


class AdvancedReporting:
    """Generate comprehensive PDF reports"""
    
    def __init__(self, db_session):
        self.db = db_session
        self.styles = None
        if REPORTLAB_AVAILABLE:
            self._init_styles()
    
    def _init_styles(self):
        """Initialize PDF styles"""
        self.styles = getSampleStyleSheet()
        
        # Custom styles - use unique names to avoid conflicts
        self.styles.add(ParagraphStyle(
            name='ReportTitle',
            parent=self.styles['Heading1'],
            fontSize=24,
            spaceAfter=30,
            alignment=TA_CENTER,
            textColor=colors.HexColor('#667eea')
        ))
        
        self.styles.add(ParagraphStyle(
            name='SectionHeader',
            parent=self.styles['Heading2'],
            fontSize=16,
            spaceBefore=20,
            spaceAfter=10,
            textColor=colors.HexColor('#1e293b')
        ))
        
        self.styles.add(ParagraphStyle(
            name='SubHeader',
            parent=self.styles['Heading3'],
            fontSize=12,
            spaceBefore=15,
            spaceAfter=8,
            textColor=colors.HexColor('#475569')
        ))
        
        self.styles.add(ParagraphStyle(
            name='ReportBody',
            parent=self.styles['Normal'],
            fontSize=10,
            spaceAfter=8,
            textColor=colors.HexColor('#334155')
        ))
        
        self.styles.add(ParagraphStyle(
            name='MetricValue',
            parent=self.styles['Normal'],
            fontSize=28,
            alignment=TA_CENTER,
            textColor=colors.HexColor('#667eea')
        ))
        
        self.styles.add(ParagraphStyle(
            name='MetricLabel',
            parent=self.styles['Normal'],
            fontSize=10,
            alignment=TA_CENTER,
            textColor=colors.HexColor('#64748b')
        ))
    
    def _get_cases_data(self, case_ids: List[int] = None, 
                        start_date: str = None, 
                        end_date: str = None,
                        include_archived: bool = False) -> List[Dict]:
        """Fetch cases data from database"""
        from sqlalchemy import text
        
        query = """
            SELECT 
                c.id, c.case_number, c.filing_datetime, c.style,
                c.address, c.address_override, c.parcel_id,
                c.arv, c.rehab, c.closing_costs, c.outstanding_liens,
                c.archived
            FROM cases c
            WHERE 1=1
        """
        params = {}
        
        if case_ids:
            # Build placeholders for IN clause (SQLite compatible)
            placeholders = ", ".join([f":id_{i}" for i in range(len(case_ids))])
            query += f" AND c.id IN ({placeholders})"
            for i, cid in enumerate(case_ids):
                params[f"id_{i}"] = cid
        
        if not include_archived:
            query += " AND (c.archived IS NULL OR c.archived = 0)"
        
        if start_date:
            query += " AND c.filing_datetime >= :start_date"
            params["start_date"] = start_date
        
        if end_date:
            query += " AND c.filing_datetime <= :end_date"
            params["end_date"] = end_date
        
        query += " ORDER BY c.filing_datetime DESC"
        
        rows = self.db.execute(text(query), params).fetchall()
        
        cases = []
        for row in rows:
            # Parse liens
            total_liens = 0.0
            try:
                liens_data = json.loads(row[10]) if row[10] else []
                for lien in liens_data:
                    if isinstance(lien, dict):
                        amt = str(lien.get("amount", "0")).replace("$", "").replace(",", "")
                        total_liens += float(amt) if amt else 0
            except:
                pass
            
            arv = float(row[7] or 0)
            rehab = float(row[8] or 0)
            closing = float(row[9] or 0)
            
            # Calculate metrics
            max_offer = (arv * 0.70) - rehab - closing if arv > 0 else 0
            estimated_profit = arv - max_offer - rehab - closing - total_liens if arv > 0 else 0
            equity_pct = ((arv - total_liens) / arv * 100) if arv > 0 else 0
            roi_pct = (estimated_profit / max_offer * 100) if max_offer > 0 else 0
            
            # Calculate score
            score = self._calculate_deal_score(equity_pct, roi_pct, estimated_profit, total_liens, max_offer)
            
            cases.append({
                "id": row[0],
                "case_number": row[1],
                "filing_date": row[2],
                "style": row[3],
                "address": row[5] or row[4] or "",
                "parcel_id": row[6],
                "arv": arv,
                "rehab": rehab,
                "closing_costs": closing,
                "total_liens": total_liens,
                "max_offer": max_offer,
                "estimated_profit": estimated_profit,
                "equity_pct": equity_pct,
                "roi_pct": roi_pct,
                "score": score,
                "archived": bool(row[11])
            })
        
        return cases
    
    def _calculate_deal_score(self, equity_pct, roi_pct, profit, liens, max_offer):
        """Calculate deal score"""
        score = 50
        
        if equity_pct >= 40:
            score += 25
        elif equity_pct >= 30:
            score += 15
        elif equity_pct >= 20:
            score += 5
        
        if roi_pct >= 30:
            score += 25
        elif roi_pct >= 20:
            score += 15
        elif roi_pct >= 10:
            score += 5
        
        if profit >= 50000:
            score += 10
        elif profit >= 25000:
            score += 5
        
        if liens > 0 and liens > max_offer:
            score -= 20
        
        return max(0, min(100, score))
    
    def _create_chart(self, chart_type: str, data: Dict, width: int = 400, height: int = 250) -> Optional[bytes]:
        """Create chart image and return as bytes"""
        if not MATPLOTLIB_AVAILABLE:
            return None
        
        fig, ax = plt.subplots(figsize=(width/100, height/100), dpi=100)
        
        try:
            if chart_type == "score_distribution":
                labels = ['Excellent\n(80+)', 'Good\n(60-79)', 'Fair\n(40-59)', 'Poor\n(<40)']
                values = [
                    data.get("excellent", 0),
                    data.get("good", 0),
                    data.get("fair", 0),
                    data.get("poor", 0)
                ]
                colors_list = ['#10b981', '#3b82f6', '#f59e0b', '#ef4444']
                
                bars = ax.bar(labels, values, color=colors_list, edgecolor='white', linewidth=1)
                ax.set_ylabel('Number of Deals')
                ax.set_title('Deal Score Distribution', fontsize=12, fontweight='bold')
                
                # Add value labels on bars
                for bar, val in zip(bars, values):
                    if val > 0:
                        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.5,
                               str(int(val)), ha='center', va='bottom', fontsize=10)
            
            elif chart_type == "profit_distribution":
                profit_ranges = data.get("ranges", [])
                counts = data.get("counts", [])
                
                ax.bar(profit_ranges, counts, color='#667eea', edgecolor='white')
                ax.set_ylabel('Number of Deals')
                ax.set_xlabel('Estimated Profit Range')
                ax.set_title('Profit Distribution', fontsize=12, fontweight='bold')
                plt.xticks(rotation=45, ha='right')
            
            elif chart_type == "monthly_trend":
                months = data.get("months", [])
                counts = data.get("counts", [])
                
                ax.plot(months, counts, marker='o', color='#667eea', linewidth=2, markersize=6)
                ax.fill_between(months, counts, alpha=0.3, color='#667eea')
                ax.set_ylabel('New Cases')
                ax.set_title('Monthly Case Trend', fontsize=12, fontweight='bold')
                plt.xticks(rotation=45, ha='right')
            
            elif chart_type == "pie_by_score":
                labels = ['Excellent', 'Good', 'Fair', 'Poor']
                values = [
                    data.get("excellent", 0),
                    data.get("good", 0),
                    data.get("fair", 0),
                    data.get("poor", 0)
                ]
                colors_list = ['#10b981', '#3b82f6', '#f59e0b', '#ef4444']
                
                # Filter out zeros
                filtered = [(l, v, c) for l, v, c in zip(labels, values, colors_list) if v > 0]
                if filtered:
                    labels, values, colors_list = zip(*filtered)
                    ax.pie(values, labels=labels, colors=colors_list, autopct='%1.0f%%',
                           startangle=90, explode=[0.02]*len(values))
                    ax.set_title('Portfolio by Deal Quality', fontsize=12, fontweight='bold')
            
            plt.tight_layout()
            
            # Save to bytes
            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=100, bbox_inches='tight',
                       facecolor='white', edgecolor='none')
            buf.seek(0)
            plt.close(fig)
            
            return buf.getvalue()
        
        except Exception as e:
            logger.error(f"Chart creation failed: {e}")
            plt.close(fig)
            return None
    
    def generate_portfolio_report(
        self,
        start_date: str = None,
        end_date: str = None,
        include_charts: bool = True,
        include_case_details: bool = True
    ) -> Tuple[bytes, str]:
        """
        Generate comprehensive portfolio report PDF
        
        Returns:
            Tuple of (pdf_bytes, filename)
        """
        if not REPORTLAB_AVAILABLE:
            raise ImportError("reportlab not installed. Run: pip install reportlab")
        
        # Fetch data
        cases = self._get_cases_data(start_date=start_date, end_date=end_date)
        
        if not cases:
            raise ValueError("No cases found for the specified criteria")
        
        # Calculate portfolio metrics
        total_cases = len(cases)
        cases_with_arv = [c for c in cases if c["arv"] > 0]
        
        total_arv = sum(c["arv"] for c in cases)
        total_potential_profit = sum(c["estimated_profit"] for c in cases if c["estimated_profit"] > 0)
        total_liens = sum(c["total_liens"] for c in cases)
        avg_score = sum(c["score"] for c in cases) / total_cases if total_cases > 0 else 0
        avg_equity = sum(c["equity_pct"] for c in cases_with_arv) / len(cases_with_arv) if cases_with_arv else 0
        
        # Score distribution
        score_dist = {"excellent": 0, "good": 0, "fair": 0, "poor": 0}
        for c in cases:
            if c["score"] >= 80:
                score_dist["excellent"] += 1
            elif c["score"] >= 60:
                score_dist["good"] += 1
            elif c["score"] >= 40:
                score_dist["fair"] += 1
            else:
                score_dist["poor"] += 1
        
        # Top deals
        top_deals = sorted(cases, key=lambda x: x["score"], reverse=True)[:10]
        
        # Create PDF
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=LETTER,
            rightMargin=0.75*inch,
            leftMargin=0.75*inch,
            topMargin=0.75*inch,
            bottomMargin=0.75*inch
        )
        
        elements = []
        
        # Title
        elements.append(Paragraph("Portfolio Analysis Report", self.styles['ReportTitle']))
        elements.append(Paragraph(
            f"Generated: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}",
            self.styles['ReportBody']
        ))
        
        if start_date or end_date:
            date_range = f"Period: {start_date or 'All'} to {end_date or 'Present'}"
            elements.append(Paragraph(date_range, self.styles['ReportBody']))
        
        elements.append(Spacer(1, 20))
        elements.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor('#667eea')))
        elements.append(Spacer(1, 20))
        
        # Executive Summary
        elements.append(Paragraph("Executive Summary", self.styles['SectionHeader']))
        
        summary_text = f"""
        This portfolio contains <b>{total_cases}</b> active foreclosure cases with a combined 
        After Repair Value (ARV) of <b>${total_arv:,.0f}</b>. The portfolio shows 
        <b>${total_potential_profit:,.0f}</b> in potential profit with an average deal score 
        of <b>{avg_score:.0f}/100</b>.
        """
        elements.append(Paragraph(summary_text, self.styles['ReportBody']))
        elements.append(Spacer(1, 20))
        
        # Key Metrics Table
        elements.append(Paragraph("Key Metrics", self.styles['SubHeader']))
        
        metrics_data = [
            ["Metric", "Value"],
            ["Total Cases", f"{total_cases}"],
            ["Cases with ARV", f"{len(cases_with_arv)}"],
            ["Total ARV", f"${total_arv:,.0f}"],
            ["Total Potential Profit", f"${total_potential_profit:,.0f}"],
            ["Total Outstanding Liens", f"${total_liens:,.0f}"],
            ["Average Deal Score", f"{avg_score:.0f}/100"],
            ["Average Equity %", f"{avg_equity:.1f}%"],
            ["Excellent Deals (80+)", f"{score_dist['excellent']}"],
            ["Good Deals (60-79)", f"{score_dist['good']}"],
        ]
        
        metrics_table = Table(metrics_data, colWidths=[3*inch, 2*inch])
        metrics_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#667eea')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
        ]))
        elements.append(metrics_table)
        elements.append(Spacer(1, 30))
        
        # Charts
        if include_charts and MATPLOTLIB_AVAILABLE:
            elements.append(Paragraph("Portfolio Analysis", self.styles['SectionHeader']))
            
            # Score distribution chart
            chart_bytes = self._create_chart("score_distribution", score_dist)
            if chart_bytes:
                chart_img = Image(io.BytesIO(chart_bytes), width=4*inch, height=2.5*inch)
                elements.append(chart_img)
                elements.append(Spacer(1, 20))
            
            # Pie chart
            chart_bytes = self._create_chart("pie_by_score", score_dist)
            if chart_bytes:
                chart_img = Image(io.BytesIO(chart_bytes), width=3.5*inch, height=2.5*inch)
                elements.append(chart_img)
                elements.append(Spacer(1, 20))
        
        # Top Deals
        elements.append(PageBreak())
        elements.append(Paragraph("Top 10 Deals by Score", self.styles['SectionHeader']))
        
        top_deals_data = [["#", "Case Number", "Address", "Score", "ARV", "Est. Profit"]]
        for i, deal in enumerate(top_deals, 1):
            addr = deal["address"][:30] + "..." if len(deal["address"]) > 30 else deal["address"]
            top_deals_data.append([
                str(i),
                deal["case_number"][:20] if deal["case_number"] else "—",
                addr or "—",
                f"{deal['score']}",
                f"${deal['arv']:,.0f}" if deal['arv'] > 0 else "—",
                f"${deal['estimated_profit']:,.0f}" if deal['estimated_profit'] > 0 else "—"
            ])
        
        top_table = Table(top_deals_data, colWidths=[0.4*inch, 1.5*inch, 2*inch, 0.6*inch, 1*inch, 1*inch])
        top_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#667eea')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (3, 0), (5, -1), 'RIGHT'),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
        ]))
        elements.append(top_table)
        
        # Case Details
        if include_case_details and len(cases) <= 50:
            elements.append(PageBreak())
            elements.append(Paragraph("All Cases Detail", self.styles['SectionHeader']))
            
            all_cases_data = [["Case Number", "Address", "ARV", "Liens", "Profit", "Score"]]
            for c in sorted(cases, key=lambda x: x["score"], reverse=True):
                addr = c["address"][:25] + "..." if len(c["address"]) > 25 else c["address"]
                all_cases_data.append([
                    c["case_number"][:18] if c["case_number"] else "—",
                    addr or "—",
                    f"${c['arv']:,.0f}" if c['arv'] > 0 else "—",
                    f"${c['total_liens']:,.0f}" if c['total_liens'] > 0 else "—",
                    f"${c['estimated_profit']:,.0f}" if c['estimated_profit'] != 0 else "—",
                    f"{c['score']}"
                ])
            
            all_table = Table(all_cases_data, colWidths=[1.4*inch, 1.8*inch, 0.9*inch, 0.9*inch, 0.9*inch, 0.6*inch])
            all_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#475569')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 8),
                ('ALIGN', (2, 0), (-1, -1), 'RIGHT'),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 7),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
            ]))
            elements.append(all_table)
        
        # Recommendations
        elements.append(Spacer(1, 30))
        elements.append(Paragraph("Recommendations", self.styles['SectionHeader']))
        
        recommendations = []
        if score_dist["excellent"] > 0:
            recommendations.append(f"• <b>{score_dist['excellent']} excellent deals</b> should be prioritized for immediate action")
        if score_dist["good"] > 0:
            recommendations.append(f"• <b>{score_dist['good']} good deals</b> warrant follow-up within the next 2 weeks")
        if total_potential_profit > 100000:
            recommendations.append(f"• Portfolio shows <b>${total_potential_profit:,.0f}</b> in potential profit - consider increasing acquisition budget")
        if avg_equity < 30:
            recommendations.append(f"• Average equity of <b>{avg_equity:.1f}%</b> is below target - focus on higher-equity opportunities")
        
        for rec in recommendations:
            elements.append(Paragraph(rec, self.styles['ReportBody']))
        
        # Footer
        elements.append(Spacer(1, 40))
        elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#e2e8f0')))
        elements.append(Paragraph(
            "Generated by JSN Holdings Foreclosure Manager",
            ParagraphStyle('Footer', parent=self.styles['Normal'], fontSize=8, 
                          textColor=colors.HexColor('#94a3b8'), alignment=TA_CENTER)
        ))
        
        # Build PDF
        doc.build(elements)
        buffer.seek(0)
        
        filename = f"portfolio_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        return buffer.getvalue(), filename
    
    def generate_roi_projection_report(
        self,
        case_ids: List[int]
    ) -> Tuple[bytes, str]:
        """
        Generate ROI projection report for selected cases
        
        Returns:
            Tuple of (pdf_bytes, filename)
        """
        if not REPORTLAB_AVAILABLE:
            raise ImportError("reportlab not installed. Run: pip install reportlab")
        
        cases = self._get_cases_data(case_ids=case_ids)
        
        if not cases:
            raise ValueError("No cases found for the specified IDs")
        
        # Calculate totals
        total_investment = sum(c["max_offer"] + c["rehab"] + c["closing_costs"] for c in cases if c["max_offer"] > 0)
        total_arv = sum(c["arv"] for c in cases)
        total_profit = sum(c["estimated_profit"] for c in cases if c["estimated_profit"] > 0)
        portfolio_roi = (total_profit / total_investment * 100) if total_investment > 0 else 0
        
        # Create PDF
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=LETTER,
                               rightMargin=0.75*inch, leftMargin=0.75*inch,
                               topMargin=0.75*inch, bottomMargin=0.75*inch)
        
        elements = []
        
        # Title
        elements.append(Paragraph("ROI Projection Report", self.styles['ReportTitle']))
        elements.append(Paragraph(
            f"Generated: {datetime.now().strftime('%B %d, %Y')} | {len(cases)} Cases Selected",
            self.styles['ReportBody']
        ))
        elements.append(Spacer(1, 20))
        elements.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor('#667eea')))
        elements.append(Spacer(1, 20))
        
        # Portfolio Summary
        elements.append(Paragraph("Investment Summary", self.styles['SectionHeader']))
        
        summary_data = [
            ["Metric", "Amount"],
            ["Total Investment Required", f"${total_investment:,.0f}"],
            ["Total After Repair Value", f"${total_arv:,.0f}"],
            ["Projected Total Profit", f"${total_profit:,.0f}"],
            ["Portfolio ROI", f"{portfolio_roi:.1f}%"],
            ["Average Profit per Deal", f"${total_profit/len(cases):,.0f}" if cases else "—"],
        ]
        
        summary_table = Table(summary_data, colWidths=[3*inch, 2*inch])
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#10b981')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ]))
        elements.append(summary_table)
        elements.append(Spacer(1, 30))
        
        # Individual Case Projections
        elements.append(Paragraph("Individual Deal Projections", self.styles['SectionHeader']))
        
        for c in sorted(cases, key=lambda x: x["estimated_profit"], reverse=True):
            elements.append(Paragraph(f"<b>{c['case_number'] or 'Unknown Case'}</b>", self.styles['SubHeader']))
            elements.append(Paragraph(f"Address: {c['address'] or 'Unknown'}", self.styles['ReportBody']))
            
            deal_data = [
                ["Investment", "Value", "Returns", "Value"],
                ["Max Offer (70%)", f"${c['max_offer']:,.0f}", "ARV", f"${c['arv']:,.0f}"],
                ["Rehab Costs", f"${c['rehab']:,.0f}", "Less: Investment", f"${c['max_offer']+c['rehab']+c['closing_costs']:,.0f}"],
                ["Closing Costs", f"${c['closing_costs']:,.0f}", "Less: Liens", f"${c['total_liens']:,.0f}"],
                ["Total Investment", f"${c['max_offer']+c['rehab']+c['closing_costs']:,.0f}", "Net Profit", f"${c['estimated_profit']:,.0f}"],
            ]
            
            deal_table = Table(deal_data, colWidths=[1.5*inch, 1.2*inch, 1.5*inch, 1.2*inch])
            deal_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#475569')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
                ('ALIGN', (3, 0), (3, -1), 'RIGHT'),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f0fdf4')),
            ]))
            elements.append(deal_table)
            
            roi = (c['estimated_profit'] / (c['max_offer']+c['rehab']+c['closing_costs']) * 100) if c['max_offer'] > 0 else 0
            elements.append(Paragraph(f"<b>Deal ROI: {roi:.1f}%</b> | Score: {c['score']}/100", self.styles['ReportBody']))
            elements.append(Spacer(1, 20))
        
        # Build PDF
        doc.build(elements)
        buffer.seek(0)
        
        filename = f"roi_projection_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        return buffer.getvalue(), filename
    
    def generate_deal_summary_report(
        self,
        case_id: int
    ) -> Tuple[bytes, str]:
        """
        Generate single deal summary report
        
        Returns:
            Tuple of (pdf_bytes, filename)
        """
        if not REPORTLAB_AVAILABLE:
            raise ImportError("reportlab not installed")
        
        cases = self._get_cases_data(case_ids=[case_id])
        if not cases:
            raise ValueError("Case not found")
        
        c = cases[0]
        
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=LETTER,
                               rightMargin=0.75*inch, leftMargin=0.75*inch,
                               topMargin=0.75*inch, bottomMargin=0.75*inch)
        
        elements = []
        
        # Title
        elements.append(Paragraph("Deal Analysis Summary", self.styles['ReportTitle']))
        elements.append(Paragraph(f"Case: {c['case_number'] or 'Unknown'}", self.styles['ReportBody']))
        elements.append(Paragraph(f"Generated: {datetime.now().strftime('%B %d, %Y')}", self.styles['ReportBody']))
        elements.append(Spacer(1, 20))
        
        # Score badge
        score_color = '#10b981' if c['score'] >= 80 else '#3b82f6' if c['score'] >= 60 else '#f59e0b' if c['score'] >= 40 else '#ef4444'
        score_label = 'EXCELLENT' if c['score'] >= 80 else 'GOOD' if c['score'] >= 60 else 'FAIR' if c['score'] >= 40 else 'POOR'
        
        elements.append(Paragraph(f"<font size='36' color='{score_color}'><b>{c['score']}</b></font>", 
                                 ParagraphStyle('Score', alignment=TA_CENTER)))
        elements.append(Paragraph(f"<font color='{score_color}'><b>{score_label} DEAL</b></font>",
                                 ParagraphStyle('ScoreLabel', alignment=TA_CENTER, fontSize=14)))
        elements.append(Spacer(1, 20))
        
        # Property Info
        elements.append(Paragraph("Property Information", self.styles['SectionHeader']))
        
        prop_data = [
            ["Address", c['address'] or "Unknown"],
            ["Parcel ID", c['parcel_id'] or "Unknown"],
            ["Filing Date", c['filing_date'] or "Unknown"],
        ]
        
        prop_table = Table(prop_data, colWidths=[2*inch, 4*inch])
        prop_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ]))
        elements.append(prop_table)
        elements.append(Spacer(1, 20))
        
        # Financial Analysis
        elements.append(Paragraph("Financial Analysis", self.styles['SectionHeader']))
        
        fin_data = [
            ["Metric", "Value"],
            ["After Repair Value (ARV)", f"${c['arv']:,.0f}"],
            ["Rehab Estimate", f"${c['rehab']:,.0f}"],
            ["Closing Costs", f"${c['closing_costs']:,.0f}"],
            ["Outstanding Liens", f"${c['total_liens']:,.0f}"],
            ["Max Offer (70% Rule)", f"${c['max_offer']:,.0f}"],
            ["Estimated Profit", f"${c['estimated_profit']:,.0f}"],
            ["Equity Position", f"{c['equity_pct']:.1f}%"],
            ["ROI", f"{c['roi_pct']:.1f}%"],
        ]
        
        fin_table = Table(fin_data, colWidths=[3*inch, 2*inch])
        fin_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#667eea')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('BACKGROUND', (0, -2), (-1, -1), colors.HexColor('#f0fdf4')),
        ]))
        elements.append(fin_table)
        
        # Build PDF
        doc.build(elements)
        buffer.seek(0)
        
        filename = f"deal_summary_{c['case_number'] or case_id}_{datetime.now().strftime('%Y%m%d')}.pdf"
        return buffer.getvalue(), filename


def get_reporting_service(db_session) -> AdvancedReporting:
    """Factory function"""
    return AdvancedReporting(db_session)
