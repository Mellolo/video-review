// ── editor.js  ES module ─────────────────────────────────────────
// Storyboard editor: entity CRUD, scene detail modal, video generation launch.
//
// ⚠️ 开发注意：
// 1. 本模块通过 window.createdStoryboard / window.createdStoryboardPath 读写数据，
//    这些是 create.js 模块变量的活绑定（defineProperty），不是独立副本
// 2. 修改 scene 的 seedance_prompt 后必须同步 groups[idx].sora_prompt
//    （历史遗留冗余字段，漏写会导致数据不一致）
// 3. 本模块 textarea id 使用 "editor-scene-" 前缀（如 editor-scene-seedance-0），
//    create.js 的 review 页面使用 "review-scene-" 前缀，两者已隔离

import { esc, apiFetch, showToast, parseApiJsonSafely } from './utils.js';
import { t } from './i18n.js';
import {
  selectedVideoJobId, setSelectedVideoJobId,
  assetData, setAssetData,
  ws,
} from './state.js';
import { switchTab } from './nav.js';

/* ── helpers that still live in index.html (not yet extracted) ── */
const _win = window;
const getCreatedStoryboard      = () => _win.createdStoryboard;
const setCreatedStoryboard      = (v) => { _win.createdStoryboard = v; };
const getCreatedStoryboardPath  = () => _win.createdStoryboardPath;
const setCreatedStoryboardPath  = (v) => { _win.createdStoryboardPath = v; };
const getCurrentLang            = () => _win.currentLang ?? 'zh';
const getCurrentBackend         = () => _win.currentBackend ?? 'jimeng';
const showCreatePhase           = (...a) => _win.showCreatePhase?.(...a);
const bindEditorTotalDurationInput = (...a) => _win.bindEditorTotalDurationInput?.(...a);
const getStoryboardTotalDuration   = (...a) => _win.getStoryboardTotalDuration?.(...a);
const parseStoryboardDurationSeconds = (...a) => _win.parseStoryboardDurationSeconds?.(...a);
const formatDurationSecondsForInput  = (...a) => _win.formatDurationSecondsForInput?.(...a);
const loadStoryboardList        = (...a) => _win.loadStoryboardList?.(...a);
const loadVideoJobs             = (...a) => _win.loadVideoJobs?.(...a);
const closeUnitModal            = (...a) => _win.closeUnitModal?.(...a);
const navigateUnit              = (...a) => _win.navigateUnit?.(...a);
const showImage                 = (...a) => _win.showImage?.(...a);
const closeLightbox             = (...a) => _win.closeLightbox?.(...a);
const closeImgLightbox          = (...a) => _win.closeImgLightbox?.(...a);

// ── Editor Scene Detail Modal ──────────────────────────────────
let currentEditorSceneIdx = 0;

export function showEditorSceneDetail(idx) {
  const sb = getCreatedStoryboard();
  if (!sb || !sb.storyboard) return;
  currentEditorSceneIdx = idx;
  renderEditorSceneModal();
  document.getElementById('editor-scene-modal').classList.add('show');
}

export function renderEditorSceneModal() {
  const sb = getCreatedStoryboard();
  const scenes = sb?.storyboard || [];
  const scene = scenes[currentEditorSceneIdx];
  if (!scene) return;
  const idx = currentEditorSceneIdx;

  // Update title and navigation
  document.getElementById('editor-scene-modal-title').textContent = t('editor.scene_title').replace('{0}', scene.scene_number);
  document.getElementById('editor-scene-modal-nav').textContent = `${idx + 1} / ${scenes.length}`;

  // Update navigation buttons
  document.getElementById('editor-scene-nav-prev').disabled = idx <= 0;
  document.getElementById('editor-scene-nav-next').disabled = idx >= scenes.length - 1;

  // Render content
  const content = document.getElementById('editor-scene-modal-content');
  let html = '';

  // Narrative (editable)
  const narrative = scene.narrative_summary || scene.plot_description || scene.description || '';
  html += `<div class="modal-section-label">Narrative</div>
    <textarea id="editor-scene-narrative-${idx}" style="width:100%;min-height:80px;padding:10px;border-radius:8px;border:1px solid var(--border-subtle);background:var(--bg-glass);color:var(--text-secondary);font-size:13px;line-height:1.6;font-family:var(--font-sans);resize:vertical;margin-bottom:16px">${esc(narrative)}</textarea>`;

  // Seedance Prompt (editable)
  const seedance = scene.seedance_prompt || '';
  html += `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
    <div class="modal-section-label" style="margin-bottom:0">Seedance Prompt</div>
  </div>
  <textarea id="editor-scene-seedance-${idx}" style="width:100%;min-height:120px;padding:10px;border-radius:8px;border:1px solid var(--border-subtle);background:var(--bg-glass);color:var(--text-secondary);font-size:13px;line-height:1.6;font-family:var(--font-sans);resize:vertical;margin-bottom:16px">${esc(seedance)}</textarea>`;

  // Audio (read-only, if exists)
  if (scene.audio_description) {
    html += `<div class="modal-section-label">Audio</div>
      <div class="modal-prompt-text" style="margin-bottom:20px">${esc(scene.audio_description)}</div>`;
  }

  // Metadata
  html += `<div class="modal-section-label">Details</div>`;
  html += `<div style="display:grid;grid-template-columns:120px 1fr;gap:8px;font-size:13px;color:var(--text-secondary)">`;

  html += `<div style="color:var(--text-muted)">Duration:</div><div class="scene-duration-field"><input id="editor-scene-duration-${idx}" class="scene-duration-input" type="number" min="1" step="0.1" value="${esc(formatDurationSecondsForInput(parseStoryboardDurationSeconds(scene.duration) || 10))}" /><span class="scene-duration-suffix">秒</span></div>`;
  if (scene.location || scene.scene_location) {
    html += `<div style="color:var(--text-muted)">Location:</div><div>${esc(scene.location || scene.scene_location)}</div>`;
  }
  if (scene.time_of_day) {
    html += `<div style="color:var(--text-muted)">Time of Day:</div><div>${esc(scene.time_of_day)}</div>`;
  }
  if (scene.camera_angle) {
    html += `<div style="color:var(--text-muted)">Camera Angle:</div><div>${esc(scene.camera_angle)}</div>`;
  }
  if (scene.camera_movement) {
    html += `<div style="color:var(--text-muted)">Camera Movement:</div><div>${esc(scene.camera_movement)}</div>`;
  }
  if (scene.mood) {
    html += `<div style="color:var(--text-muted)">Mood:</div><div>${esc(scene.mood)}</div>`;
  }
  if (scene.characters_in_scene && scene.characters_in_scene.length) {
    html += `<div style="color:var(--text-muted)">Characters:</div><div>${scene.characters_in_scene.map(c => `<span class="scene-tag char">${esc(c)}</span>`).join(' ')}</div>`;
  }
  if (scene.props_in_scene && scene.props_in_scene.length) {
    html += `<div style="color:var(--text-muted)">Props:</div><div>${scene.props_in_scene.map(p => `<span class="scene-tag">${esc(p)}</span>`).join(' ')}</div>`;
  }

  html += `</div>`;

  // Save button
  html += `<div style="display:flex;gap:12px;margin-top:16px;padding-top:12px;border-top:1px solid var(--border-subtle)">
    <button class="btn-primary" style="font-size:13px;padding:6px 16px" onclick="saveEditorScene(${idx})">${t('misc.save_scene')}</button>
  </div>`;

  content.innerHTML = html;
}

