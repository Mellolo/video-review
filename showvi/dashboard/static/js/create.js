// ══════════════════════════════════════════════════════════════
// create.js — Creation Mode ES module
// Extracted from index.html lines 8698-10584
//
// ⚠️ 开发注意：
// 1. 模块变量（createdStoryboard 等）通过 main.js 的 Object.defineProperty
//    与 window 活绑定，直接赋值即可同步到 window，无需手动写 window.xxx = ...
// 2. 修改 storyboard[].seedance_prompt 时必须同步 groups[].sora_prompt
//    （历史遗留冗余，当前约 6 处双写，漏写会导致数据不一致）
// 3. window._reviewStoryboard / _pendingScreenplay / _chatHistory 等是
//    无追踪的隐式全局状态；新增类似变量时优先用 export let + setter
// ══════════════════════════════════════════════════════════════

import { esc, apiFetch, showToast, nativeFetch } from './utils.js';
import { t } from './i18n.js';
import {
  currentData,
  videoJobsData,
  selectedVideoJobId, setSelectedVideoJobId,
  monitorBrowseMode, setMonitorBrowseMode,
} from './state.js';
import { switchTab } from './nav.js';

/* ── Cross-module references (not yet extracted) ─────────────── */
const _win = window;
const getCurrentBackend         = () => _win.currentBackend ?? 'jimeng';
const _guardedBtnAction         = (...a) => _win._guardedBtnAction?.(...a);
const renderEditor              = (...a) => _win.renderEditor?.(...a);
const renderEditorScenes        = (...a) => _win.renderEditorScenes?.(...a);
const refreshSceneContinuitySection = (...a) => _win.refreshSceneContinuitySection?.(...a);
const loadProjectList           = (...a) => _win.loadProjectList?.(...a);
const refreshRepositoryData     = (...a) => _win.refreshRepositoryData?.(...a);
const loadVideoJobsFn           = (...a) => _win.loadVideoJobs?.(...a);
const loadRepository            = (...a) => _win.loadRepository?.(...a);
const renderMonitor             = (...a) => _win.renderMonitor?.(...a);
const openSegmentModal          = (...a) => _win.openSegmentModal?.(...a);

// ══════════════════════════════════════════════════════════════
// State
// ══════════════════════════════════════════════════════════════
export let createMode = null;
export let createJobId = null;
export let createPhase = 'form'; // form | pipeline | editor
export let createdStoryboard = null;
export let createdStoryboardPath = null;
export let uploadedNovelPath = null;
export let uploadedVideoPath = null;
export let uploadedVideoDurationSeconds = null;
export let activeCreateJobMeta = null;
window._createOneClick = false;

// Split button state
let _splitBtnIsOneClick = true;
let _currentUnifiedMode = 'quickchat'; // quickchat | prompt | novel | video
let _novelFileName = null; // track uploaded novel file name

// ── Empty-input flash hint ──────────────────────────────────
function _flashEmptyHint(textarea) {
  if (!textarea) return;
  textarea.focus();
  textarea.classList.add('shake');
  const status = document.getElementById('create-submit-status');
  if (status) {
    status.textContent = t('create.empty_hint');
    status.style.color = 'var(--warning, #fbbf24)';
    clearTimeout(status._timer);
    status._timer = setTimeout(() => { status.textContent = ''; }, 3000);
  }
  setTimeout(() => textarea.classList.remove('shake'), 500);
}

// ── Setters (for cross-module mutation) ─────────────────────
export function setCreateMode(v) { createMode = v; }
export function setCreateJobId(v) { createJobId = v; }
export function setCreatePhase(v) { createPhase = v; }
export function setCreatedStoryboard(v) { createdStoryboard = v; }
export function setCreatedStoryboardPath(v) { createdStoryboardPath = v; }
export function setUploadedNovelPath(v) { uploadedNovelPath = v; }
export function setUploadedVideoPath(v) { uploadedVideoPath = v; }
export function setUploadedVideoDurationSeconds(v) { uploadedVideoDurationSeconds = v; }
export function setActiveCreateJobMeta(v) { activeCreateJobMeta = v; }

// ══════════════════════════════════════════════════════════════
// Submission readiness
// ══════════════════════════════════════════════════════════════

export function isCreateSubmissionReady(mode = createMode) {
  if (!mode) {
    // quickchat mode: createMode is null, check textarea
    if (_currentUnifiedMode === 'quickchat') {
      return !!document.getElementById('unified-textarea')?.value.trim();
    }
    return false;
  }
  if (mode === 'video') return !!uploadedVideoPath;
  if (mode === 'novel') return !!(document.getElementById('unified-textarea')?.value.trim() || uploadedNovelPath);
  if (mode === 'prompt') return !!document.getElementById('unified-textarea')?.value.trim();
  return false;
}

export function updateCreateSubmitButtons() {
  updateUnifiedButtons();
}

export function updateUnifiedButtons() {
  // When a job is active, disable buttons and show busy tooltip
  const busy = isCreateJobActive();
  const tip = busy ? t('create.job_active_wait') : '';
  const busyLabel = busy ? t('create.job_active_label') : '';
  const quickBtn = document.getElementById('quick-send-btn');
  const splitMain = document.getElementById('split-btn-main');
  const splitToggle = document.getElementById('split-btn-toggle');
  const splitWrap = document.getElementById('split-btn');
  const splitLabel = document.getElementById('split-btn-label');
  if (quickBtn) {
    // Use aria-disabled + pointer-events instead of disabled so hover still works
    if (busy) {
      quickBtn.setAttribute('data-busy', '1');
      quickBtn.setAttribute('data-busy-tip', tip);
      quickBtn.setAttribute('aria-disabled', 'true');
      quickBtn.style.pointerEvents = 'none';
      quickBtn.style.opacity = '0.7';
      quickBtn.style.cursor = 'not-allowed';
      // Show busy text inside button
      if (!quickBtn.querySelector('.quick-send-busy-label')) {
        const origSvg = quickBtn.querySelector('svg');
        if (origSvg) origSvg.style.display = 'none';
        const label = document.createElement('span');
        label.className = 'quick-send-busy-label';
        label.textContent = t('create.job_active_label');
        quickBtn.appendChild(label);
        quickBtn.style.width = 'auto';
        quickBtn.style.padding = '0 14px';
        quickBtn.style.fontSize = '12px';
        quickBtn.style.fontWeight = '600';
      }
    } else {
      quickBtn.removeAttribute('data-busy');
      quickBtn.removeAttribute('data-busy-tip');
      quickBtn.removeAttribute('aria-disabled');
      quickBtn.style.pointerEvents = '';
      quickBtn.style.opacity = '';
      quickBtn.style.cursor = '';
      // Restore original icon
      const busyLabel = quickBtn.querySelector('.quick-send-busy-label');
      if (busyLabel) busyLabel.remove();
      const origSvg = quickBtn.querySelector('svg');
      if (origSvg) origSvg.style.display = '';
      quickBtn.style.width = '';
      quickBtn.style.padding = '';
      quickBtn.style.fontSize = '';
      quickBtn.style.fontWeight = '';
    }
  }
  if (splitMain) {
    if (busy) {
      splitMain.setAttribute('aria-disabled', 'true');
      splitMain.style.pointerEvents = 'none';
      splitMain.style.opacity = '0.45';
      splitMain.style.cursor = 'not-allowed';
    } else {
      splitMain.removeAttribute('aria-disabled');
      splitMain.style.pointerEvents = '';
      splitMain.style.opacity = '';
      splitMain.style.cursor = '';
    }
  }
  if (splitLabel) {
    if (busy) {
      splitLabel.setAttribute('data-original-text', splitLabel.textContent);
      splitLabel.textContent = busyLabel || t('create.job_active_label');
    } else {
      const orig = splitLabel.getAttribute('data-original-text');
      if (orig) { splitLabel.textContent = orig; splitLabel.removeAttribute('data-original-text'); }
    }
  }
  if (splitToggle) {
    splitToggle.disabled = busy;
  }
  if (splitWrap) {
    if (busy) {
      splitWrap.setAttribute('data-busy', '1');
      splitWrap.setAttribute('data-busy-tip', tip);
    } else {
      splitWrap.removeAttribute('data-busy');
      splitWrap.removeAttribute('data-busy-tip');
    }
  }
}

// ══════════════════════════════════════════════════════════════
// Unified textarea helpers (replaces quick-chat helpers)
// ══════════════════════════════════════════════════════════════

export function unifiedTextareaResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 160) + 'px';
}

export function unifiedTextareaKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    if (_currentUnifiedMode === 'quickchat') {
      quickChatSubmit();
    } else {
      splitBtnSubmit();
    }
  }
}

export function hintChipFill(chip) {
  const ta = document.getElementById('unified-textarea');
  if (!ta) return;
  ta.value = chip.textContent.trim();
  ta.focus();
  unifiedTextareaResize(ta);
  updateUnifiedButtons();
}

// Legacy aliases for backward compat
export function quickChatAutoResize(el) { unifiedTextareaResize(el); }
export function quickChatUpdateSend() { updateUnifiedButtons(); }
export function quickChatKeydown(e) { unifiedTextareaKeydown(e); }
export function quickChatFill(chip) { hintChipFill(chip); }

export async function quickChatSubmit() {
  const ta = document.getElementById('unified-textarea');
  const idea = ta?.value.trim();
  if (!idea) {
    _flashEmptyHint(ta);
    return;
  }
  // In quickchat mode, directly submit as one-click prompt
  createMode = 'prompt';
  submitCreate(true);
}

// ══════════════════════════════════════════════════════════════
// Mode switching (unified)
// ══════════════════════════════════════════════════════════════

export function switchMode(mode) {
  _currentUnifiedMode = mode;

  // Map to createMode for backend
  if (mode === 'quickchat') {
    createMode = null; // will be set to 'prompt' on submit
  } else {
    createMode = mode;
  }

  // Update card active states
  document.querySelectorAll('.mode-card-v2').forEach(c =>
    c.classList.toggle('active', c.dataset.mode === mode)
  );

  const textarea = document.getElementById('unified-textarea');
  const videoUpload = document.getElementById('unified-video-upload');
  const hintChips = document.getElementById('hint-chips');
  const extraFields = document.getElementById('extra-fields');
  const extraRow2 = document.getElementById('extra-fields-row2');
  const quickBtn = document.getElementById('quick-send-btn');
  const splitBtn = document.getElementById('split-btn');
  const fileTagBar = document.getElementById('file-tag-bar');
  const novelUploadBtn = document.getElementById('novel-upload-btn');

  // Reset visibility
  if (textarea) textarea.style.display = '';
  if (videoUpload) videoUpload.style.display = 'none';
  if (hintChips) hintChips.style.display = '';
  if (extraFields) extraFields.style.display = 'none';
  if (extraRow2) extraRow2.style.display = 'none';
  if (quickBtn) quickBtn.style.display = '';
  if (splitBtn) splitBtn.style.display = 'none';
  if (novelUploadBtn) novelUploadBtn.style.display = 'none';
  if (fileTagBar && mode !== 'novel') fileTagBar.style.display = 'none';

  if (mode === 'quickchat') {
    // Textarea visible, hints visible, no extra fields, paper-plane button
    if (textarea) textarea.placeholder = t('quickchat.placeholder');
  } else if (mode === 'prompt') {
    if (textarea) textarea.placeholder = t('form.creative_placeholder');
    if (hintChips) hintChips.style.display = 'none';
    if (extraFields) extraFields.style.display = '';
    if (quickBtn) quickBtn.style.display = 'none';
    if (splitBtn) splitBtn.style.display = '';
  } else if (mode === 'novel') {
    if (textarea) textarea.placeholder = t('form.paste_placeholder');
    if (hintChips) hintChips.style.display = 'none';
    if (extraFields) extraFields.style.display = '';
    if (quickBtn) quickBtn.style.display = 'none';
    if (splitBtn) splitBtn.style.display = '';
    if (novelUploadBtn) novelUploadBtn.style.display = '';
    // Show file tag if a file was previously uploaded
    if (fileTagBar && _novelFileName) fileTagBar.style.display = '';
  } else if (mode === 'video') {
    if (textarea) textarea.style.display = 'none';
    if (videoUpload) videoUpload.style.display = '';
    if (hintChips) hintChips.style.display = 'none';
    if (extraFields) extraFields.style.display = '';
    if (extraRow2) extraRow2.style.display = '';
    if (quickBtn) quickBtn.style.display = 'none';
    if (splitBtn) splitBtn.style.display = '';
  }

  toggleVideoRecreateDirection();
  updateUnifiedButtons();
}

// Legacy alias
export function selectCreateMode(mode) {
  switchMode(mode);
}

export function toggleVideoRecreateDirection() {
  const modeEl = document.getElementById('create-video-mode');
  const groupEl = document.getElementById('video-recreate-direction-group');
  if (!modeEl || !groupEl) return;
  const shouldShow = createMode === 'video' && modeEl.value === 'recreate';
  groupEl.style.display = shouldShow ? '' : 'none';
}

// ══════════════════════════════════════════════════════════════
// Duration helpers
// ══════════════════════════════════════════════════════════════

export function formatDurationSecondsForInput(value) {
  const num = Number(value);
  if (!Number.isFinite(num) || num <= 0) return '';
  return Number.isInteger(num) ? String(num) : String(Math.round(num * 10) / 10);
}

export function parseStoryboardDurationSeconds(raw) {
  if (typeof raw === 'number') return Number.isFinite(raw) ? raw : 0;
  const text = String(raw || '').trim();
  if (!text) return 0;
  const match = text.match(/\d+(?:\.\d+)?/);
  return match ? parseFloat(match[0]) : 0;
}

export function getStoryboardTotalDuration(sb = createdStoryboard) {
  const scenes = sb?.storyboard || [];
  const total = scenes.reduce((sum, scene) => sum + parseStoryboardDurationSeconds(scene.duration), 0);
  if (total > 0) return total;
  const metaTotal = Number(sb?._meta?.estimated_duration_seconds || 0);
  return Number.isFinite(metaTotal) ? metaTotal : 0;
}

export function setCreateDurationInput(value) {
  const input = document.getElementById('create-duration');
  if (!input) return;
  const formatted = formatDurationSecondsForInput(value);
  if (formatted) input.value = formatted;
}

export function applyStoryboardTotalDuration(totalSeconds) {
  if (!createdStoryboard) return false;
  const target = Number(totalSeconds);
  if (!Number.isFinite(target) || target <= 0) return false;

  const scenes = createdStoryboard.storyboard || [];
  if (!scenes.length) return false;

  const currentDurations = scenes.map(scene => {
    const parsed = parseStoryboardDurationSeconds(scene.duration);
    return parsed > 0 ? parsed : 10;
  });
  const currentTotal = currentDurations.reduce((sum, value) => sum + value, 0);
  if (!currentTotal || currentTotal <= 0) return false;

  const ratio = target / currentTotal;
  const nextDurations = currentDurations.map(value => Math.max(1, Math.round(value * ratio * 10) / 10));
  const adjustedTotal = nextDurations.reduce((sum, value) => sum + value, 0);
  const delta = Math.round((target - adjustedTotal) * 10) / 10;
  if (Math.abs(delta) > 0.001 && nextDurations.length) {
    nextDurations[nextDurations.length - 1] = Math.max(1, Math.round((nextDurations[nextDurations.length - 1] + delta) * 10) / 10);
  }

  scenes.forEach((scene, idx) => {
    const dur = nextDurations[idx];
    scene.duration = `${formatDurationSecondsForInput(dur)}秒`;
    if (createdStoryboard.groups && createdStoryboard.groups[idx]) {
      createdStoryboard.groups[idx].total_seconds = dur;
    }
  });

  createdStoryboard._meta = createdStoryboard._meta || {};
  createdStoryboard._meta.estimated_duration_seconds = Math.round(nextDurations.reduce((sum, value) => sum + value, 0) * 10) / 10;
  createdStoryboard._meta.target_duration_seconds = Math.round(target * 10) / 10;
  return true;
}

