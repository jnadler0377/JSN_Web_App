/**
 * V2.0 Deal Analysis JavaScript
 * Fetches and displays deal scores, metrics, and recommendations
 */

(function() {
  'use strict';
  
  const API_BASE = '/api/v2';
  
  // ==========================================
  // Deal Score Colors
  // ==========================================
  
  function getScoreClass(score) {
    if (score >= 80) return 'excellent';
    if (score >= 60) return 'good';
    if (score >= 40) return 'fair';
    return 'poor';
  }
  
  function getScoreLabel(score) {
    if (score >= 80) return 'Excellent';
    if (score >= 60) return 'Good';
    if (score >= 40) return 'Fair';
    return 'Poor';
  }
  
  // ==========================================
  // Format Helpers
  // ==========================================
  
  function formatCurrency(value) {
    if (value === null || value === undefined) return '$0';
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    }).format(value);
  }
  
  function formatPercent(value) {
    if (value === null || value === undefined) return '0%';
    return `${Math.round(value)}%`;
  }
  
  // ==========================================
  // Analyze Single Case
  // ==========================================
  
  async function analyzeCase(caseId) {
    try {
      const response = await fetch(`${API_BASE}/cases/${caseId}/analyze`, {
        method: 'POST'
      });
      
      if (!response.ok) throw new Error('Analysis failed');
      
      return await response.json();
    } catch (error) {
      console.error('Error analyzing case:', error);
      return null;
    }
  }
  
  // ==========================================
  // Get Top Deals
  // ==========================================
  
  async function getTopDeals(limit = 10, minScore = 60) {
    try {
      const response = await fetch(
        `${API_BASE}/cases/top-deals?limit=${limit}&min_score=${minScore}`
      );
      
      if (!response.ok) throw new Error('Failed to fetch top deals');
      
      return await response.json();
    } catch (error) {
      console.error('Error fetching top deals:', error);
      return null;
    }
  }
  
  // ==========================================
  // Get Deal Distribution
  // ==========================================
  
  async function getDealDistribution() {
    try {
      const response = await fetch(`${API_BASE}/analytics/deal-distribution`);
      
      if (!response.ok) throw new Error('Failed to fetch distribution');
      
      return await response.json();
    } catch (error) {
      console.error('Error fetching distribution:', error);
      return null;
    }
  }
  
  // ==========================================
  // Render Deal Score Badge
  // ==========================================
  
  function renderDealScoreBadge(score, container) {
    const scoreClass = getScoreClass(score);
    const scoreLabel = getScoreLabel(score);
    
    container.innerHTML = `
      <div class="deal-score-badge ${scoreClass}">
        <span class="deal-score-number">${score}</span>
        <span class="deal-score-label">${scoreLabel}</span>
      </div>
    `;
  }
  
  // ==========================================
  // Render Deal Analysis Card
  // ==========================================
  
  function renderDealAnalysisCard(analysis, container) {
    if (!analysis || analysis.error) {
      container.innerHTML = '<div class="deal-analysis-error">Unable to analyze deal</div>';
      return;
    }
    
    const { score, metrics, recommendations } = analysis;
    const scoreClass = getScoreClass(score);
    
    container.innerHTML = `
      <div class="deal-analysis-card">
        <div class="deal-analysis-header">
          <h4>Deal Analysis</h4>
          <div class="deal-score-badge ${scoreClass}">
            <span class="deal-score-number">${score}</span>
            <span>/100</span>
          </div>
        </div>
        
        <div class="deal-metrics-grid">
          <div class="deal-metric">
            <div class="deal-metric-label">Max Offer</div>
            <div class="deal-metric-value">${formatCurrency(metrics.max_offer)}</div>
          </div>
          <div class="deal-metric">
            <div class="deal-metric-label">Est. Profit</div>
            <div class="deal-metric-value ${metrics.estimated_profit >= 0 ? 'positive' : 'negative'}">
              ${formatCurrency(metrics.estimated_profit)}
            </div>
          </div>
          <div class="deal-metric">
            <div class="deal-metric-label">ROI</div>
            <div class="deal-metric-value ${metrics.roi_pct >= 20 ? 'positive' : ''}">
              ${formatPercent(metrics.roi_pct)}
            </div>
          </div>
          <div class="deal-metric">
            <div class="deal-metric-label">Equity</div>
            <div class="deal-metric-value ${metrics.equity_pct >= 30 ? 'positive' : ''}">
              ${formatPercent(metrics.equity_pct)}
            </div>
          </div>
          <div class="deal-metric">
            <div class="deal-metric-label">Quality</div>
            <div class="deal-metric-value">${metrics.deal_quality}</div>
          </div>
          <div class="deal-metric">
            <div class="deal-metric-label">Cash-on-Cash</div>
            <div class="deal-metric-value ${metrics.cash_on_cash >= 50 ? 'positive' : ''}">
              ${formatPercent(metrics.cash_on_cash)}
            </div>
          </div>
        </div>
        
        ${recommendations && recommendations.length > 0 ? `
          <div class="deal-recommendations">
            <h5>Recommendations</h5>
            <div class="recommendation-list">
              ${recommendations.map(rec => `
                <span class="recommendation-tag">${escapeHtml(rec)}</span>
              `).join('')}
            </div>
          </div>
        ` : ''}
      </div>
    `;
  }
  
  // ==========================================
  // Load Deal Analysis for Case Detail Page
  // ==========================================
  
  async function loadDealAnalysisForCase(caseId, containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    
    // Show loading state
    container.innerHTML = `
      <div class="deal-analysis-loading">
        <div class="spinner"></div>
        <span>Analyzing deal...</span>
      </div>
    `;
    
    const analysis = await analyzeCase(caseId);
    renderDealAnalysisCard(analysis, container);
  }
  
  // ==========================================
  // Render Deal Score Distribution Chart
  // ==========================================
  
  async function renderDistributionChart(canvasId) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    
    const data = await getDealDistribution();
    if (!data) return;
    
    const ctx = canvas.getContext('2d');
    
    new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: ['Excellent (80-100)', 'Good (60-79)', 'Fair (40-59)', 'Poor (0-39)'],
        datasets: [{
          data: [
            data.distribution.excellent,
            data.distribution.good,
            data.distribution.fair,
            data.distribution.poor
          ],
          backgroundColor: [
            'rgba(34, 197, 94, 0.8)',
            'rgba(59, 130, 246, 0.8)',
            'rgba(245, 158, 11, 0.8)',
            'rgba(239, 68, 68, 0.8)'
          ],
          borderColor: [
            'rgba(34, 197, 94, 1)',
            'rgba(59, 130, 246, 1)',
            'rgba(245, 158, 11, 1)',
            'rgba(239, 68, 68, 1)'
          ],
          borderWidth: 2
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: 'bottom',
            labels: {
              color: '#9ca3af',
              padding: 16,
              font: { size: 12 }
            }
          },
          tooltip: {
            callbacks: {
              label: function(context) {
                const total = data.total_analyzed;
                const value = context.raw;
                const percent = total > 0 ? Math.round((value / total) * 100) : 0;
                return `${context.label}: ${value} (${percent}%)`;
              }
            }
          }
        }
      }
    });
  }
  
  // ==========================================
  // Render Top Deals Table
  // ==========================================
  
  async function renderTopDealsTable(containerId, limit = 10) {
    const container = document.getElementById(containerId);
    if (!container) return;
    
    container.innerHTML = '<div class="deal-analysis-loading"><div class="spinner"></div><span>Loading top deals...</span></div>';
    
    const data = await getTopDeals(limit, 50);
    if (!data || !data.top_deals) {
      container.innerHTML = '<p>Unable to load top deals</p>';
      return;
    }
    
    if (data.top_deals.length === 0) {
      container.innerHTML = '<p>No deals found with score >= 50</p>';
      return;
    }
    
    container.innerHTML = `
      <table class="top-deals-table">
        <thead>
          <tr>
            <th>Score</th>
            <th>Case #</th>
            <th>Max Offer</th>
            <th>Est. Profit</th>
            <th>ROI</th>
            <th>Quality</th>
          </tr>
        </thead>
        <tbody>
          ${data.top_deals.map(deal => {
            const scoreClass = getScoreClass(deal.score);
            return `
              <tr onclick="window.location.href='/cases/${deal.case_id}'" style="cursor: pointer;">
                <td>
                  <span class="deal-score-badge ${scoreClass}">
                    <span class="deal-score-number">${deal.score}</span>
                  </span>
                </td>
                <td>${escapeHtml(deal.case_number)}</td>
                <td>${formatCurrency(deal.metrics.max_offer)}</td>
                <td class="${deal.metrics.estimated_profit >= 0 ? 'positive' : 'negative'}">
                  ${formatCurrency(deal.metrics.estimated_profit)}
                </td>
                <td>${formatPercent(deal.metrics.roi_pct)}</td>
                <td>${deal.metrics.deal_quality}</td>
              </tr>
            `;
          }).join('')}
        </tbody>
      </table>
    `;
  }
  
  // ==========================================
  // Bulk Analyze Button Handler
  // ==========================================
  
  async function bulkAnalyze(buttonElement) {
    if (!buttonElement) return;
    
    const originalText = buttonElement.textContent;
    buttonElement.disabled = true;
    buttonElement.textContent = 'Analyzing...';
    
    try {
      const response = await fetch(`${API_BASE}/cases/bulk-analyze`, {
        method: 'POST'
      });
      
      if (!response.ok) throw new Error('Bulk analysis failed');
      
      const data = await response.json();
      
      window.showNotificationToast(
        'Analysis Complete',
        `Analyzed ${data.total_analyzed} cases`,
        'success'
      );
      
      // Refresh the page or specific components
      location.reload();
      
    } catch (error) {
      console.error('Bulk analysis error:', error);
      window.showNotificationToast(
        'Analysis Failed',
        'Unable to complete bulk analysis',
        'error'
      );
    } finally {
      buttonElement.disabled = false;
      buttonElement.textContent = originalText;
    }
  }
  
  // ==========================================
  // Utility
  // ==========================================
  
  function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }
  
  // ==========================================
  // Expose Functions Globally
  // ==========================================
  
  window.DealAnalysis = {
    analyzeCase,
    getTopDeals,
    getDealDistribution,
    renderDealScoreBadge,
    renderDealAnalysisCard,
    loadDealAnalysisForCase,
    renderDistributionChart,
    renderTopDealsTable,
    bulkAnalyze,
    getScoreClass,
    getScoreLabel,
    formatCurrency,
    formatPercent
  };
  
})();