export function saveEditorScene(idx) {
  const sb = getCreatedStoryboard();
  const scene = sb?.storyboard?.[idx];
  if (!scene) return;
  const btn = event?.target;
  const originalLabel = btn?.innerHTML;
  const nEl = document.getElementById(`editor-scene-narrative-${idx}`);
  const sEl = document.getElementById(`editor-scene-seedance-${idx}`);
  const dEl = document.getElementById(`editor-scene-duration-${idx}`);
  if (nEl) {
    scene.narrative_summary = nEl.value;
    if (sb.groups && sb.groups[idx]) {
      sb.groups[idx].narrative_summary = nEl.value;
    }
  }
  if (sEl) {
    scene.seedance_prompt = sEl.value;
    if (sb.groups && sb.groups[idx]) {
      sb.groups[idx].sora_prompt = sEl.value;
    }
  }
  if (dEl) {
    const dur = parseFloat(dEl.value);
    if (Number.isFinite(dur) && dur > 0) {
      scene.duration = `${formatDurationSecondsForInput(dur)}秒`;
      if (sb.groups && sb.groups[idx]) {
        sb.groups[idx].total_seconds = dur;
      }
    }
  }
  sb._meta = sb._meta || {};
  sb._meta.estimated_duration_seconds = Math.round(getStoryboardTotalDuration(sb) * 10) / 10;
  renderEditorScenes();
  bindEditorTotalDurationInput();

  // Persist to disk
  saveStoryboard(false).then(ok => {
    if (btn) {
      btn.innerHTML = ok ? t('editor.save_success') : t('editor.save_failed_short');
      setTimeout(() => {
        closeEditorSceneModal();
        btn.innerHTML = originalLabel || t('misc.save_scene');
      }, ok ? 250 : 1500);
    } else {
      closeEditorSceneModal();
    }
  });
}

export function navigateEditorScene(dir) {
  const scenes = getCreatedStoryboard()?.storyboard || [];
  const next = currentEditorSceneIdx + dir;
  if (next < 0 || next >= scenes.length) return;
  currentEditorSceneIdx = next;
  renderEditorSceneModal();
}

export function closeEditorSceneModal() {
  document.getElementById('editor-scene-modal').classList.remove('show');
}

export function continuityAnchorHtml(anchor) {
  if (!anchor || typeof anchor !== 'object' || Object.keys(anchor).length === 0) return '';
  const rows = [
    ['开场镜头', anchor.opening_shot_type],
    ['开场主体', anchor.opening_subject],
    ['开场动作', anchor.opening_action_state],
    ['开场方向', anchor.opening_screen_direction],
    ['开场环境锚点', anchor.opening_environment_anchor],
    ['结尾镜头', anchor.ending_shot_type],
    ['结尾主体', anchor.ending_subject],
    ['结尾动作', anchor.ending_action_state],
    ['结尾方向', anchor.ending_screen_direction],
    ['结尾环境锚点', anchor.ending_environment_anchor],
    ['承接建议', anchor.bridge_hint],
  ].filter(([, value]) => value);
  if (!rows.length) return '';
  return `<div style="display:grid;grid-template-columns:120px 1fr;gap:6px 10px;font-size:12px;line-height:1.5">${rows.map(([label, value]) => `<div style="color:var(--text-muted)">${esc(label)}</div><div style="color:var(--text-secondary)">${esc(value)}</div>`).join('')}</div>`;
}

export function continuitySectionHtml(scene, idx) {
  const strategy = scene?.transition_strategy || '';
  const anchor = scene?.continuity_anchor || null;
  if (!strategy && (!anchor || Object.keys(anchor).length === 0)) return '';
  return `
    <div id="scene-continuity-${idx}" style="margin-bottom:10px;padding:10px 12px;border-radius:10px;border:1px solid rgba(99,102,241,0.18);background:linear-gradient(135deg,rgba(99,102,241,0.06),rgba(139,92,246,0.04))">
      <div style="font-size:12px;color:var(--text-muted);margin-bottom:8px">Continuity</div>
      ${strategy ? `<div style="font-size:12px;color:var(--text-primary);margin-bottom:${anchor && Object.keys(anchor).length ? '8px' : '0'}"><strong style="font-weight:600">过渡策略：</strong>${esc(strategy)}</div>` : ''}
      ${continuityAnchorHtml(anchor)}
    </div>`;
}

export function refreshSceneContinuitySection(sceneIndex, storyboard) {
  const scene = storyboard?.storyboard?.[sceneIndex];
  const host = document.getElementById(`scene-continuity-host-${sceneIndex}`);
  if (!scene || !host) return;
  host.innerHTML = continuitySectionHtml(scene, sceneIndex);
}

// ── Lightbox ───────────────────────────────────────────────────
export function playVideo(src) {
  const lb = document.getElementById('video-lightbox'), vid = document.getElementById('lightbox-video');
  vid.src = src; lb.classList.add('show'); vid.play();
}

export function _closeLightbox() {
  const lb = document.getElementById('video-lightbox'), vid = document.getElementById('lightbox-video');
  vid.pause(); vid.src = ''; lb.classList.remove('show');
}

