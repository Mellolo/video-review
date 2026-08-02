// ── nav.js — Navigation tabs & project drawer ──────────────────
import {
  currentTab, setCurrentTab,
  monitorBrowseMode, setMonitorBrowseMode,
  activeProject, setActiveProject,
  repoData, projectList, setProjectList,
  monitorJobsPanelCollapsed, setMonitorJobsPanelCollapsed,
  videoJobsData,
  currentData,
  assetData, setAssetData,
} from './state.js';
import { t } from './i18n.js';
import { updateProjectsToggleLabel } from './i18n.js';
import { esc } from './utils.js';

// ── Tabs ───────────────────────────────────────────────────────
export function switchTab(tab) {
  const previousTab = currentTab;
  const enteringMonitor = previousTab !== 'monitor' && tab === 'monitor';
  setCurrentTab(tab);

  document.querySelectorAll('.nav-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  const viewEl = document.getElementById('view-' + tab);
  if (viewEl) viewEl.classList.add('active');

  if (previousTab === 'monitor' && tab !== 'monitor' && drawerOpen) {
    toggleDrawer(false);
  }

  if (tab === 'repo') window.loadRepository();
  if (tab === 'create') { window.loadStoryboardList(); window.updateCreateSubmitButtons?.(); window.syncCreateTabJobStatus?.(); }
  if (tab === 'storyboards') window.loadStoryboardList();
  if (tab === 'assets') window.loadAssetLibrary();
  if (tab === 'pipeline') window.syncPipelineView?.();
  if (tab === 'monitor') {
    if (enteringMonitor && monitorJobsPanelCollapsed) {
      setMonitorJobsPanelCollapsed(false);
    }
    window.renderMonitor();
    Promise.all([
      window.loadVideoJobs(),
      window.refreshRepositoryData(),
      assetData ? Promise.resolve(assetData) : fetch('/api/assets').then(r => r.json()).then(data => { setAssetData(data); return data; }).catch(() => null),
    ]).then(() => {
      window.renderMonitor();
      if (drawerOpen) renderDrawerList();
    }).catch((e) => console.error('Failed to refresh monitor data', e));
  }
}

// ── Drawer ─────────────────────────────────────────────────────
export let drawerOpen = false;
export function setDrawerOpen(v) { drawerOpen = v; }

export function toggleDrawer(force) {
  const willOpen = force !== undefined ? force : !drawerOpen;
  if (willOpen && !drawerOpen) {
    Promise.all([
      window.refreshRepositoryData(),
      loadProjectList(),
    ]).then(() => renderDrawerList()).catch((e) => console.error('Failed to refresh drawer data', e));
  }
  drawerOpen = willOpen;
  document.getElementById('projects-drawer')?.classList.toggle('open', drawerOpen);
  document.getElementById('drawer-backdrop')?.classList.toggle('show', drawerOpen);
  document.getElementById('projects-toggle')?.classList.toggle('open', drawerOpen);
}

export function selectProjectRun(projectName, runId) {
  setMonitorBrowseMode(null);
  setActiveProject(projectName);
  updateProjectsToggleLabel();
  const msg = { type: 'switch_project', project_name: projectName };
  if (runId) msg.run_id = runId;
  window._wsSend(msg);
  highlightDrawerActive(projectName, runId);
  toggleDrawer(false);
}

export function selectDrawerProjectLatest(projectName) {
  const rd = repoData;
  const repoProject = rd?.find(p => p.project_name === projectName);
  const latestRunId = repoProject?.runs?.[0]?.run_id || undefined;
  selectProjectRun(projectName, latestRunId);
}

export function highlightDrawerActive(projectName, runId) {
  document.querySelectorAll('.drawer-project-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.project === projectName)
  );
  document.querySelectorAll('.drawer-run-btn').forEach(b => {
    if (runId) b.classList.toggle('active', b.dataset.project === projectName && b.dataset.run === runId);
    else b.classList.remove('active');
  });
}

export async function loadProjectList() {
  try {
    const r = await fetch('/api/projects');
    setProjectList(await r.json());
    renderDrawerList();
  } catch (e) { console.error('Failed to load projects', e); }
}

export function renderDrawerList() {
  const container = document.getElementById('drawer-list');
  const currentProject = currentData?.storyboard_name || '';
  const currentRun = currentData?.run_id || '';
  const pl = projectList;
  document.getElementById('drawer-count').textContent = `${pl.length} ${t('misc.projects')}`;
  updateProjectsToggleLabel();

  container.innerHTML = pl.map(p => {
    const label = p.title !== p.project_name ? p.title : p.project_name.replace('_storyboard', '');
    const isActive = p.project_name === currentProject;
    const initial = label.charAt(0).toUpperCase();

    // Fetch runs for this project from repo data or use run_count
    let runsHTML = '';
    const rd = repoData;
    const repoProject = rd?.find(rp => rp.project_name === p.project_name);
    if (repoProject) {
      runsHTML = repoProject.runs.map((run, i) => {
        const isRunActive = isActive && run.run_id === currentRun;
        return `<button class="drawer-run-btn${isRunActive ? ' active' : ''}" data-project="${p.project_name}" data-run="${run.run_id}"
          onclick="event.stopPropagation(); selectProjectRun('${p.project_name}','${run.run_id}')">
          <span class="drawer-run-dot"></span>
          ${run.date}
          <span style="margin-left:auto;font-size:10px;color:var(--text-muted)">${run.video_count}v ${run.image_count}i</span>
        </button>`;
      }).join('');
    }

    return `<div class="drawer-project expanded" data-project="${p.project_name}">
      <button type="button" class="drawer-project-btn${isActive ? ' active' : ''}" data-project="${p.project_name}"
        onclick="selectDrawerProjectLatest('${p.project_name}')">
        <div class="drawer-project-icon">${initial}</div>
        <div class="drawer-project-info">
          <div class="drawer-project-title">${esc(label)}</div>
          <div class="drawer-project-meta">${p.run_count} ${t('misc.runs')}${p.theme ? ' · ' + esc(p.theme) : ''}</div>
        </div>
      </button>
      <div class="drawer-runs">${runsHTML}</div>
    </div>`;
  }).join('');
}
