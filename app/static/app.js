/**
 * Uplift — app.js
 * ──────────────────────────────────────────────────────────────
 * Minimal client-side JS:
 *   1. Character counter on the write form
 *   2. Dark-mode toggle (persisted in localStorage)
 */

(function () {
    'use strict';

    // ── 1. Character counter ─────────────────────────────────────
    var msgBox = document.getElementById('msgBox');
    var charCount = document.getElementById('charCount');

    if (msgBox && charCount) {
        msgBox.addEventListener('input', function () {
            charCount.textContent = msgBox.value.length;
        });
    }

    // ── 2. Dark-mode toggle ──────────────────────────────────────
    var toggle = document.getElementById('themeToggle');
    var html = document.documentElement;

    // Apply saved preference
    var saved = localStorage.getItem('uplift-theme');
    if (saved) {
        html.setAttribute('data-theme', saved);
    }

    if (toggle) {
        toggle.addEventListener('click', function () {
            var current = html.getAttribute('data-theme') || 'light';
            var next = current === 'light' ? 'dark' : 'light';
            html.setAttribute('data-theme', next);
            localStorage.setItem('uplift-theme', next);
        });
    }
})();