export function _showImage(src) {
  document.getElementById('img-lightbox-img').src = src;
  document.getElementById('img-lightbox').classList.add('show');
}

export function _closeImgLightbox() {
  document.getElementById('img-lightbox').classList.remove('show');
}

export function initEditorKeyboardNav() {
  document.getElementById('video-lightbox')?.addEventListener('click', e => {
    if (e.target === e.currentTarget) _closeLightbox();
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      closeUnitModal();
      _closeLightbox();
      _closeImgLightbox();
      closeEditorSceneModal();
    }
    if (_win.currentModalUid && document.getElementById('unit-modal')?.classList.contains('show')) {
      if (e.key === 'ArrowLeft') navigateUnit(-1);
      if (e.key === 'ArrowRight') navigateUnit(1);
    }
    if (document.getElementById('editor-scene-modal')?.classList.contains('show')) {
      if (e.key === 'ArrowLeft') navigateEditorScene(-1);
      if (e.key === 'ArrowRight') navigateEditorScene(1);
    }
  });
}

// ── Editor ──────────────────────────────────────────────────────
let editorUploadTarget = null;
let editorUploadName = null;
let currentEntityEditor = null;

export function getEntityList(type) {
  const sb = getCreatedStoryboard();
  if (!sb) return [];
  if (type === 'character') return sb.characters || [];
  if (type === 'location') return sb.locations || [];
  return sb.props || [];
}

export function getEntityTypeMeta(type) {
  return type === 'character'
    ? {
        label: t('editor.characters'),
        collectionKey: 'characters',
        sceneField: 'characters_in_scene',
        assetCategory: 'characters',
        descField: 'description',
        extraFields: [
          { key: 'personality', label: t('editor.personality'), type: 'textarea' },
          { key: 'voice_description', label: t('editor.voice'), type: 'textarea' },
        ],
      }
    : type === 'location'
      ? {
          label: t('editor.locations'),
          collectionKey: 'locations',
          sceneField: 'scene_location',
          assetCategory: 'locations',
          descField: 'description',
          extraFields: [],
        }
      : {
          label: t('editor.props'),
          collectionKey: 'props',
          sceneField: 'props_in_scene',
          assetCategory: 'props',
          descField: 'description',
          extraFields: [],
        };
}

export function getSceneIndicesForEntity(type, name) {
  const sb = getCreatedStoryboard();
  const scenes = (sb && sb.storyboard) || [];
  if (!name) return [];
  return scenes
    .map((scene, idx) => {
      if (type === 'location') {
        return (scene.scene_location || '') === name ? idx : -1;
      }
      const list = type === 'character' ? (scene.characters_in_scene || []) : (scene.props_in_scene || []);
      return list.includes(name) ? idx : -1;
    })
    .filter(idx => idx >= 0);
}

export function entityDescriptionPreview(entity, type) {
  if (type === 'character') return entity.personality || entity.description || '';
  return entity.description || '';
}

export function buildEntityCard(type, entity, index, isGenerating = false) {
  const currentLang = getCurrentLang();
  const imgSrc = entity.image_path ? `/asset?path=${encodeURIComponent(entity.image_path)}` : '';
  let imgEl;
  if (isGenerating) {
    // 图片生成中：显示 spinner 覆盖在图片/占位符上
    const bgStyle = imgSrc
      ? `background:url('${imgSrc}') center/cover no-repeat;`
      : 'background:rgba(99,102,241,0.08);';
    imgEl = `<div class="editor-entity-img-generating" style="${bgStyle}">
      <div class="editor-entity-gen-spinner"></div>
    </div>`;
  } else if (imgSrc) {
    imgEl = `<img class="editor-entity-img" src="${imgSrc}" onclick="event.stopPropagation();showImage('${imgSrc}')" />`;
  } else {
    imgEl = `<div class="editor-entity-placeholder" onclick="event.stopPropagation();triggerImageUpload('${type}','${esc(entity.name)}')">+</div>`;
  }
  const meta = getEntityTypeMeta(type);
  const clearBtn = imgSrc && !isGenerating
    ? `<button class="editor-upload-btn" style="color:var(--text-muted);font-size:11px" onclick="event.stopPropagation();clearEntityImage('${type}','${esc(entity.name)}')">${t('editor.clear')}</button>`
    : '';
  return `<div class="editor-entity" onclick="showEntityDetail('${type}',${index})" style="cursor:pointer">
    ${imgEl}
    <div class="editor-entity-info">
      <div class="editor-entity-name">${esc(entity.name)}</div>
      <div class="editor-entity-desc">${esc(entityDescriptionPreview(entity, type))}</div>
    </div>
    <div class="editor-entity-actions">
      <button class="editor-upload-btn" onclick="event.stopPropagation();triggerImageUpload('${type}','${esc(entity.name)}')">${t('editor.upload')}</button>
      <button class="editor-upload-btn editor-lib-btn" onclick="event.stopPropagation();pickFromAssetLibrary('${type}','${esc(entity.name)}','${meta.assetCategory}')">${t('editor.library')}</button>
      ${clearBtn}
    </div>
  </div>`;
}

export function renderEntityList(type, targetId, generatingNames = new Set()) {
  const list = getEntityList(type);
  const el = document.getElementById(targetId);
  if (!el) return;
  el.innerHTML = list.length
    ? list.map((entity, idx) => buildEntityCard(type, entity, idx, generatingNames.has(entity.name))).join('')
    : `<div class="editor-empty">${t('editor.no_entities')}</div>`;
}

export function buildSceneReferenceChips(selected = []) {
  const sb = getCreatedStoryboard();
  const scenes = (sb && sb.storyboard) || [];
  if (!scenes.length) return `<div class="editor-empty">${t('editor.no_refs')}</div>`;
  return `<div class="editor-chip-list">${scenes.map((scene, idx) => {
    const active = selected.includes(idx);
    const summary = scene.narrative_summary || scene.plot_description || scene.description || '';
    return `<label class="editor-chip" style="border-color:${active ? 'var(--border-accent)' : 'var(--border-subtle)'};background:${active ? 'rgba(99,102,241,0.12)' : 'rgba(255,255,255,0.04)'}">
      <input type="checkbox" data-scene-ref value="${idx}" ${active ? 'checked' : ''} style="accent-color:var(--accent)">
      <span>S${scene.scene_number} ${esc(summary.slice(0, 24))}</span>
    </label>`;
  }).join('')}</div>`;
}

