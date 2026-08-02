/**
 * video-jobs.js — Seeddance Backend, Segment Preview, Video Job Management (ES module)
 * Extracted from index.html lines 11867-12556
 */

import {
  videoJobsData, setVideoJobsData,
  selectedVideoJobId, setSelectedVideoJobId,
  currentData,
  monitorActionState,
  concatMode, setConcatMode,
  concatFadeSeconds, setConcatFadeSeconds,
  repoData, setRepoData,
  ws,
} from './state.js';

import { esc, apiFetch, showToast } from './utils.js';
import { t } from './i18n.js';

// ══════════════════════════════════════════════════════════════
//  SEEDDANCE BACKEND SELECTOR
// ══════════════════════════════════════════════════════════════
let currentBackend = 'jimeng';

export async function loadBackendSetting() {
  try {
    const { response, data } = await apiFetch('/api/settings');
    if (!response.ok) throw new Error(data.error || t('video.load_settings_failed'));
    currentBackend = 'jimeng';
    syncBackendSelects();
  } catch (e) {
    currentBackend = 'jimeng';
    syncBackendSelects();
  }
}

export function syncBackendSelects() {
  currentBackend = 'jimeng';
}

export async function setSeeddanceBackend() {
  currentBackend = 'jimeng';
  syncBackendSelects();
}

// ══════════════════════════════════════════════════════════════
//  SEGMENT PREVIEW MODAL
// ══════════════════════════════════════════════════════════════
let currentSegments = [];
let currentSegmentIndex = 0;

export function openSegmentModal(index) {
  const screenplayData = window._lastScreenplayData;
  if (!screenplayData?.segments) return;

  currentSegments = screenplayData.segments;
  currentSegmentIndex = index;
  renderSegmentModal();
  document.getElementById('segment-modal-overlay').classList.add('show');
}

export function closeSegmentModal() {
  document.getElementById('segment-modal-overlay').classList.remove('show');
}

export function navigateSegment(direction) {
  currentSegmentIndex += direction;
  if (currentSegmentIndex < 0) currentSegmentIndex = 0;
  if (currentSegmentIndex >= currentSegments.length) currentSegmentIndex = currentSegments.length - 1;
  renderSegmentModal();
}

export function renderSegmentModal() {
  const seg = currentSegments[currentSegmentIndex];
  if (!seg) return;

  document.getElementById('segment-modal-title').textContent = `Segment ${currentSegmentIndex + 1} of ${currentSegments.length}`;
  document.getElementById('segment-narrative').textContent = seg.narrative_summary || seg.description || 'No narrative available';

  document.getElementById('segment-prev-btn').disabled = currentSegmentIndex === 0;
  document.getElementById('segment-next-btn').disabled = currentSegmentIndex === currentSegments.length - 1;

  const scenes = seg.scenes || [];
  const scenesEl = document.getElementById('segment-scenes-list');
  if (!scenes.length) {
    scenesEl.innerHTML = '<div style="color:var(--text-muted);font-size:13px">No scenes in this segment</div>';
    return;
  }

  scenesEl.innerHTML = scenes.map(s => {
    const chars = (s.characters_in_scene || []).map(c =>
      `<span class="scene-tag char">${esc(c)}</span>`
    ).join('');
    const loc = s.scene_location ? `<span class="scene-tag">${esc(s.scene_location)}</span>` : '';
    const props = (s.props_in_scene || []).map(p =>
      `<span class="scene-tag">${esc(p)}</span>`
    ).join('');

    return `<div class="segment-scene-card">
      <div class="segment-scene-header">
        <span class="segment-scene-num">Scene ${s.scene_number}</span>
        <span class="segment-scene-dur">${s.duration || '—'}</span>
      </div>
      <div class="segment-scene-summary">${esc(s.narrative_summary || s.plot_description || s.description || '')}</div>
      <div class="segment-scene-meta">${chars}${loc}${props}</div>
    </div>`;
  }).join('');
}

// Keyboard navigation for segment modal
document.addEventListener('keydown', (e) => {
  const modal = document.getElementById('segment-modal-overlay');
  if (!modal.classList.contains('show')) return;

  if (e.key === 'ArrowLeft') navigateSegment(-1);
  else if (e.key === 'ArrowRight') navigateSegment(1);
  else if (e.key === 'Escape') closeSegmentModal();
});

