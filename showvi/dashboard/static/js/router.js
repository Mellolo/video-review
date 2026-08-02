/**
 * router.js — Event delegation dispatcher
 *
 * Replaces the window.xxx function registration pattern with a centralized
 * event delegation system using data-action attributes.
 *
 * Usage in HTML:
 *   <button data-action="editor:save-scene" data-scene-id="3">Save</button>
 *
 * Usage in JS:
 *   import { registerActions } from './router.js';
 *   registerActions('editor', {
 *     'save-scene': (data) => { console.log(data.sceneId); },
 *   });
 *
 * Migration: during transition, both onclick="window.xxx()" and data-action
 * coexist. New features should use data-action exclusively.
 */

const _handlers = new Map();

/**
 * Register action handlers under a namespace.
 * @param {string} namespace — e.g. 'editor', 'video', 'monitor'
 * @param {Object<string, function>} handlers — { actionName: handler }
 */
export function registerActions(namespace, handlers) {
  for (const [name, fn] of Object.entries(handlers)) {
    _handlers.set(`${namespace}:${name}`, fn);
  }
}

/**
 * Unregister all actions under a namespace.
 */
export function unregisterNamespace(namespace) {
  for (const key of [..._handlers.keys()]) {
    if (key.startsWith(namespace + ':')) {
      _handlers.delete(key);
    }
  }
}

/**
 * Manually dispatch an action (useful for programmatic triggers).
 */
export function dispatch(action, data = {}) {
  const handler = _handlers.get(action);
  if (handler) {
    handler(data);
    return true;
  }
  return false;
}

function _extractDataset(el) {
  const data = {};
  for (const [key, value] of Object.entries(el.dataset)) {
    if (key === 'action') continue;
    data[key] = value;
  }
  return data;
}

function _handleClick(e) {
  const el = e.target.closest('[data-action]');
  if (!el) return;
  const action = el.dataset.action;
  const handler = _handlers.get(action);
  if (handler) {
    e.preventDefault();
    handler({ ...(_extractDataset(el)), event: e, element: el });
  }
}

function _handleChange(e) {
  const el = e.target.closest('[data-action-change]');
  if (!el) return;
  const action = el.dataset.actionChange;
  const handler = _handlers.get(action);
  if (handler) {
    handler({ ...(_extractDataset(el)), value: el.value, checked: el.checked, event: e, element: el });
  }
}

function _handleSubmit(e) {
  const el = e.target.closest('[data-action-submit]');
  if (!el) return;
  const action = el.dataset.actionSubmit;
  const handler = _handlers.get(action);
  if (handler) {
    e.preventDefault();
    const formData = new FormData(el);
    handler({ ...(_extractDataset(el)), formData, event: e, element: el });
  }
}

/**
 * Initialize event delegation on the document.
 * Call once during app bootstrap.
 */
export function initRouter() {
  document.addEventListener('click', _handleClick);
  document.addEventListener('change', _handleChange);
  document.addEventListener('submit', _handleSubmit);
}
