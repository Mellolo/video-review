/**
 * monitor.js — Monitor View Rendering (ES module)
 * Extracted from index.html lines 5290-6371
 */

import {
  currentData, videoJobsData, selectedVideoJobId, setSelectedVideoJobId,
  monitorBrowseMode, setMonitorBrowseMode,
  activeProject, assetData,
  monitorJobsPanelCollapsed, setMonitorJobsPanelCollapsed,
  monitorActionState,
  concatMode, concatFadeSeconds,
  repoData,
  _browseLiveSigCache, set_browseLiveSigCache,
  clearDebouncedBrowsePaneRefresh,
  computeBrowseRefreshSigsFromState,
} from './state.js';

import { esc, apiFetch, showToast } from './utils.js';
import { t, getProjectDisplayName, getCurrentLang } from './i18n.js';

import {
  getUnitStatus, getDisplayAttempts, getPreferredAttemptIndex,
  getBestAttemptIndex, unitHasCheckpointVideoOutput,
  getAttemptVisualStatus, isAttemptPlaceholder,
  isDraftRegenAttempt, isQueuedRegenAttempt,
  buildAttemptPlaceholderMessage,
  renderAttemptVideoStage, renderAttemptMetaBadges,
  getUnitStatusLabel,
  getAttemptMaxAttempts,
  setUnitDataMap, resetUnitDataMap,
  unitDataMap,
  isEditableDraftAttempt,
} from './unit-helpers.js';

import {
  getAttemptPrompt, getAttemptImageRefMap, getAttemptImageRefAssets,
  buildPromptEditorBlock,
  mountPromptEditorForUnit,
  buildPromptAssetCandidates,
  renderPromptWithRefs,
} from './prompt-editor.js';

// ── Cross-module refs (still in index.html, accessed via window) ──
// window.buildFullCritiqueHTML, window.buildCritiqueHTML, window.buildUnitActionControls
// window.syncConcatModeControls, window.syncMonitorActionButtons
// window._wsSend, window._applyMonitorData
// window.switchAttemptVideo, window.openUnitModal, window.showImage
// window.openAddEntityModal, window._onRegenSheetClick, window._onUploadSheetClick
// window.onConcatModeChange, window.onConcatFadeChange, window.onConcatFadePreset
// window.concatCurrentRun, window.deleteCurrentFinal
// window.openRunInMonitor, window.pauseVideoJob, window.unpauseVideoJob
// window.stopVideoJob, window.resumeVideoJob, window.deleteVideoJob
// window.switchTab, window.updateProjectsToggleLabel

// ── Module-level state ──────────────────────────────────────────
export let browseSelectedUnit = 0;
export function setBrowseSelectedUnit(v) { browseSelectedUnit = v; }
let _monitorDetailFallbackTimer = null;

// ══════════════════════════════════════════════════════════════
// Monitor View Rendering (Active Jobs Only)
// ══════════════════════════════════════════════════════════════

