/**
 * store.js — Reactive state management (Single Source of Truth)
 *
 * Replaces the scattered export-let + setter pattern in state.js with a
 * centralized store that supports path-based get/set and subscriptions.
 *
 * Migration strategy: state.js variables are gradually moved here.
 * During transition, both systems coexist — state.js setters forward to
 * store.set() and store subscribers can read from state.js.
 */

const _state = {};
const _listeners = new Map();
let _batchDepth = 0;
const _pendingNotifications = new Set();

/**
 * Get a value by dot-separated path.
 * @param {string} path — e.g. 'user', 'promptEditors.u1'
 * @returns {*}
 */
export function get(path) {
  if (!path) return undefined;
  const keys = path.split('.');
  let obj = _state;
  for (const key of keys) {
    if (obj == null) return undefined;
    obj = obj[key];
  }
  return obj;
}

/**
 * Set a value by dot-separated path and notify subscribers.
 * @param {string} path
 * @param {*} value
 */
export function set(path, value) {
  if (!path) return;
  const keys = path.split('.');
  const last = keys.pop();
  let obj = _state;
  for (const key of keys) {
    if (obj[key] == null || typeof obj[key] !== 'object') {
      obj[key] = {};
    }
    obj = obj[key];
  }
  const oldValue = obj[last];
  obj[last] = value;
  if (oldValue !== value) {
    _notify(path);
  }
}

/**
 * Subscribe to changes on a path (or any sub-path).
 * @param {string} path
 * @param {function} callback — called with (newValue, path)
 * @returns {function} unsubscribe
 */
export function subscribe(path, callback) {
  if (!_listeners.has(path)) {
    _listeners.set(path, new Set());
  }
  _listeners.get(path).add(callback);
  return () => _listeners.get(path)?.delete(callback);
}

/**
 * Batch multiple set() calls — subscribers are notified once at the end.
 * @param {function} fn
 */
export function batch(fn) {
  _batchDepth++;
  try {
    fn();
  } finally {
    _batchDepth--;
    if (_batchDepth === 0) {
      const paths = [..._pendingNotifications];
      _pendingNotifications.clear();
      for (const path of paths) {
        _fireListeners(path);
      }
    }
  }
}

function _notify(changedPath) {
  if (_batchDepth > 0) {
    _pendingNotifications.add(changedPath);
    return;
  }
  _fireListeners(changedPath);
}

function _fireListeners(changedPath) {
  for (const [subscribedPath, callbacks] of _listeners) {
    if (changedPath === subscribedPath
        || changedPath.startsWith(subscribedPath + '.')
        || subscribedPath.startsWith(changedPath + '.')) {
      const value = get(subscribedPath);
      for (const cb of callbacks) {
        try { cb(value, changedPath); } catch (e) { console.error('[store] subscriber error:', e); }
      }
    }
  }
}

/**
 * Get a snapshot of the entire state (for debugging).
 */
export function snapshot() {
  return JSON.parse(JSON.stringify(_state));
}

/**
 * Initialize store with default values.
 */
export function init(defaults = {}) {
  for (const [key, value] of Object.entries(defaults)) {
    if (!(key in _state)) {
      _state[key] = value;
    }
  }
}