// ══════════════════════════════════════════════════════════════
//  VIDEO GENERATION JOBS MANAGEMENT
// ══════════════════════════════════════════════════════════════
export async function loadVideoJobs() {
  try {
    const { response, data } = await apiFetch('/api/generate/jobs');
    if (!response.ok) throw new Error(data.error || t('video.load_jobs_failed'));
    setVideoJobsData(Array.isArray(data) ? data : []);
    renderVideoJobsPanel();
    return videoJobsData;
  } catch (e) {
    console.error('Failed to load video jobs', e);
    return [];
  }
}

export function renderVideoJobsPanel() {
  if (!document.getElementById('view-monitor').classList.contains('active')) return;
  const detail = document.getElementById('monitor-detail-pane');
  if (detail?.querySelector('.browse-layout') && window.patchMonitorJobsSidebarFromData()) {
    // browse 模式下侧边栏已 patch，但若当前选中任务已完成（不再活跃），
    // 需要刷新 browse pane 以反映最新状态（如移除进度条、显示完成状态）
    const activeJobs = (videoJobsData || []).filter(j =>
      ['queued', 'running', 'paused', 'stopped', 'crashed', 'interrupted'].includes(j.status)
    );
    const selectedStillActive = activeJobs.some(j => j.job_id === selectedVideoJobId);
    if (!selectedStillActive) {
      // 选中任务已完成，触发完整重渲染
      window.renderMonitor?.();
    } else {
      // 任务仍活跃，只刷新 browse pane 内容
      window.scheduleDebouncedBrowsePaneRefresh?.();
    }
    return;
  }
  window.renderMonitor();
}

export function selectVideoJob(jobId, storyboardName) {
  setSelectedVideoJobId(jobId);
  if (storyboardName && ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'switch_project', project_name: storyboardName }));
  }
}

// ── Button action state machine ──────────────────────────────────────
const _btnActionPending = new Set();
const _BTN_ACTION_TIMEOUT = 15000;

export async function _guardedBtnAction(key, asyncFn) {
  if (_btnActionPending.has(key)) return;
  _btnActionPending.add(key);
  const timer = setTimeout(() => { _btnActionPending.delete(key); }, _BTN_ACTION_TIMEOUT);
  try {
    await asyncFn();
  } finally {
    clearTimeout(timer);
    _btnActionPending.delete(key);
  }
}

export async function stopVideoJob(jobId) {
  await _guardedBtnAction('stop-' + jobId, async () => {
    try {
      const res = await fetch(`/api/generate/stop/${jobId}`, { method: 'POST' });
      const data = await res.json();
      if (data.ok) {
        showToast(t('toast.job_stopped'), 'info');
        loadVideoJobs();
      } else {
        showToast(data.error || 'Failed to stop job', 'error');
      }
    } catch (e) { showToast('Error: ' + e.message, 'error'); }
  });
}

export async function pauseVideoJob(jobId) {
  await _guardedBtnAction('pause-' + jobId, async () => {
    try {
      const res = await fetch(`/api/generate/pause/${jobId}`, { method: 'POST' });
      const data = await res.json();
      if (data.ok) {
        showToast('Job paused', 'info');
        loadVideoJobs();
      } else {
        showToast(data.error || 'Failed to pause job', 'error');
      }
    } catch (e) { showToast('Error: ' + e.message, 'error'); }
  });
}

export async function unpauseVideoJob(jobId) {
  await _guardedBtnAction('unpause-' + jobId, async () => {
    try {
      const res = await fetch(`/api/generate/unpause/${jobId}`, { method: 'POST' });
      const data = await res.json();
      if (data.ok) {
        showToast('Job resumed', 'success');
        loadVideoJobs();
      } else {
        showToast(data.error || 'Failed to resume job', 'error');
      }
    } catch (e) { showToast('Error: ' + e.message, 'error'); }
  });
}

export async function resumeVideoJob(jobId) {
  await _guardedBtnAction('resume-' + jobId, async () => {
    try {
      const res = await fetch(`/api/generate/resume/${jobId}`, { method: 'POST' });
      const data = await res.json();
      if (data.ok) {
        showToast(t('toast.job_resumed') + ' (PID ' + data.pid + ')', 'success');
        loadVideoJobs();
      } else {
        showToast(data.error || 'Failed to resume job', 'error');
      }
    } catch (e) { showToast('Error: ' + e.message, 'error'); }
  });
}