export function bindEditorTotalDurationInput() {
  const input = document.getElementById('editor-total-duration');
  if (!input) return;
  const total = getStoryboardTotalDuration(createdStoryboard);
  input.value = formatDurationSecondsForInput(total);
  input.onchange = () => {
    const next = parseFloat(input.value);
    if (!applyStoryboardTotalDuration(next)) {
      input.value = formatDurationSecondsForInput(getStoryboardTotalDuration(createdStoryboard));
      return;
    }
    renderEditorScenes();
    input.value = formatDurationSecondsForInput(getStoryboardTotalDuration(createdStoryboard));
    const statusEl = document.getElementById('editor-save-status');
    if (statusEl) {
      statusEl.textContent = t('create.duration_updated');
      setTimeout(() => { if (statusEl.textContent === t('create.duration_updated')) statusEl.textContent = ''; }, 2500);
    }
  };
  input.onkeydown = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      input.blur();
    }
  };
}

// ══════════════════════════════════════════════════════════════
// Split Button logic
// ══════════════════════════════════════════════════════════════

export function initSplitButton() {
  // Restore last choice from localStorage
  const saved = localStorage.getItem('splitBtnAction');
  _splitBtnIsOneClick = saved === 'false' ? false : true;
  _syncSplitBtnLabel();

  // Close menu on outside click
  document.addEventListener('click', (e) => {
    const menu = document.getElementById('split-btn-menu');
    const toggle = document.getElementById('split-btn-toggle');
    if (menu && !menu.contains(e.target) && e.target !== toggle && !toggle?.contains(e.target)) {
      menu.classList.remove('open');
    }
  });
}

export function toggleSplitMenu(e) {
  if (e) e.stopPropagation();
  const menu = document.getElementById('split-btn-menu');
  if (menu) menu.classList.toggle('open');
}

export function setSplitAction(isOneClick) {
  _splitBtnIsOneClick = isOneClick;
  localStorage.setItem('splitBtnAction', String(isOneClick));
  _syncSplitBtnLabel();
  const menu = document.getElementById('split-btn-menu');
  if (menu) menu.classList.remove('open');
}

function _syncSplitBtnLabel() {
  const label = document.getElementById('split-btn-label');
  if (label) {
    label.textContent = _splitBtnIsOneClick ? t('create.one_click') : t('create.start_gen');
  }
  // Mark active option
  document.querySelectorAll('.split-btn-option').forEach(opt => {
    const isOC = opt.dataset.oneClick === 'true';
    opt.classList.toggle('active', isOC === _splitBtnIsOneClick);
  });
}

export function splitBtnSubmit() {
  if (_currentUnifiedMode === 'quickchat') {
    createMode = 'prompt';
  }
  if (!isCreateSubmissionReady()) {
    const ta = document.getElementById('unified-textarea');
    _flashEmptyHint(ta);
    return;
  }
  submitCreate(_splitBtnIsOneClick);
}

// ══════════════════════════════════════════════════════════════
// Novel file tag
// ══════════════════════════════════════════════════════════════

function _showNovelFileTag(fileName) {
  _novelFileName = fileName;
  const bar = document.getElementById('file-tag-bar');
  const nameEl = document.getElementById('file-tag-name');
  if (bar && nameEl) {
    nameEl.textContent = fileName;
    bar.style.display = '';
  }
}

export function removeNovelFile() {
  _novelFileName = null;
  uploadedNovelPath = null;
  const bar = document.getElementById('file-tag-bar');
  if (bar) bar.style.display = 'none';
  // Don't clear textarea — user may have typed additional instructions
  updateUnifiedButtons();
}

// ══════════════════════════════════════════════════════════════
// File upload
// ══════════════════════════════════════════════════════════════

export async function handleNovelUpload(input) {
  const file = input.files[0];
  if (!file) return;

  if (file.name.endsWith('.txt') || file.name.endsWith('.md')) {
    const text = await file.text();
    const ta = document.getElementById('unified-textarea');
    if (ta) ta.value = text;
    _showNovelFileTag(file.name);
    updateUnifiedButtons();
  } else {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch('/api/upload-source', { method: 'POST', body: form });
    const data = await res.json();
    uploadedNovelPath = data.path;
    _showNovelFileTag(file.name);
    updateUnifiedButtons();
  }
}

export async function handleVideoUpload(input) {
  const file = input.files[0];
  if (!file) return;
  await _processVideoFile(file);
}

export async function _processVideoFile(file) {
  uploadedVideoPath = '';
  uploadedVideoDurationSeconds = null;
  updateCreateSubmitButtons();
  const zone = document.getElementById('video-upload-zone');
  const hint = document.getElementById('video-upload-hint');
  hint.textContent = t('create.uploading_file').replace('{0}', file.name);

  const form = new FormData();
  form.append('file', file);
  try {
    const res = await fetch('/api/upload-source', { method: 'POST', body: form });
    const data = await res.json();
    uploadedVideoPath = data.path;
    uploadedVideoDurationSeconds = Number(data.duration_seconds || 0) || null;
    zone.classList.add('has-file');
    hint.textContent = t('create.uploaded_file').replace('{0}', file.name) + (uploadedVideoDurationSeconds ? ` · ${formatDurationSecondsForInput(uploadedVideoDurationSeconds)}s` : '');
    if (uploadedVideoDurationSeconds) {
      setCreateDurationInput(uploadedVideoDurationSeconds);
    }
    updateCreateSubmitButtons();
  } catch (e) {
    uploadedVideoPath = '';
    uploadedVideoDurationSeconds = null;
    zone.classList.remove('has-file');
    hint.textContent = t('create.upload_failed');
    updateCreateSubmitButtons();
  }
}

export function initVideoUploadDrop() {
  const zone = document.getElementById('video-upload-zone');
  if (!zone) return;
  zone.addEventListener('dragenter', e => { e.preventDefault(); e.stopPropagation(); zone.classList.add('drag-over'); });
  zone.addEventListener('dragover',  e => { e.preventDefault(); e.stopPropagation(); zone.classList.add('drag-over'); });
  zone.addEventListener('dragleave', e => { e.preventDefault(); e.stopPropagation(); zone.classList.remove('drag-over'); });
  zone.addEventListener('drop', e => {
    e.preventDefault(); e.stopPropagation();
    zone.classList.remove('drag-over');
    const file = e.dataTransfer?.files?.[0];
    if (file && file.type.startsWith('video/')) {
      _processVideoFile(file);
    } else if (file) {
      document.getElementById('video-upload-hint').textContent = t('create.drag_video');
    }
  });
}

export function initNovelUploadDrop() {
  // Novel drag-drop on the input-area (when in novel mode)
  const area = document.getElementById('input-area');
  if (!area) return;
  area.addEventListener('dragenter', e => {
    if (_currentUnifiedMode !== 'novel') return;
    e.preventDefault(); e.stopPropagation();
    area.classList.add('drag-over');
  });
  area.addEventListener('dragover', e => {
    if (_currentUnifiedMode !== 'novel') return;
    e.preventDefault(); e.stopPropagation();
    area.classList.add('drag-over');
  });
  area.addEventListener('dragleave', e => {
    e.preventDefault(); e.stopPropagation();
    area.classList.remove('drag-over');
  });
  area.addEventListener('drop', async e => {
    area.classList.remove('drag-over');
    if (_currentUnifiedMode !== 'novel') return;
    e.preventDefault(); e.stopPropagation();
    const file = e.dataTransfer?.files?.[0];
    if (file && (file.name.endsWith('.txt') || file.name.endsWith('.md'))) {
      const text = await file.text();
      const ta = document.getElementById('unified-textarea');
      if (ta) ta.value = text;
      _showNovelFileTag(file.name);
      updateUnifiedButtons();
    }
  });
}

// ══════════════════════════════════════════════════════════════
// Submit & Job control
// ══════════════════════════════════════════════════════════════

export async function submitCreate(oneClick = false) {
  if (isCreateJobActive()) {
    showToast(t('create.job_active_wait'), 'error');
    return;
  }

  // Ensure createMode is set (quickchat → prompt)
  if (!createMode && _currentUnifiedMode === 'quickchat') {
    createMode = 'prompt';
  }
  if (!createMode || !isCreateSubmissionReady()) return;

  const splitMain = document.getElementById('split-btn-main');
  const quickBtn = document.getElementById('quick-send-btn');
  const status = document.getElementById('create-submit-status');
  if (splitMain) splitMain.disabled = true;
  if (quickBtn) quickBtn.disabled = true;
  if (status) status.textContent = oneClick ? t('misc.one_click_starting') : t('misc.starting');

  const textarea = document.getElementById('unified-textarea');
  const textValue = textarea?.value?.trim() || '';

  const isQuickchat = _currentUnifiedMode === 'quickchat';
  const durationInput = document.getElementById('create-duration');
  const parsedDuration = parseFloat(durationInput?.value);
  const fallbackDuration = createMode === 'video' ? uploadedVideoDurationSeconds : 60;
  // quickchat 模式不读 duration input（UI 隐藏），传 null 让后端 LLM 解析
  const resolvedDuration = isQuickchat
    ? null
    : (Number.isFinite(parsedDuration) && parsedDuration > 0 ? parsedDuration : (fallbackDuration || null));

  const currentBackend = getCurrentBackend();
  const seeddanceModel = document.getElementById('seeddance-model-select')?.value || 'seedance-2.0';
  const body = {
    mode: createMode,
    quickchat: isQuickchat,
    title: document.getElementById('create-title')?.value?.trim() || '',
    style: document.getElementById('create-style')?.value?.trim() || '',
    duration: resolvedDuration,
    segment_mode: true,
    one_click: oneClick,
    auto_start_video: oneClick,
    generation_mode: document.getElementById('editor-generation-mode')?.value || 'parallel',
    seeddance_backend: currentBackend,
    seeddance_model: seeddanceModel,
  };

  if (createMode === 'prompt') {
    body.idea = textValue;
    body.style_hint = '';
  } else if (createMode === 'novel') {
    body.chapter_text = textValue;
    body.style_hint = document.getElementById('create-style')?.value?.trim() || '';
  } else if (createMode === 'video') {
    body.video_path = uploadedVideoPath || '';
    body.video_mode = document.getElementById('create-video-mode')?.value || 'replicate';
    body.recreate_direction = document.getElementById('create-video-recreate-direction')?.value || '';
  }

  try {
    const res = await fetch('/api/create/start', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok || !data.job_id) throw new Error(data.error || 'Failed to start');
    createJobId = data.job_id;
    activeCreateJobMeta = { job_id: data.job_id, title: body.title || '', status: 'running', phase: 'starting' };
    window._createOneClick = oneClick;
    updateUnifiedButtons();
    if (status) status.textContent = '';
    showCreatePhase('pipeline');
    setStepActive('screenplay');
    const pipelineContent = document.getElementById('pipeline-content');
    if (pipelineContent) {
      pipelineContent.innerHTML = `<div class="pipeline-waiting"><div class="live-dot"></div> ${t('pipeline.gen_screenplay')}</div>`;
    }
  } catch (e) {
    if (status) status.textContent = 'Failed to start: ' + e.message;
    if (splitMain) splitMain.disabled = false;
    if (quickBtn) quickBtn.disabled = false;
  }
}

export function isCreateJobActive(job = activeCreateJobMeta) {
  return !!job && ['running', 'stopping', 'pausing'].includes(job.status);
}

// (banner removed – single-task model: form is locked while a job is active)

// ══════════════════════════════════════════════════════════════
// Phase navigation
// ══════════════════════════════════════════════════════════════

/**
 * Sync the pipeline view's empty-state vs active-content visibility.
 * Called when entering the pipeline tab.
 */
export function syncPipelineView() {
  const emptyState = document.getElementById('pipeline-empty-state');
  const pipelinePhase = document.getElementById('create-pipeline-phase');
  const editorPhase = document.getElementById('create-editor-phase');
  const hasJob = isCreateJobActive() || createPhase === 'pipeline' || createPhase === 'editor';
  if (emptyState) emptyState.style.display = hasJob ? 'none' : '';
  if (pipelinePhase) pipelinePhase.classList.toggle('active', hasJob && createPhase !== 'editor');
  if (editorPhase) editorPhase.classList.toggle('active', createPhase === 'editor');
}

export function showCreatePhase(phase) {
  createPhase = phase;
  const pipelinePhase = document.getElementById('create-pipeline-phase');
  const editorPhase = document.getElementById('create-editor-phase');
  const emptyState = document.getElementById('pipeline-empty-state');

  if (phase === 'form') {
    // Go back to home — just switch tab, don't stop any job
    if (pipelinePhase) pipelinePhase.classList.remove('active');
    if (editorPhase) editorPhase.classList.remove('active');
    switchTab('create');
    return;
  }

  // For pipeline / editor phases, ensure we're on the pipeline tab
  switchTab('pipeline');
  if (emptyState) emptyState.style.display = 'none';
  if (pipelinePhase) pipelinePhase.classList.toggle('active', phase === 'pipeline');
  if (editorPhase) editorPhase.classList.toggle('active', phase === 'editor');
}

export function createGoBack() {
  // In the separated pipeline view, the only "back" is editor -> screenplay review
  if (createPhase === 'editor') {
    showCreatePhase('pipeline');
    const content = document.getElementById('pipeline-content');
    const screenplay = window._pendingScreenplay;
    if (screenplay) {
      // Edit mode (from repository)
      if (window._editModeStoryboardPath && !createJobId) {
        renderScreenplayReviewForEdit(content, screenplay);
      } else {
        renderScreenplayReview(content, screenplay);
      }
    }
  }
}

export function resetPipeline() {
  document.querySelectorAll('.step-item').forEach(s => {
    s.classList.remove('completed', 'active', 'error');
  });
  const pc = document.getElementById('pipeline-content');
  if (pc) {
    pc.style.display = '';
    pc.style.flexDirection = '';
    pc.style.padding = '';
    pc.style.overflow = '';
    pc.style.height = '';
    pc.style.alignItems = '';
    pc.style.justifyContent = '';
    pc.innerHTML =
      `<div class="pipeline-waiting"><div class="live-dot"></div> ${t('pipeline.waiting')}</div>`;
  }
}

export async function pauseCurrentCreateJob() {
  if (!createJobId) return;
  await _guardedBtnAction('create-pause-' + createJobId, async () => {
    const pauseBtn = document.getElementById('create-pause-job-btn');
    if (pauseBtn) pauseBtn.disabled = true;
    try {
      const res = await fetch(`/api/create/pause/${createJobId}`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'Failed to pause job');
      if (activeCreateJobMeta) activeCreateJobMeta.status = data.status || 'pausing';
      showToast(t('create.pausing'), 'success');
    } catch (e) {
      if (pauseBtn) pauseBtn.disabled = false;
      showToast('Error: ' + e.message, 'error');
    }
  });
}

