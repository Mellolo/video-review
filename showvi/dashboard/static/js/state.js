/**
 * state.js — 全局状态变量 & browse pane 刷新辅助函数
 * 从 index.html 提取为 ES module
 */

// ── State ──────────────────────────────────────────────────────
export let currentData = null;
export let ws = null;
export let reconnectTimer = null;
export let repoData = null;
export let projectList = [];
export let activeProject = null;
export let videoJobsData = [];
export let selectedVideoJobId = null;
export let currentTab = document.querySelector('.nav-btn.active')?.dataset.tab || 'create';
export let monitorBrowseMode = null; // { project, run_id } when viewing from History
export let currentAssetTab = 'characters';
export let assetData = null;
export let assetDataLoadingPromise = null;
export let voiceData = null;
export let currentAudioEl = null;
export let assetUploadFile = null;
export let currentUser = null;
export const nativeFetch = window.fetch.bind(window);
export let assetUploadContext = {
  mode: 'assets-library',
  category: null,
  afterUpload: null,
};
export let promptAssetPickerContext = null;
export let monitorActionState = {
  concat: false,
  deleteFinal: false,
};
export let concatMode = localStorage.getItem('concat_mode') || 'hard';
export let concatFadeSeconds = Number(localStorage.getItem('concat_fade_seconds') || '0.5');
if (!Number.isFinite(concatFadeSeconds) || concatFadeSeconds <= 0) concatFadeSeconds = 0.5;

/** 监控页左侧「活跃任务」栏折叠状态 */
export let monitorJobsPanelCollapsed = localStorage.getItem('dashboard_monitor_jobs_collapsed') === '1';

/** WebSocket 刷新用：对比签名缓存 */
export let _browseLiveSigCache = null;

// ── Setter functions (供其他模块修改 state) ──────────────────────
export function setCurrentData(v) { currentData = v; }
export function setWs(v) { ws = v; }
export function setReconnectTimer(v) { reconnectTimer = v; }
export function setRepoData(v) { repoData = v; }
export function setProjectList(v) { projectList = v; }
export function setActiveProject(v) { activeProject = v; }
export function setVideoJobsData(v) { videoJobsData = v; }
export function setSelectedVideoJobId(v) { selectedVideoJobId = v; }
export function setCurrentTab(v) { currentTab = v; }
export function setMonitorBrowseMode(v) { monitorBrowseMode = v; }
export function setCurrentAssetTab(v) { currentAssetTab = v; }
export function setAssetData(v) { assetData = v; }
export function setAssetDataLoadingPromise(v) { assetDataLoadingPromise = v; }
export function setVoiceData(v) { voiceData = v; }
export function setCurrentAudioEl(v) { currentAudioEl = v; }
export function setAssetUploadFile(v) { assetUploadFile = v; }
export function setCurrentUser(v) { currentUser = v; }
export function setAssetUploadContext(v) { assetUploadContext = v; }
export function setPromptAssetPickerContext(v) { promptAssetPickerContext = v; }
export function setMonitorActionState(v) { monitorActionState = v; }
export function setConcatMode(v) { concatMode = v; }
export function setConcatFadeSeconds(v) { concatFadeSeconds = v; }
export function set_browseLiveSigCache(v) { _browseLiveSigCache = v; }

// ── Monitor jobs panel collapse ─────────────────────────────────
export function setMonitorJobsPanelCollapsed(collapsed) {
  monitorJobsPanelCollapsed = !!collapsed;
  localStorage.setItem('dashboard_monitor_jobs_collapsed', monitorJobsPanelCollapsed ? '1' : '0');
}

// ── Browse pane debounce / refresh helpers ──────────────────────
let _debounceBrowsePaneRefreshTimer = null;

export function clearDebouncedBrowsePaneRefresh() {
  if (_debounceBrowsePaneRefreshTimer) {
    clearTimeout(_debounceBrowsePaneRefreshTimer);
    _debounceBrowsePaneRefreshTimer = null;
  }
}

export function scheduleDebouncedBrowsePaneRefresh() {
  clearDebouncedBrowsePaneRefresh();
  _debounceBrowsePaneRefreshTimer = setTimeout(() => {
    _debounceBrowsePaneRefreshTimer = null;
    const pane = document.getElementById('monitor-detail-pane');
    if (
      pane
      && document.getElementById('view-monitor')?.classList.contains('active')
      && shouldRefreshMonitorBrowseInPlace(pane)
    ) {
      if (maybeApplyIncrementalBrowseWsRefresh(pane)) return;
      window._renderMonitorBrowse(pane);
    }
  }, 750);
}