export function collectEntityEditorValues() {
  if (!currentEntityEditor) return null;
  const { type, index } = currentEntityEditor;
  const meta = getEntityTypeMeta(type);
  const name = document.getElementById('entity-form-name')?.value.trim() || '';
  const description = document.getElementById('entity-form-description')?.value.trim() || '';
  const sceneIndices = Array.from(document.querySelectorAll('#entity-modal-body [data-scene-ref]:checked')).map(el => Number(el.value));
  const payload = {
    name,
    description,
    image_path: currentEntityEditor.image_path || '',
    scene_indices: sceneIndices,
  };
  meta.extraFields.forEach(field => {
    payload[field.key] = document.getElementById(`entity-form-${field.key}`)?.value.trim() || '';
  });
  return { type, index, payload };
}

export async function applyEntitySync(edits) {
  const sb = getCreatedStoryboard();
  const sbPath = getCreatedStoryboardPath();
  if (!sb || !sbPath) return false;
  const statusEl = document.getElementById('editor-save-status');
  if (statusEl) statusEl.textContent = t('misc.saving');
  try {
    const res = await fetch('/api/storyboard/sync-entities', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        storyboard_path: sbPath,
        storyboard: sb,
        edits,
      }),
    });
    const data = await res.json();
    if (!data.ok || !data.storyboard) throw new Error(data.error || 'sync failed');
    setCreatedStoryboard(data.storyboard);
    renderEditor(data.storyboard);
    const summaryText = formatEntitySyncSummary(data.sync_summary || {});
    if (statusEl) {
      statusEl.textContent = summaryText ? `${t('editor.sync_done')} · ${summaryText}` : t('editor.sync_done');
      setTimeout(() => { statusEl.textContent = ''; }, 3500);
    }
    return true;
  } catch (e) {
    if (statusEl) statusEl.textContent = 'Error: ' + e.message;
    showToast('Error: ' + e.message, 'error');
    return false;
  }
}

export function openEntityEditor(type, index = null) {
  const currentLang = getCurrentLang();
  const meta = getEntityTypeMeta(type);
  const list = getEntityList(type);
  const entity = index === null ? null : list[index];
  const sceneIndices = entity ? getSceneIndicesForEntity(type, entity.name) : [];
  currentEntityEditor = {
    type,
    index,
    image_path: entity?.image_path || '',
    previous_name: entity?.name || '',
  };

  document.getElementById('entity-modal-title').textContent = `${meta.label} · ${index === null ? t('editor.add') : t('editor.manage_entity')}`;

  let html = `
    <div style="display:grid;gap:14px">
      <div>
        <div style="font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.6px;margin-bottom:6px">${t('editor.name')}</div>
        <input id="entity-form-name" type="text" value="${esc(entity?.name || '')}" style="width:100%;padding:10px 12px;border-radius:8px;border:1px solid var(--border-subtle);background:var(--bg-glass);color:var(--text-primary);font-size:13px" />
      </div>
      <div>
        <div style="font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.6px;margin-bottom:6px">${t('editor.description')}</div>
        <textarea id="entity-form-description" style="width:100%;min-height:92px;padding:10px 12px;border-radius:8px;border:1px solid var(--border-subtle);background:var(--bg-glass);color:var(--text-secondary);font-size:13px;line-height:1.6;resize:vertical">${esc(entity?.description || '')}</textarea>
      </div>`;

  meta.extraFields.forEach(field => {
    html += `
      <div>
        <div style="font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.6px;margin-bottom:6px">${esc(field.label)}</div>
        <textarea id="entity-form-${field.key}" style="width:100%;min-height:72px;padding:10px 12px;border-radius:8px;border:1px solid var(--border-subtle);background:var(--bg-glass);color:var(--text-secondary);font-size:13px;line-height:1.6;resize:vertical">${esc(entity?.[field.key] || '')}</textarea>
      </div>`;
  });

  html += `
      <div>
        <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px">
          <div style="font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.6px">${t('editor.references')}</div>
          <button class="editor-upload-btn" onclick="renderEntityScenePicker(true)">${t('editor.detect_refs')}</button>
        </div>
        <div id="entity-scene-picker">${buildSceneReferenceChips(sceneIndices)}</div>
        <div class="editor-inline-help">${t('editor.manual_refs')}</div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:4px">
        <button class="editor-upload-btn" style="flex:1;min-width:110px;padding:8px 12px;font-size:12px" onclick="saveEntityEditor()">${index === null ? t('editor.add') : t('misc.save_scene')}</button>`;

  if (entity) {
    html += `<button class="editor-upload-btn editor-danger-btn" style="flex:1;min-width:110px;padding:8px 12px;font-size:12px" onclick="deleteEntityFromEditor()">${t('editor.delete')}</button>`;
  }

  html += `
        <button class="editor-upload-btn" style="flex:1;min-width:110px;padding:8px 12px;font-size:12px" onclick="closeEntityModal()">${t('misc.cancel')}</button>
      </div>
    </div>`;

  document.getElementById('entity-modal-body').innerHTML = html;
  const modal = document.getElementById('entity-detail-modal');
  modal.style.display = 'flex';
  requestAnimationFrame(() => modal.style.opacity = '1');
}

export function renderEntityScenePicker(autoDetect = false) {
  if (!currentEntityEditor) return;
  const name = document.getElementById('entity-form-name')?.value.trim() || '';
  const selected = autoDetect && name
    ? inferSceneRefsForEntity(currentEntityEditor.type, name)
    : Array.from(document.querySelectorAll('#entity-modal-body [data-scene-ref]:checked')).map(el => Number(el.value));
  const picker = document.getElementById('entity-scene-picker');
  if (picker) picker.innerHTML = buildSceneReferenceChips(selected);
}

export function formatEntitySyncSummary(summary = {}) {
  const sb = getCreatedStoryboard();
  const lines = [];
  ['character', 'location', 'prop'].forEach(type => {
    const added = (summary.added?.[type] || []).filter(item => (item.scene_indices || []).length);
    const deleted = (summary.deleted?.[type] || []).filter(item => (item.scene_indices || []).length);
    if (added.length) {
      lines.push(...added.map(item => `${item.name} → S${(item.scene_indices || []).map(idx => (sb?.storyboard?.[idx]?.scene_number ?? idx + 1)).join(', S')}`));
    }
    if (deleted.length) {
      lines.push(...deleted.map(item => `${item.name} × S${(item.scene_indices || []).map(idx => (sb?.storyboard?.[idx]?.scene_number ?? idx + 1)).join(', S')}`));
    }
  });
  return lines.slice(0, 3).join('；');
}

