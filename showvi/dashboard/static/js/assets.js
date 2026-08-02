/**
 * assets.js — Asset Library UI: tabs, upload, preview, voice playback, prompt-asset picker
 * Extracted from index.html lines 11237-11866 as ES module.
 */

import {
  assetData, setAssetData,
  assetDataLoadingPromise, setAssetDataLoadingPromise,
  voiceData, setVoiceData,
  currentAudioEl, setCurrentAudioEl,
  currentAssetTab, setCurrentAssetTab,
  assetUploadFile, setAssetUploadFile,
  assetUploadContext, setAssetUploadContext,
  promptAssetPickerContext, setPromptAssetPickerContext,
} from './state.js';

import { esc, apiFetch, showToast, parseApiJsonSafely } from './utils.js';
import { t } from './i18n.js';

import {
  ensureAssetDataLoaded,
  getDisplayAttempts,
  isEditableDraftAttempt,
  getCurrentPromptEditorIdForMode,
  getPromptEditorState,
  getPromptAssetPickerCategory,
  getPromptAssetPickerEntityType,
  insertAssetIntoPromptEditor,
} from './unit-helpers.js';

// ── module-level variables ──────────────────────────────────────
let _addEntitySelectedFile = null;
let _addEntitySelectedAssetPath = '';

// ══════════════════════════════════════════════════════════════
//  ASSET LIBRARY
// ══════════════════════════════════════════════════════════════

export function getAssetTabLabel(tab = currentAssetTab) {
  return t(`assets.${tab}`);
}

export function getAssetEmptyMessage(tab = currentAssetTab) {
  return t('assets.empty_prefix') + getAssetTabLabel(tab) + t('assets.empty_suffix');
}

export function getVoicePlayIcon() {
  return '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>';
}

export function getVoicePauseIcon() {
  return '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>';
}

export function resetVoicePlayback() {
  if (currentAudioEl) {
    currentAudioEl.pause();
    setCurrentAudioEl(null);
  }
  document.querySelectorAll('.voice-play-btn').forEach(btn => {
    btn.classList.remove('playing');
    btn.innerHTML = getVoicePlayIcon();
  });
}

export function syncAssetUploadButton() {
  const btn = document.getElementById('assets-upload-btn');
  if (!btn) return;
  const disabled = currentAssetTab === 'voices';
  btn.disabled = disabled;
  btn.title = disabled ? t('assets.upload_need_image_tab') : '';
}

// ── Add Entity to Storyboard (browse sidebar) ──────────────────

export function openAddEntityModal(category = 'characters') {
  _addEntitySelectedFile = null;
  _addEntitySelectedAssetPath = '';
  const modal = document.getElementById('add-entity-modal');
  document.getElementById('add-entity-category').value = category;
  document.getElementById('add-entity-name').value = '';
  document.getElementById('add-entity-desc').value = '';
  document.getElementById('add-entity-file-input').value = '';
  const zone = document.getElementById('add-entity-upload-zone');
  zone.textContent = t('assets.click_select_image');
  zone.classList.remove('has-file');
  switchAddEntityImageSource('local');
  modal.classList.add('show');
  setTimeout(() => document.getElementById('add-entity-name')?.focus(), 80);
}

export function closeAddEntityModal() {
  document.getElementById('add-entity-modal')?.classList.remove('show');
  _addEntitySelectedFile = null;
  _addEntitySelectedAssetPath = '';
}

export function switchAddEntityImageSource(source) {
  const tabs = document.querySelectorAll('#add-entity-modal .image-source-tab');
  tabs.forEach(tab => tab.classList.toggle('active', tab.dataset.source === source));
  document.getElementById('add-entity-image-local').style.display = source === 'local' ? '' : 'none';
  document.getElementById('add-entity-image-library').style.display = source === 'library' ? '' : 'none';
  if (source === 'library') {
    _renderAddEntityLibraryGrid();
  }
}

export function onAddEntityFileChange(input) {
  const file = input.files?.[0];
  _addEntitySelectedFile = file || null;
  _addEntitySelectedAssetPath = '';
  const zone = document.getElementById('add-entity-upload-zone');
  if (file) {
    zone.textContent = `${t('assets.file_selected')}: ${file.name}`;
    zone.classList.add('has-file');
  } else {
    zone.textContent = t('assets.click_select_image');
    zone.classList.remove('has-file');
  }
}

