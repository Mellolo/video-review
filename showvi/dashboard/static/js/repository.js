// ── repository.js — Repository View ─────────────────────────────
import {
  repoData, setRepoData,
  videoJobsData, setVideoJobsData,
  activeProject, setActiveProject,
  monitorBrowseMode, setMonitorBrowseMode,
  selectedVideoJobId, setSelectedVideoJobId,
  ws,
} from './state.js';
import { esc } from './utils.js';
import { t, updateProjectsToggleLabel } from './i18n.js';

// ── Load Repository ─────────────────────────────────────────────
export async function loadRepository() {
  try {
    const [repoRes, vjRes] = await Promise.all([
      fetch('/api/repository').then(r => r.json()),
      fetch('/api/generate/jobs').then(r => r.json()),
    ]);
    setRepoData(repoRes);
    setVideoJobsData(vjRes);
    renderRepoSidebar();
    _renderRepoJobsSections();

    // Always select a project to populate the content area
    if (repoData.length > 0) {
      const target = activeProject && repoData.find(p => p.project_name === activeProject)
        ? activeProject
        : repoData[0].project_name;
      selectProject(target);
    }
  } catch (e) { console.error('Failed to load repository', e); }
}

// ── Render job sections (completed) ─────────────────────────────
function _renderRepoJobsSections() {
  const completed = videoJobsData.filter(j => j.status === 'completed');

  document.getElementById('repo-resumable-section')?.remove();
  document.getElementById('repo-completed-section')?.remove();

  const content = document.getElementById('repo-content');

  let sectionsHTML = '';

  if (completed.length) {
    sectionsHTML += `
      <div id="repo-completed-section" style="padding:16px 20px;border-bottom:1px solid var(--border-subtle);">
        <div class="section-title" style="margin-bottom:12px">${t('repo.recently_completed')}</div>
        <div class="vj-cards">
          ${completed.slice(0, 6).map(j => {
            const p = j.progress || {};
            const stageLabel = window.getVideoStageLabel(p, j.status);
            const progressHint = window.getVideoProgressHint(p);
            const progressText = window.getVideoProgressText(p);
            return `<div class="vj-card" style="cursor:pointer" onclick="selectProject('${j.storyboard_name}')">
              <div class="vj-card-top">
                <div class="vj-card-title">${esc(j.title || j.storyboard_name)}</div>
                <span class="vj-status ${j.status}">${j.status}</span>
              </div>
              <div class="vj-progress-row">
                <div class="vj-progress-bar"><div class="vj-progress-fill" style="width:100%"></div></div>
                <div class="vj-progress-text">${progressText}</div>
              </div>
              <div class="vj-stage-row">
                <div class="vj-stage-label">${stageLabel}</div>
                <div class="vj-stage-value">${progressHint}</div>
              </div>
              <div class="vj-meta">
                <span>${p.attempts || 0} attempts</span>
                <span>${j.started_at ? new Date(j.started_at).toLocaleString() : ''}</span>
              </div>
            </div>`;
          }).join('')}
        </div>
      </div>`;
  }

  if (sectionsHTML) {
    const temp = document.createElement('div');
    temp.innerHTML = sectionsHTML;
    while (temp.firstChild) {
      content.insertBefore(temp.firstChild, content.firstChild);
    }
  }
}

// ── Render sidebar ──────────────────────────────────────────────
export function renderRepoSidebar() {
  if (!repoData) return;
  document.getElementById('repo-project-count').textContent = repoData.length;
  const list = document.getElementById('repo-project-list');

  list.innerHTML = repoData.map(p => {
    const meta = p.storyboard_meta;
    const title = meta?.title || p.project_name;
    const theme = meta?.theme || '';
    return `
      <button class="repo-project-btn" data-project="${p.project_name}" onclick="selectProject('${p.project_name}')">
        <div class="repo-project-name"><span class="title-text">${esc(title)}</span></div>
        <div class="repo-project-stats">
          <span><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z"/><path d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>${p.total_videos} ${t('repo.videos')}</span>
          <span><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>${p.total_images} ${t('repo.imgs')}</span>
          <span>${p.total_runs} ${t('repo.runs')}</span>
        </div>
        ${theme ? `<div class="repo-project-theme">${esc(theme)}</div>` : ''}
      </button>`;
  }).join('');
}