function _buildRecentRunsHTML() {
  if (!repoData || !repoData.length) return '';

  const activeRunDirs = new Set();
  for (const j of (videoJobsData || [])) {
    if (['queued', 'running', 'paused'].includes(j.status) && j.run_dir) {
      const runId = j.run_dir.split('/').pop();
      const projName = j.storyboard_name;
      if (runId && projName) {
        activeRunDirs.add(`${projName}_storyboard/${runId}`);
        activeRunDirs.add(`${projName}/${runId}`);
      }
    }
  }

  const recent = [];
  for (const proj of repoData) {
    for (const run of (proj.runs || [])) {
      const key = `${proj.project_name}/${run.run_id}`;
      if (activeRunDirs.has(key)) continue;
      recent.push({ proj, run });
    }
  }
  recent.sort((a, b) => b.run.run_id.localeCompare(a.run.run_id));
  const top5 = recent.slice(0, 5);
  if (!top5.length) return '';

  const cardsHTML = top5.map(({ proj, run }) => {
    const title = proj.storyboard_meta?.title || proj.project_name.replace('_storyboard','');
    const hasFinal = run.has_final;
    const progress = run.progress || 0;
    const progressLabel = run.units_total ? `${run.units_completed}/${run.units_total}` : '';
    const finalThumb = run.final
      ? `/repo-media/${proj.project_name}/${run.run_id}/${run.final}${run.final_mtime != null && run.final_mtime !== '' ? `?v=${encodeURIComponent(run.final_mtime)}` : ''}`
      : '';
    const thumbSrc = finalThumb
      || (run.segments?.[0] ? `/repo-media/${proj.project_name}/${run.run_id}/${run.segments[0]}` : '');
    return `<div class="recent-run-card" onclick="openRunInMonitor('${proj.project_name}','${run.run_id}')">
      <div class="recent-run-thumb">
        ${thumbSrc
          ? `<video src="${thumbSrc}" preload="metadata" muted></video>`
          : `<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-muted);font-size:10px">${t('monitor.no_video')}</div>`}
        ${hasFinal ? `<div class="recent-run-final-badge">🎬</div>` : ''}
      </div>
      <div class="recent-run-info">
        <div class="recent-run-title">${esc(title)}</div>
        <div class="recent-run-meta">${run.date}</div>
        ${progressLabel ? `<div class="recent-run-progress">
          <div class="vj-progress-bar" style="height:3px"><div class="vj-progress-fill" style="width:${progress}%"></div></div>
          <span style="font-size:10px;color:var(--text-muted)">${progressLabel}</span>
        </div>` : ''}
      </div>
    </div>`;
  }).join('');

  return `<div class="recent-runs-section">
    <div class="recent-runs-title">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
      ${t('monitor.recent_runs')}
    </div>
    <div class="recent-runs-list">${cardsHTML}</div>
  </div>`;
}

export function getVideoStageLabel(progress, jobStatus) {
  if (jobStatus === 'queued') return t('monitor.stage_queued');
  if (progress?.stage === 'incomplete') return t('monitor.stage_incomplete');
  if (progress?.stage === 'finished' || jobStatus === 'completed') return t('monitor.stage_completed');
  const stage = progress?.stage || 'starting';
  if (stage === 'charsheet') {
    const done = progress?.charsheet_done || 0;
    const total = progress?.charsheet_total || 0;
    return t('monitor.stage_charsheet').replace('{0}', done).replace('{1}', total);
  }
  if (stage === 'image_generation' || stage === 'generating_images') return t('monitor.stage_image');
  if (stage === 'video_generation' || stage === 'generating_video') return t('monitor.stage_video');
  if (stage === 'finalizing') return t('monitor.stage_finalizing');
  return t('monitor.stage_init');
}

export function getVideoProgressText(progress) {
  const done = Number(progress?.display_completed ?? progress?.completed ?? 0);
  const total = Number(progress?.total || 0);
  return `${done}/${total}`;
}

export function getVideoProgressHint(progress) {
  const stage = progress?.stage || 'starting';
  if (stage === 'charsheet') {
    const pending = progress?.charsheet_pending || [];
    const waiting = progress?.charsheet_waiting || [];
    const parts = [];
    if (pending.length) parts.push(t('monitor.generating') + pending.join('、'));
    if (waiting.length) parts.push(t('monitor.waiting_for') + waiting.join('、'));
    return parts.join('  ') || t('monitor.preparing');
  }
  if (stage === 'incomplete') {
    const miss = Number(progress?.units_missing_video ?? 0);
    if (miss > 0) return t('monitor.missing_video').replace('{0}', miss);
    return t('monitor.check_units');
  }
  if (stage === 'image_generation' || stage === 'generating_images') {
    const generated = Number(progress?.image_generated || 0);
    const total = Number(progress?.image_total || 0);
    if (total > 0) return `${generated}/${total} (${progress?.image_percent || 0}%)`;
    return '--';
  }

  const available = Number(progress?.available || 0);
  const critiquing = Number(progress?.critiquing || 0);
  const inProgress = Number(progress?.in_progress || 0);
  const hints = [];
  if (available > 0) hints.push(t('monitor.available').replace('{0}', available));
  if (critiquing > 0) hints.push(t('monitor.critiquing').replace('{0}', critiquing));
  if (inProgress > 0) hints.push(t('monitor.in_progress').replace('{0}', inProgress));
  return hints.length ? hints.join(' · ') : '--';
}

export function buildActiveJobsListHTML(activeJobs) {
  if (!activeJobs.length) {
    return `<div class="empty-state" style="height:auto;min-height:180px;padding:24px 12px 20px">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <rect x="2" y="2" width="20" height="20" rx="2.18"/><line x1="7" y1="2" x2="7" y2="22"/>
          <line x1="17" y1="2" x2="17" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/>
        </svg>
        <div style="font-size:15px;font-weight:600;margin-top:10px">${t('monitor.no_jobs')}</div>
        <div style="color:var(--text-muted);font-size:12px">${t('monitor.no_jobs_hint')}</div>
      </div>`;
  }
  return activeJobs.map(j => {
    const p = j.progress || {};
    const stageLabel = getVideoStageLabel(p, j.status);
    const progressHint = getVideoProgressHint(p);
    const progressText = getVideoProgressText(p);
    const isSelected = j.job_id === selectedVideoJobId;
    let actions = '';
    if (j.status === 'queued') {
      actions = `
            <button class="vj-btn danger" onclick="event.stopPropagation(); stopVideoJob('${j.job_id}')">⏹ ${t('misc.stop')}</button>
          `;
    } else if (j.status === 'running') {
      actions = `
            <button class="vj-btn pause" onclick="event.stopPropagation(); pauseVideoJob('${j.job_id}')">⏸ ${t('misc.pause')}</button>
            <button class="vj-btn danger" onclick="event.stopPropagation(); stopVideoJob('${j.job_id}')">⏹ ${t('misc.stop')}</button>
          `;
    } else if (j.status === 'paused') {
      actions = `
            <button class="vj-btn resume" onclick="event.stopPropagation(); unpauseVideoJob('${j.job_id}')">▶ ${t('misc.resume')}</button>
            <button class="vj-btn danger" onclick="event.stopPropagation(); stopVideoJob('${j.job_id}')">⏹ ${t('misc.stop')}</button>
          `;
    } else {
      actions = `
            <button class="vj-btn resume" onclick="event.stopPropagation(); resumeVideoJob('${j.job_id}')">${t('misc.resume')}</button>
            <button class="vj-btn delete" onclick="event.stopPropagation(); deleteVideoJob('${j.job_id}')">${t('misc.delete')}</button>
          `;
    }
    return `<div class="vj-card ${isSelected ? 'selected' : ''}" style="margin-bottom:8px" onclick="selectVideoJobForDetail('${j.job_id}')">
          <div class="vj-card-top">
            <div class="vj-card-title">${esc(j.title || j.storyboard_name)}</div>
            <span class="vj-status ${j.status}">${j.status}</span>
          </div>
          <div class="vj-progress-row">
            <div class="vj-progress-bar"><div class="vj-progress-fill" style="width:${p.percent || 0}%"></div></div>
            <div class="vj-progress-text">${progressText}</div>
          </div>
          <div class="vj-stage-row">
            <div class="vj-stage-label">${stageLabel}</div>
            <div class="vj-stage-value">${progressHint}</div>
          </div>
          <div class="vj-meta">
            <span>${p.attempts || 0} attempts</span>
            <span>${j.generation_mode === 'sequential' ? '🔗 ' + t('monitor.sequential_transition') : `⚡ ${j.max_parallel || 3} workers`}</span>
          </div>
          ${actions ? `<div class="vj-actions">${actions}</div>` : ''}
        </div>`;
  }).join('');
}

/** 仅更新左侧任务列表，避免打断右侧单元/成片浏览（video_jobs_update 用） */
export function patchMonitorJobsSidebarFromData() {
  const panel = document.getElementById('monitor-jobs-panel');
  const content = panel?.querySelector('.monitor-jobs-content');
  const titleEl = panel?.querySelector('.monitor-jobs-header-title');
  if (!panel || !content || !titleEl) return false;
  const activeJobs = (videoJobsData || []).filter(j => ['queued', 'running', 'paused', 'stopped', 'crashed', 'interrupted'].includes(j.status));

  const existingCards = content.querySelectorAll('[data-job-id]');
  const existingIds = new Set();
  existingCards.forEach(c => existingIds.add(c.dataset.jobId));
  const newIds = new Set(activeJobs.map(j => j.job_id));

  const sameSet = existingIds.size === newIds.size && [...existingIds].every(id => newIds.has(id));
  if (!sameSet) {
    content.innerHTML = buildActiveJobsListHTML(activeJobs) + _buildRecentRunsHTML();
  } else {
    for (const job of activeJobs) {
      const card = content.querySelector(`[data-job-id="${job.job_id}"]`);
      if (!card) continue;
      const statusEl = card.querySelector('.vj-status');
      if (statusEl) {
        const p = job.progress || {};
        const newLabel = getVideoStageLabel(p, job.status);
        if (statusEl.textContent !== newLabel) statusEl.textContent = newLabel;
      }
      const progressEl = card.querySelector('.vj-progress-text');
      if (progressEl) {
        const newText = getVideoProgressText(job.progress || {});
        if (progressEl.textContent !== newText) progressEl.textContent = newText;
      }
    }
  }
  titleEl.textContent = `${t('monitor.active_jobs')} (${activeJobs.length})`;
  return true;
}

export function renderMonitor(data) {
  clearDebouncedBrowsePaneRefresh();
  set_browseLiveSigCache(null);
  const container = document.getElementById('monitor-active-jobs');
  const activeJobs = (videoJobsData || []).filter(j => ['queued','running','paused','stopped','crashed','interrupted'].includes(j.status));
  const recentRunsHTML = _buildRecentRunsHTML();
  const browseTarget = monitorBrowseMode;
  const selectedJob = browseTarget
    ? (selectedVideoJobId ? activeJobs.find(j => j.job_id === selectedVideoJobId) || null : null)
    : (activeJobs.find(j => j.job_id === selectedVideoJobId) || activeJobs[0] || null);

  if (!browseTarget && selectedJob && !selectedVideoJobId) setSelectedVideoJobId(selectedJob.job_id);

  const activeJobsListHTML = buildActiveJobsListHTML(activeJobs);
  const jobsPanelClass = `monitor-jobs-panel${monitorJobsPanelCollapsed ? ' collapsed' : ''}`;

  container.innerHTML = `<div style="display:flex;height:100%;overflow:hidden">
    <div class="${jobsPanelClass}" id="monitor-jobs-panel">
      <button class="collapse-btn" onclick="toggleMonitorJobsPanel()" title="Collapse panel">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="15 18 9 12 15 6"/>
        </svg>
      </button>
      <div class="monitor-jobs-header">
        <div class="section-title monitor-jobs-header-title">${t('monitor.active_jobs')} (${activeJobs.length})</div>
      </div>
      <div class="monitor-jobs-content" style="overflow-y:auto;flex:1">
        ${activeJobsListHTML}
        ${recentRunsHTML}
      </div>
    </div>
    <div id="monitor-detail-pane" style="flex:1;display:flex;flex-direction:column;overflow:hidden"></div>
  </div>`;

  const detailPane = document.getElementById('monitor-detail-pane');
  if (browseTarget) {
    if (currentData && currentData.storyboard_name === browseTarget.project && currentData.run_id === browseTarget.run_id) {
      _renderMonitorBrowse(detailPane);
    } else if (detailPane) {
      detailPane.innerHTML = `<div class="empty-state" style="flex:1;display:flex;align-items:center;justify-content:center">
        <div style="width:32px;height:32px;border:3px solid var(--border-subtle);border-top-color:var(--accent);border-radius:50%;animation:spin 0.8s linear infinite"></div>
        <div style="color:var(--text-muted);margin-top:12px">${t('monitor.loading_project')}</div>
      </div>`;
    }
  } else if (selectedJob && selectedJob.storyboard_name) {
    _renderMonitorDetail(selectedJob);
  } else if (detailPane) {
    detailPane.innerHTML = `<div class="empty-state" style="flex:1;display:flex;align-items:center;justify-content:center">
      <svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="2" width="20" height="20" rx="2.18"/><line x1="7" y1="2" x2="7" y2="22"/><line x1="17" y1="2" x2="17" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/></svg>
      <div style="font-size:16px;font-weight:600">${t('monitor.select_job')}</div>
      <div style="color:var(--text-muted);font-size:12px">${recentRunsHTML ? t('monitor.recent_runs') : t('monitor.no_jobs_hint')}</div>
    </div>`;
  }
}

export function selectVideoJobForDetail(jobId) {
  setMonitorBrowseMode(null);
  setSelectedVideoJobId(jobId);
  const job = videoJobsData.find(j => j.job_id === jobId);
  if (job) {
    const runId = job.run_id || (job.run_dir ? job.run_dir.split('/').pop() : '');
    // Only send switch_project when we have a concrete run_id.
    // Without it, the backend would find_latest_run and load a previous
    // run's data, making a brand-new job look like it resumed old results.
    if (runId) {
      const switchMsg = { type: 'switch_project', project_name: job.storyboard_name };
      switchMsg.run_id = runId;
      window._wsSend(switchMsg);
    }
    renderMonitor();
  }
}

export function toggleMonitorJobsPanel() {
  const panel = document.getElementById('monitor-jobs-panel');
  if (panel) {
    panel.classList.toggle('collapsed');
    setMonitorJobsPanelCollapsed(panel.classList.contains('collapsed'));
  }
}

export function _renderMonitorDetail(job) {
  const detailPane = document.getElementById('monitor-detail-pane');
  const runId = job.run_id || (job.run_dir ? job.run_dir.split('/').pop() : '');

  // ── New job with no run_dir yet (queued / just started) ──
  // Do NOT send switch_project — that would cause the backend to
  // find_latest_run and load a *previous* run's data, making it look
  // like we're resuming the old result.
  if (!runId) {
    const statusLabel = job.status === 'queued' ? t('monitor.queued_status') : t('monitor.starting_status');
    detailPane.innerHTML = `<div class="empty-state" style="flex:1;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:12px">
      <div style="width:28px;height:28px;border:3px solid var(--border-subtle);border-top-color:var(--accent);border-radius:50%;animation:spin 0.8s linear infinite"></div>
      <div style="font-size:15px;font-weight:600">${esc(job.title || job.storyboard_name)}</div>
      <div style="color:var(--text-muted);font-size:13px">${statusLabel}，${t('monitor.waiting_desk')}</div>
    </div>`;
    // Poll until the job gets a run_dir
    if (_monitorDetailFallbackTimer) clearTimeout(_monitorDetailFallbackTimer);
    _monitorDetailFallbackTimer = setTimeout(() => {
      _monitorDetailFallbackTimer = null;
      const freshJob = videoJobsData.find(j => j.job_id === job.job_id);
      if (freshJob) _renderMonitorDetail(freshJob);
    }, 2000);
    return;
  }

  const sameProject = currentData && currentData.storyboard_name === job.storyboard_name;
  const sameRun = !job.run_id || currentData?.run_id === job.run_id;
  if (!sameProject || !sameRun) {
    // Show a spinner immediately
    detailPane.innerHTML = `<div class="empty-state" style="flex:1;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:8px">
      <div style="width:28px;height:28px;border:3px solid var(--border-subtle);border-top-color:var(--accent);border-radius:50%;animation:spin 0.8s linear infinite"></div>
    </div>`;
    // Send switch_project so WS starts pushing data for this project
    if (job.storyboard_name) {
      const switchMsg = { type: 'switch_project', project_name: job.storyboard_name };
      if (runId) switchMsg.run_id = runId;
      window._wsSend?.(switchMsg);
    }
    // Also fire an HTTP fetch in parallel — whichever arrives first wins
    if (_monitorDetailFallbackTimer) clearTimeout(_monitorDetailFallbackTimer);
    _monitorDetailFallbackTimer = null;
    if (job.storyboard_name && runId) {
      (async () => {
        try {
          const res = await fetch(`/api/run-detail/${encodeURIComponent(job.storyboard_name)}/${encodeURIComponent(runId)}`);
          if (res.ok) {
            // Only apply if WS hasn't already delivered the data
            const stillMissing = !(currentData && currentData.storyboard_name === job.storyboard_name);
            if (stillMissing) {
              const detail = await res.json();
              window._applyMonitorData({
                storyboard: detail.storyboard,
                checkpoint: detail.checkpoint,
                media: detail.media,
                run_id: detail.run_id,
                storyboard_name: detail.project_name,
                all_runs: currentData?.all_runs || [],
              });
              _renderMonitorBrowse(detailPane);
            }
          }
        } catch (e) {
          console.error('Monitor detail HTTP fetch failed', e);
        }
      })();
    }
    return;
  }
  if (_monitorDetailFallbackTimer) { clearTimeout(_monitorDetailFallbackTimer); _monitorDetailFallbackTimer = null; }
  _renderMonitorBrowse(detailPane);
}

/** final_video.mp4 路径固定，重新剪辑会覆盖文件；用 mtime 作查询参数避免浏览器继续播缓存。 */
export function finalVideoRepoUrl(mediaBase, media) {
  if (!media?.final) return null;
  const path = `${mediaBase}/${media.final}`;
  const v = media.final_mtime;
  if (v != null && v !== '') return `${path}?v=${encodeURIComponent(v)}`;
  return path;
}

/** 供全量/增量刷新共用：从 currentData 计算监控 browse 视图所需结构 */
export function _computeBrowseRenderState() {
  const sb = currentData.storyboard || {};
  const cp = currentData.checkpoint || {};
  const media = currentData.media || {};
  const units = cp.script?.work_units || [];
  const projectName = currentData.storyboard_name || '';
  const runId = currentData.run_id || '';
  const mediaBase = (projectName && runId) ? `/repo-media/${projectName}/${runId}` : '/media';
  const title = sb.title || cp.script?.title || projectName.replace('_storyboard', '');

  const effectiveUnits = units.length > 0 ? units : (sb.groups || sb.storyboard || []).map((g, i) => ({
    unit_id: i + 1,
    prompt: g.seedance_prompt || g.sora_prompt || '',
    original_prompt: g.seedance_prompt || g.sora_prompt || '',
    duration_seconds: g.total_seconds || g.duration_seconds || 0,
    narrative_summary: g.narrative_summary || g.name || '',
    group_name: g.name || '',
    attempts: [],
    is_completed: false,
    final_video_path: null,
    _placeholder: true,
  }));

  const unitInfos = effectiveUnits.map(u => {
    const displayAttempts = getDisplayAttempts(u);
    const attemptVideos = displayAttempts.map(a =>
      a.output_path ? `${mediaBase}/${a.output_path.split('/').pop()}` : null
    );
    const bestIdx = getPreferredAttemptIndex(u);
    return { unit: u, displayAttempts, attemptVideos, bestIdx, videoSrc: bestIdx >= 0 ? attemptVideos[bestIdx] : null };
  });

  // ── Left sidebar: narrative + characters + locations ──
  const fallbackNarrative = cp.script?.description || '';
  const narrativeHTML = (sb.narrative || fallbackNarrative) ? esc(sb.narrative || fallbackNarrative) : 'No narrative';

  const charsHTML = (sb.characters || []).map(c => {
    const refSrc = c.image_path ? `/asset?path=${encodeURIComponent(c.image_path)}` : '';
    const csMatches = (media.charsheets||[]).filter(f => {
      const base = f.replace(/^charsheet_/i, '').replace(/(_v\d+)?\.png$/i, '');
      return base === c.name.replace(/\s/g,'_');
    });
    const csMatch = csMatches.length ? csMatches[csMatches.length - 1] : null;
    const imgSrc = csMatch ? `${mediaBase}/${csMatch}` : refSrc;
    const avatarHTML = imgSrc
      ? `<div class="browse-char-avatar" onclick="showImage('${imgSrc}')"><img src="${imgSrc}" loading="lazy"/></div>`
      : '';
    const regenBtn = `<button class="regen-sheet-btn" data-regen-type="character" data-regen-name="${esc(c.name)}" data-regen-desc="${esc(c.description || '')}" data-regen-personality="${esc(c.personality || '')}" onclick="event.stopPropagation(); _onRegenSheetClick(this)">${t('monitor.regen_image')}</button>`;
    const uploadBtn = `<button class="upload-sheet-btn" data-upload-type="character" data-upload-name="${esc(c.name)}" onclick="event.stopPropagation(); _onUploadSheetClick(this)">${t('monitor.upload_image')}</button>`;
    return `<div class="browse-char-card" id="entity-card-char-${esc(c.name)}">
      ${avatarHTML}
      <div class="browse-char-info">
        <div class="browse-char-name">${esc(c.name)}</div>
        <div class="browse-char-desc">${esc(c.description || c.personality || '')}</div>
      </div>
      <div class="sheet-action-btns">${regenBtn}${uploadBtn}</div>
    </div>`;
  }).join('');

  const locsHTML = (sb.locations || []).map(l => {
    const lsMatches = (media.locsheets||[]).filter(f => {
      const base = f.replace(/^locsheet_/i, '').replace(/(_v\d+)?\.png$/i, '');
      return base === l.name.replace(/\s/g,'_');
    });
    const lsMatch = lsMatches.length ? lsMatches[lsMatches.length - 1] : null;
    const imgSrc = lsMatch ? `${mediaBase}/${lsMatch}` : (l.image_path ? `/asset?path=${encodeURIComponent(l.image_path)}` : '');
    const avatarHTML = imgSrc
      ? `<div class="browse-loc-avatar" onclick="showImage('${imgSrc}')"><img src="${imgSrc}" loading="lazy"/></div>`
      : '';
    const regenBtn = `<button class="regen-sheet-btn" data-regen-type="location" data-regen-name="${esc(l.name)}" data-regen-desc="${esc(l.description || '')}" data-regen-personality="" onclick="event.stopPropagation(); _onRegenSheetClick(this)">${t('monitor.regen_image')}</button>`;
    const uploadBtn = `<button class="upload-sheet-btn" data-upload-type="location" data-upload-name="${esc(l.name)}" onclick="event.stopPropagation(); _onUploadSheetClick(this)">${t('monitor.upload_image')}</button>`;
    return `<div class="browse-loc-card" id="entity-card-loc-${esc(l.name)}">
      ${avatarHTML}
      <div class="browse-loc-info">
        <div class="browse-loc-name">${esc(l.name)}</div>
        <div class="browse-loc-desc">${esc(l.description || '')}</div>
      </div>
      <div class="sheet-action-btns">${regenBtn}${uploadBtn}</div>
    </div>`;
  }).join('');

  const propsHTML = (sb.props || []).map(p => {
    const psMatch = (media.propsheets||[]).find(f => f.toLowerCase().includes((p.name||'').toLowerCase().replace(/\s/g,'_')));
    const imgSrc = psMatch ? `${mediaBase}/${psMatch}` : (p.image_path ? `/asset?path=${encodeURIComponent(p.image_path)}` : '');
    const avatarHTML = imgSrc
      ? `<div class="browse-loc-avatar"><img src="${imgSrc}" onclick="showImage('${imgSrc}')" /></div>`
      : '';
    const regenBtn = `<button class="regen-sheet-btn" data-regen-type="prop" data-regen-name="${esc(p.name || '')}" data-regen-desc="${esc(p.description || '')}" data-regen-personality="" onclick="event.stopPropagation(); _onRegenSheetClick(this)">${t('monitor.regen_image')}</button>`;
    const uploadBtn = `<button class="upload-sheet-btn" data-upload-type="prop" data-upload-name="${esc(p.name || '')}" onclick="event.stopPropagation(); _onUploadSheetClick(this)">${t('monitor.upload_image')}</button>`;
    return `<div class="browse-loc-card" id="entity-card-prop-${esc(p.name || '')}">
      ${avatarHTML}
      <div class="browse-loc-info">
        <div class="browse-loc-name">${esc(p.name || '')}</div>
        <div class="browse-loc-desc">${esc(p.description || '')}</div>
      </div>
      <div class="sheet-action-btns">${regenBtn}${uploadBtn}</div>
    </div>`;
  }).join('');

  // ── Filmstrip ──
  const filmstripHTML = unitInfos.map((info, i) => {
    const u = info.unit;
    const unitStatus = getUnitStatus(u);
    let thumbHTML;
    if (info.videoSrc) {
      const critiqueOverlay = unitStatus === 'critiquing'
        ? `<div style="position:absolute;bottom:3px;left:3px;background:rgba(0,0,0,0.7);color:#a78bfa;font-size:9px;font-weight:600;padding:1px 5px;border-radius:10px;pointer-events:none">${t('monitor.critiquing_unit')}</div>`
        : '';
      thumbHTML = `<div style="position:relative;width:100%;height:100%"><video src="${info.videoSrc}" preload="metadata" muted style="width:100%;height:100%;object-fit:cover"></video>${critiqueOverlay}</div>`;
    } else if (unitStatus === 'in_progress') {
      thumbHTML = `<div class="thumb-placeholder" style="color:var(--warning);font-size:10px;display:flex;align-items:center;gap:3px"><div style="width:8px;height:8px;border:1.5px solid rgba(245,158,11,0.3);border-top-color:var(--warning);border-radius:50%;animation:spin 0.8s linear infinite;flex-shrink:0"></div>${t('monitor.generating_video')}</div>`;
    } else if (unitStatus === 'queued') {
      thumbHTML = `<div class="thumb-placeholder" style="color:var(--accent);font-size:10px;display:flex;align-items:center;gap:3px">${t('monitor.created')}</div>`;
    } else {
      thumbHTML = `<div class="thumb-placeholder" style="font-size:10px">${t('monitor.waiting_unit')}</div>`;
    }
    return `<div class="browse-filmstrip-item${i === browseSelectedUnit ? ' active' : ''}" onclick="browseSelectUnit(${i})">
      <div class="browse-filmstrip-thumb">${thumbHTML}</div>
      <div class="browse-filmstrip-label">
        <span><span class="unit-num">${u.unit_id}</span> ${esc((u.group_name||'').substring(0,12))}</span>
        <span class="unit-dur">${u.duration_seconds||0}s</span>
      </div>
    </div>`;
  }).join('');

  // Final video card
  const finalSrc = finalVideoRepoUrl(mediaBase, media);
  const finalCardHTML = finalSrc ? `
    <div class="filmstrip-final-divider"></div>
    <div class="browse-filmstrip-item final-card${browseSelectedUnit === -1 ? ' active' : ''}" onclick="browseSelectFinal()">
      <div class="browse-filmstrip-thumb">
        <video src="${finalSrc}" preload="metadata" muted></video>
      </div>
      <div class="browse-filmstrip-label">
        <span>🎬 ${t('monitor.final_product')}</span>
      </div>
    </div>` : '';

  // Total duration
  const totalDur = units.reduce((s, u) => s + (u.duration_seconds || 0), 0);
  const completedCount = units.filter(u => unitHasCheckpointVideoOutput(u)).length;
  const availableClipCount = units.filter(u => {
    if (u.final_video_path) return true;
    return (u.attempts || []).some(a => !!a.output_path);
  }).length;
  const canConcat = availableClipCount > 0;
  const hasFinal = !!finalSrc;

  const isFromHistory = !!monitorBrowseMode;

  // Current activity status
  const _currentJob = videoJobsData?.find(j => j.job_id === selectedVideoJobId);
  const _currentJobActive = ['running', 'paused'].includes(_currentJob?.status || '');
  const inProgressUnits = _currentJobActive ? units.filter(u => (u.attempts||[]).some(a => a.status === 'in_progress')) : [];
  const critiquingUnits = _currentJobActive ? units.filter(u => {
    if (u.is_completed) return false;
    const attempts = u.attempts || [];
    if (attempts.length === 0) return false;
    const last = attempts[attempts.length - 1];
    return last.status === 'success' && last.output_path && !last.critique_result && !last.critique_error;
  }) : [];
  let currentActivityHTML = '';
  if (!isFromHistory && (inProgressUnits.length > 0 || critiquingUnits.length > 0)) {
    const parts = [];
    if (inProgressUnits.length > 0) {
      const unitIds = inProgressUnits.map(u => `#${u.unit_id}`).join(', ');
      const label = inProgressUnits.length <= 4 ? t('monitor.generating_video_ids').replace('{0}', unitIds) : t('monitor.generating_video_count').replace('{0}', inProgressUnits.length);
      parts.push(`<span class="monitor-summary-chip busy" style="color:var(--warning);border-color:rgba(245,158,11,0.2);background:rgba(245,158,11,0.08)"><span style="width:6px;height:6px;background:var(--warning);border-radius:50%;animation:pulse 1.5s ease-in-out infinite;flex-shrink:0"></span>${label}</span>`);
    }
    if (critiquingUnits.length > 0) {
      const unitIds = critiquingUnits.map(u => `#${u.unit_id}`).join(', ');
      const label = critiquingUnits.length <= 4 ? t('monitor.critiquing_ids').replace('{0}', unitIds) : t('monitor.critiquing_count').replace('{0}', critiquingUnits.length);
      parts.push(`<span class="monitor-summary-chip busy" style="color:#c4b5fd;border-color:rgba(167,139,250,0.25);background:rgba(167,139,250,0.08)"><span style="width:6px;height:6px;background:#a78bfa;border-radius:50%;animation:pulse 1.5s ease-in-out infinite;flex-shrink:0"></span>${label}</span>`);
    }
    currentActivityHTML = parts.join('');
  } else if (!isFromHistory && units.length > 0 && completedCount < units.length) {
    const pendingCount = units.length - completedCount - inProgressUnits.length - critiquingUnits.length;
    if (pendingCount > 0) {
      currentActivityHTML = `<span class="monitor-summary-chip"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>${t('monitor.pending_count').replace('{0}', pendingCount)}</span>`;
    }
  }

  return {
    sb, cp, media, units, projectName, runId, mediaBase, title,
    unitInfos, narrativeHTML, charsHTML, locsHTML, propsHTML,
    filmstripHTML, finalCardHTML, finalSrc,
    totalDur, completedCount, availableClipCount, canConcat, hasFinal, isFromHistory,     currentActivityHTML,
  };
}