export async function _renderAddEntityLibraryGrid() {
  const grid = document.getElementById('add-entity-library-grid');
  if (!grid) return;
  await ensureAssetDataLoaded();
  const category = document.getElementById('add-entity-category')?.value || 'characters';
  const items = assetData?.[category] || [];
  if (!items.length) {
    grid.innerHTML = `<div style="color:var(--text-muted);font-size:12px;padding:10px">${t('assets.no_items_for_category')}</div>`;
    return;
  }
  grid.innerHTML = items.map((item, i) => {
    const url = item.url || (item.path ? `/asset?path=${encodeURIComponent(item.path)}` : '');
    const selected = _addEntitySelectedAssetPath === item.path;
    return `<div class="library-grid-item${selected ? ' selected' : ''}" onclick="selectAddEntityLibraryItem(${i})" title="${esc(item.name || item.filename || '')}">
      ${url ? `<img src="${url}" loading="lazy" />` : `<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-muted);font-size:10px">${t('assets.no_image')}</div>`}
    </div>`;
  }).join('');
}

export function selectAddEntityLibraryItem(index) {
  const category = document.getElementById('add-entity-category')?.value || 'characters';
  const items = assetData?.[category] || [];
  const item = items[index];
  if (!item) return;
  _addEntitySelectedAssetPath = item.path || '';
  _addEntitySelectedFile = null;
  document.getElementById('add-entity-file-input').value = '';
  const zone = document.getElementById('add-entity-upload-zone');
  zone.textContent = t('assets.click_select_image');
  zone.classList.remove('has-file');
  // Auto-fill name if empty
  const nameInput = document.getElementById('add-entity-name');
  if (!nameInput.value.trim() && item.name) {
    nameInput.value = item.name;
  }
  _renderAddEntityLibraryGrid();
}

export async function submitAddEntity() {
  const category = document.getElementById('add-entity-category')?.value || 'characters';
  const name = (document.getElementById('add-entity-name')?.value || '').trim();
  const desc = (document.getElementById('add-entity-desc')?.value || '').trim();
  const btn = document.getElementById('add-entity-submit-btn');

  if (!name) {
    showToast(t('assets.upload_need_name'), 'error');
    document.getElementById('add-entity-name')?.focus();
    return;
  }

  const currentData = window.currentData;
  const sbPath = currentData?.storyboard_path;
  if (!sbPath) {
    showToast(t('assets.no_storyboard_path'), 'error');
    return;
  }

  const form = new FormData();
  form.append('storyboard_path', sbPath);
  form.append('category', category);
  form.append('name', name);
  form.append('description', desc);
  if (_addEntitySelectedFile) {
    form.append('file', _addEntitySelectedFile);
  } else if (_addEntitySelectedAssetPath) {
    form.append('asset_path', _addEntitySelectedAssetPath);
  }

  if (btn) btn.disabled = true;
  try {
    const res = await fetch('/api/storyboard/add-entity', { method: 'POST', body: form });
    const data = await parseApiJsonSafely(res);
    if (!res.ok || !data.ok) throw new Error(data.error || t('assets.add_failed'));
    closeAddEntityModal();
    // Update currentData.storyboard with the returned storyboard
    if (data.storyboard && currentData) {
      currentData.storyboard = data.storyboard;
    }
    // Refresh the browse view
    const pane = document.getElementById('monitor-detail-pane');
    if (pane && window._renderMonitorBrowse) window._renderMonitorBrowse(pane);
    showToast(`${t('assets.add_success_prefix')}${name}${t('assets.add_success_suffix')}`, 'success');
  } catch (e) {
    showToast(e.message || t('assets.add_failed'), 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Re-render library grid when category changes
document.addEventListener('change', (e) => {
  if (e.target?.id === 'add-entity-category') {
    const libPanel = document.getElementById('add-entity-image-library');
    if (libPanel && libPanel.style.display !== 'none') {
      _renderAddEntityLibraryGrid();
    }
  }
});

export function openAssetUploadPicker(category = null, options = {}) {
  const nextCategory = category || currentAssetTab;
  if (nextCategory === 'voices') {
    showToast(t('assets.upload_need_image_tab'), 'info');
    return;
  }
  setAssetUploadContext({
    mode: options?.mode || 'assets-library',
    category: nextCategory,
    afterUpload: typeof options?.afterUpload === 'function' ? options.afterUpload : null,
  });
  const input = document.getElementById('asset-library-upload-input');
  if (!input) return;
  input.value = '';
  input.click();
}

export function openAssetUploadModal() {
  const modal = document.getElementById('asset-upload-modal');
  const nameInput = document.getElementById('asset-upload-name-input');
  const categoryEl = document.getElementById('asset-upload-category-value');
  const fileEl = document.getElementById('asset-upload-file-value');
  if (!modal || !nameInput || !categoryEl || !fileEl || !assetUploadFile) return;
  const category = assetUploadContext?.category || currentAssetTab;
  categoryEl.textContent = getAssetTabLabel(category);
  fileEl.textContent = assetUploadFile.name;
  const baseName = assetUploadFile.name.replace(/\.[^.]+$/, '');
  nameInput.value = baseName;
  modal.classList.add('show');
  setTimeout(() => {
    nameInput.focus();
    nameInput.select();
  }, 80);
}

export function closeAssetUploadModal(resetFile = false) {
  const modal = document.getElementById('asset-upload-modal');
  if (modal) modal.classList.remove('show');
  const nameInput = document.getElementById('asset-upload-name-input');
  if (nameInput) nameInput.value = '';
  const fileInput = document.getElementById('asset-library-upload-input');
  if (fileInput) fileInput.value = '';
  if (resetFile) {
    setAssetUploadFile(null);
    setAssetUploadContext({ mode: 'assets-library', category: null, afterUpload: null });
  }
}

export function ensurePromptAssetMenu() {
  let menu = document.getElementById('unit-media-action-menu');
  if (menu) return menu;
  menu = document.createElement('div');
  menu.id = 'unit-media-action-menu';
  menu.className = 'unit-media-action-menu';
  document.body.appendChild(menu);
  return menu;
}

export function hidePromptAssetMenu() {
  const menu = document.getElementById('unit-media-action-menu');
  if (!menu) return;
  menu.classList.remove('show');
  menu.innerHTML = '';
}

export function togglePromptAssetMenu(event, unitId, mode = 'modal') {
  event?.preventDefault?.();
  event?.stopPropagation?.();
  const uid = `unit-${unitId}`;
  const unitDataMap = window.unitDataMap || {};
  const currentModalAttemptIdx = window.currentModalAttemptIdx ?? -1;
  const info = mode === 'browse'
    ? (window._browseUnitInfos || []).find(x => Number(x?.unit?.unit_id) === Number(unitId)) || null
    : unitDataMap[uid] || null;
  const attemptIdx = mode === 'browse'
    ? (info?.bestIdx ?? -1)
    : (Number.isInteger(currentModalAttemptIdx) && currentModalAttemptIdx >= 0 ? currentModalAttemptIdx : (info?.bestIdx ?? -1));
  const attempt = info?.displayAttempts?.[attemptIdx] || getDisplayAttempts(info?.unit || {})?.[attemptIdx] || null;
  if (!info?.unit || !isEditableDraftAttempt(attempt)) {
    showToast(t('assets.only_draft_editable'), 'info');
    return;
  }
  const trigger = event?.currentTarget instanceof HTMLElement ? event.currentTarget : document.getElementById(`prompt-asset-trigger-${mode}-${unitId}`);
  if (!trigger) return;
  const menu = ensurePromptAssetMenu();
  const rect = trigger.getBoundingClientRect();
  menu.innerHTML = `
    <button type="button" class="unit-media-action-item" onclick="promptAssetUploadLocal('${unitId}','${mode}')">
      ${t('assets.select_local_upload')}
      <small>${t('assets.select_local_upload_hint')}</small>
    </button>
    <button type="button" class="unit-media-action-item" onclick="promptAssetPickFromLibrary('${unitId}','${mode}')">
      ${t('assets.select_from_library')}
      <small>${t('assets.select_from_library_hint')}</small>
    </button>
  `;
  menu.style.left = `${Math.max(12, Math.min(rect.left, window.innerWidth - 220))}px`;
  menu.style.top = `${Math.min(rect.bottom + 8, window.innerHeight - 120)}px`;
  menu.classList.add('show');
}

export async function promptAssetUploadLocal(unitId, mode = 'modal') {
  hidePromptAssetMenu();
  const category = (() => {
    const editorId = getCurrentPromptEditorIdForMode(unitId, mode);
    const state = getPromptEditorState(editorId);
    const existingAsset = Object.values(state?.assets || {}).slice(-1)[0];
    return getPromptAssetPickerCategory(existingAsset?.type || 'prop');
  })();
  await ensureAssetDataLoaded();
  openPromptAssetCategoryPicker(unitId, mode, 'upload', category);
}

export async function promptAssetPickFromLibrary(unitId, mode = 'modal') {
  hidePromptAssetMenu();
  await ensureAssetDataLoaded();
  openPromptAssetCategoryPicker(unitId, mode, 'library', 'characters');
}

export function openPromptAssetCategoryPicker(unitId, mode = 'modal', action = 'library', selectedCategory = 'characters') {
  setPromptAssetPickerContext({
    unitId: Number(unitId),
    mode,
    action,
    category: selectedCategory,
  });
  renderPromptAssetPicker();
}

export function renderPromptAssetPicker() {
  const ctx = promptAssetPickerContext;
  if (!ctx) return;
  const categories = ['characters', 'locations', 'props'];
  const items = assetData?.[ctx.category] || [];
  const overlayId = 'prompt-asset-picker-overlay';
  document.getElementById(overlayId)?.remove();
  const overlay = document.createElement('div');
  overlay.id = overlayId;
  overlay.style.cssText = 'position:fixed;inset:0;z-index:1250;background:rgba(0,0,0,0.78);display:flex;align-items:center;justify-content:center;backdrop-filter:blur(8px)';
  overlay.addEventListener('click', (e) => { if (e.target === overlay) closePromptAssetPicker(); });
  overlay.innerHTML = `
    <div style="width:min(860px,92vw);max-height:82vh;display:flex;flex-direction:column;overflow:hidden;border-radius:20px;border:1px solid rgba(255,255,255,0.08);background:linear-gradient(180deg, rgba(20,24,38,0.98), rgba(13,16,26,0.98));box-shadow:0 24px 80px rgba(0,0,0,0.5)" onclick="event.stopPropagation()">
      <div style="padding:16px 18px;border-bottom:1px solid var(--border-subtle);display:flex;align-items:center;justify-content:space-between;gap:12px">
        <div>
          <div style="font-size:15px;font-weight:700;color:var(--text-primary)">${ctx.action === 'upload' ? t('assets.select_dialog_title') : t('assets.pick_for_prompt_title')}</div>
          <div style="font-size:12px;color:var(--text-muted);margin-top:4px">${t('assets.pick_category_hint')}</div>
        </div>
        <button class="unit-modal-close" onclick="closePromptAssetPicker()">&times;</button>
      </div>
      <div style="padding:12px 18px;border-bottom:1px solid var(--border-subtle);display:flex;gap:8px;flex-wrap:wrap">
        ${categories.map(cat => `<button class="${cat === ctx.category ? 'btn-primary' : 'btn-secondary'}" style="padding:6px 12px" onclick="switchPromptAssetPickerCategory('${cat}')">${getAssetTabLabel(cat)}</button>`).join('')}
        <div style="flex:1"></div>
        <button class="unit-media-action-btn primary" onclick="startLocalUploadForPromptAsset()">${t('assets.select_local_upload')}</button>
      </div>
      <div style="padding:18px;overflow:auto;flex:1">
        ${items.length ? `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px">${items.map((a, i) => `
          <div class="asset-lib-card" onclick="selectPromptAssetFromLibrary(${i})">
            <img src="${a.url}" alt="${esc(a.name || a.filename || '')}" loading="lazy">
            <div class="asset-lib-card-info">
              <div class="asset-lib-card-name">${esc(a.name || a.filename || '')}</div>
            </div>
          </div>`).join('')}</div>` : `<div style="padding:28px 12px;text-align:center;color:var(--text-muted);font-size:13px">${t('assets.no_items_for_category')}</div>`}
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
}

export function switchPromptAssetPickerCategory(category) {
  if (!promptAssetPickerContext) return;
  setPromptAssetPickerContext({ ...promptAssetPickerContext, category });
  renderPromptAssetPicker();
}

export function closePromptAssetPicker() {
  document.getElementById('prompt-asset-picker-overlay')?.remove();
  setPromptAssetPickerContext(null);
}

export function getPromptAssetInsertTarget(unitId, mode = 'modal') {
  const editorId = getCurrentPromptEditorIdForMode(unitId, mode);
  const state = getPromptEditorState(editorId);
  return { editorId, state };
}

export async function selectPromptAssetFromLibrary(index) {
  const ctx = promptAssetPickerContext;
  if (!ctx) return;
  const items = assetData?.[ctx.category] || [];
  const picked = items[index];
  if (!picked) return;
  const { editorId, state } = getPromptAssetInsertTarget(ctx.unitId, ctx.mode);
  if (!state) {
    showToast(t('assets.only_draft_editable'), 'info');
    return;
  }
  const item = {
    type: getPromptAssetPickerEntityType(ctx.category),
    name: picked.name || picked.filename || '',
    image: picked.url || '',
    path: picked.path || '',
  };
  insertAssetIntoPromptEditor(editorId, item);
  closePromptAssetPicker();
  showToast(t('assets.insert_success'), 'success');
}

export function startLocalUploadForPromptAsset() {
  const ctx = promptAssetPickerContext;
  if (!ctx) return;
  const category = ctx.category || 'characters';
  openAssetUploadPicker(category, {
    mode: 'prompt-asset',
    afterUpload: async (asset) => {
      const { editorId, state } = getPromptAssetInsertTarget(ctx.unitId, ctx.mode);
      if (!state) return;
      insertAssetIntoPromptEditor(editorId, {
        type: getPromptAssetPickerEntityType(category),
        name: asset?.name || asset?.filename || '',
        image: asset?.url || '',
        path: asset?.path || '',
      });
      closePromptAssetPicker();
      showToast(t('assets.insert_success'), 'success');
    },
  });
}

export async function submitAssetUpload() {
  const category = assetUploadContext?.category || currentAssetTab;
  if (category === 'voices') {
    showToast(t('assets.upload_need_image_tab'), 'info');
    return;
  }
  if (!assetUploadFile) {
    showToast(t('assets.upload_need_file'), 'error');
    return;
  }

  const nameInput = document.getElementById('asset-upload-name-input');
  const submitBtn = document.getElementById('asset-upload-submit-btn');
  const rawName = nameInput?.value?.trim() || '';
  if (!rawName) {
    showToast(t('assets.upload_need_name'), 'error');
    nameInput?.focus();
    return;
  }

  const form = new FormData();
  form.append('file', assetUploadFile);
  form.append('name', rawName);
  form.append('category', category);

  if (submitBtn) submitBtn.disabled = true;
  try {
    const res = await fetch('/api/assets/upload', { method: 'POST', body: form });
    const data = await parseApiJsonSafely(res);
    if (!res.ok || !data.ok) throw new Error(data.error || t('assets.upload_failed'));
    const uploadedAsset = data?.asset || null;
    const afterUpload = assetUploadContext?.afterUpload;
    closeAssetUploadModal(true);
    await ensureAssetDataLoaded(true);
    const currentTab = window.currentTab;
    if (currentTab === 'assets') await loadAssetLibrary();
    if (typeof afterUpload === 'function' && uploadedAsset) {
      await afterUpload(uploadedAsset);
    }
    showToast(t('assets.upload_success'), 'success');
  } catch (e) {
    showToast(e.message || t('assets.upload_failed'), 'error');
  } finally {
    if (submitBtn) submitBtn.disabled = false;
  }
}

// ── Global event listeners ──────────────────────────────────────

document.addEventListener('click', (e) => {
  const modal = document.getElementById('asset-upload-modal');
  if (e.target === modal) closeAssetUploadModal(true);
  if (window.closeUserMenu) {
    const userMenuModal = document.getElementById('user-menu-modal');
    if (e.target === userMenuModal) window.closeUserMenu();
  }
  const menu = document.getElementById('unit-media-action-menu');
  if (menu?.classList.contains('show')) {
    const trigger = e.target instanceof HTMLElement ? e.target.closest('.unit-media-action-btn') : null;
    if (!trigger && !menu.contains(e.target)) hidePromptAssetMenu();
  }
});

document.addEventListener('keydown', (e) => {
  const uploadModal = document.getElementById('asset-upload-modal');
  if (uploadModal?.classList.contains('show')) {
    if (e.key === 'Escape') closeAssetUploadModal(true);
    if (e.key === 'Enter' && e.target.id === 'asset-upload-name-input') {
      e.preventDefault();
      submitAssetUpload();
    }
    return;
  }
  if (e.key === 'Escape') {
    hidePromptAssetMenu();
    closePromptAssetPicker();
    if (window.closeUserMenu) window.closeUserMenu();
  }
});

document.addEventListener('change', (e) => {
  if (e.target?.id !== 'asset-library-upload-input') return;
  const file = e.target.files?.[0] || null;
  if (!file) {
    setAssetUploadFile(null);
    return;
  }
  setAssetUploadFile(file);
  openAssetUploadModal();
});

// ── Asset Library main view ─────────────────────────────────────

export async function loadAssetLibrary() {
  try {
    const [aRes, vRes] = await Promise.all([
      fetch('/api/assets').then(r => r.json()),
      fetch('/api/voices').then(r => r.json())
    ]);
    setAssetData(aRes);
    setVoiceData(vRes);
    document.getElementById('asset-count-characters').textContent = aRes.characters?.length || 0;
    document.getElementById('asset-count-locations').textContent = aRes.locations?.length || 0;
    document.getElementById('asset-count-props').textContent = aRes.props?.length || 0;
    document.getElementById('asset-count-voices').textContent = '';
    syncAssetUploadButton();
    renderAssetView();
  } catch (e) {
    document.getElementById('assets-body').innerHTML = `<div style="color:var(--error);padding:20px">${t('assets.load_failed')}${e.message}</div>`;
  }
}

export function switchAssetTab(tab) {
  setCurrentAssetTab(tab);
  document.querySelectorAll('.assets-tab').forEach(b => b.classList.toggle('active', b.dataset.atype === tab));
  resetVoicePlayback();
  syncAssetUploadButton();
  renderAssetView();
}

export function renderAssetView() {
  const body = document.getElementById('assets-body');
  if (currentAssetTab === 'voices') {
    renderVoiceGrid(body);
    return;
  }
  const items = assetData?.[currentAssetTab] || [];
  if (!items.length) {
    body.innerHTML = `<div style="color:var(--text-muted);font-size:14px;padding:40px;text-align:center">${getAssetEmptyMessage(currentAssetTab)}<br><span style="font-size:12px;margin-top:8px;display:inline-block">${t('assets.empty_hint')}</span></div>`;
    return;
  }
  body.innerHTML = `<div class="assets-grid">${items.map((a, i) => `
    <div class="asset-lib-card" onclick="openAssetPreview(${JSON.stringify(currentAssetTab).replace(/"/g, '&quot;')}, ${i})">
      <img src="${a.url}" alt="${a.name || a.filename}" loading="lazy">
      <div class="asset-lib-card-info">
        <div class="asset-lib-card-name">${a.name || a.filename}</div>
        <div class="asset-lib-card-meta">
          <span>${a.project}</span>
          <span>${(a.size / 1024).toFixed(0)} KB</span>
        </div>
      </div>
    </div>
  `).join('')}</div>`;
}