export async function resumeCurrentCreateJob() {
  if (!createJobId) return;
  try {
    const res = await fetch(`/api/create/continue/${createJobId}`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({}) });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Failed to resume job');
    if (activeCreateJobMeta) activeCreateJobMeta.status = 'running';
    showCreatePhase('pipeline');
    showToast(t('toast.job_resumed'), 'success');
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  }
}

export async function stopCurrentCreateJob() {
  // Kept for internal compatibility — use deleteCurrentCreateJob() for UI
  return deleteCurrentCreateJob();
}

export async function deleteCurrentCreateJob() {
  if (!createJobId) return;
  if (!confirm(t('create.confirm_delete_job'))) return;
  await _guardedBtnAction('create-delete-' + createJobId, async () => {
    const deleteBtn = document.getElementById('create-delete-job-btn');
    if (deleteBtn) deleteBtn.disabled = true;
    try {
      const res = await fetch(`/api/create/delete/${createJobId}`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'Failed to delete job');
      activeCreateJobMeta = null;
      createJobId = null;
      resetPipeline();
      showCreatePhase('form');
      updateCreateSubmitButtons();
      updateUnifiedButtons();
      showToast(t('toast.job_deleted'), 'success');
    } catch (e) {
      if (deleteBtn) deleteBtn.disabled = false;
      showToast('Error: ' + e.message, 'error');
    }
  });
}

// ══════════════════════════════════════════════════════════════
// Step indicators
// ══════════════════════════════════════════════════════════════

export function setStepActive(stepName) {
  const steps = ['screenplay', 'storyboard', 'review', 'fix', 'done'];
  const idx = steps.indexOf(stepName);
  steps.forEach((s, i) => {
    const el = document.getElementById('step-' + s);
    if (!el) return;
    el.classList.remove('completed', 'active', 'error');
    if (i < idx) el.classList.add('completed');
    else if (i === idx) el.classList.add('active');
  });
}

export function setStepError() {
  document.querySelectorAll('.step-item.active').forEach(s => {
    s.classList.remove('active');
    s.classList.add('error');
  });
}

// ══════════════════════════════════════════════════════════════
// Generation log viewer
// ══════════════════════════════════════════════════════════════

function _logViewerLabel() {
  const phase = activeCreateJobMeta?.phase || '';
  if (phase === 'starting' || phase === 'screenplay') return t('pipeline.gen_screenplay');
  return t('pipeline.gen_storyboard');
}

function _ensureLogViewer(container, label) {
  if (!container) return null;
  let viewer = container.querySelector('.gen-log-viewer');
  if (viewer) {
    // Update the header text if a label was explicitly provided
    if (label) {
      const hdr = container.querySelector('.gen-log-header-text');
      if (hdr) hdr.textContent = label;
    }
    return viewer;
  }

  const headerText = label || _logViewerLabel();
  container.style.display = 'flex';
  container.style.flexDirection = 'column';
  container.style.overflow = 'hidden';
  container.style.alignItems = 'stretch';
  container.style.justifyContent = 'flex-start';
  container.innerHTML = `
    <div class="pipeline-waiting" style="flex-shrink:0;padding-bottom:8px">
      <div class="live-dot"></div> <span class="gen-log-header-text">${headerText}</span>
    </div>
    <div class="gen-log-viewer"><pre class="gen-log-pre"></pre></div>`;
  return container.querySelector('.gen-log-viewer');
}

function _appendLogText(container, text) {
  if (!container || !text) return;
  const viewer = _ensureLogViewer(container);
  if (!viewer) return;
  const pre = viewer.querySelector('.gen-log-pre');
  if (!pre) return;

  pre.textContent += text + '\n';

  const isNearBottom = viewer.scrollHeight - viewer.scrollTop - viewer.clientHeight < 80;
  if (isNearBottom) {
    requestAnimationFrame(() => { viewer.scrollTop = viewer.scrollHeight; });
  }
}

// ══════════════════════════════════════════════════════════════
// Storyboard step progress tracker
// ══════════════════════════════════════════════════════════════

const _SB_STEP_LABELS = ['剧本拆分', '剧本分组', '缺失实体分析', '冗余实体清理', '提示词生成', '连续性增强'];

function _ensureStepTracker(container) {
  if (!container) return null;
  let tracker = container.querySelector('.sb-step-tracker');
  if (tracker) return tracker;
  // Ensure log viewer exists (creates the container layout)
  _ensureLogViewer(container);
  // Insert tracker between the header row and the log viewer
  const logViewer = container.querySelector('.gen-log-viewer');
  tracker = document.createElement('div');
  tracker.className = 'sb-step-tracker';
  tracker.innerHTML = _SB_STEP_LABELS.map((label, i) => `
    <div class="sb-step-item pending" data-step="${i + 1}">
      <div class="sb-step-circle">${i + 1}</div>
      <div class="sb-step-label">${label}</div>
    </div>`).join('');
  if (logViewer) {
    container.insertBefore(tracker, logViewer);
  } else {
    container.appendChild(tracker);
  }
  return tracker;
}

function _updateStepTracker(container, stepIndex, status) {
  const tracker = _ensureStepTracker(container);
  if (!tracker) return;
  tracker.querySelectorAll('.sb-step-item').forEach(item => {
    const n = parseInt(item.dataset.step, 10);
    const circle = item.querySelector('.sb-step-circle');
    item.classList.remove('completed', 'active', 'pending');
    if (n < stepIndex || (n === stepIndex && status === 'done')) {
      item.classList.add('completed');
      circle.innerHTML = '✓';
    } else if (n === stepIndex && status === 'running') {
      item.classList.add('active');
      circle.textContent = n;
    } else {
      item.classList.add('pending');
      circle.textContent = n;
    }
  });
}

// ══════════════════════════════════════════════════════════════
// Pipeline progress handler
// ══════════════════════════════════════════════════════════════

export function handleCreateProgress(msg) {
  if (msg.job_id !== createJobId) return;
  const phase = msg.phase;
  const data = msg.data;
  const content = document.getElementById('pipeline-content');
  // For starting/screenplay events, always switch to pipeline tab so the user sees generation progress.
  // For other events, only update if already on the pipeline tab (don't interrupt the user).
  if ((phase === 'starting' || phase === 'screenplay') && createPhase !== 'pipeline' && createPhase !== 'editor') {
    showCreatePhase('pipeline');
  } else if (createPhase !== 'pipeline' && createPhase !== 'editor') {
    createPhase = 'pipeline';
    const pipelineView = document.getElementById('view-pipeline');
    if (pipelineView?.classList.contains('active')) {
      const emptyState = document.getElementById('pipeline-empty-state');
      if (emptyState) emptyState.style.display = 'none';
      document.getElementById('create-pipeline-phase')?.classList.add('active');
    }
  }
  // Ensure pipeline panel is active regardless (covers case where tab is already correct but panel hidden)
  if (createPhase === 'pipeline') {
    const emptyState = document.getElementById('pipeline-empty-state');
    if (emptyState) emptyState.style.display = 'none';
    document.getElementById('create-pipeline-phase')?.classList.add('active');
  }
  // Log events are high-frequency — append to existing log viewer and return early.
  // Ignore late log events that arrive after a terminal phase (they would
  // recreate the log viewer and overwrite the review/done UI).
  if (phase === 'log') {
    const cur = activeCreateJobMeta?.phase || '';
    if (cur === 'screenplay_review' || cur === 'storyboard_review' || cur === 'done') return;
    _appendLogText(content, data.text || '');
    return;
  }

  // Storyboard step progress — update the tracker widget without touching other UI.
  if (phase === 'storyboard_step') {
    const cur = activeCreateJobMeta?.phase || '';
    if (cur === 'storyboard_review' || cur === 'done') return;
    _updateStepTracker(content, data.step_index || 1, data.status || 'running');
    return;
  }

  // Guard: ignore events that would regress to an earlier pipeline phase.
  // This prevents the file-watcher's "screenplay_done" from overwriting
  // the engine's "screenplay_review" when events arrive out of order.
  {
    const _PHASE_SEQ = ['starting', 'screenplay', 'screenplay_done', 'screenplay_review', 'storyboard', 'storyboard_review', 'done'];
    const prev = activeCreateJobMeta?.phase || '';
    const pi = _PHASE_SEQ.indexOf(prev);
    const ci = _PHASE_SEQ.indexOf(phase);
    if (pi >= 0 && ci >= 0 && ci < pi) return;
  }

  // 重置 storyboard review / screenplay review 可能设置的 flex 布局
  if (content) {
    content.style.display = '';
    content.style.flexDirection = '';
    content.style.padding = '';
    content.style.overflow = '';
    content.style.height = '';
    content.style.alignItems = '';
    content.style.justifyContent = '';
  }
  activeCreateJobMeta = {
    ...(activeCreateJobMeta || {}),
    job_id: msg.job_id,
    status: phase === 'stopping' ? 'stopping'
          : phase === 'pausing' ? 'pausing'
          : phase === 'stopped' ? 'stopped'
          : phase === 'paused' ? 'paused'
          : phase === 'screenplay_review' || phase === 'storyboard_review' ? 'paused'
          : 'running',
    phase,
  };
  updateUnifiedButtons();

  // Show delete button during active generation; disable during transitional states
  const _deleteBtn = document.getElementById('create-delete-job-btn');
  if (_deleteBtn) _deleteBtn.disabled = ['stopping', 'pausing'].includes(phase);

  if (phase === 'starting' || phase === 'screenplay') {
    setStepActive('screenplay');
    if (phase === 'starting') {
      window._chatHistory = [];
      window._reviewStoryboard = null;
      if (content) {
        content.innerHTML = `<div class="pipeline-waiting"><div class="live-dot"></div> ${t('pipeline.gen_screenplay')}</div>`;
      }
    } else if (content) {
      // If a log viewer already exists, just update its header; don't wipe the logs
      if (content.querySelector('.gen-log-viewer')) {
        _ensureLogViewer(content, t('pipeline.gen_screenplay'));
      } else if (!content.querySelector('.pipeline-waiting')) {
        content.innerHTML = `<div class="pipeline-waiting"><div class="live-dot"></div> ${t('pipeline.gen_screenplay')}</div>`;
      }
    }
  }
  else if (phase === 'screenplay_done') {
    setStepActive('storyboard');
    renderScreenplayArtifact(content, data);
  }
  else if (phase === 'screenplay_review') {
    setStepActive('screenplay');
    document.getElementById('step-screenplay').classList.remove('active');
    document.getElementById('step-screenplay').classList.add('completed');
    // Always prefer the LLM-generated title from the screenplay
    const llmTitle = data.screenplay?.title || data.title || '';
    if (llmTitle && llmTitle !== 'untitled') {
      document.getElementById('create-title').value = llmTitle;
      // Keep createdStoryboardPath in sync so the editor uses the right filename
      createdStoryboardPath = `storyboards/${llmTitle}_storyboard.json`;
    }
    renderScreenplayReview(content, data.screenplay);
  }
  else if (phase === 'storyboard_review') {
    // 自动跳过分镜审核窗格，直接确认并继续到编辑器
    window._reviewStoryboard = data.storyboard;
    window._reviewOutputPath = data.output_path || '';
    const sbTitle = data.storyboard?.title || '';
    if (sbTitle && sbTitle !== 'untitled') {
      document.getElementById('create-title').value = sbTitle;
      createdStoryboardPath = data.output_path || `storyboards/${sbTitle}_storyboard.json`;
    }
    // 显示等待提示
    content.innerHTML = `<div class="pipeline-waiting"><div class="live-dot"></div> ${t('pipeline.finalizing')}</div>`;
    // 自动调用 continue-storyboard，无需用户手动确认
    _autoConfirmStoryboard(data.storyboard);
  }
  else if (phase.startsWith('01_') || phase.startsWith('02_') || phase.startsWith('03_') || phase.startsWith('04_')) {
    // fine-grained mode intermediate phases — ignored in prompt mode
  }
  else if (phase.startsWith('05_') || phase === 'done') {
    activeCreateJobMeta = null;
    updateUnifiedButtons();
    setStepActive('done');
    document.getElementById('step-done').classList.remove('active');
    document.getElementById('step-done').classList.add('completed');

    if (phase === 'done' && data.storyboard) {
      createdStoryboard = data.storyboard;
      createdStoryboardPath = data.output_path || createdStoryboardPath || '';
    } else if (data) {
      createdStoryboard = data;
    }
    if (!createdStoryboardPath) {
      const title = document.getElementById('create-title').value.trim() || 'untitled';
      createdStoryboardPath = `storyboards/${title}_storyboard.json`;
    }

    const autoStartedVideo = !!data?.auto_started_video;
    if (autoStartedVideo) {
      setSelectedVideoJobId(data?.video_job?.job_id || selectedVideoJobId);
      document.getElementById('pipeline-content').innerHTML = `<div class="pipeline-done-msg">${t('create.auto_video_redirect')}</div>`;
      // Do NOT send switch_project — the new video job has no run_dir yet.
      // The monitor will show a "waiting" state and auto-refresh.
      setTimeout(() => {
        loadVideoJobsFn();
        switchTab('monitor');
      }, 800);
      return;
    }

    // 直接进入编辑器（跳过"生成视频/进入编辑器"过渡界面）
    if (createdStoryboard) {
      showCreatePhase('editor');
      window.syncEditorModelFromHome?.();
      renderEditor(createdStoryboard);
    }
  }
  else if (phase === 'stopping') {
    content.innerHTML = `<div class="pipeline-waiting"><div class="live-dot"></div> ${t('create.job_stopping')}</div>`;
  }
  else if (phase === 'pausing') {
    content.innerHTML = `<div class="pipeline-waiting"><div class="live-dot"></div> ${t('create.pausing_wait')}</div>`;
  }
  else if (phase === 'paused') {
    // Job was paused mid-generation (not at a review checkpoint)
    content.innerHTML = `
      <div class="pipeline-waiting" style="color:var(--warning)">${t('create.paused_hint')}</div>
      <div style="display:flex;gap:10px;padding:16px 24px;border-top:1px solid var(--border-subtle);background:var(--bg-card)">
        <button class="btn-primary" onclick="resumeCurrentCreateJob()">▶ ${t('misc.resume')}</button>
        <button class="btn-danger-soft" style="margin-left:auto" onclick="deleteCurrentCreateJob()">🗑 ${t('misc.delete')}</button>
      </div>`;
    showToast(t('create.paused_toast'), 'success');
  }
  else if (phase === 'stopped' || phase === 'deleted') {
    activeCreateJobMeta = null;
    createJobId = null;
    resetPipeline();
    showCreatePhase('form');
    updateCreateSubmitButtons();
    updateUnifiedButtons();
    if (phase === 'deleted') showToast(t('toast.job_deleted'), 'success');
    else showToast(t('create.job_stopped'), 'success');
  }
  else if (phase === 'error') {
    activeCreateJobMeta = null;
    updateUnifiedButtons();
    setStepError();
    const rawErr = data.error || '';
    const isHtmlError = rawErr.trimStart().startsWith('<');
    const errMsg = isHtmlError ? '' : esc(rawErr);
    content.innerHTML = `<div style="color:var(--error);padding:20px;font-size:14px">
      <strong>${t('create.gen_failed')}</strong><br/><br/>
      <span style="font-family:var(--font-mono);font-size:12px">${errMsg || t('create.gen_failed_retry')}</span>
    </div>`;
  }
}