/** 双击标题进入编辑模式 */
export function startEditMonitorTitle(el) {
  const currentTitle = el.textContent.trim();
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'monitor-run-title-edit';
  input.value = currentTitle;
  input.setAttribute('data-original', currentTitle);
  el.replaceWith(input);
  input.focus();
  input.select();

  const commit = async () => {
    const newTitle = input.value.trim();
    if (!newTitle || newTitle === currentTitle) {
      const span = document.createElement('span');
      span.className = 'monitor-run-title';
      span.setAttribute('ondblclick', 'startEditMonitorTitle(this)');
      span.setAttribute('title', t('monitor.dblclick_edit'));
      span.textContent = currentTitle;
      input.replaceWith(span);
      return;
    }
    const projectName = currentData?.storyboard_name || '';
    if (!projectName) return;
    try {
      const res = await fetch('/api/storyboard/rename', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_name: projectName, new_title: newTitle }),
      });
      const data = await res.json();
      if (data.ok) {
        if (currentData?.storyboard) currentData.storyboard.title = newTitle;
        if (currentData?.checkpoint?.script) currentData.checkpoint.script.title = newTitle;
        renderMonitor();
      } else {
        alert(data.error || t('monitor.rename_failed'));
        const span = document.createElement('span');
        span.className = 'monitor-run-title';
        span.setAttribute('ondblclick', 'startEditMonitorTitle(this)');
        span.setAttribute('title', t('monitor.dblclick_edit'));
        span.textContent = currentTitle;
        input.replaceWith(span);
      }
    } catch (e) {
      console.error('Rename failed', e);
      const span = document.createElement('span');
      span.className = 'monitor-run-title';
      span.setAttribute('ondblclick', 'startEditMonitorTitle(this)');
      span.setAttribute('title', t('monitor.dblclick_edit'));
      span.textContent = currentTitle;
      input.replaceWith(span);
    }
  };

  input.addEventListener('blur', commit);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
    if (e.key === 'Escape') { input.value = currentTitle; input.blur(); }
  });
}

