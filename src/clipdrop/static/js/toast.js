/**
 * Toast Notification Module
 * Handles toast notifications for user feedback
 */

(function() {
    'use strict';

    const toastContainer = document.getElementById('toast-container');

    /**
     * Show a toast notification
     * @param {string} message - The message to display
     * @param {string} type - 'success', 'error', or 'info'
     * @param {number} duration - Duration in milliseconds (default: 4000)
     */
    function showToast(message, type = 'success', duration = 4000) {
        if (!toastContainer) {
            console.warn('Toast container not found');
            return;
        }

        const toastId = 'toast-' + Date.now();
        const iconClass = getIconClass(type);
        const toastClass = getToastClass(type);

        const toastHtml = `
            <div id="${toastId}" class="toast ${toastClass}" role="alert" aria-live="assertive">
                <i class="fas ${iconClass} toast-icon" aria-hidden="true"></i>
                <div class="toast-content">
                    <span class="toast-message">${escapeHtml(message)}</span>
                </div>
                <button class="toast-close" aria-label="Close notification">
                    <i class="fas fa-times" aria-hidden="true"></i>
                </button>
            </div>
        `;

        toastContainer.insertAdjacentHTML('beforeend', toastHtml);
        const toastElement = document.getElementById(toastId);

        // Close button handler
        toastElement.querySelector('.toast-close').addEventListener('click', function() {
            removeToast(toastElement);
        });

        // Auto-remove after duration
        setTimeout(function() {
            if (toastElement.parentNode) {
                removeToast(toastElement);
            }
        }, duration);

        return toastElement;
    }

    /**
     * Remove a toast with animation
     * @param {HTMLElement} toastElement - The toast element to remove
     */
    function removeToast(toastElement) {
        toastElement.style.animation = 'fadeIn 0.25s ease reverse forwards';
        setTimeout(function() {
            if (toastElement.parentNode) {
                toastElement.remove();
            }
        }, 250);
    }

    /**
     * Get icon class based on toast type
     * @param {string} type - Toast type
     * @returns {string} Font Awesome icon class
     */
    function getIconClass(type) {
        switch (type) {
            case 'success': return 'fa-check-circle';
            case 'error': return 'fa-exclamation-circle';
            case 'warning': return 'fa-exclamation-triangle';
            default: return 'fa-info-circle';
        }
    }

    /**
     * Get CSS class based on toast type
     * @param {string} type - Toast type
     * @returns {string} CSS class
     */
    function getToastClass(type) {
        switch (type) {
            case 'success': return 'toast-success';
            case 'error': return 'toast-error';
            case 'warning': return 'toast-warning';
            default: return 'toast-info';
        }
    }

    /**
     * Escape HTML to prevent XSS
     * @param {string} text - Text to escape
     * @returns {string} Escaped text
     */
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // Export globally
    window.showToast = showToast;
    window.ClipDrop = window.ClipDrop || {};
    window.ClipDrop.toast = { show: showToast };
})();