// ══════════════════════════════════════════════════════════════
// Screenplay artifact rendering
// ══════════════════════════════════════════════════════════════

export function countScenes(data) {
  if (data?.storyboard?.length) return data.storyboard.length;
  if (Array.isArray(data)) return data.length;
  return '?';
}

export function renderScreenplayArtifact(container, data) {
  window._lastScreenplayData = data; // Save for segment modal
  let html = '<div class="pipeline-content-title">' + t('create.screenplay_generated') + '</div>';

  if (data?.video_analysis) {
    const va = data.video_analysis;
    html += '<div class="artifact-section"><div class="artifact-label">' + t('create.analysis') + '</div>';
    html += `<div style="font-size:12px;color:var(--text-secondary);line-height:1.7">`;
    if (va.theme) html += `<strong>${t('create.theme')}:</strong> ${esc(va.theme)}<br/>`;
    if (va.style) html += `<strong>${t('create.style_label')}:</strong> ${esc(va.style)}<br/>`;
    if (va.tone) html += `<strong>${t('create.tone')}:</strong> ${esc(va.tone)}<br/>`;
    html += `</div></div>`;
  }

  const chars = data?.characters || [];
  if (chars.length) {
    html += '<div class="artifact-section"><div class="artifact-label">' + t('create.characters_count').replace('{0}', chars.length) + '</div>';
    html += chars.map(c => `<div style="font-size:12px;color:var(--text-secondary);margin-bottom:4px">
      <strong style="color:var(--cyan)">${esc(c.name)}</strong> — ${esc(c.personality || c.description || '')}
    </div>`).join('');
    html += '</div>';
  }

  const narrative = data?.narrative || data?.synopsis || '';
  if (narrative) {
    html += `<div class="artifact-section"><div class="artifact-label">${t('create.narrative_label')}</div>
      <div class="screenplay-preview">${esc(narrative)}</div></div>`;
  }

  // Segments (if available)
  const segments = data?.segments || [];
  if (segments.length) {
    html += '<div class="artifact-section"><div class="artifact-label">' + t('create.segments_count').replace('{0}', segments.length) + '</div>';
    html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px">';
    segments.forEach((seg, idx) => {
      const dur = seg.duration || '—';
      const summary = seg.narrative_summary || seg.description || '';
      const truncSummary = summary.length > 60 ? summary.slice(0, 60) + '…' : summary;
      html += `<div class="segment-preview-card" onclick="openSegmentModal(${idx})" style="
        padding:12px 14px;border-radius:8px;border:1px solid var(--border-subtle);
        background:var(--bg-card);cursor:pointer;transition:var(--transition);
      " onmouseover="this.style.borderColor='var(--border-accent)'" onmouseout="this.style.borderColor='var(--border-subtle)'">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
          <span style="font-size:11px;font-weight:700;color:var(--accent)">${t('create.segment_n').replace('{0}', idx + 1)}</span>
          <span style="font-size:10px;color:var(--text-muted);font-family:var(--font-mono)">${dur}</span>
        </div>
        <div style="font-size:11px;color:var(--text-secondary);line-height:1.5">${esc(truncSummary)}</div>
      </div>`;
    });
    html += '</div></div>';
  }

  html += '<div style="font-size:12px;color:var(--text-muted);margin-top:12px">' + t('create.converting_storyboard') + '</div>';
  container.innerHTML = html;
}

// ══════════════════════════════════════════════════════════════
// Style editor helpers
// ══════════════════════════════════════════════════════════════

export function _buildStyleEditorHTML(screenplay, idSuffix) {
  const va = screenplay?.video_analysis || {};
  const currentStyle = va.style || '';
  return `<div style="display:flex;gap:8px;align-items:center">
    <span style="font-size:12px;font-weight:600;color:var(--text-muted);white-space:nowrap;flex-shrink:0">${t('create.style_editor_label')}</span>
    <input id="style-edit-${idSuffix}" type="text" value="${esc(currentStyle)}" placeholder="${t('create.style_placeholder_hint')}" style="
      flex:1;padding:7px 12px;border-radius:8px;
      border:1px solid var(--border-subtle);background:var(--bg-card);
      color:var(--text-primary);font-size:13px;font-family:var(--font-sans);
    "/>
    <button class="btn-secondary" style="white-space:nowrap;padding:7px 12px;font-size:12px;flex-shrink:0" onclick="_applyStyleChange('${idSuffix}')">${t('create.apply_style')}</button>
  </div>`;
}

export function _applyStyleChange(idSuffix) {
  // 判断是 screenplay 还是 storyboard 类型
  if (idSuffix === 'screenplay' || idSuffix === 'edit-screenplay') {
    const sp = window._pendingScreenplay;
    if (sp) {
      _syncStyleToScreenplay(sp, idSuffix);
      showToast && showToast(t('create.style_applied_screenplay'), 'success');
    } else {
      showToast && showToast(t('create.no_data_found'), 'error');
    }
  } else {
    const sb = window._reviewStoryboard || window._editModeStoryboard;
    if (sb) {
      _syncStyleToStoryboard(sb, idSuffix);
      showToast && showToast(t('create.style_applied_storyboard'), 'success');
    } else {
      showToast && showToast(t('create.no_data_found'), 'error');
    }
  }
}

export function _syncStyleToScreenplay(screenplay, idSuffix) {
  const el = document.getElementById('style-edit-' + idSuffix);
  if (!el) return;
  const newStyle = el.value.trim();
  if (!newStyle) return;
  const va = screenplay.video_analysis || (screenplay.video_analysis = {});
  const oldStyle = (va.style || '').trim();
  if (newStyle === oldStyle) return;
  va.style = newStyle;
  // 替换 characters / locations / props description 开头的风格前缀
  const _replaceDescPrefix = (desc) => {
    if (!desc) return desc;
    // 去掉开头的旧风格文本（如 "3D CG动画风格，" 或 "真人写实风格，"）
    let cleaned = desc;
    if (oldStyle) {
      const escaped = oldStyle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      cleaned = cleaned.replace(new RegExp('^' + escaped + '[，,、\\s]*'), '');
    }
    return newStyle + '，' + cleaned;
  };
  (screenplay.characters || []).forEach(c => { if (c.description) c.description = _replaceDescPrefix(c.description); });
  (screenplay.locations || []).forEach(l => { if (l.description) l.description = _replaceDescPrefix(l.description); });
  (screenplay.props || []).forEach(p => { if (p.description) p.description = _replaceDescPrefix(p.description); });
}

export function _syncStyleToStoryboard(storyboard, idSuffix) {
  const el = document.getElementById('style-edit-' + idSuffix);
  if (!el) return;
  const newStyle = el.value.trim();
  if (!newStyle) return;
  const va = storyboard.video_analysis || (storyboard.video_analysis = {});
  const oldStyle = (va.style || '').trim();
  if (newStyle === oldStyle) return;
  va.style = newStyle;
  // 替换 characters / locations / props description 开头的风格前缀
  const _replaceDescPrefix = (desc) => {
    if (!desc) return desc;
    let cleaned = desc;
    cleaned = cleaned.replace(/^(?:[^，,。.]*(?:风格|动画|写实|古装|真人|CG|3D|2D|国漫|国产|色彩|特效|画面)[^，,。.]*[，,、。.\s]*)+/u, '');
    if (!cleaned) cleaned = desc; // safety: if regex ate everything, keep original
    return newStyle + '，' + cleaned;
  };
  (storyboard.characters || []).forEach(c => { if (c.description) c.description = _replaceDescPrefix(c.description); });
  (storyboard.locations || []).forEach(l => { if (l.description) l.description = _replaceDescPrefix(l.description); });
  (storyboard.props || []).forEach(p => { if (p.description) p.description = _replaceDescPrefix(p.description); });
  // 替换每个分镜 seedance_prompt 的第一行风格（优先从 textarea 读取最新值）
  const _replacePromptStyle = (prompt) => {
    if (!prompt) return prompt;
    const lines = prompt.split('\n');
    if (lines.length > 0 && /^\s*(?:风格|画面风格|整体画面风格|style)\s*[:：]/i.test(lines[0])) {
      lines[0] = '风格：' + newStyle;
    }
    return lines.join('\n');
  };
  (storyboard.storyboard || []).forEach((scene, idx) => {
    // 先从 textarea 同步最新内容到对象
    const ta = document.getElementById('review-scene-seedance-' + idx);
    if (ta) scene.seedance_prompt = ta.value;
    // 替换风格行
    if (scene.seedance_prompt) scene.seedance_prompt = _replacePromptStyle(scene.seedance_prompt);
    // 回写到 textarea
    if (ta && scene.seedance_prompt) ta.value = scene.seedance_prompt;
  });
}

// ══════════════════════════════════════════════════════════════
// Screenplay Review + Chat
// ══════════════════════════════════════════════════════════════

export function renderScreenplayReview(container, screenplay) {
  window._pendingScreenplay = screenplay;
  // 保留历史记录，仅在首次初始化
  if (!window._chatHistory) window._chatHistory = [];

  const narrative = screenplay?.narrative || screenplay?.synopsis || '';

  // 初始剧本作为第一条 AI 消息（仅在历史为空时插入）
  if (window._chatHistory.length === 0 && narrative) {
    window._chatHistory.push({ role: 'assistant', content: narrative, _isNarrative: true });
  }

  // ── 聊天式布局：顶部风格编辑器 + 中间聊天历史 + 底部固定输入 ──
  container.style.display = 'flex';
  container.style.flexDirection = 'column';
  container.style.padding = '16px';
  container.style.overflow = 'hidden';
  container.style.height = '100%';
  container.style.alignItems = 'stretch';
  container.style.justifyContent = 'flex-start';

  container.innerHTML = `
    <div class="sp-review-card">
      <div class="sp-review-header" id="sp-review-header">
        <div class="sp-review-title-bar">
          <div class="sp-review-title-icon">📝</div>
          <span style="font-size:14px;font-weight:700;color:var(--text-primary)">${t('create.screenplay_review_title')}</span>
        </div>
        <div class="sp-review-style-bar" id="sp-panel-body">
          ${_buildStyleEditorHTML(screenplay, 'screenplay')}
        </div>
      </div>
      <div class="chat-messages" id="chat-messages" style="flex:1;overflow-y:auto;min-height:0"></div>
      <div class="sp-review-footer">
        <div class="chat-input-row sp-review-input-row">
          <textarea class="chat-input" id="chat-input" placeholder="${t('create.chat_placeholder')}" rows="2"></textarea>
          <button class="chat-send-btn" onclick="sendChatMessage()" id="chat-send-btn">${t('create.chat_send')}</button>
        </div>
        <div class="sp-review-actions">
          ${(()=>{ const hasSb = !!(createdStoryboard?.storyboard?.length); return `
          <button class="btn-primary" onclick="continueGeneration()" id="sp-continue-btn">${hasSb ? t('create.regen_storyboard_btn') : t('create.continue_storyboard')}</button>
          ${hasSb ? `<button class="btn-secondary" onclick="goToEditorFromScreenplayReview()">${t('create.go_editor')}</button>` : ''}
          `; })()}
          <button class="btn-danger-soft" style="margin-left:auto" onclick="deleteCurrentCreateJob()">🗑 ${t('misc.delete')}</button>
        </div>
      </div>
    </div>`;

  renderChatMessages();

  document.getElementById('chat-input')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      sendChatMessage();
    }
  });
}

export function toggleScreenplayPanel() {
  const body = document.getElementById('sp-panel-body');
  const icon = document.getElementById('sp-panel-toggle-icon');
  if (!body) return;
  const isOpen = body.style.display !== 'none';
  body.style.display = isOpen ? 'none' : '';
  if (icon) icon.textContent = isOpen ? `▼ ${t('create.show_screenplay')}` : `▲ ${t('create.hide_screenplay')}`;
}

export function renderChatMessages() {
  const container = document.getElementById('chat-messages');
  if (!container || !window._chatHistory) return;
  
  if (window._chatHistory.length === 0) {
    container.innerHTML = `<div style="color:var(--text-muted);font-size:12px;text-align:center;padding:40px 20px">${t('create.chat_hint')}</div>`;
    return;
  }

  // 找到最后一条 assistant 消息的索引（用于显示编辑按钮）
  let lastAssistantIdx = -1;
  for (let i = window._chatHistory.length - 1; i >= 0; i--) {
    if (window._chatHistory[i].role === 'assistant') { lastAssistantIdx = i; break; }
  }
  
  container.innerHTML = window._chatHistory.map((msg, idx) => {
    const isUser = msg.role === 'user';
    const isNarrative = !!msg._isNarrative;
    const isSummary = !!msg._isSummary;
    const isLastAssistant = !isUser && idx === lastAssistantIdx;

    if (isUser) {
      return `<div class="chat-message user">
        <div class="chat-avatar user">U</div>
        <div class="chat-bubble user">${esc(msg.content)}</div>
      </div>`;
    }

    // assistant 消息
    const bubbleStyle = isNarrative
      ? 'white-space:pre-wrap;font-size:13px;line-height:1.8;'
      : isSummary ? 'font-size:12px;color:var(--text-muted);font-style:italic;' : '';

    const editBtn = isLastAssistant && isNarrative
      ? `<div style="margin-top:8px;display:flex;gap:8px">
           <button class="btn-secondary" style="font-size:11px;padding:4px 10px" onclick="startEditNarrative(${idx})">${t('create.edit_narrative_btn')}</button>
         </div>`
      : '';

    return `<div class="chat-message assistant" id="chat-msg-${idx}">
      <div class="chat-avatar assistant">AI</div>
      <div style="flex:1;min-width:0">
        <div class="chat-bubble assistant" style="${bubbleStyle}">${esc(msg.content)}</div>
        ${editBtn}
      </div>
    </div>`;
  }).join('');
  container.scrollTop = container.scrollHeight;
}

export function startEditNarrative(idx) {
  const msg = window._chatHistory?.[idx];
  if (!msg) return;
  const msgEl = document.getElementById('chat-msg-' + idx);
  if (!msgEl) return;
  const bubble = msgEl.querySelector('.chat-bubble');
  if (!bubble) return;

  const original = msg.content;
  msgEl.querySelector('div[style*="margin-top"]')?.remove();
  bubble.outerHTML = `<div style="flex:1;min-width:0">
    <textarea id="narrative-edit-ta-${idx}" style="
      width:100%;min-height:200px;padding:12px;border-radius:8px;
      border:1px solid var(--border-accent);background:var(--bg-card);
      color:var(--text-primary);font-size:13px;line-height:1.8;
      font-family:var(--font-sans);resize:vertical;box-sizing:border-box;
    ">${esc(original)}</textarea>
    <div style="display:flex;gap:8px;margin-top:8px">
      <button class="btn-primary" style="font-size:11px;padding:4px 12px" onclick="saveEditNarrative(${idx})">${t('create.save_btn')}</button>
      <button class="btn-secondary" style="font-size:11px;padding:4px 10px" onclick="renderChatMessages()">${t('misc.cancel')}</button>
    </div>
  </div>`;
}

export function saveEditNarrative(idx) {
  const ta = document.getElementById('narrative-edit-ta-' + idx);
  if (!ta) return;
  const newContent = ta.value.trim();
  if (!newContent) return;
  window._chatHistory[idx].content = newContent;
  if (window._pendingScreenplay) window._pendingScreenplay.narrative = newContent;
  renderChatMessages();
}