export function _browseToolbarOuterHTML(st) {
  const {
    isFromHistory, title, runId, availableClipCount, completedCount, units, totalDur, hasFinal, currentActivityHTML, canConcat,
  } = st;
  return `
    <div class="monitor-run-toolbar">
      <div class="monitor-run-meta">
        <div class="monitor-run-title-row">
          ${isFromHistory ? `<button class="repo-monitor-btn" onclick="monitorBrowseMode=null; switchTab('repo')" style="background:rgba(255,255,255,0.06);border-color:var(--border-subtle);color:var(--text-secondary)">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 18 9 12 15 6"/></svg>
            ${t('monitor.back')}
          </button>` : ''}
          <span class="monitor-run-title" ondblclick="startEditMonitorTitle(this)" title="${t('monitor.dblclick_edit')}">${esc(title)}</span>
          <span class="monitor-run-id">RUN ${runId}</span>
        </div>
        <div class="monitor-run-summary">
          <span class="monitor-summary-chip accent"><strong>${availableClipCount}</strong> ${t('monitor.clips_available')}</span>
          <span class="monitor-summary-chip success"><strong>${completedCount}/${units.length}</strong> ${t('monitor.units_completed')}</span>
          <span class="monitor-summary-chip"><strong>${Math.round(totalDur)}s</strong> ${t('monitor.total_duration')}</span>
          ${hasFinal ? `<span class="monitor-summary-chip gold">${t('monitor.has_final')}</span>` : ''}
          ${currentActivityHTML}
        </div>
      </div>
      <div class="monitor-run-actions">
        <span class="monitor-run-stat">${completedCount}/${units.length} ${t('repo.units')}</span>
        ${canConcat ? `
          <div class="sd-model-picker concat-mode-picker">
            <select class="concat-mode-select" style="display:none" onchange="onConcatModeChange(this.value)">
              <option value="hard">${t('concat.mode.hard')}</option>
              <option value="crossfade">${t('concat.mode.crossfade')}</option>
            </select>
            <button type="button" class="sd-model-trigger concat-mode-trigger" onclick="toggleConcatModeMenu(event)">
              <span class="concat-mode-label">${concatMode === 'crossfade' ? t('concat.mode.crossfade') : t('concat.mode.hard')}</span>
              <svg viewBox="0 0 12 12" width="10" height="10"><polyline points="2,4 6,8 10,4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </button>
            <div class="sd-model-menu concat-mode-menu">
              <div class="sd-model-option${concatMode === 'hard' ? ' active' : ''}" data-value="hard" onclick="selectConcatMode(this)">${t('concat.mode.hard')}</div>
              <div class="sd-model-option${concatMode === 'crossfade' ? ' active' : ''}" data-value="crossfade" onclick="selectConcatMode(this)">${t('concat.mode.crossfade')}</div>
            </div>
          </div>
          <div class="concat-fade-controls">
            <input
              class="concat-fade-input"
              type="number"
              min="0.1"
              max="3"
              step="0.1"
              value="${concatFadeSeconds}"
              onchange="onConcatFadeChange(this.value)"
              title="${t('concat.fade')} (s)"
            />
            <div class="concat-fade-presets">
              <button type="button" class="concat-fade-preset-btn" onclick="onConcatFadePreset(0.3)">0.3s</button>
              <button type="button" class="concat-fade-preset-btn" onclick="onConcatFadePreset(0.5)">0.5s</button>
              <button type="button" class="concat-fade-preset-btn" onclick="onConcatFadePreset(0.8)">0.8s</button>
              <button type="button" class="concat-fade-preset-btn" onclick="onConcatFadePreset(1.0)">1.0s</button>
            </div>
          </div>
          <button class="vj-btn concat" id="monitor-concat-btn" onclick="concatCurrentRun(event)">${hasFinal ? t('monitor.reconcat') : t('monitor.concat_now')}</button>
        ` : ''}
      </div>
    </div>`;
}

