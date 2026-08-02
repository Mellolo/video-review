/**
 * unit-modal.js — Unit Detail Modal logic
 * Extracted from index.html lines 8092-8478 as ES module.
 */

import {
  currentData,
  videoJobsData,
  selectedVideoJobId,
  setSelectedVideoJobId,
  setMonitorBrowseMode,
} from './state.js';

import { esc, showToast, parseApiJsonSafely } from './utils.js';
import { t } from './i18n.js';

import {
  unitDataMap,
  getUnitStatus,
  getDisplayAttempts,
  getActiveUnitRegenRequest,
  isAttemptPlaceholder,
  getAttemptVisualStatus,
  isEditableDraftAttempt,
  getUnitStatusLabel,
  switchAttemptVideo,
  buildAttemptPlaceholderMessage,
  buildPromptEditorBlock,
  mountPromptEditorForUnit,
  buildFullCritiqueHTML,
  renderAttemptMetaBadges,
  getAttemptMaxAttempts,
  getAttemptPrompt,
  getAttemptRefSource,
  getDisplayAttemptIndex,
  getAttemptImageRefAssets,
  getPromptEditorIds,
  serializePromptEditorForSubmit,
  serializePromptEditorStateToSubmit,
  getUnitRegenRequests,
} from './unit-helpers.js';

import { hidePromptAssetMenu } from './assets.js';

// ── Module-level state ──────────────────────────────────────────
export let currentModalUid = null;
export let currentModalAttemptIdx = -1;
export let unitUidList = [];

export function setCurrentModalUid(v) { currentModalUid = v; }
export function setCurrentModalAttemptIdx(v) { currentModalAttemptIdx = v; }
export function setUnitUidList(v) { unitUidList = v; }

// ── openUnitModal ───────────────────────────────────────────────

export function openUnitModal(uid) {
  const info = unitDataMap[uid];
  if (!info) return;
  currentModalUid = uid;

  populateModal(uid);
  document.getElementById('unit-modal').classList.add('show');
}

// ── buildUnitActionControls ─────────────────────────────────────

export function buildUnitActionControls(u, viewingAttempt, mode = 'modal') {
  const status = getUnitStatus(u);
  const activeReq = getActiveUnitRegenRequest(u.unit_id);
  const isBusy = status === 'in_progress' || status === 'critiquing' || status === 'queued';
  const disabledReason = status === 'queued' ? t('unit.queued_hint') : t('unit.generating_hint');
  const lockedLabel = u.final_attempt_id != null
    ? `<span class="final-attempt-badge">最终剪辑 #${u.final_attempt_id}${u.final_attempt_locked ? ' · 已锁定' : ''}</span>`
    : '';
  // 只要有任何 job 处于非终止状态，就视为活跃
  const _nonTerminalStatuses = ['queued', 'running', 'paused', 'stopping', 'pausing'];
  const _jobIsActive = (videoJobsData || []).some(j => _nonTerminalStatuses.includes(j.status));
  // 当前 unit 已定版（locked）时，即使 job 仍在运行也允许显示继续生成按钮
  const _unitFinalized = u.final_attempt_locked === true;
  // 所有 units 都已生成完视频时，即使队列不为空也允许继续生成（用户希望编辑多个窗口的 prompt）
  const _allUnitsHaveVideo = (() => {
    const units = currentData?.checkpoint?.script?.work_units || [];
    if (!units.length) return false;
    return units.every(unit => unit.is_completed && (unit.attempts || []).some(a => !!a.output_path));
  })();
  let regenerateLabel, regenerateHandler;
  if (activeReq?.status === 'draft') {
    regenerateLabel = t('unit.edit_draft');
    regenerateHandler = `focusUnitDraft('${u.unit_id}','${mode}')`;
  } else if (_jobIsActive && !_allUnitsHaveVideo && ['queued', 'consumed'].includes(activeReq?.status)) {
    regenerateLabel = t('unit.jumped_queue');
    regenerateHandler = `submitUnitRegenerate('${u.unit_id}','${mode}')`;
  } else {
    regenerateLabel = t('unit.continue_gen');
    regenerateHandler = `submitUnitRegenerate('${u.unit_id}','${mode}')`;
  }
  // job 已完成、当前 unit 已定版、或所有 units 都已有视频时，显示「继续生成」按钮
  const _showRegenerate = !_jobIsActive || _unitFinalized || _allUnitsHaveVideo;
  const regenerateButton = `<button class="vj-btn resume" onclick="${regenerateHandler}" ${isBusy ? 'disabled' : ''}>${regenerateLabel}</button>`;
  const regenerateControl = isBusy
    ? `<span class="unit-action-tooltip" title="${disabledReason}">${regenerateButton}</span>`
    : regenerateButton;
  return `
    ${lockedLabel}
    ${_showRegenerate ? regenerateControl : ''}
    ${viewingAttempt?.output_path ? `<button class="vj-btn concat" onclick="selectUnitFinalAttempt('${u.unit_id}','${viewingAttempt.attempt_id}')">设为最终版本</button>` : ''}
  `;
}