export async function sendChatMessage() {
  const input = document.getElementById('chat-input');
  const sendBtn = document.getElementById('chat-send-btn');
  const message = input.value.trim();
  if (!message) return;
  
  // Add user message
  window._chatHistory.push({ role: 'user', content: message });
  input.value = '';
  sendBtn.disabled = true;
  renderChatMessages();
  
  // Add loading message
  window._chatHistory.push({ role: 'assistant', content: t('create.refining') });
  renderChatMessages();
  
  try {
    const res = await fetch('/api/create/refine-narrative', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        job_id: createJobId,
        screenplay: window._pendingScreenplay,
        user_feedback: message,
        chat_history: window._chatHistory.slice(0, -1).map(m => ({ role: m.role, content: m.content })),
      }),
    });
    const data = await res.json();
    
    // Remove loading message
    window._chatHistory.pop();
    
    if (data.ok) {
      window._pendingScreenplay = data.screenplay;
      const newNarrative = data.screenplay.narrative || '';
      // 如果有修改说明，先追加说明消息
      if (data.response && data.response !== t('create.narrative_updated')) {
        window._chatHistory.push({ role: 'assistant', content: data.response, _isSummary: true });
      }
      // 追加完整新版剧本作为 AI 消息
      window._chatHistory.push({ role: 'assistant', content: newNarrative, _isNarrative: true });
    } else {
      window._chatHistory.push({ role: 'assistant', content: '❌ Error: ' + (data.error || 'Failed to refine') });
    }
  } catch (e) {
    window._chatHistory.pop();
    window._chatHistory.push({ role: 'assistant', content: '❌ Error: ' + e.message });
  }
  
  renderChatMessages();
  sendBtn.disabled = false;
}

