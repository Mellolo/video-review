/**
 * utils.js – low-level helpers & fetch wrappers (ES module)
 *
 * Exports: nativeFetch, parseApiJsonSafely, esc,
 *          apiFetch, installFetchMonkeyPatch, showToast,
 *          _prevVideoJobStatuses, _checkVideoJobStatusChanges,
 *          setupCreateFormValidation
 */

// ── nativeFetch (grab before any monkey-patching) ──────────────
export const nativeFetch = window.fetch.bind(window);

// ── Stubs for removed auth features ───────────────────────────
export function getCookie() { return ''; }
export function updateCsrfToken() {}
export function getCurrentCsrfToken() { return ''; }
export function resetSessionClientState() {}
export function redirectToLogin() {}

// ── JSON helpers ───────────────────────────────────────────────

export async function parseApiJsonSafely(response) {
  try { return await response.json(); } catch { return {}; }
}

// ── HTML escape ────────────────────────────────────────────────

export function esc(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// ── apiFetch ───────────────────────────────────────────────────

export async function apiFetch(url, options = {}) {
  const opts = { ...options };
  const headers = new Headers(opts.headers || {});
  opts.headers = headers;
  const response = await nativeFetch(url, opts);
  let data = {};
  try { data = await response.clone().json(); } catch { /* non-JSON */ }
  return { response, data };
}

// ── Monkey-patch window.fetch (no-op in single-user mode) ──────

export function installFetchMonkeyPatch() {
  // No CSRF injection needed in single-user mode.
}

// ── Toast notifications ────────────────────────────────────────

export function showToast(message, type = 'info', duration = 3500) {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.style.cssText = 'position:fixed;bottom:24px;right:24px;z-index:9999;display:flex;flex-direction:column;gap:8px;';
    document.body.appendChild(container);
  }
  const toast = document.createElement('div');
  const colors = { info: '#3b82f6', success: '#22c55e', warning: '#f59e0b', error: '#ef4444' };
  toast.style.cssText = `background:${colors[type] || colors.info};color:#fff;padding:10px 16px;border-radius:8px;font-size:14px;max-width:320px;box-shadow:0 4px 12px rgba(0,0,0,.3);opacity:0;transition:opacity .2s;`;
  toast.textContent = message;
  container.appendChild(toast);
  requestAnimationFrame(() => { toast.style.opacity = '1'; });
  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 200);
  }, duration);
}

// ── Video job status change detection ─────────────────────────

export const _prevVideoJobStatuses = {};

export function _checkVideoJobStatusChanges(jobs) {
  if (!Array.isArray(jobs)) return;
  for (const job of jobs) {
    const prev = _prevVideoJobStatuses[job.job_id];
    const curr = job.status;
    if (prev && prev !== curr) {
      const label = job.title || job.job_id;
      if (curr === 'done') showToast(`✓ 视频生成完成：${label}`, 'success', 5000);
      else if (curr === 'failed') showToast(`✗ 视频生成失败：${label}`, 'error', 5000);
    }
    _prevVideoJobStatuses[job.job_id] = curr;
  }
}

// ── Create form validation ─────────────────────────────────────

export function setupCreateFormValidation() {
  const fields = ['#create-prompt', '#create-title'];
  fields.forEach(sel => {
    const el = document.querySelector(sel);
    if (!el) return;
    el.addEventListener('input', () => {
      if (typeof window.updateCreateSubmitButtons === 'function') window.updateCreateSubmitButtons();
    });
  });
  if (typeof window.updateCreateSubmitButtons === 'function') window.updateCreateSubmitButtons();
}