export function _browseLayoutOuterHTML(st) {
  const {
    narrativeHTML, charsHTML, locsHTML, propsHTML, units, filmstripHTML, finalCardHTML,
  } = st;
  return `
    <div class="browse-layout">
      <div class="browse-sidebar">
        <div class="browse-section-title">${t('monitor.narrative_label')}</div>
        <div class="browse-narrative">${narrativeHTML}</div>
        ${charsHTML ? `<div class="browse-section-title">${t('monitor.characters_label')}</div>${charsHTML}` : ''}
        ${locsHTML ? `<div class="browse-section-title">${t('monitor.locations_label')}</div>${locsHTML}` : ''}
        ${propsHTML ? `<div class="browse-section-title">${t('monitor.props_label')}</div>${propsHTML}` : ''}
        <div class="browse-add-asset-bar">
          <div class="browse-add-asset-title">${t('monitor.add_asset')}</div>
          <div class="browse-add-asset-btns">
            <button class="browse-add-asset-cat-btn" onclick="openAddEntityModal('characters')">${t('monitor.add_character')}</button>
            <button class="browse-add-asset-cat-btn" onclick="openAddEntityModal('locations')">${t('monitor.add_location')}</button>
            <button class="browse-add-asset-cat-btn" onclick="openAddEntityModal('props')">${t('monitor.add_prop')}</button>
          </div>
        </div>
      </div>
      <div class="browse-main">
        <div class="browse-detail" id="browse-detail-area">
          <!-- filled by browseSelectUnit -->
        </div>
        <div class="browse-filmstrip">
          <div class="browse-filmstrip-header">
            <span>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="vertical-align:-2px"><polygon points="5,3 19,12 5,21"/></svg>
              ${t('monitor.timeline')} &middot; ${t('monitor.units_count').replace('{0}', units.length)}
            </span>
          </div>
          <div class="browse-filmstrip-scroll" id="browse-filmstrip-scroll">${filmstripHTML}${finalCardHTML}</div>
        </div>
      </div>
    </div>`;
}

export function _renderMonitorBrowse(container) {
  const prevSidebarScroll = document.querySelector('.browse-sidebar')?.scrollTop;
  const monitorHeader = container.parentElement.querySelector('.monitor-header');
  if (monitorHeader) monitorHeader.style.display = 'none';
  const st = _computeBrowseRenderState();
  container.innerHTML = _browseToolbarOuterHTML(st) + _browseLayoutOuterHTML(st);
  window._browseUnitInfos = st.unitInfos;
  window._browseMediaBase = st.mediaBase;
  window._browseSb = st.sb;
  window._browseFinalSrc = st.finalSrc || null;

  if (browseSelectedUnit === -1 && st.finalSrc) {
    browseSelectFinal();
  } else if (st.unitInfos.length > 0) {
    const initialIdx = browseSelectedUnit >= 0 && browseSelectedUnit < st.unitInfos.length ? browseSelectedUnit : 0;
    browseSelectUnit(initialIdx);
  }
  if (prevSidebarScroll != null && Number.isFinite(prevSidebarScroll)) {
    requestAnimationFrame(() => {
      const side = container.querySelector('.browse-sidebar');
      if (side) side.scrollTop = prevSidebarScroll;
    });
  }
  window.syncConcatModeControls();
  window.syncMonitorActionButtons();
  set_browseLiveSigCache(computeBrowseRefreshSigsFromState(st));
}

