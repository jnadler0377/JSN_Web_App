/**
 * V2.0 Real-Time Notifications System
 * Handles notification bell, dropdown, SSE streaming, and toasts
 */

(function() {
  'use strict';
  
  // ==========================================
  // Configuration
  // ==========================================
  const API_BASE = '/api/v2';
  const SSE_RECONNECT_DELAY = 3000;
  const TOAST_DURATION = 5000;
  
  // ==========================================
  // State
  // ==========================================
  let eventSource = null;
  let notifications = [];
  let unreadCount = 0;
  let isDropdownOpen = false;
  
  // ==========================================
  // DOM Elements
  // ==========================================
  const elements = {
    badge: () => document.getElementById('notification-badge'),
    dropdown: () => document.getElementById('notification-dropdown'),
    list: () => document.getElementById('notification-list'),
    toastContainer: () => document.getElementById('toast-container'),
  };
  
  // ==========================================
  // Notification API
  // ==========================================
  
  async function fetchNotifications() {
    try {
      const response = await fetch(`${API_BASE}/notifications?limit=10`);
      if (!response.ok) throw new Error('Failed to fetch notifications');
      
      const data = await response.json();
      notifications = data.notifications || [];
      unreadCount = data.unread_count || 0;
      
      updateBadge();
      renderNotificationList();
    } catch (error) {
      console.error('Error fetching notifications:', error);
    }
  }
  
  async function markAsRead(notificationId) {
    try {
      await fetch(`${API_BASE}/notifications/${notificationId}/read`, {
        method: 'POST'
      });
      
      // Update local state
      const notification = notifications.find(n => n.id === notificationId);
      if (notification && !notification.is_read) {
        notification.is_read = 1;
        unreadCount = Math.max(0, unreadCount - 1);
        updateBadge();
        renderNotificationList();
      }
    } catch (error) {
      console.error('Error marking notification as read:', error);
    }
  }
  
  async function markAllAsRead() {
    try {
      await fetch(`${API_BASE}/notifications/mark-all-read`, {
        method: 'POST'
      });
      
      // Update local state
      notifications.forEach(n => n.is_read = 1);
      unreadCount = 0;
      updateBadge();
      renderNotificationList();
    } catch (error) {
      console.error('Error marking all as read:', error);
    }
  }
  
  // ==========================================
  // Real-Time SSE Connection
  // ==========================================
  
  function connectSSE() {
    if (eventSource) {
      eventSource.close();
    }
    
    try {
      eventSource = new EventSource(`${API_BASE}/notifications/stream`);
      
      eventSource.onopen = function() {
        console.log('✅ Connected to notification stream');
      };
      
      eventSource.onmessage = function(event) {
        try {
          const data = JSON.parse(event.data);
          
          if (data.type === 'connected') {
            console.log('🔔 Notification stream ready');
          } else if (data.type === 'heartbeat') {
            // Keep-alive, ignore
          } else {
            // New notification received!
            handleNewNotification(data);
          }
        } catch (e) {
          console.error('Error parsing SSE message:', e);
        }
      };
      
      eventSource.onerror = function(error) {
        console.error('SSE connection error:', error);
        eventSource.close();
        
        // Reconnect after delay
        setTimeout(connectSSE, SSE_RECONNECT_DELAY);
      };
    } catch (error) {
      console.error('Error creating EventSource:', error);
      setTimeout(connectSSE, SSE_RECONNECT_DELAY);
    }
  }
  
  function handleNewNotification(notification) {
    // Add to local state
    notifications.unshift(notification);
    unreadCount++;
    
    // Update UI
    updateBadge();
    renderNotificationList();
    
    // Show toast
    showToast({
      title: notification.title,
      message: notification.message,
      type: notification.type || 'info',
      link: notification.link
    });
    
    // Play sound (optional)
    playNotificationSound();
  }
  
  // ==========================================
  // UI Updates
  // ==========================================
  
  function updateBadge() {
    const badge = elements.badge();
    if (!badge) return;
    
    if (unreadCount > 0) {
      badge.textContent = unreadCount > 99 ? '99+' : unreadCount;
      badge.style.display = 'flex';
    } else {
      badge.style.display = 'none';
    }
  }
  
  function renderNotificationList() {
    const list = elements.list();
    if (!list) return;
    
    if (notifications.length === 0) {
      list.innerHTML = '<div class="notification-empty">No notifications</div>';
      return;
    }
    
    list.innerHTML = notifications.map(notification => {
      const icon = getNotificationIcon(notification.type);
      const timeAgo = formatTimeAgo(notification.created_at);
      const isUnread = !notification.is_read;
      
      return `
        <div class="notification-item ${isUnread ? 'unread' : ''}" 
             data-id="${notification.id}"
             onclick="handleNotificationClick(${notification.id}, '${notification.link || ''}')">
          <div class="notification-icon ${notification.type || 'info'}">${icon}</div>
          <div class="notification-content">
            <div class="notification-title">${escapeHtml(notification.title)}</div>
            <div class="notification-message">${escapeHtml(notification.message)}</div>
            <div class="notification-time">${timeAgo}</div>
          </div>
        </div>
      `;
    }).join('');
  }
  
  function getNotificationIcon(type) {
    const icons = {
      info: '💬',
      success: '✅',
      warning: '⚠️',
      error: '❌',
    };
    return icons[type] || icons.info;
  }
  
  // ==========================================
  // Toast Notifications
  // ==========================================
  
  function showToast(options) {
    const container = elements.toastContainer();
    if (!container) return;
    
    const { title, message, type = 'info', link, duration = TOAST_DURATION } = options;
    
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    
    const icon = getNotificationIcon(type);
    
    toast.innerHTML = `
      <div class="toast-icon">${icon}</div>
      <div class="toast-content">
        <div class="toast-title">${escapeHtml(title)}</div>
        <div class="toast-message">${escapeHtml(message)}</div>
        ${link ? `<div class="toast-action"><a href="${link}">View →</a></div>` : ''}
      </div>
      <button class="toast-close" onclick="this.parentElement.remove()">×</button>
    `;
    
    container.appendChild(toast);
    
    // Auto-dismiss
    setTimeout(() => {
      toast.classList.add('exiting');
      setTimeout(() => toast.remove(), 300);
    }, duration);
  }
  
  // ==========================================
  // Dropdown Toggle
  // ==========================================
  
  window.toggleNotifications = function() {
    const dropdown = elements.dropdown();
    if (!dropdown) return;
    
    isDropdownOpen = !isDropdownOpen;
    dropdown.classList.toggle('show', isDropdownOpen);
    
    if (isDropdownOpen) {
      fetchNotifications(); // Refresh when opening
    }
  };
  
  window.markAllNotificationsRead = function() {
    markAllAsRead();
  };
  
  window.handleNotificationClick = function(id, link) {
    markAsRead(id);
    
    if (link) {
      window.location.href = link;
    }
    
    // Close dropdown
    const dropdown = elements.dropdown();
    if (dropdown) {
      dropdown.classList.remove('show');
      isDropdownOpen = false;
    }
  };
  
  // Close dropdown when clicking outside
  document.addEventListener('click', function(event) {
    const bell = document.getElementById('notification-bell');
    if (bell && !bell.contains(event.target) && isDropdownOpen) {
      const dropdown = elements.dropdown();
      if (dropdown) {
        dropdown.classList.remove('show');
        isDropdownOpen = false;
      }
    }
  });
  
  // ==========================================
  // Utility Functions
  // ==========================================
  
  function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }
  
  function formatTimeAgo(dateString) {
    if (!dateString) return '';
    
    const date = new Date(dateString);
    const now = new Date();
    const diff = Math.floor((now - date) / 1000);
    
    if (diff < 60) return 'Just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
    
    return date.toLocaleDateString();
  }
  
  function playNotificationSound() {
    // Optional: Play a subtle notification sound
    try {
      const audio = new Audio('/static/sounds/notification.mp3');
      audio.volume = 0.3;
      audio.play().catch(() => {}); // Ignore if autoplay blocked
    } catch (e) {
      // Ignore sound errors
    }
  }
  
  // ==========================================
  // Global Toast Function (for use elsewhere)
  // ==========================================
  
  window.showNotificationToast = function(title, message, type = 'info', link = null) {
    showToast({ title, message, type, link });
  };
  
  // ==========================================
  // Initialize
  // ==========================================
  
  function init() {
    // Only initialize if user is logged in (notification bell exists)
    if (!elements.badge()) return;
    
    // Fetch initial notifications
    fetchNotifications();
    
    // Connect to SSE for real-time updates
    connectSSE();
    
    // Refresh notifications periodically as backup
    setInterval(fetchNotifications, 60000); // Every minute
  }
  
  // Run on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
  
})();