// ── populateModal ───────────────────────────────────────────────

export function populateModal(uid) {
  const info = unitDataMap[uid];
  if (!info) return;
  const u = info.unit;

  document.getElementById('unit-modal-id').textContent = `Unit #${u.unit_id}`;
  const uidIdx = unitUidList.indexOf(uid);
  document.getElementById('unit-modal-nav-label').textContent = unitUidList.length > 1 ? `${uidIdx + 1} / ${unitUidList.length}` : '';
  const statusEl = document.getElementById('unit-modal-status');
  statusEl.textContent = getUnitStatusLabel(info.status);
  statusEl.className = `unit-status ${info.status}`;

  // Nav button states
  document.getElementById('unit-nav-prev').disabled = uidIdx <= 0;
  document.getElementById('unit-nav-next').disabled = uidIdx >= unitUidList.length - 1;

  // Find which attempt is currently being viewed in card
  const displayAttempts = info.displayAttempts || getDisplayAttempts(u);
  const card = document.getElementById(uid + '-card');
  const activeIdx = card ? parseInt(card.querySelector('.attempt-dot.viewing')?.dataset?.idx ?? info.bestIdx, 10) : info.bestIdx;
  const viewingAttempt = displayAttempts?.[activeIdx];

  // Render attempt dots in modal
  const attemptDotsHTML = displayAttempts.map((a, i) => {
      const isViewing = i === activeIdx;
      const isFinal = u.final_attempt_id != null && Number(u.final_attempt_id) === Number(a.attempt_id);
      return `<div class="attempt-dot ${getAttemptVisualStatus(a)}${isViewing?' viewing':''}${isFinal?' final-picked':''}"
        data-idx="${i}"
        style="cursor:pointer"
        onclick="switchModalAttempt('${uid}',${i})"
        title="Attempt ${a.attempt_id} — ${a.metadata?.regen_status || a.status}${isFinal ? ' — final selected' : ''}">${a.attempt_id}</div>`;
    }).join('');
  const bar = document.getElementById('unit-modal-attempts');
  bar.innerHTML = `<div class="unit-attempt-toolbar"><div class="unit-attempt-toolbar-left"><span class="unit-attempt-toolbar-label">${t('misc.attempts')}</span>${attemptDotsHTML}</div><div class="unit-attempt-toolbar-right">${buildUnitActionControls(u, viewingAttempt, 'modal')}</div></div>`;

  updateModalAttempt(uid, activeIdx);

  // Reset tab to prompt
  switchModalTab('prompt');
}

// ── navigateUnit ────────────────────────────────────────────────

export function navigateUnit(dir) {
  const idx = unitUidList.indexOf(currentModalUid);
  const next = idx + dir;
  if (next < 0 || next >= unitUidList.length) return;
  const vid = document.getElementById('unit-modal-video');
  vid.pause();
  currentModalUid = unitUidList[next];
  populateModal(currentModalUid);
}

// ── updateModalAttempt ──────────────────────────────────────────

