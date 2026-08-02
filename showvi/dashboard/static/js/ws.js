// ── WebSocket ──────────────────────────────────────────────────
import {
  ws, setWs,
  reconnectTimer, setReconnectTimer,
  currentData, setCurrentData,
  videoJobsData, setVideoJobsData,
  selectedVideoJobId, monitorBrowseMode,
} from './state.js';

// --- local state ---
let _wsConnecting = false;
let _wsReconnectDelay = 1000;
const _WS_MAX_RECONNECT_DELAY = 30000;
let _wsPingInterval = null;
let _pendingWsMessages = [];

function _hasActivePromptEditor() {
  const editors = document.querySelectorAll('.prompt-editor[contenteditable="true"]');
  for (const el of editors) {
    if (el.matches(':focus') || el.contains(document.activeElement)) return true;
  }
  return false;
}

function _applyMonitorData(data) {
  if (!data) return;
  setCurrentData(data);
  window.updateProjectsToggleLabel?.();
}

function connectWS() {
  if (_wsConnecting || (ws && ws.readyState === WebSocket.OPEN)) return;
  _wsConnecting = true;
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const newWs = new WebSocket(`${proto}://${location.host}/ws`);
  newWs.onopen = () => {
    setWs(newWs);
    _wsConnecting = false;
    _wsReconnectDelay = 1000;
    if (reconnectTimer) { clearTimeout(reconnectTimer); setReconnectTimer(null); }

    // flush queued messages
    while (_pendingWsMessages.length) {
      const m = _pendingWsMessages.shift();
      try { ws.send(JSON.stringify(m)); } catch (_) { /* ignore */ }
    }

    // keep-alive ping
    if (_wsPingInterval) clearInterval(_wsPingInterval);
    _wsPingInterval = setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        try { ws.send(JSON.stringify({ type: 'ping' })); } catch (_) { /* ignore */ }
      }
    }, 25000);
  };
  newWs.onmessage = (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch (_) { return; }
    if (msg.type === 'full_update' || msg.type === 'monitor_update') {
      _applyMonitorData(msg.data);
      if (window.currentTab === 'monitor') {
        const pane = document.getElementById('monitor-detail-pane');
        if (pane && window.shouldRefreshMonitorBrowseInPlace?.(pane)) {
          window.scheduleDebouncedBrowsePaneRefresh?.();
          window.patchMonitorJobsSidebarFromData?.();
        } else if (monitorBrowseMode) {
          window.clearDebouncedBrowsePaneRefresh?.();
          // Protect active prompt editors from being overwritten
          if (!_hasActivePromptEditor()) {
            window.renderMonitor?.();
          } else {
            window.patchMonitorJobsSidebarFromData?.();
          }
        } else {
          window.clearDebouncedBrowsePaneRefresh?.();
          const job = videoJobsData.find(j => j.job_id === selectedVideoJobId);
          if (job) window._renderMonitorDetail?.(job);
        }
      }
      window.maybeRefreshEditorImages?.();
    }
    if (msg.type === 'create_progress') { window.handleCreateProgress?.(msg); }
    if (msg.type === 'video_jobs_update') {
      window._checkVideoJobStatusChanges?.(msg.jobs);
      setVideoJobsData(msg.jobs);
      window.renderVideoJobsPanel?.();
    }
    if (msg.type === 'title_renamed') {
      window.loadRepository?.();
    }
  };
  newWs.onclose = () => {
    _wsConnecting = false;
    if (ws === newWs) setWs(null);
    if (_wsPingInterval) { clearInterval(_wsPingInterval); _wsPingInterval = null; }
    if (!reconnectTimer) {
      setReconnectTimer(setTimeout(() => {
        setReconnectTimer(null);
        connectWS();
      }, _wsReconnectDelay));
      _wsReconnectDelay = Math.min(_wsReconnectDelay * 2, _WS_MAX_RECONNECT_DELAY);
    }
  };
  newWs.onerror = () => { try { newWs.close(); } catch (_) {} };
}

function _wsSend(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(msg));
  } else {
    _pendingWsMessages.push(msg);
  }
}

export { connectWS, _wsSend, _applyMonitorData };
