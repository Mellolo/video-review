/**
 * settings-modal.js — 系统设置弹窗（并发控制、Provider 配置等）
 */

import { showToast } from './utils.js';

function _setStatus(msg, type = '') {
  const el = document.getElementById('settings-status');
  if (!el) return;
  el.textContent = msg;
  el.style.color = type === 'error' ? 'var(--error)' : type === 'success' ? 'var(--success)' : 'var(--text-muted)';
}

function _getEl(id) { return document.getElementById(id); }
function _val(id) { const el = _getEl(id); return el ? el.value.trim() : ''; }

async function _loadSettings() {
  try {
    const res = await fetch('/api/settings');
    if (!res.ok) return;
    const data = await res.json();

    // Concurrency
    const imgEl = _getEl('settings-image-concurrency');
    const imgVal = _getEl('settings-image-concurrency-val');
    const vidEl = _getEl('settings-video-concurrency');
    const vidVal = _getEl('settings-video-concurrency-val');
    const sessionEl = _getEl('settings-jimeng-session');
    if (imgEl && data.image_concurrency != null) {
      imgEl.value = data.image_concurrency;
      if (imgVal) imgVal.textContent = data.image_concurrency;
    }
    if (vidEl && data.video_concurrency != null) {
      vidEl.value = data.video_concurrency;
      if (vidVal) vidVal.textContent = data.video_concurrency;
    }
    if (sessionEl && data.jimeng_session_id != null) {
      sessionEl.value = data.jimeng_session_id;
    }

    // LLM Provider
    const llmProvider = _getEl('settings-llm-provider');
    if (llmProvider && data.llm_provider) llmProvider.value = data.llm_provider;
    const llmBaseUrl = _getEl('settings-llm-base-url');
    if (llmBaseUrl) llmBaseUrl.value = data.llm_base_url || '';
    const llmApiKey = _getEl('settings-llm-api-key');
    if (llmApiKey) llmApiKey.value = data.llm_api_key || '';
    const llmModel = _getEl('settings-llm-model');
    if (llmModel) llmModel.value = data.llm_model || '';
    // Google mode fields
    const llmGoogleApiKey = _getEl('settings-llm-google-api-key');
    if (llmGoogleApiKey) llmGoogleApiKey.value = data.gemini_api_key || data.llm_api_key || '';
    const llmGoogleModel = _getEl('settings-llm-google-model');
    if (llmGoogleModel) llmGoogleModel.value = data.llm_model || '';

    // Image Provider
    const imgProvider = _getEl('settings-image-provider');
    if (imgProvider && data.image_provider) imgProvider.value = data.image_provider;
    const imgBaseUrl = _getEl('settings-image-base-url');
    if (imgBaseUrl) imgBaseUrl.value = data.image_base_url || '';
    const imgApiKey = _getEl('settings-image-api-key');
    if (imgApiKey) imgApiKey.value = data.image_api_key || '';
    const imgModel = _getEl('settings-image-model');
    if (imgModel) imgModel.value = data.image_model || '';
    // Google mode fields
    const imgGoogleModel = _getEl('settings-image-google-model');
    if (imgGoogleModel) imgGoogleModel.value = data.image_model || '';
    const imgGoogleApiKey = _getEl('settings-image-google-api-key');
    if (imgGoogleApiKey) imgGoogleApiKey.value = data.gemini_api_key || data.image_api_key || '';

    _updateProviderVisibility();
  } catch (e) {
    console.warn('[settings] load failed', e);
  }
}