export function updateModalAttempt(uid, idx) {
  const info = unitDataMap[uid];
  if (!info) return;
  currentModalAttemptIdx = idx;
  info.bestIdx = idx;
  const u = info.unit;
  const attempts = info.displayAttempts || getDisplayAttempts(u);
  const attempt = attempts?.[idx];

  // Video
  const vid = document.getElementById('unit-modal-video');
  const videoSrc = info.attemptVideos[idx];
  const videoEmpty = document.getElementById('unit-modal-video-empty');
  if (videoSrc) {
    vid.src = videoSrc;
    vid.style.display = '';
    if (videoEmpty) videoEmpty.style.display = 'none';
  } else {
    vid.removeAttribute('src');
    vid.style.display = 'none';
    if (videoEmpty) {
      videoEmpty.style.display = 'flex';
      videoEmpty.textContent = buildAttemptPlaceholderMessage(u, attempt, info.status);
    }
  }

  const mediaActionsSlot = document.getElementById('unit-modal-media-actions-slot');
  if (mediaActionsSlot) {
    mediaActionsSlot.innerHTML = '';
  }

  // Prompt
  const modalActions = document.getElementById('modal-prompt-actions');
  if (modalActions) {
    modalActions.innerHTML = buildPromptEditorBlock(u, attempt, 'modal');
    mountPromptEditorForUnit(u, idx, 'modal');
  }

  // Critique
  document.getElementById('modal-critique-content').innerHTML = buildFullCritiqueHTML(attempt);

  // Info
  document.getElementById('modal-info-content').innerHTML = `
    <div class="modal-attempt-meta">
      <span><strong>Group:</strong> ${esc(u.group_name||'—')}</span>
      <span><strong>Scenes:</strong> ${(u.scene_numbers||[]).join(', ')}</span>
      <span><strong>Duration:</strong> ${u.duration_seconds||0}s</span>
      <span><strong>Total Attempts:</strong> ${attempts.length}</span>
      <span><strong>Pending Extra Attempts:</strong> ${Number(u.pending_extra_attempts || 0)}</span>
      ${attempt ? `<span><strong>Viewing Attempt:</strong> #${attempt.attempt_id}</span>
      ${renderAttemptMetaBadges(u, attempt)}
      ${getAttemptMaxAttempts(u, idx) ? `<span><strong>Max Attempts:</strong> ${getAttemptMaxAttempts(u, idx)}</span>` : ''}
      <span><strong>Tool:</strong> ${esc(attempt.tool_used||'—')}</span>
      <span><strong>Status:</strong> ${attempt.status||'—'}</span>
      ${attempt.error_message ? `<span style="color:var(--error)"><strong>Error:</strong> ${esc(attempt.error_message)}</span>` : ''}` : ''}
      ${u.final_video_path ? `<span style="color:var(--success)"><strong>Final:</strong> ${u.final_video_path.split('/').pop()}</span>` : ''}
      ${u.final_attempt_id != null ? `<span style="color:var(--warning)"><strong>Final Attempt:</strong> #${u.final_attempt_id}${u.final_attempt_locked ? ' (locked)' : ''}</span>` : ''}
    </div>`;

  // Update modal attempt dots
  document.querySelectorAll('#unit-modal-attempts .attempt-dot').forEach(d => {
    const dotIdx = parseInt(d.dataset.idx || '-1', 10);
    d.classList.toggle('viewing', dotIdx === idx);
    const dotAttempt = attempts?.[dotIdx];
    d.classList.toggle('final-picked', Number(dotAttempt?.attempt_id) === Number(u.final_attempt_id));
  });
}

// ── switchModalAttempt ──────────────────────────────────────────

export function switchModalAttempt(uid, idx) {
  switchAttemptVideo(uid, idx);
  updateModalAttempt(uid, idx);
}

// ── switchModalTab ──────────────────────────────────────────────

export function switchModalTab(panel) {
  document.querySelectorAll('.unit-modal-tab').forEach(t => t.classList.toggle('active', t.dataset.panel === panel));
  document.querySelectorAll('.unit-modal-panel').forEach(p => p.classList.toggle('active', p.id === `modal-panel-${panel}`));
}

// ── focusUnitDraft ──────────────────────────────────────────────

export function focusUnitDraft(unitId, mode = 'modal') {
  const uid = `unit-${unitId}`;
  const focusDraft = () => {
    const info = mode === 'browse'
      ? (window._browseUnitInfos || []).find(x => Number(x?.unit?.unit_id) === Number(unitId)) || null
      : unitDataMap[uid] || null;
    if (!info) return false;
    const idx = getDisplayAttempts(info.unit).findIndex(a => isEditableDraftAttempt(a));
    if (idx < 0) return false;
    if (mode === 'browse') {
      const browseIdx = (window._browseUnitInfos || []).findIndex(x => Number(x?.unit?.unit_id) === Number(unitId));
      if (browseIdx < 0) return false;
      window.browsePlayAttempt(browseIdx, idx);
    } else {
      openUnitModal(uid);
      switchModalAttempt(uid, idx);
    }
    return true;
  };
  if (focusDraft()) return;
  showToast(t('unit.no_editable_draft'), 'info');
}

// ── submitUnitRegenerate ────────────────────────────────────────

// ── saveUnitDraft ───────────────────────────────────────────────
// 专门用于"保存草稿"按钮：直接从编辑器序列化内容并更新已有草稿，
// 不依赖 selectedAttempt 状态判断，不受 unit 生成状态拦截。

export async function saveUnitDraft(unitId, mode = 'modal') {
  if (!currentData?.storyboard_name || !currentData?.run_id) {
    showToast(t('unit.no_operable_run'), 'error');
    return;
  }

  const uid = `unit-${unitId}`;
  const unitInfo = mode === 'browse'
    ? (window._browseUnitInfos || []).find(x => Number(x?.unit?.unit_id) === Number(unitId)) || null
    : unitDataMap[uid] || null;

  if (!unitInfo) {
    showToast(t('unit.no_operable_run'), 'error');
    return;
  }

  const activeReq = getActiveUnitRegenRequest(unitId);
  if (!activeReq || activeReq.status !== 'draft') {
    showToast(t('unit.no_editable_draft'), 'info');
    return;
  }

  const { editorId } = getPromptEditorIds(unitId, mode);
  let editorState = window.getPromptEditorState ? window.getPromptEditorState(editorId) : null;

  // state 가 없으면 draft attempt 를 찾아 편집기를 다시 마운트
  if (!editorState) {
    const displayAttempts = getDisplayAttempts(unitInfo.unit);
    const draftIdx = displayAttempts.findIndex(a => isEditableDraftAttempt(a));
    if (draftIdx >= 0) mountPromptEditorForUnit(unitInfo.unit, draftIdx, mode);
    editorState = window.getPromptEditorState ? window.getPromptEditorState(editorId) : null;
  }

  // DOM sync 없이 현재 state 를 직접 직렬화 (Grammarly 등의 DOM 수정 방지)
  const { prompt: manualPrompt, assets: manualImageRefAssets } = editorState
    ? serializePromptEditorStateToSubmit(editorState)
    : serializePromptEditorForSubmit(editorId);
  const sourcePrompt = (activeReq.source_prompt || '').trim();
  const createdFromAttemptId = activeReq.created_from_attempt_id ?? null;

  try {
    const res = await fetch(`/api/run/${encodeURIComponent(currentData.storyboard_name)}/${encodeURIComponent(currentData.run_id)}/unit/${encodeURIComponent(unitId)}/regenerate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        extra_attempts: activeReq.extra_attempts || 1,
        manual_prompt: manualPrompt,
        manual_image_ref_assets: manualImageRefAssets,
        source_prompt: sourcePrompt,
        created_from_attempt_id: createdFromAttemptId,
      }),
    });
    const data = await parseApiJsonSafely(res);
    if (!res.ok || !data.ok) throw new Error(data.error || t('unit.create_draft_failed'));
    showToast(`Unit #${unitId} 草稿已保存`, 'success');
    await window.refreshCurrentRunData();
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  }
}

