/**
 * api.js — Centralized HTTP request layer
 *
 * All API calls go through this module. Provides consistent error handling
 * and response parsing.
 */

async function _request(method, url, body = null, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (body && !(body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }

  const fetchOptions = {
    method,
    headers,
    credentials: 'same-origin',
    ...options,
  };
  if (body) {
    fetchOptions.body = body instanceof FormData ? body : JSON.stringify(body);
  }

  const response = await fetch(url, fetchOptions);
  let data = null;
  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    try { data = await response.json(); } catch { data = null; }
  }

  if (!response.ok) {
    const error = new Error(data?.error || data?.detail || `HTTP ${response.status}`);
    error.status = response.status;
    error.data = data;
    throw error;
  }

  return data;
}

export function get(url, options) { return _request('GET', url, null, options); }
export function post(url, body, options) { return _request('POST', url, body, options); }
export function put(url, body, options) { return _request('PUT', url, body, options); }
export function del(url, body, options) { return _request('DELETE', url, body, options); }
export function patch(url, body, options) { return _request('PATCH', url, body, options); }