export function browseSelectUnit(idx) {
  browseSelectedUnit = idx;
  const infos = window._browseUnitInfos || [];
  if (idx < 0 || idx >= infos.length) return;

  document.querySelectorAll('.browse-filmstrip-item:not(.final-card)').forEach((el, i) =>
    el.classList.toggle('active', i === idx)
  );
  document.querySelectorAll('.browse-filmstrip-item.final-card').forEach(el =>
    el.classList.remove('active')
  );

  const scroll = document.getElementById('browse-filmstrip-scroll');
  const activeItem = scroll?.children[idx];
  if (activeItem) activeItem.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });

  const info = infos[idx];
  const u = info.unit;
  console.log(`[browseSelectUnit] idx=${idx} unit_id=${u.unit_id} bestIdx=${info.bestIdx} attempts=${u.attempts?.length}`);
  if (u.attempts?.[info.bestIdx]?.critique_result) {
    console.log(`  critique score=${u.attempts[info.bestIdx].critique_result.overall_score}`);
  }
  const area = document.getElementById('browse-detail-area');
  if (!area) return;

  const unitStatus = getUnitStatus(u);
  const displayAttempts = info.displayAttempts || getDisplayAttempts(u);
  const viewingAttempt = displayAttempts[info.bestIdx];
  const videoHTML = renderAttemptVideoStage(u, viewingAttempt, info.videoSrc, unitStatus, { includePlayerId: true, playerId: 'browse-main-video' });

  // Attempt dots
  const attemptDotsHTML = displayAttempts.map((a, i) => {
    const isViewing = i === info.bestIdx;
    const isFinal = u.final_attempt_id != null && Number(u.final_attempt_id) === Number(a.attempt_id);
    return `<div class="attempt-dot ${getAttemptVisualStatus(a)}${isViewing?' viewing':''}${isFinal?' final-picked':''}"
      style="cursor:pointer"
      onclick="browsePlayAttempt(${idx},${i})"
      title="Attempt ${a.attempt_id} — ${a.metadata?.regen_status || a.status}${isFinal ? ' — final selected' : ''}">${a.attempt_id}</div>`;
  }).join('');

  const critiqueHTML = window.buildFullCritiqueHTML(viewingAttempt);
  const maxAttemptsHint = getAttemptMaxAttempts(u, info.bestIdx);

  area.innerHTML = `
    <div class="browse-detail-shell">
      <div class="browse-detail-grid">
        <div class="browse-unit-meta">
          <div class="browse-unit-title">Unit #${u.unit_id} — ${esc(u.group_name || '')}</div>
          <div class="browse-unit-subtitle">Scenes: ${(u.scene_numbers||[]).join(', ')} &middot; ${u.duration_seconds||0}s &middot; ${displayAttempts.length} attempts${maxAttemptsHint ? ` &middot; ${t('monitor.max_attempts').replace('{0}', maxAttemptsHint)}` : ''}</div>
          ${attemptDotsHTML ? `<div class="unit-attempt-toolbar" style="margin-top:8px"><div class="unit-attempt-toolbar-left"><span class="unit-attempt-toolbar-label">${t('misc.attempts')}</span>${attemptDotsHTML}</div><div class="unit-attempt-toolbar-right">${window.buildUnitActionControls(u, viewingAttempt, 'browse')}</div></div>` : `<div class="unit-attempt-toolbar" style="margin-top:8px"><div class="unit-attempt-toolbar-left"><span class="unit-attempt-toolbar-label">${t('misc.attempts')}</span></div><div class="unit-attempt-toolbar-right">${window.buildUnitActionControls(u, viewingAttempt, 'browse')}</div></div>`}
        </div>
        <div style="display:flex;flex-direction:column;overflow-y:auto;min-height:0">
          <div class="browse-prompt-label">Prompt</div>
          ${buildPromptEditorBlock(u, viewingAttempt, 'browse')}
          ${isEditableDraftAttempt(viewingAttempt) ? '<div class="unit-action-hint" style="margin-top:8px">输入 @ 可插入角色 / 场景 / 物品；拖动 token 可调整 @图片N 的顺序。编辑内容会直接作为后续提交给 Seedance 的 prompt。</div>' : ''}
        </div>
        <div style="display:flex;flex-direction:column;overflow-y:auto;min-height:0">
          <div class="browse-prompt-label">Video</div>
          ${videoHTML}
          ${renderAttemptMetaBadges(u, viewingAttempt)}
          <div id="browse-attempt-critique">${critiqueHTML ? `<div class="browse-prompt-label">Critique</div>${critiqueHTML}` : ''}</div>
        </div>
      </div>
    </div>
  `;
  mountPromptEditorForUnit(u, info.bestIdx, 'browse');
}

export function browseSelectFinal() {
  browseSelectedUnit = -1;
  document.querySelectorAll('.browse-filmstrip-item:not(.final-card)').forEach(el =>
    el.classList.remove('active')
  );
  document.querySelectorAll('.browse-filmstrip-item.final-card').forEach(el =>
    el.classList.add('active')
  );
  const scroll = document.getElementById('browse-filmstrip-scroll');
  const finalCard = scroll?.querySelector('.final-card');
  if (finalCard) finalCard.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });

  const area = document.getElementById('browse-detail-area');
  if (!area) return;
  const src = window._browseFinalSrc;
  if (!src) return;

  const isFromHistory = !!monitorBrowseMode;
  const projectTitle = window._browseSb?.title || currentData?.storyboard_name || 'Final Cut';
  const runId = currentData?.run_id || '';
  const unitCount = (window._browseUnitInfos || []).length;
  area.innerHTML = `<div class="browse-detail-final">
    <div class="final-stage-shell">
      <div class="final-stage-head">
        <div>
          <div class="final-stage-label">${t('monitor.final_cut')}</div>
          <div class="final-stage-title">${esc(projectTitle)}</div>
          <div class="final-stage-subtitle">${t('monitor.final_preview')}</div>
          <div class="final-stage-info">
            <span class="monitor-summary-chip gold">${t('monitor.final_badge')}</span>
            <span class="monitor-summary-chip"><strong>${unitCount}</strong> ${t('repo.units')}</span>
            <span class="monitor-summary-chip"><strong>${runId}</strong> ${t('repo.run')}</span>
            ${isFromHistory ? `<span class="monitor-summary-chip accent">${t('monitor.history_browse')}</span>` : `<span class="monitor-summary-chip success">${t('monitor.active_desk')}</span>`}
          </div>
        </div>
        <div class="final-stage-actions">
          <div class="sd-model-picker concat-mode-picker">
            <select class="concat-mode-select" style="display:none" onchange="onConcatModeChange(this.value)">
              <option value="hard">${t('concat.mode.hard')}</option>
              <option value="crossfade">${t('concat.mode.crossfade')}</option>
            </select>
            <button type="button" class="sd-model-trigger concat-mode-trigger" onclick="toggleConcatModeMenu(event)">
              <span class="concat-mode-label">${concatMode === 'crossfade' ? t('concat.mode.crossfade') : t('concat.mode.hard')}</span>
              <svg viewBox="0 0 12 12" width="10" height="10"><polyline points="2,4 6,8 10,4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </button>
            <div class="sd-model-menu concat-mode-menu">
              <div class="sd-model-option${concatMode === 'hard' ? ' active' : ''}" data-value="hard" onclick="selectConcatMode(this)">${t('concat.mode.hard')}</div>
              <div class="sd-model-option${concatMode === 'crossfade' ? ' active' : ''}" data-value="crossfade" onclick="selectConcatMode(this)">${t('concat.mode.crossfade')}</div>
            </div>
          </div>
          <div class="concat-fade-controls">
            <input
              class="concat-fade-input"
              type="number"
              min="0.1"
              max="3"
              step="0.1"
              value="${concatFadeSeconds}"
              onchange="onConcatFadeChange(this.value)"
              title="${t('concat.fade')} (s)"
            />
            <div class="concat-fade-presets">
              <button type="button" class="concat-fade-preset-btn" onclick="onConcatFadePreset(0.3)">0.3s</button>
              <button type="button" class="concat-fade-preset-btn" onclick="onConcatFadePreset(0.5)">0.5s</button>
              <button type="button" class="concat-fade-preset-btn" onclick="onConcatFadePreset(0.8)">0.8s</button>
              <button type="button" class="concat-fade-preset-btn" onclick="onConcatFadePreset(1.0)">1.0s</button>
            </div>
          </div>
          <button class="vj-btn concat" id="final-reconcat-btn" onclick="concatCurrentRun(event)">${t('monitor.reconcat_btn')}</button>
          <button class="vj-btn delete" id="final-delete-btn" onclick="deleteCurrentFinal(event)">${t('monitor.delete_final')}</button>
        </div>
      </div>
      <div class="final-stage-player">
        <div class="final-stage-video-wrap">
          <video src="${src}" controls autoplay style="width:100%;height:100%;object-fit:contain"></video>
        </div>
      </div>
    </div>
  </div>`;
  window.syncConcatModeControls();
}