export function renderVoiceGrid(body) {
  body.innerHTML = `<div style="color:var(--text-muted);font-size:16px;padding:60px;text-align:center">
    <div style="font-size:36px;margin-bottom:16px">🎤</div>
    <div style="font-weight:600;margin-bottom:8px">敬请期待</div>
    <div style="font-size:13px;color:var(--text-muted)">配音功能即将上线</div>
  </div>`;
}

export function toggleVoicePlay(idx, url) {
  const btn = document.querySelector(`#voice-card-${idx} .voice-play-btn`);
  if (!btn) return;
  if (currentAudioEl && currentAudioEl._voiceIdx === idx) {
    resetVoicePlayback();
    return;
  }
  resetVoicePlayback();
  const audio = new Audio(url);
  audio._voiceIdx = idx;
  setCurrentAudioEl(audio);
  btn.classList.add('playing');
  btn.innerHTML = getVoicePauseIcon();
  audio.onended = () => resetVoicePlayback();
  audio.onerror = () => resetVoicePlayback();
  audio.play().catch(() => resetVoicePlayback());
}

export async function deleteAssetFromPreview(assetPath, overlay) {
  if (!assetPath) return;
  if (!confirm(t('assets.delete_confirm'))) return;

  try {
    const res = await fetch(`/api/assets?path=${encodeURIComponent(assetPath)}`, { method: 'DELETE' });
    const data = await parseApiJsonSafely(res);
    if (!res.ok || !data.ok) throw new Error(data.error || t('assets.delete_forbidden'));
    if (overlay?.remove) overlay.remove();
    await loadAssetLibrary();
    showToast(t('assets.delete_success'), 'success');
  } catch (e) {
    showToast(e.message || t('assets.delete_forbidden'), 'error');
  }
}