export function computeBrowseRefreshSigsFromState(st) {
  const finSig = `${currentData?.storyboard_name}|${currentData?.run_id}|${st.media?.final || ''}|${st.media?.final_mtime ?? ''}`;
  const filmSig = `${st.unitInfos.map((info) => {
    const u = info.unit;
    const att = (info.displayAttempts || []).map(a =>
      `${a.attempt_id}:${a.status}:${a.output_path || ''}:${a.critique_result ? 1 : 0}`
    ).join(',');
    return `${u.unit_id}:${window.getUnitStatus(u)}:${info.videoSrc || ''}:${att}`;
  }).join('|')}||F:${st.media?.final || ''}:${st.media?.final_mtime ?? ''}`;
  const tbSig = `${st.completedCount}/${st.units.length}:${st.availableClipCount}:${st.hasFinal}:${st.currentActivityHTML}`;
  // 设定图签名：charsheets/locsheets/propsheets 文件列表变化时触发 sidebar 刷新
  const charSig = [
    ...(st.media?.charsheets || []),
    ...(st.media?.locsheets || []),
    ...(st.media?.propsheets || []),
  ].sort().join(',');
  let unitSig = '';
  if (window.browseSelectedUnit >= 0 && window.browseSelectedUnit < st.unitInfos.length) {
    const info = st.unitInfos[window.browseSelectedUnit];
    const u = info.unit;
    const att = (info.displayAttempts || []).map(a =>
      `${a.attempt_id}:${a.status}:${a.output_path || ''}:${a.critique_result ? 1 : 0}`
    ).join(',');
    unitSig = `${u.unit_id}:${window.getUnitStatus(u)}:${info.bestIdx}:${att}`;
  }
  return { filmSig, finSig, tbSig, unitSig, charSig };
}