export function browsePlayAttempt(unitIdx, attemptIdx) {
  const infos = window._browseUnitInfos || [];
  if (unitIdx >= infos.length) return;
  const info = infos[unitIdx];
  info.bestIdx = attemptIdx;
  info.videoSrc = info.attemptVideos[attemptIdx] || null;
  browseSelectUnit(unitIdx);
  const vid = document.getElementById('browse-main-video');
  if (vid && info.videoSrc) vid.play();
}

export function clearMonitor() {
  const ptEl = document.getElementById('project-title');
  if (ptEl) ptEl.textContent = '';
  ['stat-scenes','stat-units','stat-completed','stat-attempts','stat-duration'].forEach(id => document.getElementById(id).textContent = '--');
  document.getElementById('stat-progress').textContent = '0%';
  document.getElementById('progress-bar').style.width = '0%';
  document.getElementById('narrative-block').textContent = '';
  document.getElementById('scenes-list').innerHTML = '';
  document.getElementById('style-info').innerHTML = '';
  document.getElementById('characters-list').innerHTML = '';
  document.getElementById('locations-grid').innerHTML = '';
  document.getElementById('charsheets-section').style.display = 'none';
  document.getElementById('locsheets-section').style.display = 'none';
}

export function renderOverview(data) {
  const sb = data.storyboard, cp = data.checkpoint;
  const ptEl = document.getElementById('project-title');
  if (ptEl) ptEl.textContent =
    sb ? ` / ${sb.title || data.storyboard_name || ''}` :
    data.storyboard_name ? ` / ${data.storyboard_name}` : '';
  document.getElementById('stat-scenes').textContent = sb ? sb.storyboard?.length || 0 : '--';
  if (!cp) return;
  const units = cp.script?.work_units || [];
  const completed = units.filter(u => unitHasCheckpointVideoOutput(u)).length;
  const totalAttempts = units.reduce((s, u) => s + (u.attempts?.length || 0), 0);
  const pct = units.length ? Math.round((completed / units.length) * 100) : 0;
  const totalDur = units.reduce((s, u) => s + (u.duration_seconds || 0), 0);
  document.getElementById('stat-units').textContent = units.length;
  document.getElementById('stat-completed').textContent = completed;
  document.getElementById('stat-attempts').textContent = totalAttempts;
  document.getElementById('stat-progress').textContent = pct + '%';
  document.getElementById('progress-bar').style.width = pct + '%';
  document.getElementById('stat-duration').textContent = Math.round(totalDur) + 's';
}

export function renderRunSelect(data) {
  const sel = document.getElementById('run-select');
  if (data.all_runs?.length)
    sel.innerHTML = data.all_runs.map(r => `<option value="${r}" ${r === data.run_id ? 'selected' : ''}>${r}</option>`).join('');
}

export function renderStoryboard(data) {
  const sb = data.storyboard;
  if (!sb) {
    document.getElementById('narrative-block').textContent = t('misc.no_storyboard');
    document.getElementById('scenes-list').innerHTML = '';
    return;
  }
  document.getElementById('narrative-block').textContent = sb.narrative || 'No narrative';
  const list = document.getElementById('scenes-list');
  list.innerHTML = (sb.storyboard || []).map(s => {
    const chars = (s.characters_in_scene || []).map(c => `<span class="scene-tag char">${c}</span>`).join('');
    const moodTag = s.mood ? `<span class="scene-tag mood">${s.mood}</span>` : '';
    const camTag = s.camera_angle ? `<span class="scene-tag camera">${s.camera_angle}</span>` : '';
    let dlg = '';
    if (s.dialogue_lines?.length)
      dlg = '<div class="scene-dialogue">' + s.dialogue_lines.map(d =>
        `<div><span class="dialogue-speaker">${d.speaker}</span> <span style="color:var(--text-muted);font-size:10px">${d.emotion||''}</span><br/>${esc(d.text)}</div>`
      ).join('') + '</div>';
    return `<div class="scene-card"><div class="scene-header"><span class="scene-num">Scene ${s.scene_number}</span><span class="scene-duration">${s.duration}</span></div><div class="scene-plot">${esc(s.narrative_summary || s.plot_description || s.description || '')}</div><div class="scene-meta">${chars}${moodTag}${camTag}</div>${dlg}</div>`;
  }).join('');
}

// Store unit data for modal access
let unitUidList = [];