// ── Regenerate Sheet (single entity image) ──────────────────────────

/** Find the video job matching the currently viewed project/run. */
export function _findCurrentVideoJob() {
  const projectName = currentData?.storyboard_name || '';
  const runId = currentData?.run_id || '';
  if (!projectName) return null;
  return videoJobsData.find(j =>
    j.storyboard_name === projectName
    && (!runId || String(j.run_id || '') === String(runId))
  ) || null;
}

export function _onRegenSheetClick(btn) {
  const entityType = btn.dataset.regenType || '';
  const name = btn.dataset.regenName || '';
  const desc = btn.dataset.regenDesc || '';
  const personality = btn.dataset.regenPersonality || '';

  const card = btn.closest('.browse-char-card, .browse-loc-card');
  if (!card) return;

  if (card.querySelector('.regen-sheet-edit')) return;

  const editDiv = document.createElement('div');
  editDiv.className = 'regen-sheet-edit';
  const personalityRow = (entityType === 'character' && personality)
    ? `<div style="margin-bottom:4px"><label style="font-size:11px;color:var(--text-muted)">${esc(t('video.personality_label'))}</label><input class="regen-personality-input" value="${esc(personality)}" style="width:100%;background:var(--bg-card);color:var(--text-primary);border:1px solid var(--border-subtle);border-radius:6px;padding:4px 8px;font-size:12px;box-sizing:border-box;margin-top:2px" /></div>`
    : '';
  editDiv.innerHTML = `
    <div style="margin-bottom:4px"><label style="font-size:11px;color:var(--text-muted)">${t('video.image_desc_label')}</label></div>
    <textarea class="regen-desc-input">${esc(desc)}</textarea>
    ${personalityRow}
    <div class="regen-sheet-edit-actions">
      <button class="regen-cancel" onclick="_cancelRegenEdit(this)">${t('video.cancel')}</button>
      <button class="regen-submit" onclick="_submitRegenEdit(this, '${esc(entityType)}', '${esc(name)}')">${t('video.generate_btn')}</button>
    </div>
  `;
  card.appendChild(editDiv);

  btn.style.display = 'none';

  const ta = editDiv.querySelector('.regen-desc-input');
  if (ta) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }
}

export function _cancelRegenEdit(cancelBtn) {
  const editDiv = cancelBtn.closest('.regen-sheet-edit');
  const card = editDiv?.parentElement;
  if (editDiv) editDiv.remove();
  if (card) {
    const btn = card.querySelector('.regen-sheet-btn');
    if (btn) btn.style.display = '';
  }
}