function _updateProviderVisibility() {
  const llmProvider = _val('settings-llm-provider');
  const imgProvider = _val('settings-image-provider');

  const llmOpenAI = document.getElementById('settings-llm-openai-fields');
  const llmGoogle = document.getElementById('settings-llm-google-fields');
  const llmCustom = document.getElementById('settings-llm-custom-hint');
  if (llmOpenAI) llmOpenAI.style.display = llmProvider === 'openai_compatible' ? '' : 'none';
  if (llmGoogle) llmGoogle.style.display = llmProvider === 'google' ? '' : 'none';
  if (llmCustom) llmCustom.style.display = llmProvider.startsWith('custom:') ? '' : 'none';

  const imgOpenAI = document.getElementById('settings-image-openai-fields');
  const imgGoogle = document.getElementById('settings-image-google-fields');
  const imgCustom = document.getElementById('settings-image-custom-hint');
  if (imgOpenAI) imgOpenAI.style.display = imgProvider === 'openai_compatible' ? '' : 'none';
  if (imgGoogle) imgGoogle.style.display = imgProvider === 'google' ? '' : 'none';
  if (imgCustom) imgCustom.style.display = imgProvider.startsWith('custom:') ? '' : 'none';
}

export function openSettingsModal() {
  const modal = document.getElementById('settings-modal');
  if (!modal) return;
  _setStatus('');
  modal.classList.add('show');
  _loadSettings();

  // Bind provider change events
  const llmSel = _getEl('settings-llm-provider');
  if (llmSel) llmSel.addEventListener('change', _updateProviderVisibility);
  const imgSel = _getEl('settings-image-provider');
  if (imgSel) imgSel.addEventListener('change', _updateProviderVisibility);
}

export function closeSettingsModal() {
  document.getElementById('settings-modal')?.classList.remove('show');
}

export async function saveSettings() {
  const imgEl = _getEl('settings-image-concurrency');
  const vidEl = _getEl('settings-video-concurrency');
  const sessionEl = _getEl('settings-jimeng-session');
  const body = {};
  if (imgEl) body.image_concurrency = parseInt(imgEl.value, 10);
  if (vidEl) body.video_concurrency = parseInt(vidEl.value, 10);
  if (sessionEl) body.jimeng_session_id = sessionEl.value.trim();

  // LLM Provider
  const llmProvider = _val('settings-llm-provider');
  if (llmProvider) body.llm_provider = llmProvider;
  body.llm_base_url = _val('settings-llm-base-url');
  // Read api_key and model from the currently visible group only
  if (llmProvider === 'google') {
    body.gemini_api_key = _val('settings-llm-google-api-key');
    body.llm_model      = _val('settings-llm-google-model');
  } else {
    body.llm_api_key = _val('settings-llm-api-key');
    body.llm_model   = _val('settings-llm-model');
  }

  // Image Provider
  const imgProvider = _val('settings-image-provider');
  if (imgProvider) body.image_provider = imgProvider;
  body.image_base_url = _val('settings-image-base-url');
  if (imgProvider === 'google') {
    body.gemini_api_key = _val('settings-image-google-api-key');
    body.image_model    = _val('settings-image-google-model');
  } else {
    body.image_api_key = _val('settings-image-api-key');
    body.image_model   = _val('settings-image-model');
  }

  _setStatus('保存中…');
  try {
    const res = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (data.ok) {
      _setStatus('已保存', 'success');
      showToast('设置已保存', 'success');
      setTimeout(closeSettingsModal, 800);
    } else {
      _setStatus('保存失败', 'error');
    }
  } catch (e) {
    _setStatus('网络错误', 'error');
  }
}

// ── Help toggles ─────────────────────────────────────────────────────────

export function toggleJimengHelp() {
  const box = document.getElementById('jimeng-help-box');
  const btn = document.getElementById('jimeng-help-btn');
  if (!box) return;
  const visible = box.style.display !== 'none';
  box.style.display = visible ? 'none' : '';
  if (btn) btn.style.background = visible ? '' : 'var(--accent)';
  if (btn) btn.style.color = visible ? '' : '#fff';
}

// ── API availability test ─────────────────────────────────────────────────