export async function continueGeneration() {
  const screenplay = window._pendingScreenplay;
  if (!screenplay) { alert('No screenplay data'); return; }

  // 从最后一条 narrative 消息同步最新叙事（用户可能手动编辑过）
  if (window._chatHistory) {
    for (let i = window._chatHistory.length - 1; i >= 0; i--) {
      if (window._chatHistory[i].role === 'assistant' && window._chatHistory[i]._isNarrative) {
        screenplay.narrative = window._chatHistory[i].content;
        break;
      }
    }
  }

  // 同步风格修改
  _syncStyleToScreenplay(screenplay, 'screenplay');

  // 重新生成分镜时清空旧的分镜数据和聊天历史
  window._reviewStoryboard = null;
  window._reviewOutputPath = '';
  window._chatHistory = [];

  try {
    const res = await fetch(`/api/create/continue/${createJobId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ screenplay }),
    });
    const data = await res.json();
    if (data.ok) {
      setStepActive('storyboard');
      _ensureLogViewer(document.getElementById('pipeline-content'));
    } else {
      alert('Failed: ' + (data.error || 'Unknown error'));
    }
  } catch (e) {
    alert('Error: ' + e.message);
  }
}

export function cancelGeneration() {
  deleteCurrentCreateJob();
}

// ══════════════════════════════════════════════════════════════
// Edit-mode pipeline: screenplay & storyboard review with regenerate
// ══════════════════════════════════════════════════════════════

export function renderScreenplayReviewForEdit(container, screenplay) {
  window._pendingScreenplay = screenplay;
  // 保留历史记录，仅在首次初始化
  if (!window._chatHistory) window._chatHistory = [];

  const narrative = screenplay?.narrative || screenplay?.synopsis || '';

  // 初始剧本作为第一条 AI 消息（仅在历史为空时插入）
  if (window._chatHistory.length === 0 && narrative) {
    window._chatHistory.push({ role: 'assistant', content: narrative, _isNarrative: true });
  }

  // ── 聊天式布局（与 renderScreenplayReview 一致）──
  container.style.display = 'flex';
  container.style.flexDirection = 'column';
  container.style.padding = '16px';
  container.style.overflow = 'hidden';
  container.style.height = '100%';
  container.style.alignItems = 'stretch';
  container.style.justifyContent = 'flex-start';

  container.innerHTML = `
    <div class="sp-review-card">
      <div class="sp-review-header" id="sp-review-header">
        <div class="sp-review-title-bar">
          <div class="sp-review-title-icon">📝</div>
          <span style="font-size:14px;font-weight:700;color:var(--text-primary)">${t('create.edit_screenplay_title')}</span>
        </div>
        <div class="sp-review-style-bar" id="sp-panel-body">
          ${_buildStyleEditorHTML(screenplay, 'edit-screenplay')}
        </div>
      </div>
      <div class="chat-messages" id="chat-messages" style="flex:1;overflow-y:auto;min-height:0"></div>
      <div class="sp-review-footer">
        <div class="chat-input-row sp-review-input-row">
          <textarea class="chat-input" id="chat-input" placeholder="${t('create.chat_placeholder')}" rows="2"></textarea>
          <button class="chat-send-btn" onclick="sendEditModeChatMessage()" id="chat-send-btn">${t('create.chat_send')}</button>
        </div>
        <div class="sp-review-actions">
          ${(()=>{ const hasSb = !!(window._editModeStoryboard?.storyboard?.length); return `
          <button class="btn-primary" onclick="editModeContinueToStoryboard()">${hasSb ? t('create.regen_storyboard_btn') : t('create.gen_storyboard_btn')}</button>
          <button class="btn-secondary" onclick="editModeGoToEditor()">${t('create.go_editor')}</button>
          `; })()}
          <button class="btn-danger-soft" style="margin-left:auto" onclick="deleteCurrentCreateJob()">🗑 ${t('misc.delete')}</button>
        </div>
      </div>
    </div>`;

  renderChatMessages();

  document.getElementById('chat-input')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      sendEditModeChatMessage();
    }
  });
}


// ══════════════════════════════════════════════════════════════
// Edit-mode actions
// ══════════════════════════════════════════════════════════════

export async function editModeRegenerateScreenplay() {
  if (!confirm(t('create.confirm_regen_screenplay'))) return;

  const path = window._editModeStoryboardPath || createdStoryboardPath;
  if (!path) { alert('No storyboard path'); return; }

  // Sync narrative edits
  const narrativeEl = document.getElementById('screenplay-narrative-edit');
  const narrative = narrativeEl ? narrativeEl.value.trim() : '';

  try {
    const res = await fetch('/api/create/regenerate-screenplay', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ storyboard_path: path, narrative }),
    });
    const data = await res.json();
    if (!data.ok) { alert('Failed: ' + (data.error || 'Unknown')); return; }

    // Switch to active pipeline mode with the new job
    createJobId = data.job_id;
    activeCreateJobMeta = { job_id: data.job_id, status: 'running', phase: 'screenplay' };
    window._editModeStoryboard = null; // Clear edit mode
    setStepActive('screenplay');
    document.getElementById('pipeline-content').innerHTML = `<div class="pipeline-waiting"><div class="live-dot"></div> ${t('create.regenerating_screenplay')}</div>`;
  } catch (e) { alert('Error: ' + e.message); }
}

export async function editModeContinueToStoryboard() {
  const screenplay = window._pendingScreenplay;
  if (!screenplay) { alert('No screenplay data'); return; }

  const path = window._editModeStoryboardPath || createdStoryboardPath;
  if (!path) { alert('No storyboard path'); return; }

  // 从最后一条 narrative 消息同步最新叙事
  if (window._chatHistory) {
    for (let i = window._chatHistory.length - 1; i >= 0; i--) {
      if (window._chatHistory[i].role === 'assistant' && window._chatHistory[i]._isNarrative) {
        screenplay.narrative = window._chatHistory[i].content;
        break;
      }
    }
  }

  // 同步风格修改
  _syncStyleToScreenplay(screenplay, 'edit-screenplay');

  if (!confirm(t('create.confirm_regen_storyboard_from_sp'))) return;

  try {
    const res = await fetch('/api/create/regenerate-storyboard', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ storyboard_path: path, screenplay }),
    });
    const data = await res.json();
    if (!data.ok) { alert('Failed: ' + (data.error || 'Unknown')); return; }

    // Switch to active pipeline mode
    createJobId = data.job_id;
    activeCreateJobMeta = { job_id: data.job_id, status: 'running', phase: 'storyboard' };
    window._reviewStoryboard = null;
    setStepActive('storyboard');
    document.getElementById('step-screenplay')?.classList.add('completed');
    _ensureLogViewer(document.getElementById('pipeline-content'));
  } catch (e) { alert('Error: ' + e.message); }
}

export async function editModeRegenerateStoryboard() {
  const screenplay = window._editModeScreenplay || window._pendingScreenplay;
  if (!screenplay) { alert(t('create.no_screenplay')); return; }

  const path = window._editModeStoryboardPath || createdStoryboardPath;
  if (!path) { alert('No storyboard path'); return; }

  if (!confirm(t('create.confirm_regen_storyboard'))) return;

  try {
    const res = await fetch('/api/create/regenerate-storyboard', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ storyboard_path: path, screenplay }),
    });
    const data = await res.json();
    if (!data.ok) { alert('Failed: ' + (data.error || 'Unknown')); return; }

    createJobId = data.job_id;
    activeCreateJobMeta = { job_id: data.job_id, status: 'running', phase: 'storyboard' };
    window._reviewStoryboard = null;
    setStepActive('storyboard');
    document.getElementById('step-screenplay')?.classList.add('completed');
    _ensureLogViewer(document.getElementById('pipeline-content'));
  } catch (e) { alert('Error: ' + e.message); }
}

export function editModeGoToScreenplayReview() {
  const screenplay = window._editModeScreenplay || window._pendingScreenplay;
  if (!screenplay) { alert(t('create.no_screenplay')); return; }

  const spEl = document.getElementById('step-screenplay');
  const sbEl = document.getElementById('step-storyboard');
  if (spEl) { spEl.classList.remove('completed', 'error'); spEl.classList.add('active'); }
  if (sbEl) { sbEl.classList.remove('active', 'error'); sbEl.classList.add('completed'); }

  const content = document.getElementById('pipeline-content');
  renderScreenplayReviewForEdit(content, screenplay);
}

export function editModeGoToStoryboardReview() {
  const storyboard = window._reviewStoryboard || window._editModeStoryboard;
  if (!storyboard) { alert(t('create.no_storyboard')); return; }

  const spEl = document.getElementById('step-screenplay');
  const sbEl = document.getElementById('step-storyboard');
  if (spEl) { spEl.classList.remove('active', 'error'); spEl.classList.add('completed'); }
  if (sbEl) { sbEl.classList.remove('error'); sbEl.classList.add('completed'); }

  const content = document.getElementById('pipeline-content');
  renderStoryboardReview(content, storyboard, { screenplay: window._editModeScreenplay });
}

export async function saveEditModeStoryboard() {
  const storyboard = window._reviewStoryboard || window._editModeStoryboard || createdStoryboard;
  if (!storyboard) { alert(t('create.no_storyboard')); return; }

  const sbPath = window._editModeStoryboardPath || createdStoryboardPath;
  if (!sbPath) { alert(t('create.no_storyboard_path')); return; }

  // 同步风格修改到分镜数据
  _syncStyleToStoryboard(storyboard, 'storyboard-review');

  const btn = document.getElementById('edit-mode-save-btn');
  const originalLabel = btn ? btn.innerHTML : '';
  if (btn) { btn.disabled = true; btn.innerHTML = t('create.saving'); }

  // Sync seedance textarea edits back to storyboard object
  const scenes = storyboard.storyboard || [];
  scenes.forEach((scene, idx) => {
    const sEl = document.getElementById(`review-scene-seedance-${idx}`);
    if (sEl) {
      scene.seedance_prompt = sEl.value;
      if (storyboard.groups && storyboard.groups[idx]) storyboard.groups[idx].sora_prompt = sEl.value;
    }
  });

  try {
    const res = await fetch('/api/storyboard/save', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ storyboard_path: sbPath, storyboard }),
    });
    const data = await res.json();
    if (data.ok) {
      if (btn) { btn.innerHTML = t('create.saved'); setTimeout(() => { btn.innerHTML = originalLabel; btn.disabled = false; }, 1500); }
      showToast(t('create.sb_saved'), 'success');
    } else {
      if (btn) { btn.innerHTML = t('create.save_failed'); setTimeout(() => { btn.innerHTML = originalLabel; btn.disabled = false; }, 2000); }
      showToast(t('create.sb_save_failed') + (data.error || ''), 'error');
    }
  } catch (e) {
    if (btn) { btn.innerHTML = t('create.save_error'); setTimeout(() => { btn.innerHTML = originalLabel; btn.disabled = false; }, 2000); }
    showToast('Error: ' + e.message, 'error');
  }
}

export function editModeGoToEditor() {
  // Sync any seedance edits from storyboard review
  const storyboard = window._reviewStoryboard || window._editModeStoryboard || createdStoryboard;
  if (!storyboard) { alert(t('create.no_storyboard')); return; }

  const scenes = storyboard.storyboard || [];
  scenes.forEach((scene, idx) => {
    const sEl = document.getElementById(`review-scene-seedance-${idx}`);
    if (sEl) {
      scene.seedance_prompt = sEl.value;
      if (storyboard.groups && storyboard.groups[idx]) storyboard.groups[idx].sora_prompt = sEl.value;
    }
  });

  // Persist to disk before entering editor
  const sbPath = window._editModeStoryboardPath || createdStoryboardPath;
  if (sbPath) {
    fetch('/api/storyboard/save', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ storyboard_path: sbPath, storyboard }),
    }).catch(() => {});
  }

  // Mark step-done as completed so user can click it to return to editor
  const doneEl = document.getElementById('step-done');
  if (doneEl) { doneEl.classList.remove('active', 'error'); doneEl.classList.add('completed'); }

  createdStoryboard = storyboard;
  // 确保 createdStoryboardPath 也同步，否则编辑器内 saveStoryboard() 会因 path 为空而静默失败
  if (!createdStoryboardPath) {
    createdStoryboardPath = window._editModeStoryboardPath || `storyboards/${storyboard.title || 'untitled'}_storyboard.json`;
  }
  showCreatePhase('editor');
  window.syncEditorModelFromHome?.();
  renderEditor(storyboard);
}

export async function sendEditModeChatMessage() {
  // Reuse the existing refine-narrative API but without a job_id dependency
  const input = document.getElementById('chat-input');
  const sendBtn = document.getElementById('chat-send-btn');
  if (!input || !input.value.trim()) return;

  const userMsg = input.value.trim();
  input.value = '';
  if (sendBtn) sendBtn.disabled = true;

  if (!window._chatHistory) window._chatHistory = [];
  window._chatHistory.push({ role: 'user', content: userMsg });
  window._chatHistory.push({ role: 'assistant', content: t('create.refining') });
  renderChatMessages();

  // 从最后一条 narrative 消息获取最新叙事（用户可能手动编辑过）
  if (window._pendingScreenplay && window._chatHistory) {
    for (let i = window._chatHistory.length - 2; i >= 0; i--) { // -2 跳过刚加的 loading
      if (window._chatHistory[i].role === 'assistant' && window._chatHistory[i]._isNarrative) {
        window._pendingScreenplay.narrative = window._chatHistory[i].content;
        break;
      }
    }
  }

  try {
    const res = await fetch('/api/create/refine-narrative', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        screenplay: window._pendingScreenplay,
        user_feedback: userMsg,
        chat_history: window._chatHistory.slice(0, -2).map(m => ({ role: m.role, content: m.content })),
      }),
    });
    const data = await res.json();

    // Remove loading message
    window._chatHistory.pop();

    if (data.ok) {
      window._pendingScreenplay = data.screenplay;
      const newNarrative = data.screenplay.narrative || '';
      // 如果有修改说明，先追加说明消息
      if (data.response && data.response !== '✅ ' + t('create.narrative_updated')) {
        window._chatHistory.push({ role: 'assistant', content: data.response, _isSummary: true });
      }
      // 追加完整新版剧本作为 AI 消息
      window._chatHistory.push({ role: 'assistant', content: newNarrative, _isNarrative: true });
    } else {
      window._chatHistory.push({ role: 'assistant', content: data.error || 'No changes.' });
    }
  } catch (e) {
    window._chatHistory.pop();
    window._chatHistory.push({ role: 'assistant', content: '❌ Error: ' + e.message });
  }

  renderChatMessages();
  if (sendBtn) sendBtn.disabled = false;
}

// ══════════════════════════════════════════════════════════════
// Step click navigation
// ══════════════════════════════════════════════════════════════

/**
 * 点击左侧步骤指示器时的导航逻辑。
 * - 点击"剧本"（completed）→ 返回剧本审核
 * - 点击"分镜"（completed）→ 返回分镜审核
 * 支持编辑模式和正常 pipeline 模式。
 */
export function onStepClick(stepName) {
  const el = document.getElementById('step-' + stepName);
  if (!el) return;

  // 分镜生成中：锁定返回，不允许点击 screenplay 步骤跳回
  const isStoryboardGenerating = activeCreateJobMeta
    && activeCreateJobMeta.phase === 'storyboard'
    && activeCreateJobMeta.status === 'running';
  if (isStoryboardGenerating && stepName === 'screenplay') {
    // 给 step-screenplay 短暂抖动提示
    const spEl = document.getElementById('step-screenplay');
    if (spEl && !spEl.classList.contains('step-locked-shake')) {
      spEl.classList.add('step-locked-shake');
      setTimeout(() => spEl.classList.remove('step-locked-shake'), 600);
    }
    return;
  }

  // 编辑模式（从"我的剧本"点编辑进来的）
  const isEditMode = !!window._editModeStoryboardPath && !createJobId;
  if (isEditMode) {
    if (stepName === 'screenplay' && (el.classList.contains('completed') || el.classList.contains('active'))) {
      editModeGoToScreenplayReview();
    } else if (stepName === 'storyboard' && (el.classList.contains('completed') || el.classList.contains('active'))) {
      editModeGoToStoryboardReview();
    } else if (stepName === 'done' && el.classList.contains('completed')) {
      editModeGoToEditor();
    }
    return;
  }

  // 正常 pipeline 模式
  if (stepName === 'screenplay' && el.classList.contains('completed')) {
    backToScreenplayReview();
  } else if (stepName === 'storyboard') {
    if (el.classList.contains('completed') && window._reviewStoryboard) {
      backToStoryboardReview();
    }
  }
}

/**
 * 从分镜审核页面回退到剧本审核页面。
 */
export function backToScreenplayReview() {
  if (!window._pendingScreenplay) return;
  const content = document.getElementById('pipeline-content');
  if (!content) return;
  const spEl = document.getElementById('step-screenplay');
  const sbEl = document.getElementById('step-storyboard');
  if (spEl) { spEl.classList.remove('completed', 'active', 'error'); spEl.classList.add('active'); }
  if (sbEl) {
    sbEl.classList.remove('active', 'error');
    if (window._reviewStoryboard) {
      sbEl.classList.add('completed');
    } else {
      sbEl.classList.remove('completed');
    }
  }
  renderScreenplayReview(content, window._pendingScreenplay);
}

/**
 * 从剧本审核页面返回到已有的分镜审核页面（不重新生成）。
 */
export function backToStoryboardReview() {
  if (!window._reviewStoryboard) return;
  const content = document.getElementById('pipeline-content');
  if (!content) return;
  setStepActive('storyboard');
  document.getElementById('step-storyboard')?.classList.remove('active');
  document.getElementById('step-storyboard')?.classList.add('completed');
  renderStoryboardReview(content, window._reviewStoryboard, { isPipeline: true });
}

// ══════════════════════════════════════════════════════════════
// Shared scene editing helpers (used by pipeline review + editor)
// ══════════════════════════════════════════════════════════════

export async function sceneRegenPrompt(sceneIndex, storyboard) {
  const scene = storyboard.storyboard[sceneIndex];
  const narrativeEl = document.getElementById(`review-scene-narrative-${sceneIndex}`);
  const seedanceEl = document.getElementById(`review-scene-seedance-${sceneIndex}`);
  const btn = document.getElementById(`scene-regen-btn-${sceneIndex}`);
  
  const narrative = narrativeEl ? narrativeEl.value : (scene.narrative_summary || '');
  if (btn) { btn.disabled = true; btn.textContent = t('misc.regenerating'); }
  
  try {
    const res = await fetch('/api/scene/regenerate-prompt', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scene_index: sceneIndex, narrative_summary: narrative, storyboard }),
    });
    const data = await res.json();
    if (data.ok) {
      storyboard.storyboard[sceneIndex].seedance_prompt = data.seedance_prompt;
      storyboard.storyboard[sceneIndex].narrative_summary = narrative;
      if (data.transition_strategy) storyboard.storyboard[sceneIndex].transition_strategy = data.transition_strategy;
      if (data.continuity_anchor) storyboard.storyboard[sceneIndex].continuity_anchor = data.continuity_anchor;
      if (storyboard.groups && storyboard.groups[sceneIndex]) {
        storyboard.groups[sceneIndex].sora_prompt = data.seedance_prompt;
        storyboard.groups[sceneIndex].narrative_summary = narrative;
        if (data.transition_strategy) storyboard.groups[sceneIndex].transition_strategy = data.transition_strategy;
        if (data.continuity_anchor) storyboard.groups[sceneIndex].continuity_anchor = data.continuity_anchor;
      }
      refreshSceneContinuitySection(sceneIndex, storyboard);
      if (seedanceEl) seedanceEl.value = data.seedance_prompt;
      if (btn) btn.textContent = '✓ Done';
      setTimeout(() => { if (btn) btn.textContent = t('misc.regen_prompt'); }, 1500);
    } else {
      alert('Regenerate failed: ' + (data.error || 'Unknown error'));
      if (btn) btn.textContent = t('misc.regen_prompt');
    }
  } catch (e) {
    alert('Error: ' + e.message);
    if (btn) btn.textContent = t('misc.regen_prompt');
  }
  if (btn) btn.disabled = false;
}

export async function sceneRefineChat(sceneIndex, storyboard, field) {
  const chatInput = document.getElementById(`scene-chat-input-${sceneIndex}`);
  const chatMsgs = document.getElementById(`scene-chat-msgs-${sceneIndex}`);
  const sendBtn = document.getElementById(`scene-chat-send-${sceneIndex}`);
  const feedback = chatInput ? chatInput.value.trim() : '';
  if (!feedback) return;

  // Show user message
  if (chatMsgs) chatMsgs.innerHTML += `<div style="text-align:right;margin:4px 0"><span style="background:var(--accent);color:#fff;padding:4px 10px;border-radius:10px;font-size:12px">${esc(feedback)}</span></div>`;
  if (chatInput) chatInput.value = '';
  if (sendBtn) sendBtn.disabled = true;

  try {
    const res = await fetch('/api/scene/refine-with-chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scene_index: sceneIndex, user_feedback: feedback, field, storyboard }),
    });
    const data = await res.json();
    if (data.ok) {
      storyboard.storyboard[sceneIndex].narrative_summary = data.narrative_summary;
      storyboard.storyboard[sceneIndex].seedance_prompt = data.seedance_prompt;
      if (data.transition_strategy) storyboard.storyboard[sceneIndex].transition_strategy = data.transition_strategy;
      if (data.continuity_anchor) storyboard.storyboard[sceneIndex].continuity_anchor = data.continuity_anchor;
      if (storyboard.groups && storyboard.groups[sceneIndex]) {
        storyboard.groups[sceneIndex].sora_prompt = data.seedance_prompt;
        storyboard.groups[sceneIndex].narrative_summary = data.narrative_summary;
        if (data.transition_strategy) storyboard.groups[sceneIndex].transition_strategy = data.transition_strategy;
        if (data.continuity_anchor) storyboard.groups[sceneIndex].continuity_anchor = data.continuity_anchor;
      }
      refreshSceneContinuitySection(sceneIndex, storyboard);
      const narrativeEl = document.getElementById(`review-scene-narrative-${sceneIndex}`);
      const seedanceEl = document.getElementById(`review-scene-seedance-${sceneIndex}`);
      if (narrativeEl) narrativeEl.value = data.narrative_summary;
      if (seedanceEl) seedanceEl.value = data.seedance_prompt;
      if (chatMsgs) chatMsgs.innerHTML += `<div style="margin:4px 0"><span style="background:var(--bg-glass);padding:4px 10px;border-radius:10px;font-size:12px;color:var(--text-secondary)">✓ Updated</span></div>`;
    } else {
      if (chatMsgs) chatMsgs.innerHTML += `<div style="margin:4px 0"><span style="color:var(--error);font-size:12px">❌ ${esc(data.error || 'Failed')}</span></div>`;
    }
  } catch (e) {
    if (chatMsgs) chatMsgs.innerHTML += `<div style="margin:4px 0"><span style="color:var(--error);font-size:12px">❌ ${esc(e.message)}</span></div>`;
  }
  if (sendBtn) sendBtn.disabled = false;
}

// ══════════════════════════════════════════════════════════════
// Storyboard Review (pipeline pause point)
// ══════════════════════════════════════════════════════════════

export function renderStoryboardReview(container, storyboard, opts = {}) {
  // opts.screenplay — 可选，剧本数据
  // opts.isPipeline — true 表示 pipeline 创建流程（显示"停止任务"按钮）
  window._reviewStoryboard = storyboard;
  if (opts.screenplay !== undefined) {
    window._editModeScreenplay = opts.screenplay || window._editModeScreenplay;
  }

  const scenes = storyboard.storyboard || [];

  // 滚动内容区
  let scrollHtml = '<div class="pipeline-content-title">' + t('create.storyboard_review_title') + '</div>';
  scrollHtml += '<div style="background:linear-gradient(135deg,rgba(99,102,241,0.08),rgba(139,92,246,0.06));border:1px solid rgba(99,102,241,0.2);border-radius:12px;padding:16px 20px;margin-bottom:16px;font-size:14px;color:var(--text-primary);line-height:1.6">' + t('create.storyboard_review_hint').replace('{0}', scenes.length) + '</div>';

  scrollHtml += _buildStyleEditorHTML(storyboard, 'storyboard-review');

  scenes.forEach((scene, idx) => {
    scrollHtml += `<div class="storyboard-review-scene" style="border:1px solid var(--border-subtle);border-radius:12px;padding:16px;margin-bottom:16px;background:var(--bg-card)">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
        <span style="font-weight:600;color:var(--text-primary)">Scene ${scene.scene_number} <span style="color:var(--text-muted);font-weight:400;font-size:12px">${scene.duration || ''}</span></span>
        <div style="display:flex;gap:6px">
          ${(scene.characters_in_scene||[]).map(c => '<span class="scene-tag char">'+esc(c)+'</span>').join('')}
          ${scene.scene_location ? '<span class="scene-tag">'+esc(scene.scene_location)+'</span>' : ''}
        </div>
      </div>
      <div style="margin-bottom:8px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
          <span style="font-size:12px;color:var(--text-muted)">Seedance Prompt</span>
        </div>
        <!-- review 视图的 seedance textarea，editor.js 用 "editor-scene-seedance-" 前缀 -->
        <textarea id="review-scene-seedance-${idx}" style="width:100%;min-height:160px;padding:10px;border-radius:8px;border:1px solid var(--border-subtle);background:var(--bg-glass);color:var(--text-secondary);font-size:13px;line-height:1.6;font-family:var(--font-sans);resize:vertical">${esc(scene.seedance_prompt || '')}</textarea>
      </div>
    </div>`;
  });

  // ── 外层 flex 布局：内容可滚动 + 按钮固定在底部 ──────────────────
  container.style.display = 'flex';
  container.style.flexDirection = 'column';
  container.style.padding = '0';

  let buttonsHtml;
  if (opts.isPipeline) {
    buttonsHtml = `
      <button class="btn-primary" onclick="continueFromStoryboardReview()">${t('create.enter_editor')}</button>
      <button class="btn-primary" id="edit-mode-save-btn" style="background:linear-gradient(135deg,#10b981,#059669)" onclick="saveEditModeStoryboard()">${t('create.save_btn')}</button>
      <button class="btn-danger-soft" style="margin-left:auto" onclick="deleteCurrentCreateJob()">🗑 ${t('misc.delete')}</button>`;
  } else {
    buttonsHtml = `
      <button class="btn-primary" onclick="editModeGoToEditor()">${t('create.enter_editor')}</button>
      <button class="btn-primary" id="edit-mode-save-btn" style="background:linear-gradient(135deg,#10b981,#059669)" onclick="saveEditModeStoryboard()">${t('create.save_btn')}</button>
      <button class="btn-secondary" onclick="editModeGoToScreenplayReview()">${t('create.back_screenplay')}</button>`;
  }

  container.innerHTML = `
    <div style="flex:1;overflow-y:auto;padding:24px 24px 0 24px">${scrollHtml}</div>
    <div style="flex-shrink:0;display:flex;gap:10px;padding:16px 24px;border-top:1px solid var(--border-subtle);background:var(--bg-card);flex-wrap:wrap">
      ${buttonsHtml}
    </div>`;
}

export async function startVideoFromCreate() {
  if (!createJobId) { alert('No active creation job'); return; }
  const btn = document.querySelector('[onclick="startVideoFromCreate()"]');
  if (btn) { btn.disabled = true; btn.textContent = t('editor.starting'); }

  try {
    const res = await fetch(`/api/create/start-video/${createJobId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    const data = await res.json();
    if (data.error) {
      alert(data.error);
      if (btn) { btn.disabled = false; btn.innerHTML = `▶ ${t('create.start_video_btn')}`; }
      return;
    }
    setSelectedVideoJobId(data?.video_job?.job_id || selectedVideoJobId);
    showToast(t('create.video_started'), 'success');
    setTimeout(() => {
      loadVideoJobsFn();
      switchTab('monitor');
    }, 600);
  } catch (e) {
    alert('Error: ' + e.message);
    if (btn) { btn.disabled = false; btn.innerHTML = `▶ ${t('create.start_video_btn')}`; }
  }
}

export function skipVideoGoToEditor() {
  if (createdStoryboard) {
    showCreatePhase('editor');
    window.syncEditorModelFromHome?.();
    renderEditor(createdStoryboard);
  }
}

export function goToEditorFromScreenplayReview() {
  const sb = createdStoryboard;
  if (sb) {
    showCreatePhase('editor');
    window.syncEditorModelFromHome?.();
    renderEditor(sb);
  }
}

// 自动确认分镜并继续（跳过审核窗格）
async function _autoConfirmStoryboard(storyboard) {
  if (!storyboard) return;
  const sbPath = createdStoryboardPath;
  if (sbPath) {
    try {
      await fetch('/api/storyboard/save', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ storyboard_path: sbPath, storyboard }),
      });
    } catch (_) { /* 保存失败不阻塞 */ }
  }
  try {
    const res = await fetch(`/api/create/continue-storyboard/${createJobId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ storyboard }),
    });
    const data = await res.json();
    if (!data.ok) {
      showToast('Failed: ' + (data.error || 'Unknown error'), 'error');
    }
    // done 事件会通过 WebSocket 触发编辑器打开
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  }
}

export async function continueFromStoryboardReview() {
  const storyboard = window._reviewStoryboard;
  if (!storyboard) { alert('No storyboard data'); return; }

  // 同步风格修改到分镜数据
  _syncStyleToStoryboard(storyboard, 'storyboard-review');

  // Sync seedance textarea edits back to storyboard object (narrative is read-only)
  const scenes = storyboard.storyboard || [];
  scenes.forEach((scene, idx) => {
    const sEl = document.getElementById(`review-scene-seedance-${idx}`);
    if (sEl) {
      scene.seedance_prompt = sEl.value;
      if (storyboard.groups && storyboard.groups[idx]) storyboard.groups[idx].sora_prompt = sEl.value;
    }
  });

  // 先保存到磁盘，确保编辑的 seedance prompt 不丢失
  const sbPath = createdStoryboardPath;
  if (sbPath) {
    try {
      await fetch('/api/storyboard/save', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ storyboard_path: sbPath, storyboard }),
      });
    } catch (_) { /* 保存失败不阻塞继续流程 */ }
  }

  try {
    const res = await fetch(`/api/create/continue-storyboard/${createJobId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ storyboard }),
    });
    const data = await res.json();
    if (!data.ok) {
      alert('Failed: ' + (data.error || 'Unknown error'));
    }
    // The 'done' event via WebSocket will trigger editor opening
  } catch (e) {
    alert('Error: ' + e.message);
  }
}