export async function _submitRegenEdit(submitBtn, entityType, name) {
  const editDiv = submitBtn.closest('.regen-sheet-edit');
  const card = editDiv?.parentElement;
  const ta = editDiv?.querySelector('.regen-desc-input');
  const personalityInput = editDiv?.querySelector('.regen-personality-input');
  const finalDesc = (ta?.value || '').trim();
  const personality = (personalityInput?.value || '').trim();

  if (!finalDesc) {
    showToast(t('video.desc_empty'), 'error');
    return;
  }

  const currentJob = _findCurrentVideoJob();
  const isCharsheetPhase = !currentJob
    || currentJob.status !== 'running'
    || (['starting', 'charsheet'].includes(currentJob.progress?.stage) && (currentJob.progress?.attempts || 0) === 0);

  if (!isCharsheetPhase) {
    if (!confirm(t('video.confirm_regen_pause'))) return;
  }

  // Pause the create job if it is running
  await _pauseCreateJobIfRunning();

  submitBtn.disabled = true;
  submitBtn.textContent = t('video.generating');

  let loadingEl = null;
  if (card) {
    loadingEl = document.createElement('div');
    loadingEl.className = 'regen-sheet-loading';
    loadingEl.innerHTML = '<div style="width:16px;height:16px;border:2px solid rgba(196,181,253,0.3);border-top-color:#c4b5fd;border-radius:50%;animation:spin 0.8s linear infinite"></div>' + t('video.generating_image');
    card.appendChild(loadingEl);
  }

  const projectName = currentData?.storyboard_name || '';
  const runId = currentData?.run_id || '';
  if (!projectName || !runId) {
    showToast(t('video.no_project_run'), 'error');
    if (loadingEl) loadingEl.remove();
    submitBtn.disabled = false;
    submitBtn.textContent = t('video.generate');
    return;
  }

  try {
    const res = await fetch(`/api/run/${encodeURIComponent(projectName)}/${encodeURIComponent(runId)}/regenerate-sheet`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: name,
        description: finalDesc,
        type: entityType,
        personality: personality,
      }),
    });
    const data = await res.json();
    if (data.ok) {
      let msg = t('video.image_regenerated');
      if (data.stopped_job_id) {
        msg += t('video.stopped_job_hint');
      } else if (data.in_charsheet_phase) {
        msg += t('video.charsheet_phase_hint');
      } else {
        const cj = _findCurrentVideoJob();
        if (cj && ['crashed', 'stopped'].includes(cj.status)) {
          msg += t('video.task_not_running_hint');
        }
      }
      showToast(msg, 'success');
      loadVideoJobs();
    } else if (data.generating) {
      showToast(data.error || t('video.image_generating_wait'), 'warning');
      submitBtn.disabled = false;
      submitBtn.textContent = t('video.generate');
    } else {
      showToast(data.error || t('video.image_gen_failed'), 'error');
      submitBtn.disabled = false;
      submitBtn.textContent = t('video.generate');
    }
  } catch (e) {
    showToast(t('video.image_gen_request_failed') + ': ' + e.message, 'error');
    submitBtn.disabled = false;
    submitBtn.textContent = t('video.generate');
  } finally {
    if (loadingEl) loadingEl.remove();
  }
}

// ── Upload Sheet (replace entity image with user-provided file) ──────────────

/**
 * Pause the active create job if it is currently running.
 * Returns true if a pause was issued, false otherwise.
 */
async function _pauseCreateJobIfRunning() {
  const meta = window.activeCreateJobMeta;
  const jobId = window.createJobId;
  if (!meta || !jobId) return false;
  if (!['running'].includes(meta.status)) return false;
  try {
    const res = await fetch(`/api/create/pause/${jobId}`, { method: 'POST' });
    const data = await res.json();
    if (res.ok && data.ok) {
      meta.status = data.status || 'pausing';
      showToast(t('create.pausing'), 'success');
      return true;
    }
  } catch (_) { /* ignore */ }
  return false;
}

/**
 * Called when the user clicks the "上传图片" button on an entity card.
 * Creates a hidden file input and triggers it.
 */
export function _onUploadSheetClick(btn) {
  const entityType = btn.dataset.uploadType || '';
  const name = btn.dataset.uploadName || '';
  if (!entityType || !name) return;

  // Reuse or create a hidden file input
  let input = document.getElementById('_sheet-upload-input');
  if (!input) {
    input = document.createElement('input');
    input.type = 'file';
    input.id = '_sheet-upload-input';
    input.accept = 'image/*';
    input.style.display = 'none';
    document.body.appendChild(input);
  }

  // Replace any previous listener
  input.onchange = null;
  input.value = '';
  input.onchange = () => _onUploadSheetFile(input, entityType, name);
  input.click();
}

/**
 * Handles the file selection for entity image upload.
 * Pauses the create job if running, then uploads the file.
 */