function _testItem(key, state, detail) {
  const spinner = document.getElementById(`api-test-${key}-spinner`);
  const okIcon  = document.getElementById(`api-test-${key}-ok`);
  const failIcon = document.getElementById(`api-test-${key}-fail`);
  const detailEl = document.getElementById(`api-test-${key}-detail`);
  const item    = document.getElementById(`api-test-${key}`);

  // Reset
  spinner?.classList.remove('hidden');
  okIcon?.classList.remove('show');
  failIcon?.classList.remove('show');
  item?.classList.remove('pass', 'fail');
  if (detailEl) detailEl.textContent = detail ?? '检测中…';

  if (state === 'loading') return;

  spinner?.classList.add('hidden');
  if (state === 'ok') {
    okIcon?.classList.add('show');
    item?.classList.add('pass');
    if (detailEl && detail) detailEl.textContent = detail;
  } else if (state === 'fail') {
    failIcon?.classList.add('show');
    item?.classList.add('fail');
    if (detailEl && detail) detailEl.textContent = detail;
  }
}

export function closeApiTestModal() {
  document.getElementById('api-test-modal')?.classList.remove('show');
}

export async function testApiAvailability() {
  const modal = document.getElementById('api-test-modal');
  if (!modal) return;

  // Reset both items to loading state
  _testItem('llm',   'loading', '检测中…');
  _testItem('image', 'loading', '检测中…');
  document.getElementById('api-test-close-btn').style.display = 'none';
  modal.classList.add('show');

  const body = {
    llm_provider:   _val('settings-llm-provider'),
    llm_base_url:   _val('settings-llm-base-url'),
    image_provider: _val('settings-image-provider'),
    image_base_url: _val('settings-image-base-url'),
  };
  const llmProv = body.llm_provider;
  const imgProv = body.image_provider;
  if (llmProv === 'google') {
    body.gemini_api_key = _val('settings-llm-google-api-key');
    body.llm_model      = _val('settings-llm-google-model');
  } else {
    body.llm_api_key = _val('settings-llm-api-key');
    body.llm_model   = _val('settings-llm-model');
  }
  if (imgProv === 'google') {
    body.gemini_api_key = _val('settings-image-google-api-key');
    body.image_model    = _val('settings-image-google-model');
  } else {
    body.image_api_key = _val('settings-image-api-key');
    body.image_model   = _val('settings-image-model');
  }

  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 150_000);
    const res = await fetch('/api/settings/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
    clearTimeout(timer);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ') || line === 'data: [DONE]') continue;
        try {
          const data = JSON.parse(line.slice(6));
          if (data.llm) {
            const llm = data.llm;
            _testItem('llm',
              llm.ok ? 'ok' : 'fail',
              llm.ok ? (llm.reply || '连接成功') : (llm.error || '连接失败'),
            );
          }
          if (data.image) {
            const img = data.image;
            _testItem('image',
              img.ok ? 'ok' : 'fail',
              img.ok ? (img.reply || '连接成功') : (img.error || '连接失败'),
            );
            if (img.ok && img.image_url) {
              const detailEl = document.getElementById('api-test-image-detail');
              if (detailEl) {
                const link = document.createElement('a');
                link.href = img.image_url;
                link.target = '_blank';
                link.rel = 'noopener noreferrer';
                link.textContent = img.image_url;
                link.style.cssText = 'display:block;margin-top:4px;font-size:11px;word-break:break-all;color:var(--accent);';
                detailEl.appendChild(link);
              }
            }
          }
        } catch (_) { /* ignore malformed SSE lines */ }
      }
    }
  } catch (e) {
    const msg = e.name === 'AbortError' ? '检测超时（超过 150 秒）' : '网络错误：' + e.message;
    if (document.querySelector('#api-test-llm-item.loading'))   _testItem('llm',   'fail', msg);
    if (document.querySelector('#api-test-image-item.loading')) _testItem('image', 'fail', msg);
  }

  document.getElementById('api-test-close-btn').style.display = '';
}