export async function submitUnitRegenerate(unitId, mode = 'modal') {
  const extraAttempts = 1;

  let sourcePrompt = '';
  let createdFromAttemptId = null;
  let unitInfo = null;
  let selectedIdx = -1;
  let selectedAttempt = null;
  const activeReq = getActiveUnitRegenRequest(unitId);

  if (mode === 'browse') {
    unitInfo = (window._browseUnitInfos || []).find(x => Number(x?.unit?.unit_id) === Number(unitId)) || null;
    if (unitInfo) {
      selectedIdx = unitInfo.bestIdx;
      if (['in_progress', 'critiquing', 'queued'].includes(getUnitStatus(unitInfo.unit))) {
        showToast(t('unit.generating_or_queued'), 'info');
        return;
      }
    }
  } else {
    unitInfo = unitDataMap[`unit-${unitId}`] || null;
    if (unitInfo) {
      selectedIdx = Number.isInteger(currentModalAttemptIdx) && currentModalAttemptIdx >= 0 ? currentModalAttemptIdx : unitInfo.bestIdx;
      if (['in_progress', 'critiquing', 'queued'].includes(getUnitStatus(unitInfo.unit))) {
        showToast(t('unit.generating_or_queued'), 'info');
        return;
      }
    }
  }

  if (unitInfo) {
    const attempts = unitInfo.displayAttempts || getDisplayAttempts(unitInfo.unit);
    selectedAttempt = attempts?.[selectedIdx] || null;
    const sourceAttempt = isEditableDraftAttempt(selectedAttempt)
      ? getAttemptRefSource(unitInfo.unit, selectedAttempt)
      : selectedAttempt;
    const sourceAttemptIdx = sourceAttempt ? getDisplayAttemptIndex(unitInfo.unit, sourceAttempt) : selectedIdx;
    sourcePrompt = (
      isEditableDraftAttempt(selectedAttempt)
        ? (activeReq?.source_prompt || getAttemptPrompt(unitInfo.unit, sourceAttemptIdx) || '')
        : (getAttemptPrompt(unitInfo.unit, selectedIdx) || '')
    ).trim();
    createdFromAttemptId = isEditableDraftAttempt(selectedAttempt)
      ? (selectedAttempt?.created_from_attempt_id ?? selectedAttempt?.metadata?.created_from_attempt_id ?? sourceAttempt?.attempt_id ?? null)
      : (selectedAttempt?.attempt_id ?? null);
  }

  const { editorId } = getPromptEditorIds(unitId, mode);
  const currentState = window.getPromptEditorState ? window.getPromptEditorState(editorId) : null;
  const submitPayload = isEditableDraftAttempt(selectedAttempt)
    ? (currentState ? serializePromptEditorStateToSubmit(currentState) : serializePromptEditorForSubmit(editorId))
    : {
        prompt: sourcePrompt,
        assets: getAttemptImageRefAssets(unitInfo?.unit, selectedIdx),
      };
  const manualPrompt = submitPayload.prompt;
  const manualImageRefAssets = submitPayload.assets;

  if (!currentData?.storyboard_name || !currentData?.run_id) {
    showToast(t('unit.no_operable_run'), 'error');
    return;
  }

  try {
    const res = await fetch(`/api/run/${encodeURIComponent(currentData.storyboard_name)}/${encodeURIComponent(currentData.run_id)}/unit/${encodeURIComponent(unitId)}/regenerate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        extra_attempts: extraAttempts,
        manual_prompt: manualPrompt,
        manual_image_ref_assets: manualImageRefAssets,
        source_prompt: sourcePrompt,
        created_from_attempt_id: createdFromAttemptId,
      }),
    });
    const data = await parseApiJsonSafely(res);
    if (!res.ok || !data.ok) throw new Error(data.error || t('unit.create_draft_failed'));
    const currentAttempts = unitInfo?.displayAttempts || (unitInfo ? getDisplayAttempts(unitInfo.unit) : []);
    const fallbackAttemptId = Math.max(0, ...currentAttempts.map(a => Number(a?.attempt_id || 0)), Number(createdFromAttemptId || 0)) + 1;
    const placeholderAttemptId = Number(data?.request?.placeholder_attempt_id || fallbackAttemptId || 0);
    if (placeholderAttemptId > 0) {
      window._focusAttemptAfterRefresh = { unitId: Number(unitId), attemptId: placeholderAttemptId, mode };
    }
    showToast(`Unit #${unitId} 已创建新的继续生成 attempt`, 'success');
    await window.refreshCurrentRunData();
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  }
}

// ── startUnitRegenerate ─────────────────────────────────────────

export async function startUnitRegenerate(unitId, mode = 'modal') {
  const extraAttempts = 1;

  if (!currentData?.storyboard_name || !currentData?.run_id) {
    showToast(t('unit.no_operable_run'), 'error');
    return;
  }

  const getLatestRegenRequestForStart = () => {
    const requests = getUnitRegenRequests(unitId)
      .filter(req => ['draft', 'queued'].includes(req?.status))
      .sort((a, b) => Number(b?.updated_at || b?.created_at || 0) - Number(a?.updated_at || a?.created_at || 0));
    return requests[0] || null;
  };

  try {
    let latestReq = getLatestRegenRequestForStart();
    if (!latestReq || latestReq.status !== 'draft') {
      await submitUnitRegenerate(unitId, mode);
      await window.refreshCurrentRunData();
      latestReq = getLatestRegenRequestForStart();
    }
    if (!latestReq) throw new Error(t('unit.create_draft_first'));
    if (latestReq.status !== 'draft') throw new Error(t('unit.no_submittable_draft'));

    const { editorId } = getPromptEditorIds(unitId, mode);
    const currentState = window.getPromptEditorState ? window.getPromptEditorState(editorId) : null;
    const submitPayload = currentState
      ? serializePromptEditorStateToSubmit(currentState)
      : serializePromptEditorForSubmit(editorId);
    const manualPrompt = submitPayload.prompt;
    const manualImageRefAssets = submitPayload.assets;

    const res = await fetch(`/api/run/${encodeURIComponent(currentData.storyboard_name)}/${encodeURIComponent(currentData.run_id)}/unit/${encodeURIComponent(unitId)}/regenerate/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        request_id: latestReq.request_id,
        extra_attempts: extraAttempts,
        manual_prompt: manualPrompt,
        manual_image_ref_assets: manualImageRefAssets,
        source_prompt: latestReq.source_prompt || '',
      }),
    });
    const data = await parseApiJsonSafely(res);
    if (!res.ok || !data.ok) throw new Error(data.error || t('unit.regen_failed'));
    if (data.job?.job_id) {
      setMonitorBrowseMode(null);
      setSelectedVideoJobId(data.job.job_id);
      await window.loadVideoJobs();
    }
    showToast(`Unit #${unitId} 已插队进入生成队列`, 'success');
    await window.refreshCurrentRunData();
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  }
}

// ── selectUnitFinalAttempt ──────────────────────────────────────

export async function selectUnitFinalAttempt(unitId, attemptId) {
  if (!currentData?.storyboard_name || !currentData?.run_id) {
    showToast(t('unit.no_operable_run'), 'error');
    return;
  }
  try {
    const res = await fetch(`/api/run/${encodeURIComponent(currentData.storyboard_name)}/${encodeURIComponent(currentData.run_id)}/unit/${encodeURIComponent(unitId)}/final-attempt`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ attempt_id: attemptId }),
    });
    const data = await parseApiJsonSafely(res);
    if (!res.ok || !data.ok) throw new Error(data.error || t('unit.set_final_failed'));
    showToast(`Unit #${unitId} 已锁定 Attempt #${attemptId}`, 'success');
    await window.refreshCurrentRunData();
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  }
}

// ── closeUnitModal ──────────────────────────────────────────────

export function closeUnitModal() {
  document.getElementById('unit-modal').classList.remove('show');
  hidePromptAssetMenu();
  const vid = document.getElementById('unit-modal-video');
  vid.pause(); vid.removeAttribute('src');
  const empty = document.getElementById('unit-modal-video-empty');
  if (empty) {
    empty.style.display = 'none';
    empty.textContent = '';
  }
  const mediaActionsSlot = document.getElementById('unit-modal-media-actions-slot');
  if (mediaActionsSlot) mediaActionsSlot.innerHTML = '';
  currentModalUid = null;
}