// ── Select project ──────────────────────────────────────────────
export function selectProject(projectName) {
  setActiveProject(projectName);
  updateProjectsToggleLabel();
  document.querySelectorAll('.repo-project-btn').forEach(b => b.classList.toggle('active', b.dataset.project === projectName));

  const project = repoData.find(p => p.project_name === projectName);
  if (!project) return;

  const meta = project.storyboard_meta;
  const content = document.getElementById('repo-content');

  // Save job sections before replacing content
  const completedSection = document.getElementById('repo-completed-section');
  const savedSections = [];
  if (completedSection) savedSections.push(completedSection.cloneNode(true));

  const charTags = (meta?.characters || []).map(c => `<span class="style-tag">${esc(c)}</span>`).join('');
  const styleTags = [meta?.style, meta?.theme, meta?.tone].filter(Boolean).map(s => `<span class="style-tag">${esc(s)}</span>`).join('');

  let runsHTML = project.runs.map(run => {
    const progressCls = run.progress >= 100 ? 'progress-badge' : 'progress-partial';
    const progressLabel = run.units_total ? `${run.units_completed}/${run.units_total}` : '';

    // Videos
    let videosHTML = '';
    if (run.segments.length || run.final) {
      const allVids = run.final ? [run.final, ...run.segments] : run.segments;
      videosHTML = `
        <div class="repo-videos-label">${t('repo.videos')} (${allVids.length})</div>
        <div class="repo-video-grid">
          ${allVids.map(f => {
            const src = `/repo-media/${projectName}/${run.run_id}/${f}`;
            const label = f === run.final ? t('repo.final') : f.replace('.mp4','');
            return `<div class="repo-video-thumb" onclick="playVideo('${src}')">
              <video src="${src}" preload="metadata" muted></video>
              <div class="play-overlay"><div class="play-btn"><svg width="18" height="18" viewBox="0 0 24 24" fill="white"><polygon points="5,3 19,12 5,21"/></svg></div></div>
              <div class="repo-video-label"><span>${label}</span>${f === run.final ? `<span style="color:var(--success);font-weight:600">${t('repo.final')}</span>` : ''}</div>
            </div>`;
          }).join('')}
        </div>`;
    }

    // Images
    let imagesHTML = '';
    const allImgs = [...run.charsheets, ...run.locsheets, ...run.propsheets];
    if (allImgs.length) {
      imagesHTML = `
        <div class="repo-videos-label">${t('repo.images')} (${allImgs.length})</div>
        <div class="repo-image-grid">
          ${allImgs.map(f => {
            const src = `/repo-media/${projectName}/${run.run_id}/${f}`;
            const label = f.replace('.png','').replace('charsheet_','').replace('locsheet_','').replace('propsheet_','');
            return `<div class="repo-image-thumb" onclick="showImage('${src}')">
              <img src="${src}" loading="lazy"/>
              <div class="asset-label">${esc(label)}</div>
            </div>`;
          }).join('')}
        </div>`;
    }

    return `
      <div class="repo-run-card" id="run-${run.run_id}">
        <div class="repo-run-header" onclick="toggleRun('${run.run_id}')">
          <div class="repo-run-id">
            ${run.run_id}
            <span class="repo-run-date">${run.date}</span>
          </div>
          <div class="repo-run-badges">
            ${run.video_count ? `<span class="repo-badge videos">${run.video_count} ${t('repo.videos')}</span>` : ''}
            ${run.image_count ? `<span class="repo-badge images">${run.image_count} ${t('repo.images')}</span>` : ''}
            ${progressLabel ? `<span class="repo-badge ${progressCls}">${progressLabel} ${t('repo.units')}</span>` : ''}
            <button class="repo-monitor-btn" onclick="event.stopPropagation(); openRunInMonitor('${projectName}','${run.run_id}')" title="${t('repo.open_in_monitor')}">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
              ${t('repo.monitor')}
            </button>
            <svg class="expand-arrow" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
          </div>
        </div>
        <div class="repo-run-body">
          ${videosHTML}
          ${imagesHTML}
        </div>
      </div>`;
  }).join('');

  content.innerHTML = `
    <div class="repo-project-header">
      <div class="repo-project-title">${esc(meta?.title || projectName)}</div>
      <div class="repo-project-subtitle">${project.total_runs} ${t('repo.runs')} &middot; ${project.total_videos} ${t('repo.videos')} &middot; ${project.total_images} ${t('repo.images')}${meta?.total_scenes ? ` &middot; ${meta.total_scenes} ${t('misc.scenes')}` : ''}</div>
      <div class="repo-project-tags">${styleTags}${charTags}</div>
    </div>
    <div class="repo-totals">
      <div class="repo-total-item"><strong>${project.total_videos}</strong> ${t('repo.videos')}</div>
      <div class="repo-total-item"><strong>${project.total_images}</strong> ${t('repo.images')}</div>
      <div class="repo-total-item"><strong>${project.total_runs}</strong> ${t('repo.runs')}</div>
    </div>
    <div class="repo-runs-container">${runsHTML}</div>`;

  // Restore job sections at the top
  savedSections.reverse().forEach(section => {
    content.insertBefore(section, content.firstChild);
  });

  // Auto-expand first run
  const first = project.runs[0];
  if (first) toggleRun(first.run_id);
}

// ── Toggle run expand/collapse ──────────────────────────────────
export function toggleRun(runId) {
  const card = document.getElementById('run-' + runId);
  if (!card) return;
  card.classList.toggle('expanded');
}

// ── Open run in monitor tab ─────────────────────────────────────
export async function openRunInMonitor(projectName, runId) {
  setMonitorBrowseMode({ project: projectName, run_id: runId });
  setSelectedVideoJobId(null);
  window.browseSelectedUnit = 0;

  window.switchTab('monitor');

  try {
    const res = await fetch(`/api/run-detail/${encodeURIComponent(projectName)}/${encodeURIComponent(runId)}`);
    if (res.ok) {
      const detail = await res.json();
      window._applyMonitorData({
        storyboard: detail.storyboard,
        checkpoint: detail.checkpoint,
        media: detail.media,
        run_id: detail.run_id,
        storyboard_name: detail.project_name,
        all_runs: [],
      });
      window.renderMonitor();
    }
  } catch (e) {
    console.error('Failed to load run detail', e);
  }

  if (ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'switch_project', project_name: projectName, run_id: runId }));
  }
}

// ── Convenience alias ───────────────────────────────────────────
export function refreshRepositoryData() {
  return loadRepository();
}

// ── Register on window for inline onclick handlers ──────────────
window.loadRepository = loadRepository;
window.renderRepoSidebar = renderRepoSidebar;
window.selectProject = selectProject;
window.toggleRun = toggleRun;
window.openRunInMonitor = openRunInMonitor;
window.refreshRepositoryData = refreshRepositoryData;