export function inferSceneRefsForEntity(type, name) {
  const sb = getCreatedStoryboard();
  const scenes = (sb && sb.storyboard) || [];
  const normalizedTokens = String(name || '').replace(/[（）（)\(、，,。\.·\-_/:：；;"""'？！!?]/g, ' ').split(/\s+/).map(s => s.trim().toLowerCase()).filter(s => s.length >= 2);
  const baseToken = String(name || '').trim().toLowerCase();
  const tokens = [baseToken, ...normalizedTokens].filter(Boolean);
  return scenes.map((scene, idx) => {
    const haystack = [
      scene.narrative_summary,
      scene.plot_description,
      scene.visual_description,
      scene.seedance_prompt,
      scene.dialogue,
      (scene.characters_in_scene || []).join(' '),
      scene.scene_location,
      (scene.props_in_scene || []).join(' '),
      (scene.dialogue_lines || []).map(line => `${line.speaker || ''} ${line.text || ''}`).join(' '),
    ].join(' ').toLowerCase();
    return tokens.some(token => token && haystack.includes(token)) ? idx : -1;
  }).filter(idx => idx >= 0);
}

export async function saveEntityEditor() {
  const currentLang = getCurrentLang();
  const values = collectEntityEditorValues();
  if (!values) return;
  const { type, index, payload } = values;
  if (!payload.name) {
    alert(t('assets.upload_need_name'));
    return;
  }
  const edits = { character: [], location: [], prop: [] };
  const op = { ...payload };
  if (index === null) {
    op.action = 'add';
  } else if ((currentEntityEditor.previous_name || '') && currentEntityEditor.previous_name !== payload.name) {
    op.action = 'rename';
    op.previous_name = currentEntityEditor.previous_name;
  } else {
    op.action = 'update';
    op.previous_name = currentEntityEditor.previous_name;
  }
  edits[type].push(op);
  const ok = await applyEntitySync(edits);
  if (ok) closeEntityModal();
}

export async function deleteEntityFromEditor() {
  if (!currentEntityEditor) return;
  if (!confirm(t('editor.delete_confirm'))) return;
  const edits = { character: [], location: [], prop: [] };
  edits[currentEntityEditor.type].push({
    action: 'delete',
    previous_name: currentEntityEditor.previous_name,
    name: currentEntityEditor.previous_name,
  });
  const ok = await applyEntitySync(edits);
  if (ok) closeEntityModal();
}

export function openEditor() {
  const sb = getCreatedStoryboard();
  if (!sb) return;
  showCreatePhase('editor');
  renderEditor(sb);
  // 同步首页模型选择到编辑器选择器
  if (typeof window.syncEditorModelFromHome === 'function') window.syncEditorModelFromHome();
}

export function renderEditor(sb) {
  const titleInput = document.getElementById('editor-title');
  titleInput.value = sb.title || 'Storyboard';

  titleInput.oninput = (e) => {
    const csb = getCreatedStoryboard();
    if (csb) {
      csb.title = e.target.value;
      const csbPath = getCreatedStoryboardPath();
      if (csbPath && csbPath.includes('_storyboard.json')) {
        const newTitle = e.target.value.trim() || 'untitled';
        setCreatedStoryboardPath(`storyboards/${newTitle}_storyboard.json`);
      }
    }
  };

  // Initialize global style bar
  const styleInput = document.getElementById('editor-global-style-input');
  if (styleInput) {
    const currentStyle = sb?.video_analysis?.style || '';
    styleInput.value = currentStyle;
  }

  // Reset generate button state (may be stuck from a previous storyboard's generation)
  const genBtns = document.querySelectorAll('.btn-generate-video');
  genBtns.forEach(b => {
    b.disabled = false;
    b.style.background = 'linear-gradient(135deg,#10b981,#059669)';
    b.style.borderColor = '#059669';
    // Restore original label from i18n or fallback
    const span = b.querySelector('[data-i18n="editor.save_generate"]');
    if (span) span.textContent = t('editor.save_generate');
    else b.innerHTML = '<span style="margin-right:6px">▶</span> <span data-i18n="editor.save_generate">' + t('editor.save_generate') + '</span>';
  });

  const scenes = sb.storyboard || [];
  document.getElementById('editor-scene-count').textContent = `${scenes.length} ${t('misc.scenes')}`;
  bindEditorTotalDurationInput();

  const progress = _win.currentData?.checkpoint?.progress;
  const pendingNames = new Set(progress?.charsheet_pending || []);
  _win._editorPendingNames = pendingNames;
  renderEntityList('character', 'editor-characters', pendingNames);
  renderEntityList('location', 'editor-locations', pendingNames);
  renderEntityList('prop', 'editor-props', pendingNames);
  renderEditorScenes();
}

export function renderEditorScenes() {
  const sb = getCreatedStoryboard();
  const scenes = (sb && sb.storyboard) || [];
  const listEl = document.getElementById('editor-scene-list');
  listEl.innerHTML = scenes.map((s, idx) => {
    const chars = (s.characters_in_scene || []).map(c => `<span class="scene-tag char">${esc(c)}</span>`).join('');
    const loc = s.scene_location ? `<span class="scene-tag">${esc(s.scene_location)}</span>` : '';
    const props = (s.props_in_scene || []).map(p => `<span class="scene-tag">${esc(p)}</span>`).join('');
    const mood = s.mood ? `<span class="scene-tag mood">${esc(s.mood)}</span>` : '';
    const cam = s.camera_angle ? `<span class="scene-tag camera">${esc(s.camera_angle)}</span>` : '';
    return `<div class="editor-scene" onclick="showEditorSceneDetail(${idx})">
      <div class="editor-scene-num">S${s.scene_number}</div>
      <div class="editor-scene-body">
        <div class="editor-scene-plot">${esc(s.narrative_summary || s.plot_description || s.description || '')}</div>
        <div class="editor-scene-meta">${chars}${loc}${props}${mood}${cam}</div>
        <div class="editor-scene-dur">${s.duration || '—'}</div>
      </div>
    </div>`;
  }).join('');
}

// ── Entity Detail Modal ──────────────────────────────────────────────

export function showEntityDetail(type, index) {
  const currentLang = getCurrentLang();
  const sb = getCreatedStoryboard();
  if (!sb) return;
  const list = type === 'character' ? (sb.characters || []) : type === 'location' ? (sb.locations || []) : (sb.props || []);
  const entity = list[index];
  if (!entity) return;

  const meta = getEntityTypeMeta(type);
  document.getElementById('entity-modal-title').textContent = `${meta.label} · ${entity.name}`;

  const imgSrc = entity.image_path ? `/asset?path=${encodeURIComponent(entity.image_path)}` : '';
  const fields = [];
  if (type === 'character') {
    if (entity.description) fields.push([t('editor.appearance'), entity.description]);
    if (entity.personality) fields.push([t('editor.personality'), entity.personality]);
    if (entity.voice_description) fields.push([t('editor.voice'), entity.voice_description]);
  } else {
    if (entity.description) fields.push([t('editor.description'), entity.description]);
  }

  const refs = getSceneIndicesForEntity(type, entity.name);
  let html = '';
  if (imgSrc) {
    html += `<div style="text-align:center;margin-bottom:20px">
      <img src="${imgSrc}" onclick="showImage('${imgSrc}')"
        style="max-width:100%;max-height:220px;border-radius:12px;object-fit:cover;cursor:pointer;border:1px solid var(--border-subtle)" />
    </div>`;
  } else {
    html += `<div style="display:flex;align-items:center;justify-content:center;height:80px;border-radius:12px;border:1px dashed var(--border-accent);background:var(--bg-glass);margin-bottom:20px;color:var(--text-muted);font-size:13px">
      ${t('editor.no_ref_image')}
    </div>`;
  }

  fields.forEach(([label, value]) => {
    html += `<div style="margin-bottom:14px">
      <div style="font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.6px;margin-bottom:6px">${esc(label)}</div>
      <div style="font-size:13px;color:var(--text-secondary);line-height:1.7;background:var(--bg-glass);border:1px solid var(--border-subtle);border-radius:8px;padding:10px 12px;white-space:pre-wrap">${esc(value)}</div>
    </div>`;
  });

  html += `<div style="margin-bottom:14px">
    <div style="font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.6px;margin-bottom:6px">${t('editor.references')}</div>
    ${refs.length ? `<div class="editor-chip-list">${refs.map(idx => {
      const scene = sb.storyboard[idx];
      return `<span class="editor-chip">S${scene.scene_number}</span>`;
    }).join('')}</div>` : `<div class="editor-empty">${t('editor.no_refs')}</div>`}
  </div>`;

  const clearImgBtn = imgSrc
    ? `<button class="editor-upload-btn" style="flex:1;padding:8px;font-size:12px;color:var(--text-muted)" onclick="closeEntityModal();clearEntityImage('${type}','${esc(entity.name)}')">${t('editor.clear_image')}</button>`
    : '';
  html += `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:4px">
    <button class="editor-upload-btn" style="flex:1;padding:8px;font-size:12px" onclick="closeEntityModal();triggerImageUpload('${type}','${esc(entity.name)}')">${t('editor.upload')}</button>
    <button class="editor-upload-btn editor-lib-btn" style="flex:1;padding:8px;font-size:12px" onclick="closeEntityModal();pickFromAssetLibrary('${type}','${esc(entity.name)}','${meta.assetCategory}')">${t('editor.library')}</button>
    <button class="editor-upload-btn" style="flex:1;padding:8px;font-size:12px" onclick="closeEntityModal();openEntityEditor('${type}', ${index})">${t('editor.manage_entity')}</button>
    ${clearImgBtn}
  </div>`;

  document.getElementById('entity-modal-body').innerHTML = html;
  const modal = document.getElementById('entity-detail-modal');
  modal.style.display = 'flex';
  requestAnimationFrame(() => modal.style.opacity = '1');
}

export function closeEntityModal() {
  const modal = document.getElementById('entity-detail-modal');
  modal.style.display = 'none';
  currentEntityEditor = null;
}

export function triggerImageUpload(target, name) {
  editorUploadTarget = target;
  editorUploadName = name;
  document.getElementById('entity-image-upload').click();
}

export function syncEntityEditorImage(target, name, imagePath) {
  if (!currentEntityEditor || currentEntityEditor.type !== target) return;
  const formName = document.getElementById('entity-form-name')?.value.trim();
  if ((formName || currentEntityEditor.previous_name) === name) {
    currentEntityEditor.image_path = imagePath || currentEntityEditor.image_path;
  }
}

export function initEntityImageUpload() {
  document.getElementById('entity-image-upload')?.addEventListener('change', async function() {
    const file = this.files[0];
    const sbPath = getCreatedStoryboardPath();
    if (!file || !editorUploadTarget || !sbPath) return;

    const form = new FormData();
    form.append('file', file);
    form.append('storyboard_path', sbPath);
    form.append('target', editorUploadTarget);
    form.append('name', editorUploadName);

    const statusEl = document.getElementById('editor-save-status');
    statusEl.textContent = t('misc.uploading');

    try {
      const res = await fetch('/api/upload-image', { method: 'POST', body: form });
      const data = await parseApiJsonSafely(res);
      if (!res.ok || !data.ok || !data.storyboard) {
        throw new Error(data.error || t('misc.upload_failed'));
      }
      setCreatedStoryboard(data.storyboard);
      syncEntityEditorImage(editorUploadTarget, editorUploadName, data.image_path);
      renderEditor(data.storyboard);
      statusEl.textContent = t('misc.uploaded');
    } catch (e) {
      statusEl.textContent = t('misc.upload_failed') + ': ' + e.message;
    }
    this.value = '';
    setTimeout(() => statusEl.textContent = '', 3000);
  });
}

export async function pickFromAssetLibrary(target, name, category) {
  let localAssetData;
  try {
    localAssetData = await fetch('/api/assets').then(r => r.json());
    setAssetData(localAssetData);
  } catch (e) { alert(t('assets.load_failed') + e.message); return; }
  const items = localAssetData[category] || [];
  if (!items.length) { alert(t('editor.no_assets_yet')); return; }

  const overlay = document.createElement('div');
  overlay.id = 'asset-picker-overlay';
  overlay.style.cssText = 'position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,0.8);display:flex;align-items:center;justify-content:center;backdrop-filter:blur(8px)';
  const modal = document.createElement('div');
  modal.style.cssText = 'background:var(--bg-secondary);border:1px solid var(--border-subtle);border-radius:var(--radius-xl);width:90vw;max-width:720px;max-height:80vh;display:flex;flex-direction:column;overflow:hidden';
  modal.innerHTML = `
    <div style="padding:16px 20px;border-bottom:1px solid var(--border-subtle);display:flex;align-items:center;justify-content:space-between">
      <div style="font-weight:600;font-size:15px">Select ${category.slice(0,-1)} image for "${name}"</div>
      <button onclick="document.getElementById('asset-picker-overlay').remove()" style="background:none;border:none;color:var(--text-muted);cursor:pointer;font-size:18px">&times;</button>
    </div>
    <div style="flex:1;overflow-y:auto;padding:16px 20px">
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:12px">
        ${items.map((a, i) => `
          <div class="asset-lib-card" style="cursor:pointer" onclick="selectAssetFromLibrary('${target}','${esc(name)}','${a.path.replace(/'/g,"\\'")}')">
            <img src="${a.url}" loading="lazy" style="width:100%;aspect-ratio:1;object-fit:cover">
            <div class="asset-lib-card-info"><div class="asset-lib-card-name">${a.name || a.filename}</div></div>
          </div>
        `).join('')}
      </div>
    </div>`;
  overlay.appendChild(modal);
  overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
  document.body.appendChild(overlay);
}

export async function selectAssetFromLibrary(target, name, assetPath) {
  document.getElementById('asset-picker-overlay')?.remove();
  const sbPath = getCreatedStoryboardPath();
  if (!sbPath) return;

  const statusEl = document.getElementById('editor-save-status');
  statusEl.textContent = t('editor.applying_library');

  try {
    const res = await fetch('/api/assign-asset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        storyboard_path: sbPath,
        target: target,
        name: name,
        asset_path: assetPath
      })
    });
    const data = await res.json();
    if (data.ok && data.storyboard) {
      setCreatedStoryboard(data.storyboard);
      syncEntityEditorImage(target, name, data.image_path);
      renderEditor(data.storyboard);
      statusEl.textContent = t('editor.applied_library');
    } else {
      statusEl.textContent = t('editor.apply_failed');
    }
  } catch (e) {
    statusEl.textContent = 'Error: ' + e.message;
  }
  setTimeout(() => statusEl.textContent = '', 3000);
}

export async function clearEntityImage(target, name) {
  const currentLang = getCurrentLang();
  const sbPath = getCreatedStoryboardPath();
  if (!sbPath) return;
  const statusEl = document.getElementById('editor-save-status');
  statusEl.textContent = t('editor.clearing_image');
  try {
    const res = await fetch('/api/clear-entity-image', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        storyboard_path: sbPath,
        target: target,
        name: name
      })
    });
    const data = await res.json();
    if (data.ok && data.storyboard) {
      setCreatedStoryboard(data.storyboard);
      renderEditor(data.storyboard);
      statusEl.textContent = t('editor.image_cleared');
    } else {
      statusEl.textContent = t('editor.clear_failed');
    }
  } catch (e) {
    statusEl.textContent = 'Error: ' + e.message;
  }
  setTimeout(() => statusEl.textContent = '', 3000);
}

export async function saveStoryboard(showSuccess = true) {
  const sb = getCreatedStoryboard();
  const sbPath = getCreatedStoryboardPath();
  if (!sb || !sbPath) {
    console.warn('[saveStoryboard] skipped: sb=', !!sb, 'sbPath=', sbPath);
    return false;
  }
  const statusEl = document.getElementById('editor-save-status');
  statusEl.textContent = t('misc.saving');

  try {
    const res = await fetch('/api/storyboard/save', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ storyboard_path: sbPath, storyboard: sb }),
    });
    const data = await res.json();
    if (data.ok) {
      statusEl.textContent = t('misc.saved');
      if (showSuccess) {
        showToast(t('toast.save_success'), 'success');
        loadStoryboardList();
      }
      setTimeout(() => statusEl.textContent = '', 3000);
      return true;
    } else {
      statusEl.textContent = 'Save failed';
      if (showSuccess) showToast(t('toast.save_failed'), 'error');
      return false;
    }
  } catch (e) {
    statusEl.textContent = 'Error: ' + e.message;
    if (showSuccess) showToast('Error: ' + e.message, 'error');
    return false;
  }
}

export async function saveAndGenerate() {
  const saved = await saveStoryboard(false);
  if (!saved) {
    showToast(t('toast.save_failed'), 'error');
    return;
  }
  await startVideoGeneration();
}

export function openInMonitor() {
  const sb = getCreatedStoryboard();
  if (sb?.title) {
    const pName = (sb.title || 'untitled') + '_storyboard';
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'switch_project', project_name: pName }));
    }
    switchTab('monitor');
  }
}

export async function startVideoGeneration() {
  const sbPath = getCreatedStoryboardPath();
  if (!sbPath) {
    alert(t('editor.no_storyboard_path'));
    return;
  }
  const btns = document.querySelectorAll('.btn-generate-video');
  btns.forEach(b => { b.disabled = true; b.textContent = t('editor.starting'); });

  const generationMode = document.getElementById('editor-generation-mode')?.value || 'parallel';
  // 优先从编辑器 active option 读（最可靠），fallback 到隐藏 select，再 fallback 到 localStorage
  const seeddanceModel = (
    document.querySelector('#editor-model-selector .sd-model-option.active')?.dataset?.value
    || document.getElementById('editor-model-select')?.value
    || (() => { try { return localStorage.getItem('seeddance_editor_model') || localStorage.getItem('seeddance_model'); } catch(e) { return null; } })()
    || 'seedance-2.0'
  );
  console.log('[startVideoGeneration] seeddanceModel =', seeddanceModel,
    '| active-opt =', document.querySelector('#editor-model-selector .sd-model-option.active')?.dataset?.value,
    '| select.value =', document.getElementById('editor-model-select')?.value);

  try {
    const res = await fetch('/api/generate/start', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        storyboard_path: sbPath,
        seeddance_backend: getCurrentBackend(),
        generation_mode: generationMode,
        seeddance_model: seeddanceModel,
      }),
    });
    const data = await res.json();
    if (data.ok) {
      btns.forEach(b => { b.textContent = t('editor.generation_running'); b.style.background = 'var(--success)'; });
      setSelectedVideoJobId(data.job_id);
      showToast(t('toast.video_started'), 'success');
      // Do NOT send switch_project here — the new job has no run_dir yet.
      // The monitor's _renderMonitorDetail will show a "waiting" state
      // and auto-refresh once the job gets a run_dir.
      setTimeout(() => {
        loadVideoJobs();
        switchTab('monitor');
      }, 1000);
    } else {
      btns.forEach(b => { b.disabled = false; b.textContent = t('editor.start_video'); });
      showToast('Failed: ' + (data.error || 'Unknown error'), 'error');
    }
  } catch (e) {
    btns.forEach(b => { b.disabled = false; b.textContent = t('editor.start_video'); });
    showToast('Error: ' + e.message, 'error');
  }
}

/**
 * 当 WS 推送 full_update/monitor_update 时，检查 currentData.storyboard 里的实体图片路径
 * 是否有更新，如果有则同步到 createdStoryboard 并刷新 editor 实体列表。
 * 同时从 checkpoint.progress 里提取正在生成的实体名，显示 spinner。
 * 只在 createPhase === 'editor' 时生效。
 */
export function maybeRefreshEditorImages() {
  if (_win.createPhase !== 'editor') return;
  const sb = getCreatedStoryboard();
  if (!sb) return;

  // 提取正在生成图片的实体名（charsheet_pending）
  const progress = _win.currentData?.checkpoint?.progress;
  const pendingNames = new Set(progress?.charsheet_pending || []);

  const newSb = _win.currentData?.storyboard;
  let changed = false;

  if (newSb) {
    for (const cat of ['characters', 'locations', 'props']) {
      const oldList = sb[cat] || [];
      const newList = newSb[cat] || [];
      for (const newEntity of newList) {
        const oldEntity = oldList.find(e => e.name === newEntity.name);
        if (oldEntity && newEntity.image_path && oldEntity.image_path !== newEntity.image_path) {
          oldEntity.image_path = newEntity.image_path;
          changed = true;
        }
      }
    }
  }

  // 有图片更新，或者有 pending 状态变化时都需要刷新
  const prevPending = _win._editorPendingNames;
  const pendingChanged = !prevPending
    || prevPending.size !== pendingNames.size
    || [...pendingNames].some(n => !prevPending.has(n));

  if (changed || pendingChanged) {
    _win._editorPendingNames = pendingNames;
    renderEntityList('character', 'editor-characters', pendingNames);
    renderEntityList('location', 'editor-locations', pendingNames);
    renderEntityList('prop', 'editor-props', pendingNames);
  }
}

// ── Register all public functions on window for onclick handlers ──
Object.assign(window, {
  showEditorSceneDetail,
  renderEditorSceneModal,
  saveEditorScene,
  navigateEditorScene,
  closeEditorSceneModal,
  continuityAnchorHtml,
  continuitySectionHtml,
  refreshSceneContinuitySection,
  playVideo,
  _closeLightbox,
  closeLightbox: _closeLightbox,
  _showImage,
  _closeImgLightbox,
  showImage: _showImage,
  closeImgLightbox: _closeImgLightbox,
  getEntityList,
  getEntityTypeMeta,
  getSceneIndicesForEntity,
  entityDescriptionPreview,
  buildEntityCard,
  renderEntityList,
  buildSceneReferenceChips,
  collectEntityEditorValues,
  applyEntitySync,
  openEntityEditor,
  renderEntityScenePicker,
  formatEntitySyncSummary,
  inferSceneRefsForEntity,
  saveEntityEditor,
  deleteEntityFromEditor,
  openEditor,
  renderEditor,
  renderEditorScenes,
  showEntityDetail,
  closeEntityModal,
  triggerImageUpload,
  syncEntityEditorImage,
  pickFromAssetLibrary,
  selectAssetFromLibrary,
  clearEntityImage,
  saveStoryboard,
  saveAndGenerate,
  openInMonitor,
  startVideoGeneration,
  maybeRefreshEditorImages,
  applyEditorGlobalStyle,
});

export function applyEditorGlobalStyle() {
  const sb = getCreatedStoryboard();
  if (!sb) { showToast(t('create.no_data_found'), 'error'); return; }

  const input = document.getElementById('editor-global-style-input');
  if (!input) return;
  const newStyle = input.value.trim();
  if (!newStyle) { showToast(t('editor.style_empty_hint'), 'error'); return; }

  const va = sb.video_analysis || (sb.video_analysis = {});
  const oldStyle = (va.style || '').trim();
  va.style = newStyle;

  const _replaceDescPrefix = (desc) => {
    if (!desc) return desc;
    let cleaned = desc;
    if (oldStyle) {
      const escaped = oldStyle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      cleaned = cleaned.replace(new RegExp('^' + escaped + '[，,、\\s]*'), '');
    }
    return newStyle + '，' + cleaned;
  };

  (sb.characters || []).forEach(c => { if (c.description) c.description = _replaceDescPrefix(c.description); });
  (sb.locations || []).forEach(l => { if (l.description) l.description = _replaceDescPrefix(l.description); });
  (sb.props || []).forEach(p => { if (p.description) p.description = _replaceDescPrefix(p.description); });

  const _replacePromptStyle = (prompt) => {
    if (!prompt) return prompt;
    const lines = prompt.split('\n');
    if (lines.length > 0 && /^\s*(?:风格|画面风格|整体画面风格|style)\s*[:：]/i.test(lines[0])) {
      lines[0] = '风格：' + newStyle;
    }
    return lines.join('\n');
  };

  (sb.storyboard || []).forEach((scene, idx) => {
    // 先从编辑器 textarea 同步最新内容
    const ta = document.getElementById('editor-scene-seedance-' + idx);
    if (ta) scene.seedance_prompt = ta.value;
    if (scene.seedance_prompt) scene.seedance_prompt = _replacePromptStyle(scene.seedance_prompt);
    if (sb.groups && sb.groups[idx]) sb.groups[idx].sora_prompt = scene.seedance_prompt;
    // 回写到 textarea
    if (ta && scene.seedance_prompt) ta.value = scene.seedance_prompt;
  });

  setCreatedStoryboard(sb);
  showToast(t('create.style_applied_storyboard'), 'success');
}