export function openAssetPreview(type, idx) {
  const items = assetData?.[type] || [];
  const a = items[idx];
  if (!a) return;
  const canDelete = a.project === 'assets';
  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.8);display:flex;align-items:center;justify-content:center;cursor:pointer;backdrop-filter:blur(8px)';
  overlay.onclick = () => overlay.remove();
  overlay.innerHTML = `
    <div style="max-width:80vw;max-height:80vh;display:flex;flex-direction:column;align-items:center;gap:16px" onclick="event.stopPropagation()">
      <img src="${a.url}" style="max-width:80vw;max-height:70vh;object-fit:contain;border-radius:12px;box-shadow:0 24px 80px rgba(0,0,0,0.6)">
      <div style="text-align:center">
        <div style="font-size:16px;font-weight:600;color:var(--text-primary)">${a.name || a.filename}</div>
        <div style="font-size:12px;color:var(--text-muted);margin-top:4px">${a.project}${a.run ? ' / ' + a.run : ''}</div>
      </div>
      ${canDelete ? `<button class="btn-danger-soft" style="padding:8px 18px" id="asset-preview-delete-btn">${t('assets.delete')}</button>` : ''}
    </div>`;
  document.body.appendChild(overlay);
  if (canDelete) {
    overlay.querySelector('#asset-preview-delete-btn')?.addEventListener('click', (e) => {
      e.stopPropagation();
      deleteAssetFromPreview(a.path, overlay);
    });
  }
}

// ── Expose to window for inline onclick handlers ────────────────
Object.assign(window, {
  getAssetTabLabel, getAssetEmptyMessage,
  getVoicePlayIcon, getVoicePauseIcon, resetVoicePlayback,
  syncAssetUploadButton,
  openAddEntityModal, closeAddEntityModal, switchAddEntityImageSource,
  onAddEntityFileChange, _renderAddEntityLibraryGrid, selectAddEntityLibraryItem,
  submitAddEntity,
  openAssetUploadPicker, openAssetUploadModal, closeAssetUploadModal,
  ensurePromptAssetMenu, hidePromptAssetMenu, togglePromptAssetMenu,
  promptAssetUploadLocal, promptAssetPickFromLibrary,
  openPromptAssetCategoryPicker, renderPromptAssetPicker,
  switchPromptAssetPickerCategory, closePromptAssetPicker,
  getPromptAssetInsertTarget, selectPromptAssetFromLibrary,
  startLocalUploadForPromptAsset,
  submitAssetUpload,
  loadAssetLibrary, switchAssetTab, renderAssetView,
  renderVoiceGrid, toggleVoicePlay,
  deleteAssetFromPreview, openAssetPreview,
});