export function renderUnits(data) {
  const cp = data.checkpoint, grid = document.getElementById('units-grid');
  resetUnitDataMap();
  unitUidList = [];
  if (!cp?.script?.work_units?.length) {
    grid.innerHTML = `<div class="empty-state"><svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="2" width="20" height="20" rx="2.18"/><line x1="7" y1="2" x2="7" y2="22"/><line x1="17" y1="2" x2="17" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/></svg><div>${t('monitor.waiting')}</div></div>`;
    return;
  }
  const projectName = data.storyboard_name || '';
  const runId = data.run_id || '';
  const base = (projectName && runId) ? `/repo-media/${projectName}/${runId}` : '/media';
  window.unitMediaBase = base;

  grid.innerHTML = cp.script.work_units.map(u => {
    const status = getUnitStatus(u);
    const displayAttempts = getDisplayAttempts(u);
    const attemptVideos = displayAttempts.map(a =>
      a.output_path ? `${base}/${a.output_path.split('/').pop()}` : null
    );
    const bestIdx = getBestAttemptIndex(u);
    const vp = bestIdx >= 0 ? attemptVideos[bestIdx] : null;
    const uid = `unit-${u.unit_id}`;

    // Store for modal and navigation
    setUnitDataMap({ ...unitDataMap, [uid]: { unit: u, displayAttempts, attemptVideos, bestIdx, status } });
    unitUidList.push(uid);

    let videoHTML = vp
      ? `<div class="unit-video-wrap" id="${uid}-video" onclick="playVideo(this.querySelector('video').src)"><video src="${vp}" preload="metadata" muted></video><div class="play-overlay"><div class="play-btn"><svg width="20" height="20" viewBox="0 0 24 24" fill="white"><polygon points="5,3 19,12 5,21"/></svg></div></div>${status==='critiquing'?'<div class="critique-overlay"><div class="gen-spinner" style="border-color:rgba(167,139,250,0.3);border-top-color:#a78bfa"></div>'+t('monitor.critiquing_unit')+'</div>':''}</div>`
      : (() => {
          const selectedAttempt = displayAttempts?.[bestIdx];
          let placeholderContent;
          if (status === 'in_progress') {
            const inProgressAttempt = (u.attempts||[]).find(a => a.status === 'in_progress' && !isAttemptPlaceholder(a));
            const toolUsed = inProgressAttempt?.tool_used || '';
            const meta = inProgressAttempt?.metadata || {};
            const queueStatus = meta.queue_status;   // 1=排队, 2=生成中
            const queueIdx = meta.queue_idx;
            const queueLength = meta.queue_length;
            const estimatedTime = meta.estimated_time;

            let taskLabel;
            if (toolUsed.includes('sora')) {
              taskLabel = '生成视频 (Sora)';
            } else if (toolUsed.includes('seeddance')) {
              taskLabel = '生成视频';
            } else if (toolUsed) {
              taskLabel = '生成视频';
            } else {
              taskLabel = '生成中';
            }

            // 排队中：显示排队位置、总人数、预估时间
            if (queueStatus === 1 && queueIdx != null) {
              const estMins = queueLength != null ? Math.round(queueLength / 5000 * 30) : null;
              const estText = estMins != null ? `约 ${estMins} 分钟` : '';
              placeholderContent = `<div class="unit-placeholder-generating">
                <div class="gen-spinner" style="border-color:rgba(99,179,237,0.3);border-top-color:#63b3ed"></div>
                <span style="color:var(--text-muted);font-size:12px;text-align:center;line-height:1.6">
                  排队中<br>
                  <span style="color:var(--accent);font-size:13px;font-weight:600">#${queueIdx}</span>
                  <span style="color:var(--text-muted)"> / ${queueLength != null ? queueLength.toLocaleString() : '—'}</span>
                  ${estText ? `<br><span style="color:var(--text-muted);font-size:11px">预计等待 ${estText}</span>` : ''}
                </span>
              </div>`;
            } else if (queueStatus === 2) {
              // 生成中：显示预估剩余时间
              const etText = estimatedTime != null ? `约 ${Math.round(estimatedTime)} 秒` : '';
              placeholderContent = `<div class="unit-placeholder-generating">
                <div class="gen-spinner"></div>
                <span style="color:var(--text-muted);font-size:12px;text-align:center;line-height:1.6">
                  ${taskLabel}<br>
                  ${etText ? `<span style="font-size:11px">预计还需 ${etText}</span>` : ''}
                </span>
              </div>`;
            } else {
              placeholderContent = `<div class="unit-placeholder-generating"><div class="gen-spinner"></div><span class="${taskLabel === '生成中' ? 'gen-dots' : ''}">${taskLabel}</span></div>`;
            }
          } else {
            const msg = buildAttemptPlaceholderMessage(u, selectedAttempt, status);
            const color = status === 'queued' || isDraftRegenAttempt(selectedAttempt) || isQueuedRegenAttempt(selectedAttempt) ? 'var(--accent)' : 'var(--text-muted)';
            placeholderContent = `<div class="unit-placeholder-pending" style="color:${color}"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>${msg}</div>`;
          }
          return `<div class="unit-video-wrap" id="${uid}-video"><div class="unit-video-placeholder"><svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="opacity:0.3"><rect x="2" y="2" width="20" height="20" rx="2.18"/><polygon points="10,8 16,12 10,16"/></svg>${placeholderContent}</div></div>`;
        })();

    const viewingIdx = bestIdx;
    const attempts = displayAttempts.map((a, i) => {
      const hasVideo = attemptVideos[i] != null;
      const isViewing = i === viewingIdx;
      const isFinal = u.final_attempt_id != null && Number(u.final_attempt_id) === Number(a.attempt_id);
      const clickAttr = `onclick="event.stopPropagation(); switchAttemptVideo('${uid}', ${i})"`;
      return `<div class="attempt-dot ${getAttemptVisualStatus(a)}${isViewing?' viewing':''}${isFinal?' final-picked':''}" data-uid="${uid}" data-idx="${i}" title="${a.tool_used||''} — ${a.metadata?.regen_status || a.status}${hasVideo?'':' (no video)'}${isFinal?' (final selected)':''}" ${clickAttr}>${a.attempt_id}</div>`;
    }).join('');

    const critiqueHTML = window.buildCritiqueHTML(displayAttempts?.[bestIdx]);
    const promptText = getAttemptPrompt(u, bestIdx);
    const imageRefMap = getAttemptImageRefMap(u, bestIdx);
    const imageRefAssets = getAttemptImageRefAssets(u, bestIdx);
    const maxAttemptsHint = getAttemptMaxAttempts(u, bestIdx);

    return `<div class="unit-card" id="${uid}-card">
      ${videoHTML}
      <div class="unit-info">
        <div class="unit-header"><div class="unit-id">Unit #${u.unit_id}</div><div class="unit-status ${status}">${getUnitStatusLabel(status)}</div></div>
        ${u.group_name?`<div class="unit-group">${esc(u.group_name)}</div>`:''}
        <div class="unit-scenes">Scenes: <span>${(u.scene_numbers||[]).join(', ')}</span> &middot; ${u.duration_seconds}s${maxAttemptsHint ? ` &middot; ${t('monitor.max_attempts').replace('{0}', maxAttemptsHint)}` : ''}</div>
        <div class="unit-attempts">${attempts}</div>
        ${renderAttemptMetaBadges(u, displayAttempts?.[bestIdx])}
        <div id="${uid}-critique">${critiqueHTML}</div>
        <div class="unit-prompt-preview" id="${uid}-prompt">${renderPromptWithRefs(promptText, imageRefMap, imageRefAssets)}</div>
        <button class="unit-expand-btn" onclick="openUnitModal('${uid}')">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg>
          Expand
        </button>
      </div>
    </div>`;
  }).join('');
}

export function renderAssets(data) {
  const sb = data.storyboard;
  if (!sb) return;
  const projectName = data.storyboard_name || '';
  const runId = data.run_id || '';
  const mediaBase = (projectName && runId) ? `/repo-media/${projectName}/${runId}` : '/media';

  const va = sb.video_analysis || {};
  document.getElementById('style-info').innerHTML = [va.style,va.theme,va.tone,...(va.key_elements||[])].filter(Boolean).map(t => `<span class="style-tag">${esc(t)}</span>`).join('');
  document.getElementById('characters-list').innerHTML = (sb.characters||[]).map(c => {
    const src = c.image_path ? `/asset?path=${encodeURIComponent(c.image_path)}` : '';
    const av = src ? `<img class="char-avatar" src="${src}" onclick="showImage('${src}')" />` : `<div class="char-avatar" style="background:var(--accent-glow);display:flex;align-items:center;justify-content:center;font-weight:700;color:var(--accent)">${c.name[0]}</div>`;
    return `<div class="char-info"><div class="char-info-header">${av}<div><div class="char-name">${esc(c.name)}</div><div class="char-personality">${esc(c.personality||'')}</div></div></div><div class="char-desc">${esc(c.description||'')}</div></div>`;
  }).join('');
  document.getElementById('locations-grid').innerHTML = (sb.locations||[]).map(l => {
    const src = l.image_path ? (l.image_path.startsWith('/')?`/asset?path=${encodeURIComponent(l.image_path)}`:`${mediaBase}/${l.image_path.split('/').pop()}`) : '';
    if (!src) return `<div class="asset-card asset-card-wide" style="background:var(--bg-glass);display:flex;align-items:center;justify-content:center;color:var(--text-muted);font-size:12px">${esc(l.name)}</div>`;
    return `<div class="asset-card asset-card-wide" onclick="showImage('${src}')"><img src="${src}" loading="lazy"/><div class="asset-label">${esc(l.name)}</div></div>`;
  }).join('');
  const media = data.media || {};
  if (media.charsheets?.length) {
    document.getElementById('charsheets-section').style.display = 'block';
    document.getElementById('charsheets-grid').innerHTML = media.charsheets.map(f => `<div class="asset-card" onclick="showImage('${mediaBase}/${f}')"><img src="${mediaBase}/${f}" loading="lazy"/><div class="asset-label">${esc(f.replace('charsheet_','').replace('.png',''))}</div></div>`).join('');
  }
  if (media.locsheets?.length) {
    document.getElementById('locsheets-section').style.display = 'block';
    document.getElementById('locsheets-grid').innerHTML = media.locsheets.map(f => `<div class="asset-card asset-card-wide" onclick="showImage('${mediaBase}/${f}')"><img src="${mediaBase}/${f}" loading="lazy"/><div class="asset-label">${esc(f.replace('locsheet_','').replace('.png',''))}</div></div>`).join('');
  }
}

// ── Lightbox helpers (re-exported for window binding) ────────────
export function playVideo(src) {
  const lb = document.getElementById('video-lightbox'), vid = document.getElementById('lightbox-video');
  vid.src = src; lb.classList.add('show'); vid.play();
}

export function openImageLightbox(src) {
  document.getElementById('img-lightbox-img').src = src;
  document.getElementById('img-lightbox').classList.add('show');
}

export function closeImageLightbox() {
  document.getElementById('img-lightbox').classList.remove('show');
}

// ── Render video jobs panel (targeted update — only refreshes the sidebar) ──
export function renderVideoJobsPanel() {
  const jobsContent = document.querySelector('#monitor-jobs-panel .monitor-jobs-content');
  if (!jobsContent) {
    renderMonitor();
    return;
  }
  const activeJobs = (videoJobsData || []).filter(j => ['queued','running','paused','stopped','crashed','interrupted'].includes(j.status));
  const recentRunsHTML = _buildRecentRunsHTML();
  jobsContent.innerHTML = buildActiveJobsListHTML(activeJobs) + recentRunsHTML;

  const headerTitle = document.querySelector('#monitor-jobs-panel .monitor-jobs-header-title');
  if (headerTitle) {
    headerTitle.textContent = `${t('monitor.active_jobs')} (${activeJobs.length})`;
  }
}