export function renderReviewIssues(container, data) {
  let html = '<div class="pipeline-content-title">' + t('create.review_issues_title') + '</div>';
  const issues = data?.issues || data || [];
  if (Array.isArray(issues)) {
    html += `<div class="artifact-section"><div class="artifact-label">${issues.length} Issues</div>`;
    html += issues.map(issue => {
      const severity = (issue.severity || issue.type || 'info').toLowerCase();
      const cls = severity.includes('critical') ? 'critical' : severity.includes('warn') ? 'warning' : 'info';
      return `<div class="issue-card ${cls}">
        <div class="issue-title">${esc(issue.issue || issue.description || issue.title || 'Issue')}</div>
        ${issue.suggestion ? `<div style="margin-top:4px">${esc(issue.suggestion)}</div>` : ''}
        ${issue.affected_scenes ? `<div class="issue-scenes">Scenes: ${Array.isArray(issue.affected_scenes) ? issue.affected_scenes.join(', ') : issue.affected_scenes}</div>` : ''}
      </div>`;
    }).join('');
    html += '</div>';
  }
  html += '<div style="font-size:12px;color:var(--text-muted);margin-top:12px">' + t('create.generating_fixes') + '</div>';
  container.innerHTML = html;
}

export function renderFixPatches(container, data) {
  let html = '<div class="pipeline-content-title">' + t('create.fix_patches_title') + '</div>';
  const patches = data?.patches || data?.fixes || data || [];
  if (Array.isArray(patches)) {
    html += `<div class="artifact-section"><div class="artifact-label">${patches.length} Patches</div>`;
    html += patches.map(p => `<div class="patch-card">
      <div class="patch-scene">${esc(p.scene_number ? 'Scene ' + p.scene_number : p.target || 'Patch')}</div>
      <div class="patch-changes">${esc(p.description || p.changes || JSON.stringify(p).substring(0, 200))}</div>
    </div>`).join('');
    html += '</div>';
  }
  html += '<div style="font-size:12px;color:var(--text-muted);margin-top:12px">' + t('create.merging_fixes') + '</div>';
  container.innerHTML = html;
}

// ══════════════════════════════════════════════════════════════
// My Storyboards
// ══════════════════════════════════════════════════════════════

export async function loadStoryboardList() {
  try {
    const [sbRes, jobsRes] = await Promise.all([
      fetch('/api/storyboards'),
      fetch('/api/create/jobs'),
    ]);
    const list = await sbRes.json();
    const jobs = await jobsRes.json();
    // Build a map: output_path → job (only paused/interrupted jobs are interesting)
    const jobByPath = {};
    for (const j of jobs) {
      if (j.output_path && (j.status === 'paused' || j.status === 'interrupted')) {
        jobByPath[j.output_path] = j;
      }
    }
    renderStoryboardList(list, jobByPath);

    // ── Restore active creation job on page load ──────────────────
    // If there's already an active job in memory, skip restoration
    if (!createJobId && !isCreateJobActive()) {
      _restoreActiveCreationJob(jobs);
    }
  } catch (e) { console.error('Failed to load storyboards', e); }
}

/**
 * 页面刷新后恢复活跃的创作任务。
 * 找到 running/paused 状态的 job，恢复 banner + pipeline 视图。
 * 优先 running，其次 paused；同状态中取最后一个（最新创建）。
 */
async function _restoreActiveCreationJob(jobs) {
  if (!Array.isArray(jobs)) return;
  const reversed = [...jobs].reverse();
  const activeJob = reversed.find(j => j.status === 'running')
                 || reversed.find(j => j.status === 'paused');
  if (!activeJob) return;

  const jobId = activeJob.job_id;
  const phase = activeJob.phase;
  const status = activeJob.status;

  // Restore state
  createJobId = jobId;
  activeCreateJobMeta = {
    job_id: jobId,
    title: activeJob.title || '',
    status,
    phase,
    output_path: activeJob.output_path || '',
  };
  window._createOneClick = !!activeJob.one_click;
  updateUnifiedButtons();

  // If the job is at a review checkpoint (paused), restore the review UI
  if ((phase === 'screenplay_review' || phase === 'screenplay_done') && status === 'paused') {
    showCreatePhase('pipeline');
    setStepActive('screenplay');
    document.getElementById('step-screenplay')?.classList.remove('active');
    document.getElementById('step-screenplay')?.classList.add('completed');
    const content = document.getElementById('pipeline-content');
    if (activeJob.screenplay_data) {
      const llmTitle = activeJob.screenplay_data.title || activeJob.title || '';
      if (llmTitle && llmTitle !== 'untitled') {
        document.getElementById('create-title').value = llmTitle;
        createdStoryboardPath = `storyboards/${llmTitle}_storyboard.json`;
      }
      renderScreenplayReview(content, activeJob.screenplay_data);
    } else {
      content.innerHTML = `<div style="color:var(--warning);padding:16px">${t('create.paused_no_screenplay')}</div>`;
    }
  } else if (phase === 'storyboard_review' && status === 'paused') {
    showCreatePhase('pipeline');
    setStepActive('storyboard');
    document.getElementById('step-screenplay')?.classList.add('completed');
    document.getElementById('step-storyboard')?.classList.remove('active');
    document.getElementById('step-storyboard')?.classList.add('completed');
    const content = document.getElementById('pipeline-content');
    if (activeJob.output_path) {
      try {
        const sbRes = await fetch(`/api/storyboard/load?path=${encodeURIComponent(activeJob.output_path)}`);
        const storyboard = await sbRes.json();
        window._reviewStoryboard = storyboard;
        window._reviewOutputPath = activeJob.output_path;
        createdStoryboardPath = activeJob.output_path;
        renderStoryboardReview(content, storyboard, { isPipeline: true });
      } catch (_) {
        content.innerHTML = `<div style="color:var(--warning);padding:16px">${t('create.paused_no_storyboard')}</div>`;
      }
    } else if (activeJob.storyboard_data) {
      window._reviewStoryboard = activeJob.storyboard_data;
      renderStoryboardReview(content, activeJob.storyboard_data, { isPipeline: true });
    }
  } else if (status === 'running') {
    // Job is actively running — show pipeline with a waiting indicator
    showCreatePhase('pipeline');
    const content = document.getElementById('pipeline-content');
    if (phase === 'starting' || phase === 'screenplay') {
      setStepActive('screenplay');
      content.innerHTML = `<div class="pipeline-waiting"><div class="live-dot"></div> ${t('pipeline.gen_screenplay')}</div>`;
    } else if (phase === 'screenplay_done' || phase === 'storyboard'
               || /^0[1-5]_/.test(phase)) {
      setStepActive('storyboard');
      _ensureLogViewer(content);
    } else {
      setStepActive('storyboard');
      content.innerHTML = `<div class="pipeline-waiting"><div class="live-dot"></div> ${t('create.generating_in_progress')}</div>`;
    }
  } else if (status === 'paused') {
    // Generic paused state (not at a review checkpoint)
    showCreatePhase('pipeline');
    setStepActive('screenplay');
    const content = document.getElementById('pipeline-content');
    content.innerHTML = `
      <div class="pipeline-waiting" style="color:var(--warning)">${t('create.paused_generic')}</div>
      <div style="display:flex;gap:10px;padding:16px 24px;border-top:1px solid var(--border-subtle);background:var(--bg-card)">
        <button class="btn-primary" onclick="resumeCurrentCreateJob()">▶ ${t('misc.resume')}</button>
        <button class="btn-danger-soft" style="margin-left:auto" onclick="deleteCurrentCreateJob()">🗑 ${t('misc.delete')}</button>
      </div>`;
  }
}

export function renderStoryboardList(list, jobByPath = {}) {
  const el = document.getElementById('storyboard-list');
  if (!list.length) {
    el.innerHTML = `<div style="color:var(--text-muted);font-size:13px;padding:8px;grid-column:1/-1">${t('create.no_storyboards')}</div>`;
    return;
  }
  // Sort by mtime descending (newest first), fallback to modified string
  const sorted = [...list].sort((a, b) => (b.mtime || 0) - (a.mtime || 0));
  el.innerHTML = sorted.map(sb => {
    const desc = sb.description || '';
    const truncDesc = desc.length > 80 ? desc.slice(0, 80) + '…' : desc;
    const timeLabel = sb.mtime ? relativeTime(sb.mtime) : sb.modified;
    const job = jobByPath[sb.path];
    const encodedPath = encodeURIComponent(sb.path);
    const jobBadge = job
      ? job.status === 'paused'
        ? `<span style="font-size:10px;padding:2px 7px;border-radius:10px;background:rgba(99,102,241,0.18);color:var(--accent);font-weight:600;margin-left:6px">${t('create.badge_paused')}</span>`
        : `<span style="font-size:10px;padding:2px 7px;border-radius:10px;background:rgba(236,72,153,0.15);color:var(--pink);font-weight:600;margin-left:6px">${t('create.badge_interrupted')}</span>`
      : '';
    const resumeBtn = job && job.status === 'paused'
      ? `<button class="sb-btn-sm" style="background:rgba(99,102,241,0.15);border-color:var(--border-accent)" onclick="event.stopPropagation(); resumePausedJob('${esc(job.job_id)}', decodeURIComponent('${encodedPath}'))">${t('create.resume_btn')}</button>`
      : '';
    return `<div class="storyboard-item">
      <div class="storyboard-item-header">
        <div class="storyboard-item-icon">📋</div>
        <div class="storyboard-item-title">${esc(sb.title)}${jobBadge}</div>
      </div>
      ${truncDesc ? `<div style="font-size:12px;color:var(--text-secondary);line-height:1.5">${esc(truncDesc)}</div>` : ''}
      <div class="storyboard-item-meta">
        <span>${sb.scenes} ${t('misc.scenes')}</span>
        <span>${sb.characters} chars</span>
        ${sb.style ? `<span>${esc(sb.style)}</span>` : ''}
        <span title="${esc(sb.modified)}">${esc(timeLabel)}</span>
      </div>
      <div class="storyboard-item-actions">
        ${resumeBtn}
        <button class="sb-btn-sm primary" onclick="event.stopPropagation(); openStoryboardInEditor(decodeURIComponent('${encodedPath}'))">${t('create.edit_btn')}</button>
        <button class="sb-btn-sm" onclick="event.stopPropagation(); duplicateStoryboard(decodeURIComponent('${encodedPath}'))">${t('create.copy_btn')}</button>
        <button class="sb-btn-sm" style="border-color:rgba(239,68,68,0.24);color:rgba(254,202,202,0.96)" onclick="event.stopPropagation(); deleteStoryboard(decodeURIComponent('${encodedPath}'))">${t('create.delete_btn')}</button>
      </div>
    </div>`;
  }).join('');
}

export function relativeTime(mtime) {
  const now = Date.now() / 1000;
  const diff = now - mtime;
  if (diff < 60)          return t('time.just_now');
  if (diff < 3600)        return t('time.minutes_ago').replace('{0}', Math.floor(diff / 60));
  if (diff < 86400)       return t('time.hours_ago').replace('{0}', Math.floor(diff / 3600));
  if (diff < 86400 * 7)   return t('time.days_ago').replace('{0}', Math.floor(diff / 86400));
  if (diff < 86400 * 30)  return t('time.weeks_ago').replace('{0}', Math.floor(diff / 86400 / 7));
  if (diff < 86400 * 365) return t('time.months_ago').replace('{0}', Math.floor(diff / 86400 / 30));
  return t('time.years_ago').replace('{0}', Math.floor(diff / 86400 / 365));
}

export async function resumePausedJob(jobId, storyboardPath) {
  // Restore the paused job into the active pipeline view so the user can continue
  try {
    const res = await fetch(`/api/create/jobs`);
    const jobs = await res.json();
    const job = jobs.find(j => j.job_id === jobId);
    if (!job) { alert('Job not found'); return; }

    // Switch to pipeline view
    createJobId = jobId;
    activeCreateJobMeta = job;
    switchTab('pipeline');
    showCreatePhase('pipeline');
    setStepActive('screenplay');

    const content = document.getElementById('pipeline-content');

    if (job.phase === 'screenplay_review') {
      // Reload screenplay data from server
      const sbRes = await fetch(`/api/create/job/${jobId}`);
      const sbData = await sbRes.json();
      const screenplay = sbData.screenplay_data;
      if (screenplay) {
        document.getElementById('step-screenplay').classList.remove('active');
        document.getElementById('step-screenplay').classList.add('completed');
        const llmTitle = screenplay.title || '';
        if (llmTitle && llmTitle !== 'untitled') {
          document.getElementById('create-title').value = llmTitle;
          createdStoryboardPath = `storyboards/${llmTitle}_storyboard.json`;
        }
        renderScreenplayReview(content, screenplay);
      } else {
        content.innerHTML = `<div style="color:var(--error);padding:16px">${t('create.no_screenplay_data')}</div>`;
      }
    } else if (job.phase === 'storyboard_review') {
      // Load storyboard from disk
      const sbRes = await fetch(`/api/storyboard/load?path=${encodeURIComponent(storyboardPath)}`);
      const storyboard = await sbRes.json();
      document.getElementById('step-screenplay').classList.add('completed');
      document.getElementById('step-storyboard').classList.remove('active');
      document.getElementById('step-storyboard').classList.add('completed');
      window._reviewStoryboard = storyboard;
      window._reviewOutputPath = storyboardPath;
      createdStoryboardPath = storyboardPath;
      const sbTitle = storyboard.title || '';
      if (sbTitle && sbTitle !== 'untitled') document.getElementById('create-title').value = sbTitle;
      renderStoryboardReview(content, storyboard, { isPipeline: true });
    } else {
      content.innerHTML = `<div style="color:var(--text-muted);padding:16px">${t('create.interrupted_phase').replace('{0}', job.phase)}</div>`;
    }
  } catch (e) {
    alert(t('create.restore_failed') + e.message);
  }
}