export async function _onUploadSheetFile(input, entityType, name) {
  const file = input.files?.[0];
  if (!file) return;

  const projectName = currentData?.storyboard_name || '';
  const runId = currentData?.run_id || '';
  const sbPath = currentData?.storyboard_path || '';

  if (!projectName || !runId) {
    showToast(t('video.no_project_run'), 'error');
    return;
  }

  // Pause create job before replacing the image
  await _pauseCreateJobIfRunning();

  // Find the card to show loading state
  const typeToCardClass = { character: 'browse-char-card', location: 'browse-loc-card', prop: 'browse-loc-card' };
  const cardId = `entity-card-${entityType === 'character' ? 'char' : entityType === 'location' ? 'loc' : 'prop'}-${name}`;
  const card = document.getElementById(cardId);

  let loadingEl = null;
  if (card) {
    loadingEl = document.createElement('div');
    loadingEl.className = 'regen-sheet-loading';
    loadingEl.innerHTML = '<div style="width:16px;height:16px;border:2px solid rgba(196,181,253,0.3);border-top-color:#c4b5fd;border-radius:50%;animation:spin 0.8s linear infinite"></div>' + t('misc.uploading');
    card.appendChild(loadingEl);
    const uploadBtn = card.querySelector('.upload-sheet-btn');
    if (uploadBtn) uploadBtn.disabled = true;
  }

  try {
    const form = new FormData();
    form.append('file', file);
    form.append('storyboard_path', sbPath);
    form.append('target', entityType);
    form.append('name', name);
    form.append('project_name', projectName);
    form.append('run_id', runId);

    const res = await fetch('/api/upload-image', { method: 'POST', body: form });
    const data = await res.json();

    if (!res.ok || !data.ok) {
      throw new Error(data.error || t('misc.upload_failed'));
    }

    showToast(t('monitor.upload_image') + ' ✓', 'success');

    // Update the avatar image in the card immediately
    if (card && data.image_path) {
      const imgEl = card.querySelector('img');
      const newSrc = `/asset?path=${encodeURIComponent(data.image_path)}`;
      if (imgEl) {
        imgEl.src = newSrc + '&t=' + Date.now();
      } else {
        // No avatar yet — create one
        const avatarDiv = document.createElement('div');
        avatarDiv.className = entityType === 'character' ? 'browse-char-avatar' : 'browse-loc-avatar';
        avatarDiv.onclick = () => window.showImage?.(newSrc);
        avatarDiv.innerHTML = `<img src="${newSrc}" loading="lazy"/>`;
        card.insertBefore(avatarDiv, card.firstChild);
      }
    }

    // Reload video jobs to refresh the full browse pane
    loadVideoJobs();
  } catch (e) {
    showToast(t('misc.upload_failed') + ': ' + e.message, 'error');
  } finally {
    if (loadingEl) loadingEl.remove();
    if (card) {
      const uploadBtn = card.querySelector('.upload-sheet-btn');
      if (uploadBtn) uploadBtn.disabled = false;
    }
    input.value = '';
  }
}