// ── Browse toolbar & filmstrip patch helpers ────────────────────
export function patchBrowseToolbarAndFilmstripFromState(container, st) {
  const toolbar = container.querySelector('.monitor-run-toolbar');
  const scroll = container.querySelector('#browse-filmstrip-scroll');
  if (!toolbar || !scroll) return false;
  const wrap = document.createElement('div');
  wrap.innerHTML = window._browseToolbarOuterHTML(st).trim();
  toolbar.replaceWith(wrap.firstElementChild);
  scroll.innerHTML = st.filmstripHTML + st.finalCardHTML;
  window._browseUnitInfos = st.unitInfos;
  window._browseMediaBase = st.mediaBase;
  window._browseSb = st.sb;
  window._browseFinalSrc = st.finalSrc || null;
  document.querySelectorAll('.browse-filmstrip-item:not(.final-card)').forEach((el, i) =>
    el.classList.toggle('active', i === window.browseSelectedUnit && window.browseSelectedUnit >= 0)
  );
  document.querySelectorAll('.browse-filmstrip-item.final-card').forEach(el =>
    el.classList.toggle('active', window.browseSelectedUnit === -1)
  );
  const hdr = container.querySelector('.browse-filmstrip-header span');
  if (hdr) {
    hdr.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="vertical-align:-2px"><polygon points="5,3 19,12 5,21"/></svg> Timeline &middot; ${st.units.length} units`;
  }
  window.syncConcatModeControls();
  window.syncMonitorActionButtons();
  return true;
}

export function maybeApplyIncrementalBrowseWsRefresh(pane) {
  if (!pane || !currentData) return false;
  let st;
  try {
    st = window._computeBrowseRenderState();
  } catch (e) {
    return false;
  }
  const sigs = computeBrowseRefreshSigsFromState(st);
  const prev = _browseLiveSigCache;

  if (prev && sigs.filmSig === prev.filmSig && sigs.finSig === prev.finSig && sigs.tbSig === prev.tbSig && sigs.unitSig === prev.unitSig && sigs.charSig === prev.charSig) {
    return true;
  }

  const onFinal = window.browseSelectedUnit === -1 && pane.querySelector('#browse-detail-area .final-stage-shell');
  const onUnitDetail = window.browseSelectedUnit >= 0 && pane.querySelector('#browse-detail-area .browse-detail-shell');

  if (onFinal) {
    if (!prev || sigs.finSig !== prev.finSig) return false;
    patchBrowseToolbarAndFilmstripFromState(pane, st);
    if (!prev || sigs.charSig !== prev.charSig) _patchBrowseSidebarCharsheets(pane, st);
    _browseLiveSigCache = sigs;
    return true;
  }

  if (onUnitDetail) {
    const unitChanged = !prev || sigs.unitSig !== prev.unitSig;
    if (unitChanged) {
      window._browseUnitInfos = st.unitInfos;
      window._browseMediaBase = st.mediaBase;
      window._browseSb = st.sb;
      window._browseFinalSrc = st.finalSrc || null;
      window.browseSelectUnit(window.browseSelectedUnit);
    }
    if (!prev || sigs.filmSig !== prev.filmSig || sigs.tbSig !== prev.tbSig || sigs.finSig !== prev.finSig) {
      patchBrowseToolbarAndFilmstripFromState(pane, st);
    }
    if (!prev || sigs.charSig !== prev.charSig) _patchBrowseSidebarCharsheets(pane, st);
    _browseLiveSigCache = sigs;
    return true;
  }

  return false;
}

/**
 * 局部刷新 sidebar 中的设定图（charsheets/locsheets/propsheets）。
 * 只更新 img src，不重新渲染整个面板，避免打断视频播放。
 */
function _patchBrowseSidebarCharsheets(pane, st) {
  const sidebar = pane.querySelector('.browse-sidebar');
  if (!sidebar) return;
  const sb = st.sb || {};
  const media = st.media || {};
  const mediaBase = st.mediaBase || '';

  // 更新角色设定图
  (sb.characters || []).forEach(c => {
    const card = sidebar.querySelector(`#entity-card-char-${CSS.escape(c.name)}`);
    if (!card) return;
    const csMatches = (media.charsheets || []).filter(f => {
      const base = f.replace(/^charsheet_/i, '').replace(/(_v\d+)?\.png$/i, '');
      return base === c.name.replace(/\s/g, '_');
    });
    const csMatch = csMatches.length ? csMatches[csMatches.length - 1] : null;
    const imgSrc = csMatch ? `${mediaBase}/${csMatch}` : (c.image_path ? `/asset?path=${encodeURIComponent(c.image_path)}` : '');
    const avatarEl = card.querySelector('.browse-char-avatar img');
    if (avatarEl && imgSrc) {
      if (avatarEl.src !== imgSrc && !avatarEl.src.endsWith(imgSrc)) avatarEl.src = imgSrc;
    } else if (!avatarEl && imgSrc) {
      // 之前没有图片，现在有了，插入 avatar
      const avatarDiv = document.createElement('div');
      avatarDiv.className = 'browse-char-avatar';
      avatarDiv.onclick = () => window.showImage?.(imgSrc);
      avatarDiv.innerHTML = `<img src="${imgSrc}" loading="lazy"/>`;
      card.prepend(avatarDiv);
    }
  });

  // 更新场景设定图
  (sb.locations || []).forEach(l => {
    const card = sidebar.querySelector(`#entity-card-loc-${CSS.escape(l.name)}`);
    if (!card) return;
    const lsMatches = (media.locsheets || []).filter(f => {
      const base = f.replace(/^locsheet_/i, '').replace(/(_v\d+)?\.png$/i, '');
      return base === l.name.replace(/\s/g, '_');
    });
    const lsMatch = lsMatches.length ? lsMatches[lsMatches.length - 1] : null;
    const imgSrc = lsMatch ? `${mediaBase}/${lsMatch}` : (l.image_path ? `/asset?path=${encodeURIComponent(l.image_path)}` : '');
    const avatarEl = card.querySelector('.browse-loc-avatar img');
    if (avatarEl && imgSrc) {
      if (avatarEl.src !== imgSrc && !avatarEl.src.endsWith(imgSrc)) avatarEl.src = imgSrc;
    } else if (!avatarEl && imgSrc) {
      const avatarDiv = document.createElement('div');
      avatarDiv.className = 'browse-loc-avatar';
      avatarDiv.onclick = () => window.showImage?.(imgSrc);
      avatarDiv.innerHTML = `<img src="${imgSrc}" loading="lazy"/>`;
      card.prepend(avatarDiv);
    }
  });

  // 更新道具设定图
  (sb.props || []).forEach(p => {
    const card = sidebar.querySelector(`#entity-card-prop-${CSS.escape(p.name)}`);
    if (!card) return;
    const psMatch = (media.propsheets || []).find(f => f.toLowerCase().includes((p.name || '').toLowerCase().replace(/\s/g, '_')));
    const imgSrc = psMatch ? `${mediaBase}/${psMatch}` : (p.image_path ? `/asset?path=${encodeURIComponent(p.image_path)}` : '');
    const avatarEl = card.querySelector('.browse-prop-avatar img');
    if (avatarEl && imgSrc) {
      if (avatarEl.src !== imgSrc && !avatarEl.src.endsWith(imgSrc)) avatarEl.src = imgSrc;
    } else if (!avatarEl && imgSrc) {
      const avatarDiv = document.createElement('div');
      avatarDiv.className = 'browse-prop-avatar';
      avatarDiv.onclick = () => window.showImage?.(imgSrc);
      avatarDiv.innerHTML = `<img src="${imgSrc}" loading="lazy"/>`;
      card.prepend(avatarDiv);
    }
  });
}

export function shouldRefreshMonitorBrowseInPlace(pane) {
  if (!pane?.querySelector('.browse-layout')) return false;
  if (monitorBrowseMode) {
    return (
      currentData?.storyboard_name === monitorBrowseMode.project
      && String(currentData?.run_id || '') === String(monitorBrowseMode.run_id || '')
    );
  }
  const job = videoJobsData.find(j => j.job_id === selectedVideoJobId);
  if (!job) return false;
  return (
    currentData?.storyboard_name === job.storyboard_name
    && (!job.run_id || String(currentData?.run_id || '') === String(job.run_id))
  );
}