export async function openStoryboardInEditor(path) {
  if (isCreateJobActive()) {
    showToast(t('create.job_active_no_edit'), 'error');
    return;
  }
  try {
    const res = await fetch('/api/storyboard/load-with-screenplay?path=' + encodeURIComponent(path));
    const data = await res.json();
    if (!res.ok) { alert('Failed to load: ' + (data.error || 'Unknown')); return; }

    const sb = data.storyboard;
    const screenplay = data.screenplay;
    createdStoryboard = sb;
    createdStoryboardPath = path;

    // Store for edit-mode pipeline
    window._editModeStoryboardPath = path;
    window._editModeScreenplay = screenplay;
    window._editModeStoryboard = sb;

    // Set title
    const sbTitle = sb.title || '';
    if (sbTitle && sbTitle !== 'untitled') document.getElementById('create-title').value = sbTitle;

    // Switch to the pipeline tab so the user sees the change immediately
    switchTab('pipeline');

    // Enter pipeline view with both steps pre-completed
    showCreatePhase('pipeline');

    // Mark both steps as completed (data already exists)
    const spEl = document.getElementById('step-screenplay');
    const sbEl = document.getElementById('step-storyboard');
    const doneEl = document.getElementById('step-done');
    if (spEl) { spEl.classList.remove('active', 'error'); spEl.classList.add('completed'); }
    if (sbEl) { sbEl.classList.remove('active', 'error'); sbEl.classList.add('completed'); }
    if (doneEl) { doneEl.classList.remove('completed', 'active', 'error'); }

    // Store screenplay & storyboard in window for navigation
    if (screenplay) {
      window._pendingScreenplay = screenplay;
      // Sync narrative from storyboard (storyboard is the source of truth after regeneration)
      if (sb.narrative && sb.narrative !== screenplay.narrative) {
        window._pendingScreenplay.narrative = sb.narrative;
      }
      if (sb.characters) window._pendingScreenplay.characters = sb.characters;
      if (sb.locations) window._pendingScreenplay.locations = sb.locations;
      if (sb.video_analysis) window._pendingScreenplay.video_analysis = sb.video_analysis;
    } else {
      window._pendingScreenplay = {
        title: sb.title || '',
        narrative: sb.narrative || '',
        characters: sb.characters || [],
        locations: sb.locations || [],
        video_analysis: sb.video_analysis || {},
      };
    }
    window._reviewStoryboard = sb;
    window._reviewOutputPath = path;

    // Directly enter editor (skip storyboard review)
    showCreatePhase('editor');
    window.syncEditorModelFromHome?.();
    renderEditor(sb);
  } catch (e) { alert('Failed to load: ' + e.message); }
}

export async function duplicateStoryboard(path) {
  if (!path) return;
  try {
    const res = await fetch('/api/storyboard/duplicate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      throw new Error(data.error || 'Failed to duplicate storyboard');
    }
    showToast(t('create.duplicated').replace('{0}', data.new_title), 'success');
    loadStoryboardList();
  } catch (e) {
    alert(t('create.duplicate_failed') + e.message);
  }
}

export async function deleteStoryboard(path) {
  if (!path) return;
  if (!confirm(t('create.confirm_delete_sb'))) {
    return;
  }

  try {
    const res = await fetch('/api/storyboard?path=' + encodeURIComponent(path), { method: 'DELETE' });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      throw new Error(data.error || 'Failed to delete storyboard');
    }

    if (activeCreateJobMeta?.output_path === path) {
      activeCreateJobMeta = null;
      createJobId = null;
      resetPipeline();
      showCreatePhase('form');
      updateCreateSubmitButtons();
    }

    if (createdStoryboardPath === path) {
      createdStoryboard = null;
      createdStoryboardPath = null;
      if (createPhase === 'editor') {
        showCreatePhase('form');
      }
    }

    if (currentData?.storyboard_name === data.project_name || monitorBrowseMode?.project === data.project_name) {
      setMonitorBrowseMode(null);
    }

    await Promise.all([
      loadStoryboardList(),
      loadProjectList(),
      refreshRepositoryData(),
      loadVideoJobsFn(),
    ]);

    if (document.getElementById('view-repo').classList.contains('active')) {
      loadRepository();
    }
    if (document.getElementById('view-monitor').classList.contains('active')) {
      renderMonitor();
    }

    showToast(t('toast.storyboard_deleted'), 'success');
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  }
}

export async function launchGenerationFromPath(path) {
  createdStoryboardPath = path;
  const currentBackend = getCurrentBackend();
  const seeddanceModel = document.getElementById('seeddance-model-select')?.value || 'seedance-2.0';
  try {
    const res = await fetch('/api/generate/start', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        storyboard_path: path,
        seeddance_backend: currentBackend,
        seeddance_model: seeddanceModel,
        generation_mode: 'parallel',
      }),
    });
    const data = await res.json();
    if (data.ok) {
      setSelectedVideoJobId(data.job_id);
      showToast(t('toast.video_started'), 'success');
      // Do NOT send switch_project here — the new job has no run_dir yet.
      // Sending switch_project would cause the backend to find_latest_run
      // and load a previous run's data, making it look like a resume.
      // The monitor's _renderMonitorDetail will handle the "waiting" state
      // and auto-refresh once the job gets a run_dir.
      setTimeout(() => {
        loadVideoJobsFn();
        switchTab('monitor');
      }, 1000);
    } else {
      showToast('Failed: ' + (data.error || 'Unknown error'), 'error');
    }
  } catch (e) { 
    showToast('Error: ' + e.message, 'error');
  }
}

// ══════════════════════════════════════════════════════════════
// Window bindings (for onclick handlers in HTML templates)
// ══════════════════════════════════════════════════════════════
window.createMode = createMode;
window.createJobId = createJobId;
// Use defineProperty so window.createPhase always reflects the module variable
Object.defineProperty(window, 'createPhase', {
  get() { return createPhase; },
  set(v) { createPhase = v; },
  configurable: true,
});
// Use defineProperty so window.createdStoryboard/Path always reflects the module variable
Object.defineProperty(window, 'createdStoryboard', {
  get() { return createdStoryboard; },
  set(v) { createdStoryboard = v; },
  configurable: true,
});
Object.defineProperty(window, 'createdStoryboardPath', {
  get() { return createdStoryboardPath; },
  set(v) { createdStoryboardPath = v; },
  configurable: true,
});
window.uploadedNovelPath = uploadedNovelPath;
window.uploadedVideoPath = uploadedVideoPath;
window.uploadedVideoDurationSeconds = uploadedVideoDurationSeconds;
window.activeCreateJobMeta = activeCreateJobMeta;

window.isCreateSubmissionReady = isCreateSubmissionReady;
window.updateCreateSubmitButtons = updateCreateSubmitButtons;
window.updateUnifiedButtons = updateUnifiedButtons;

// Called by nav.js when switching to the create tab — re-sync job status
// in case the stopped/error event was missed while on another tab
window.syncCreateTabJobStatus = async function() {
  if (!activeCreateJobMeta || !createJobId) return;
  const terminalStatuses = ['stopped', 'done', 'error', 'failed'];
  if (terminalStatuses.includes(activeCreateJobMeta.status)) {
    activeCreateJobMeta = null;
    updateUnifiedButtons();
    return;
  }
  // If status is stopping/pausing, poll the server to see if it's already done
  if (['stopping', 'pausing'].includes(activeCreateJobMeta.status)) {
    try {
      const res = await fetch('/api/create/jobs');
      if (!res.ok) return;
      const jobs = await res.json();
      const job = jobs.find(j => j.job_id === createJobId);
      if (!job || terminalStatuses.includes(job.status)) {
        activeCreateJobMeta = null;
        createJobId = null;
        updateUnifiedButtons();
      }
    } catch (_) { /* ignore */ }
  }
};
window.unifiedTextareaResize = unifiedTextareaResize;
window.unifiedTextareaKeydown = unifiedTextareaKeydown;
window.hintChipFill = hintChipFill;
window.quickChatAutoResize = quickChatAutoResize;
window.quickChatUpdateSend = quickChatUpdateSend;
window.quickChatKeydown = quickChatKeydown;
window.quickChatFill = quickChatFill;
window.quickChatSubmit = quickChatSubmit;
window.switchMode = switchMode;
window.selectCreateMode = selectCreateMode;
window.toggleVideoRecreateDirection = toggleVideoRecreateDirection;
window.initSplitButton = initSplitButton;
window.toggleSplitMenu = toggleSplitMenu;
window.setSplitAction = setSplitAction;
window.splitBtnSubmit = splitBtnSubmit;
window.removeNovelFile = removeNovelFile;
window.formatDurationSecondsForInput = formatDurationSecondsForInput;
window.parseStoryboardDurationSeconds = parseStoryboardDurationSeconds;
window.getStoryboardTotalDuration = getStoryboardTotalDuration;
window.setCreateDurationInput = setCreateDurationInput;
window.applyStoryboardTotalDuration = applyStoryboardTotalDuration;
window.bindEditorTotalDurationInput = bindEditorTotalDurationInput;
window.handleNovelUpload = handleNovelUpload;
window.handleVideoUpload = handleVideoUpload;
window.initVideoUploadDrop = initVideoUploadDrop;
window.initNovelUploadDrop = initNovelUploadDrop;
window.submitCreate = submitCreate;
window.isCreateJobActive = isCreateJobActive;
window.showCreatePhase = showCreatePhase;
window.syncPipelineView = syncPipelineView;
window.createGoBack = createGoBack;
window.resetPipeline = resetPipeline;
window.pauseCurrentCreateJob = pauseCurrentCreateJob;
window.resumeCurrentCreateJob = resumeCurrentCreateJob;
window.stopCurrentCreateJob = stopCurrentCreateJob;
window.deleteCurrentCreateJob = deleteCurrentCreateJob;
window.setStepActive = setStepActive;
window.setStepError = setStepError;
window.handleCreateProgress = handleCreateProgress;
window.countScenes = countScenes;
window.renderScreenplayArtifact = renderScreenplayArtifact;
window._buildStyleEditorHTML = _buildStyleEditorHTML;
window._applyStyleChange = _applyStyleChange;
window._syncStyleToScreenplay = _syncStyleToScreenplay;
window._syncStyleToStoryboard = _syncStyleToStoryboard;
window.renderScreenplayReview = renderScreenplayReview;
window.toggleScreenplayPanel = toggleScreenplayPanel;
window.renderChatMessages = renderChatMessages;
window.startEditNarrative = startEditNarrative;
window.saveEditNarrative = saveEditNarrative;
window.sendChatMessage = sendChatMessage;
window.continueGeneration = continueGeneration;
window.cancelGeneration = cancelGeneration;
window.renderScreenplayReviewForEdit = renderScreenplayReviewForEdit;
window.editModeRegenerateScreenplay = editModeRegenerateScreenplay;
window.editModeContinueToStoryboard = editModeContinueToStoryboard;
window.editModeRegenerateStoryboard = editModeRegenerateStoryboard;
window.editModeGoToScreenplayReview = editModeGoToScreenplayReview;
window.editModeGoToStoryboardReview = editModeGoToStoryboardReview;
window.saveEditModeStoryboard = saveEditModeStoryboard;
window.editModeGoToEditor = editModeGoToEditor;
window.sendEditModeChatMessage = sendEditModeChatMessage;
window.onStepClick = onStepClick;
window.backToScreenplayReview = backToScreenplayReview;
window.backToStoryboardReview = backToStoryboardReview;
window.sceneRegenPrompt = sceneRegenPrompt;
window.sceneRefineChat = sceneRefineChat;
window.renderStoryboardReview = renderStoryboardReview;
window.continueFromStoryboardReview = continueFromStoryboardReview;
window.startVideoFromCreate = startVideoFromCreate;
window.skipVideoGoToEditor = skipVideoGoToEditor;
window.goToEditorFromScreenplayReview = goToEditorFromScreenplayReview;
window.renderReviewIssues = renderReviewIssues;
window.renderFixPatches = renderFixPatches;
window.loadStoryboardList = loadStoryboardList;
window.renderStoryboardList = renderStoryboardList;
window.relativeTime = relativeTime;
window.resumePausedJob = resumePausedJob;
window.openStoryboardInEditor = openStoryboardInEditor;
window.duplicateStoryboard = duplicateStoryboard;
window.deleteStoryboard = deleteStoryboard;
window.launchGenerationFromPath = launchGenerationFromPath;

// ══════════════════════════════════════════════════════════════
// Demo Video Gallery
// ══════════════════════════════════════════════════════════════

let _demoVideos = [];

export async function loadDemoGallery() {
  const grid = document.getElementById('demo-gallery-grid');
  console.log('[DemoGallery] grid element:', grid);
  if (!grid) return;
  try {
    const res = await nativeFetch('/api/demo-videos');
    console.log('[DemoGallery] API response status:', res.status);
    if (!res.ok) throw new Error('Failed to load demo videos');
    _demoVideos = await res.json();
    console.log('[DemoGallery] loaded', _demoVideos.length, 'videos');
  } catch (e) {
    console.error('Failed to load demo gallery', e);
    _demoVideos = [];
  }
  renderDemoGallery();
}

function renderDemoGallery() {
  const grid = document.getElementById('demo-gallery-grid');
  if (!grid) return;
  if (!_demoVideos.length) {
    grid.innerHTML = `<div class="demo-gallery-empty">${t('demo.empty') || '暂无作品'}</div>`;
    return;
  }
  grid.innerHTML = _demoVideos.map(item => {
    const coverSrc = esc(item.cover);
    const videoSrc = esc(item.video);
    // If a cover image is provided, use it; otherwise use video first frame
    const coverHTML = coverSrc
      ? `<img class="demo-card-cover" src="${coverSrc}" alt="${esc(item.title)}" loading="lazy" onerror="this.style.display='none'">`
      : `<video class="demo-card-cover" src="${videoSrc}#t=0.1" preload="metadata" muted playsinline></video>`;
    return `
    <div class="demo-card" onclick="openDemoModal('${esc(item.id)}')">
      <div class="demo-card-cover-wrap">
        ${coverHTML}
        <div class="demo-card-play">
          <div class="demo-card-play-icon">
            <svg viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
          </div>
        </div>
      </div>
      <div class="demo-card-info">
        <div class="demo-card-title">${esc(item.title)}</div>
        <div class="demo-card-style">${esc(item.style || '')}</div>
      </div>
    </div>`;
  }).join('');
}

export function openDemoModal(id) {
  const item = _demoVideos.find(d => d.id === id);
  if (!item) return;
  const modal = document.getElementById('demo-modal');
  const video = document.getElementById('demo-modal-video');
  document.getElementById('demo-modal-title').textContent = item.title || '';
  document.getElementById('demo-modal-style').textContent = item.style || '';
  document.getElementById('demo-modal-synopsis').textContent = item.synopsis || '';
  video.src = item.video || '';
  modal.classList.add('show');
  video.play().catch(() => {});
}

export function closeDemoModal() {
  const modal = document.getElementById('demo-modal');
  const video = document.getElementById('demo-modal-video');
  modal.classList.remove('show');
  video.pause();
  video.src = '';
}

window.loadDemoGallery = loadDemoGallery;
window.openDemoModal = openDemoModal;
window.closeDemoModal = closeDemoModal;