export async function deleteVideoJob(jobId) {
  if (!confirm(t('video.confirm_delete_job'))) {
    return;
  }
  try {
    const res = await fetch(`/api/generate/delete/${jobId}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.ok) {
      showToast(t('toast.job_deleted'), 'success');
      if (selectedVideoJobId === jobId) {
        setSelectedVideoJobId(null);
      }
      loadVideoJobs();
    } else {
      showToast(data.error || 'Failed to delete job', 'error');
    }
  } catch (e) { showToast('Error: ' + e.message, 'error'); }
}

export async function refreshCurrentRunData() {
  const projectName = currentData?.storyboard_name;
  const runId = currentData?.run_id;
  if (!projectName || !runId) return;

  try {
    const res = await fetch(`/api/run-detail/${encodeURIComponent(projectName)}/${encodeURIComponent(runId)}`);
    if (!res.ok) return;
    const detail = await res.json();
    window._applyMonitorData({
      storyboard: detail.storyboard,
      checkpoint: detail.checkpoint,
      media: detail.media,
      regen_requests: detail.regen_requests || [],
      run_id: detail.run_id,
      storyboard_name: detail.project_name,
      all_runs: currentData?.all_runs || [],
    });
    if (document.getElementById('view-monitor').classList.contains('active')) {
      const pane = document.getElementById('monitor-detail-pane');
      if (pane && window.shouldRefreshMonitorBrowseInPlace(pane)) {
        window._renderMonitorBrowse(pane);
      } else {
        window.renderMonitor();
      }
      const focus = window._focusAttemptAfterRefresh;
      if (focus && Number(focus.unitId) >= 0 && Number(focus.attemptId) > 0) {
        const uid = `unit-${focus.unitId}`;
        const info = window.unitDataMap?.[uid];
        if (info) {
          const idx = window.getDisplayAttempts(info.unit).findIndex(a => Number(a?.attempt_id) === Number(focus.attemptId));
          if (idx >= 0) {
            if (window.monitorBrowseMode) {
              const browseIdx = (window._browseUnitInfos || []).findIndex(x => Number(x?.unit?.unit_id) === Number(focus.unitId));
              if (browseIdx >= 0) {
                window.browseSelectUnit(browseIdx);
                window.browsePlayAttempt(browseIdx, idx);
              }
            } else {
              window.openUnitModal(uid);
              window.switchModalAttempt(uid, idx);
            }
          }
        }
        window._focusAttemptAfterRefresh = null;
      }
    }
  } catch (e) {
    console.error('Failed to refresh current run data', e);
  }
}

export async function refreshRepositoryData() {
  try {
    const { response, data } = await apiFetch('/api/repository');
    if (!response.ok) throw new Error(data.error || t('video.load_repo_failed'));
    setRepoData(data);
  } catch (e) {
    console.error('Failed to refresh repository data', e);
  }
}

export function setButtonLoading(button, loading, loadingText = '') {
  if (!button) return;
  if (!button.dataset.originalText) {
    button.dataset.originalText = button.textContent;
  }
  button.classList.toggle('loading', loading);
  button.disabled = loading;
  button.textContent = loading ? loadingText : button.dataset.originalText;
}

export function syncConcatModeControls() {
  document.querySelectorAll('.concat-mode-select').forEach(el => {
    el.value = concatMode;
    el.disabled = monitorActionState.concat || monitorActionState.deleteFinal;
  });
  // sync custom dropdown label + active state
  document.querySelectorAll('.concat-mode-picker').forEach(picker => {
    const label = picker.querySelector('.concat-mode-label');
    if (label) {
      label.textContent = concatMode === 'crossfade' ? (window._t?.('concat.mode.crossfade') || 'Cross dissolve') : (window._t?.('concat.mode.hard') || '拼接模式');
    }
    picker.querySelectorAll('.sd-model-option').forEach(opt => {
      opt.classList.toggle('active', opt.dataset.value === concatMode);
    });
    const trigger = picker.querySelector('.concat-mode-trigger');
    if (trigger) trigger.disabled = monitorActionState.concat || monitorActionState.deleteFinal;
  });
  document.querySelectorAll('.concat-fade-controls').forEach(el => {
    el.style.display = concatMode === 'crossfade' ? 'flex' : 'none';
  });
  document.querySelectorAll('.concat-fade-input').forEach(el => {
    el.value = String(concatFadeSeconds);
    const locked = monitorActionState.concat || monitorActionState.deleteFinal;
    el.disabled = locked || concatMode !== 'crossfade';
  });
  document.querySelectorAll('.concat-fade-preset-btn').forEach(el => {
    const locked = monitorActionState.concat || monitorActionState.deleteFinal;
    el.disabled = locked || concatMode !== 'crossfade';
  });
}

export function onConcatModeChange(value) {
  const next = String(value || '').trim().toLowerCase();
  setConcatMode(next === 'crossfade' ? 'crossfade' : 'hard');
  localStorage.setItem('concat_mode', concatMode);
  syncConcatModeControls();
}

export function onConcatFadeChange(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return;
  const clamped = Math.max(0.1, Math.min(3.0, parsed));
  setConcatFadeSeconds(Number(clamped.toFixed(2)));
  localStorage.setItem('concat_fade_seconds', String(concatFadeSeconds));
  syncConcatModeControls();
}

export function onConcatFadePreset(value) {
  onConcatFadeChange(value);
}

export function syncMonitorActionButtons() {
  const concatButtons = [
    document.getElementById('monitor-concat-btn'),
    document.getElementById('final-reconcat-btn'),
  ];
  const deleteButtons = [
    document.getElementById('final-delete-btn'),
  ];
  const busy = monitorActionState.concat || monitorActionState.deleteFinal;

  concatButtons.forEach(btn => {
    setButtonLoading(btn, monitorActionState.concat, t('video.concatenating'));
    if (btn && !monitorActionState.concat) btn.disabled = busy;
  });
  deleteButtons.forEach(btn => {
    setButtonLoading(btn, monitorActionState.deleteFinal, t('video.deleting'));
    if (btn && !monitorActionState.deleteFinal) btn.disabled = busy;
  });
  syncConcatModeControls();
}

export async function concatCurrentRun(event) {
  event?.stopPropagation?.();
  if (monitorActionState.concat) return;

  const projectName = currentData?.storyboard_name;
  const runId = currentData?.run_id;
  if (!projectName || !runId) {
    showToast(t('video.no_run_concat'), 'error');
    return;
  }

  monitorActionState.concat = true;
  syncMonitorActionButtons();

  try {
    showToast(t('toast.concat_started'), 'info');
    const modeLabel = concatMode === 'crossfade' ? t('concat.mode.crossfade') : t('concat.mode.hard');
    const fadeSeconds = concatMode === 'crossfade' ? concatFadeSeconds : 0;
    const res = await fetch(`/api/run/${encodeURIComponent(projectName)}/${encodeURIComponent(runId)}/concat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: concatMode, fade_seconds: fadeSeconds }),
    });
    const data = await res.json();
    if (data.ok) {
      await Promise.all([refreshCurrentRunData(), refreshRepositoryData(), loadVideoJobs()]);
      const fadeText = data.concat_mode === 'crossfade' ? ` · ${Number(data.fade_seconds || fadeSeconds).toFixed(1)}s` : '';
      showToast(`${t('toast.concat_done')} (${data.clip_count || 0} clips · ${modeLabel}${fadeText})`, 'success');
      window.browseSelectFinal();
    } else {
      showToast(data.error || 'Failed to create final cut', 'error');
    }
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  } finally {
    monitorActionState.concat = false;
    syncMonitorActionButtons();
  }
}

export async function deleteCurrentFinal(event) {
  event?.stopPropagation?.();
  if (monitorActionState.deleteFinal) return;

  const projectName = currentData?.storyboard_name;
  const runId = currentData?.run_id;
  if (!projectName || !runId) {
    showToast(t('video.no_final_delete'), 'error');
    return;
  }
  if (!confirm(t('video.confirm_delete_final'))) {
    return;
  }

  monitorActionState.deleteFinal = true;
  syncMonitorActionButtons();

  try {
    const res = await fetch(`/api/run/${encodeURIComponent(projectName)}/${encodeURIComponent(runId)}/final`, {
      method: 'DELETE'
    });
    const data = await res.json();
    if (data.ok) {
      await Promise.all([refreshCurrentRunData(), refreshRepositoryData(), loadVideoJobs()]);
      showToast(t('toast.final_deleted'), 'success');
      window.browseSelectedUnit = 0;
      window.renderMonitor();
    } else {
      showToast(data.error || 'Failed to delete final cut', 'error');
    }
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  } finally {
    monitorActionState.deleteFinal = false;
    syncMonitorActionButtons();
  }
}

// ── Expose to window for cross-module / inline-HTML access ──────────
window.loadBackendSetting = loadBackendSetting;
window.syncBackendSelects = syncBackendSelects;
window.setSeeddanceBackend = setSeeddanceBackend;
window.openSegmentModal = openSegmentModal;
window.closeSegmentModal = closeSegmentModal;
window.navigateSegment = navigateSegment;
window.renderSegmentModal = renderSegmentModal;
window.loadVideoJobs = loadVideoJobs;
window.renderVideoJobsPanel = renderVideoJobsPanel;
window.selectVideoJob = selectVideoJob;
window._guardedBtnAction = _guardedBtnAction;
window.stopVideoJob = stopVideoJob;
window.pauseVideoJob = pauseVideoJob;
window.unpauseVideoJob = unpauseVideoJob;
window.resumeVideoJob = resumeVideoJob;
window._findCurrentVideoJob = _findCurrentVideoJob;
window._onRegenSheetClick = _onRegenSheetClick;
window._cancelRegenEdit = _cancelRegenEdit;
window._submitRegenEdit = _submitRegenEdit;
window._onUploadSheetClick = _onUploadSheetClick;
window._onUploadSheetFile = _onUploadSheetFile;
window.deleteVideoJob = deleteVideoJob;
window.refreshCurrentRunData = refreshCurrentRunData;
window.refreshRepositoryData = refreshRepositoryData;
window.setButtonLoading = setButtonLoading;
window.syncConcatModeControls = syncConcatModeControls;
window.onConcatModeChange = onConcatModeChange;
window.onConcatFadeChange = onConcatFadeChange;
window.onConcatFadePreset = onConcatFadePreset;
window.syncMonitorActionButtons = syncMonitorActionButtons;
window.concatCurrentRun = concatCurrentRun;
window.deleteCurrentFinal = deleteCurrentFinal;
