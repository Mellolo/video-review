/**
 * unit-helpers.js — unit/attempt status helpers, prompt editor, critique HTML
 * Extracted from index.html lines 6615-8091 as ES module.
 */

import {
  currentData,
  videoJobsData,
  selectedVideoJobId,
  assetData,
  assetDataLoadingPromise,
  setAssetData,
  setAssetDataLoadingPromise,
} from './state.js';

import { esc, apiFetch, showToast } from './utils.js';
import { t, getCurrentLang } from './i18n.js';

// ── Module-level state ──────────────────────────────────────────
export let unitDataMap = {};
export function setUnitDataMap(v) { unitDataMap = v; }
export function resetUnitDataMap() { unitDataMap = {}; }

const promptEditorState = {};
let activePromptEditorId = null;
let activeMentionIndex = 0;

// ── Attempt / Unit status helpers ────────────────────────────────

export function isAttemptPlaceholder(a) {
  return !!(a && a.status === 'in_progress' && !a.output_path && a.metadata?.placeholder);
}

export function getAttemptVisualStatus(a) {
  if (!a) return 'failed';
  if (a.status === 'success') return 'success';
  if (a.status === 'draft') return 'pending';
  if (a.status === 'in_progress') return isAttemptPlaceholder(a) ? 'pending' : 'running';
  return 'failed';
}

export function getAttemptMetaBadges(u, attempt) {
  if (!attempt) return [];
  const badges = [];
  const outputName = (attempt.output_path || '').split('/').pop() || '';
  const finalName = (u?.final_video_path || '').split('/').pop() || '';
  const attempts = getDisplayAttempts(u);
  const latestAttemptId = attempts.length ? Number(attempts[attempts.length - 1]?.attempt_id) : null;
  const isFinalSelected = finalName && outputName && finalName === outputName;

  if (/recovered/i.test(outputName)) {
    badges.push({ key: 'recovered', label: t('unit.recovery'), cls: 'recovered' });
  }
  if (isFinalSelected && attempts.length > 1 && latestAttemptId != null && Number(attempt.attempt_id) !== latestAttemptId) {
    badges.push({ key: 'selector', label: t('unit.selector_final'), cls: 'selector' });
  }
  if (attempt.critique_error) {
    badges.push({ key: 'critique-failed', label: t('unit.review_failed'), cls: 'critique-failed' });
  } else if (attempt.output_path && !attempt.critique_result) {
    badges.push({ key: 'critique-missing', label: t('unit.no_review'), cls: 'critique-missing' });
  }
  return badges;
}

export function renderAttemptMetaBadges(u, attempt) {
  const badges = getAttemptMetaBadges(u, attempt);
  if (!badges.length) return '';
  return `<div class="attempt-meta-badges">${badges.map(b => `<span class="attempt-meta-badge ${b.cls}">${esc(b.label)}</span>`).join('')}</div>`;
}

export function getUnitRegenRequests(unitId) {
  return (currentData?.regen_requests || []).filter(req => Number(req?.unit_id) === Number(unitId));
}

export function getActiveUnitRegenRequest(unitId) {
  const requests = getUnitRegenRequests(unitId)
    .filter(req => ['draft', 'queued'].includes(req?.status))
    .sort((a, b) => Number(b?.updated_at || b?.created_at || 0) - Number(a?.updated_at || a?.created_at || 0));
  if (!requests.length) return null;
  const req = requests[0];
  if (req.status === 'queued') {
    const job = videoJobsData?.find(j => j.job_id === selectedVideoJobId);
    const jobIsActive = ['running', 'paused'].includes(job?.status || '');
    if (!jobIsActive) return null;
  }
  return req;
}

export function getSyntheticRegenAttempt(u) {
  const req = getActiveUnitRegenRequest(u?.unit_id);
  if (!req) return null;
  const attempts = u?.attempts || [];
  const fallbackAttemptId = Math.max(0, ...attempts.map(a => Number(a?.attempt_id || 0)), Number(req?.created_from_attempt_id || 0)) + 1;
  const placeholderAttemptId = Number(req?.placeholder_attempt_id || fallbackAttemptId || 0);
  if (!placeholderAttemptId) return null;
  if (attempts.some(a => Number(a?.attempt_id) === placeholderAttemptId)) return null;
  // Use manual_image_ref_assets directly as the single source of truth.
  // Do NOT merge with sourceAttempt assets — that caused numbering conflicts
  // when compact renumbering shifted keys between attempts.
  const assets = req.manual_image_ref_assets || {};
  const assetMap = Object.fromEntries(Object.entries(assets).map(([key, value]) => [key, value?.label || key]));
  return {
    attempt_id: placeholderAttemptId,
    created_from_attempt_id: req.created_from_attempt_id ?? null,
    status: req.status === 'draft' ? 'draft' : 'in_progress',
    tool_used: 'skill:pending',
    output_path: null,
    critique_result: null,
    error_message: null,
    input_params: {
      prompt: req.manual_prompt || req.source_prompt || u?.prompt || '',
      manual_image_ref_assets: assets,
    },
    metadata: {
      placeholder: true,
      synthetic: true,
      regen_request_id: req.request_id,
      regen_status: req.status,
      created_from_attempt_id: req.created_from_attempt_id ?? null,
      manual_image_ref_assets: assets,
    },
    max_attempts_hint: req.extra_attempts ?? 1,
    image_ref_assets: assets,
    image_ref_map: assetMap,
  };
}

export function getDisplayAttempts(u) {
  const attempts = [...(u?.attempts || [])];
  const synthetic = getSyntheticRegenAttempt(u);
  if (synthetic) attempts.push(synthetic);
  return attempts;
}

export function isDraftRegenAttempt(attempt) {
  return !!(attempt?.metadata?.synthetic && attempt?.metadata?.regen_status === 'draft');
}

export function isQueuedRegenAttempt(attempt) {
  return !!(attempt?.metadata?.synthetic && attempt?.metadata?.regen_status === 'queued');
}

export function isEditableDraftAttempt(attempt) {
  return isDraftRegenAttempt(attempt);
}

export function getPreferredAttemptIndex(u) {
  const attempts = getDisplayAttempts(u);
  const syntheticIdx = attempts.findIndex(a => a?.metadata?.synthetic);
  if (syntheticIdx >= 0) return syntheticIdx;
  const activeReq = getActiveUnitRegenRequest(u?.unit_id);
  if (activeReq?.placeholder_attempt_id != null) {
    const regenIdx = attempts.findIndex(a => Number(a?.attempt_id) === Number(activeReq.placeholder_attempt_id));
    if (regenIdx >= 0) return regenIdx;
  }
  if (u?.final_attempt_id != null) {
    const idx = attempts.findIndex(a => Number(a?.attempt_id) === Number(u.final_attempt_id));
    if (idx >= 0) return idx;
  }
  if (u?.final_video_path) {
    const fn = u.final_video_path.split('/').pop();
    for (let i = attempts.length - 1; i >= 0; i--) {
      if (attempts[i]?.output_path && attempts[i].output_path.split('/').pop() === fn) return i;
    }
  }
  for (let i = attempts.length - 1; i >= 0; i--) {
    if (attempts[i]?.output_path) return i;
  }
  return attempts.length ? attempts.length - 1 : -1;
}

export function unitHasCheckpointVideoOutput(u) {
  if (!u) return false;
  if (u.final_video_path) return true;
  return getDisplayAttempts(u).some(a => !!a?.output_path);
}

export function getUnitStatus(u, jobStatus) {
  const activeReq = getActiveUnitRegenRequest(u?.unit_id);
  const attempts = u.attempts || [];
  const _jobStatus = jobStatus ?? (() => {
    const job = videoJobsData?.find(j => j.job_id === selectedVideoJobId);
    return job?.status || '';
  })();
  const jobIsActive = ['running', 'paused'].includes(_jobStatus);
  if (jobIsActive && attempts.some(a => a.status === 'in_progress' && !isAttemptPlaceholder(a))) return 'in_progress';
  if (jobIsActive && attempts.some(a => isAttemptPlaceholder(a))) return 'queued';
  if (jobIsActive && (activeReq?.status === 'queued' || activeReq?.status === 'consumed')) return 'queued';
  if (activeReq?.status === 'draft') return 'draft';
  if (u.is_completed && unitHasCheckpointVideoOutput(u)) return 'completed';
  if (u.is_completed && !unitHasCheckpointVideoOutput(u)) return 'failed';
  if (!u.is_completed && attempts.length > 0) {
    const last = attempts[attempts.length - 1];
    if (last.status === 'success' && last.output_path && !last.critique_result && !last.critique_error) return 'critiquing';
  }
  if (attempts.length > 0 && !u.is_completed) return 'failed';
  return 'pending';
}

export function getUnitStatusLabel(status) {
  const map = {
    completed: t('unit.status_completed'),
    in_progress: t('unit.status_in_progress'),
    queued: t('unit.status_queued'),
    draft: t('unit.status_pending'),
    critiquing: t('unit.status_critiquing'),
    failed: t('unit.status_retry'),
    pending: t('unit.status_waiting'),
  };
  return map[status] || status;
}

export function getBestAttemptIndex(u) {
  return getPreferredAttemptIndex(u);
}

export function getBestVideoPath(u, projectName, runId) {
  const base = (projectName && runId) ? `/repo-media/${projectName}/${runId}` : '/media';
  if (u.final_video_path) return `${base}/${u.final_video_path.split('/').pop()}`;
  for (let i = (u.attempts||[]).length - 1; i >= 0; i--)
    if (u.attempts[i].output_path) return `${base}/${u.attempts[i].output_path.split('/').pop()}`;
  return null;
}

export function buildAttemptPlaceholderMessage(u, attempt, unitStatus = '') {
  if (isDraftRegenAttempt(attempt)) return t('unit.draft_hint');
  if (isQueuedRegenAttempt(attempt) || unitStatus === 'queued') return t('unit.created_waiting');
  if (attempt?.status === 'in_progress' && !isAttemptPlaceholder(attempt)) return t('unit.status_in_progress');
  if (!u?.is_completed) return t('unit.no_video');
  return t('unit.no_video');
}

export function renderAttemptVideoStage(u, attempt, videoSrc, unitStatus = '', opts = {}) {
  const { elementId = '', includePlayerId = false, playerId = '' } = opts || {};
  if (videoSrc) {
    const videoAttrs = includePlayerId && playerId ? ` id="${playerId}"` : '';
    const wrapId = elementId ? ` id="${elementId}"` : '';
    return `<div class="browse-prompt-block"${wrapId} style="flex:1;min-height:260px;padding:0;display:flex;align-items:center;justify-content:center;background:#000;overflow:hidden;border-color:rgba(255,255,255,0.12);border-radius:18px"><video${videoAttrs} src="${videoSrc}" controls style="width:100%;height:100%;object-fit:contain"></video></div>`;
  }
  const wrapId = elementId ? ` id="${elementId}"` : '';
  const msg = buildAttemptPlaceholderMessage(u, attempt, unitStatus);
  const color = unitStatus === 'queued' || isDraftRegenAttempt(attempt) || isQueuedRegenAttempt(attempt) ? 'var(--accent)' : 'var(--text-muted)';
  return `<div class="browse-prompt-block"${wrapId} style="flex:1;min-height:260px;padding:0;display:flex;align-items:center;justify-content:center;background:#000;overflow:hidden;border-color:rgba(255,255,255,0.12);border-radius:18px"><div style="display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;width:100%;height:100%;color:${color};font-size:13px"><svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="2" width="20" height="20" rx="2.18"/><polygon points="10,8 16,12 10,16"/></svg><div>${msg}</div></div></div>`;
}

// ── Attempt stored prompt / ref helpers ──────────────────────────

export function getAttemptStoredPrompt(u, idx) {
  const attempts = getDisplayAttempts(u);
  if (idx >= 0 && idx < attempts.length) {
    const a = attempts[idx];
    // Prefer input_params.prompt (always original numbering), then
    // metadata.prompt (same numbering after arch fix), then fallbacks.
    if (a.input_params?.prompt) return a.input_params.prompt;
    if (a.metadata?.prompt) return a.metadata.prompt;
    if (a.prompt) return a.prompt;
  }
  return u.prompt || '';
}

export function getAttemptRefSource(u, attempt) {
  if (!u || !attempt) return null;
  const realAttempts = u?.attempts || [];
  const createdFromAttemptId = Number(
    attempt?.created_from_attempt_id
    ?? attempt?.metadata?.created_from_attempt_id
    ?? attempt?.metadata?.source_attempt_id
    ?? NaN
  );
  if (Number.isFinite(createdFromAttemptId) && createdFromAttemptId > 0) {
    const exact = realAttempts.find(a => Number(a?.attempt_id) === createdFromAttemptId);
    if (exact) return exact;
  }
  if (u?.final_attempt_id != null) {
    const finalAttempt = realAttempts.find(a => Number(a?.attempt_id) === Number(u.final_attempt_id));
    if (finalAttempt) return finalAttempt;
  }
  return [...realAttempts].reverse().find(a => {
    const assets = a?.image_ref_assets || a?.metadata?.image_ref_assets || {};
    const map = a?.image_ref_map || a?.metadata?.image_ref_map || {};
    return Object.keys(assets).length || Object.keys(map).length;
  }) || realAttempts[realAttempts.length - 1] || null;
}

export function getAttemptBaseImageRefAssets(u, idx) {
  const attempts = getDisplayAttempts(u);
  if (idx >= 0 && idx < attempts.length) {
    const attempt = attempts[idx] || {};
    // Single source: prefer top-level (synthetic attempts), then metadata.
    // No fallback to other attempts — each attempt owns its own assets.
    return attempt.image_ref_assets || attempt.metadata?.image_ref_assets || {};
  }
  return {};
}

export function getAttemptMaxAttempts(u, idx) {
  const attempts = getDisplayAttempts(u);
  if (idx >= 0 && idx < attempts.length) {
    const a = attempts[idx];
    return a.max_attempts_hint ?? a.metadata?.max_attempts_hint ?? null;
  }
  return null;
}

export function getAttemptBaseImageRefMap(u, idx) {
  const attempts = getDisplayAttempts(u);
  if (idx >= 0 && idx < attempts.length) {
    const attempt = attempts[idx] || {};
    return attempt.image_ref_map || attempt.metadata?.image_ref_map || {};
  }
  return {};
}

export function getStoryboardScenesForUnit(u) {
  const storyboardScenes = currentData?.storyboard?.storyboard || [];
  const sceneNumbers = new Set((u?.scene_numbers || []).map(n => Number(n)).filter(Number.isFinite));
  if (!sceneNumbers.size) return [];
  return storyboardScenes.filter(scene => sceneNumbers.has(Number(scene?.scene_number)));
}

// ── Prompt asset name normalization & resolution ────────────────

export function normalizePromptAssetName(name = '') {
  return (name || '')
    .toLowerCase()
    .replace(/[（(][^()（）]*[)）]/g, '')
    .replace(/[·•・]/g, '')
    .replace(/\s+/g, '');
}

export function resolvePromptAssetImageSrc(path = '') {
  if (!path) return '';
  if (/^https?:\/\//.test(path)) return path;
  if (path.startsWith('/asset?') || path.startsWith('/repo-media/') || path.startsWith('/media/')) return path;
  if (path.startsWith('/')) return `/asset?path=${encodeURIComponent(path)}`;
  return `/asset?path=${encodeURIComponent(path)}`;
}

// ── Asset data loading ──────────────────────────────────────────

export async function ensureAssetDataLoaded(force = false) {
  const needsLoad = force || !assetData;
  if (!needsLoad) return assetData;
  if (!assetDataLoadingPromise) {
    setAssetDataLoadingPromise(
      fetch('/api/assets')
        .then(r => r.json())
        .then(data => {
          setAssetData(data);
          return data;
        })
        .catch(err => {
          console.error('Failed to load asset library for prompt mentions', err);
          return assetData || { characters: [], locations: [], props: [] };
        })
        .finally(() => {
          setAssetDataLoadingPromise(null);
        })
    );
  }
  return assetDataLoadingPromise;
}

// ── Build prompt asset candidates ───────────────────────────────

export function buildPromptAssetCandidates() {
  const sb = currentData?.storyboard || {};
  const media = currentData?.media || {};
  const projectName = currentData?.storyboard_name || '';
  const runId = currentData?.run_id || '';
  const mediaBase = (projectName && runId) ? `/repo-media/${projectName}/${runId}` : '/media';

  const resolveAssetImage = (type, entity) => {
    const name = entity?.name || '';
    const normalized = name.toLowerCase().replace(/\s/g, '_');
    const list = type === 'character'
      ? (media.charsheets || [])
      : type === 'location'
        ? (media.locsheets || [])
        : (media.propsheets || []);
    const matched = list.find(f => f.toLowerCase().includes(normalized));
    if (matched) return `${mediaBase}/${matched}`;
    if (entity?.image_path) return `/asset?path=${encodeURIComponent(entity.image_path)}`;
    return '';
  };

  const projectCandidates = [
    ...(sb.characters || []).map(item => ({ type: 'character', name: item.name || '', image: resolveAssetImage('character', item), path: item.image_path || '' })),
    ...(sb.locations || []).map(item => ({ type: 'location', name: item.name || '', image: resolveAssetImage('location', item), path: item.image_path || '' })),
    ...(sb.props || []).map(item => ({ type: 'prop', name: item.name || '', image: resolveAssetImage('prop', item), path: item.image_path || '' })),
  ].filter(item => item.name);

  const merged = [];
  const seen = new Set();
  projectCandidates.forEach(item => {
    const key = `${item.type}::${normalizePromptAssetName(item.name)}`;
    if (!item.name || seen.has(key)) return;
    seen.add(key);
    merged.push(item);
  });
  return merged;
}

export function findPromptAssetCandidateByLabel(label = '') {
  const normalizedLabel = normalizePromptAssetName(label);
  if (!normalizedLabel) return null;
  const candidates = buildPromptAssetCandidates();
  return candidates.find(item => normalizePromptAssetName(item.name) === normalizedLabel)
    || candidates.find(item => {
      const candidateName = normalizePromptAssetName(item.name);
      return candidateName.includes(normalizedLabel) || normalizedLabel.includes(candidateName);
    })
    || null;
}

export function findPromptAssetCandidateByLabelAndType(label = '', type = '') {
  const normalizedLabel = normalizePromptAssetName(label);
  if (!normalizedLabel) return null;
  const candidates = buildPromptAssetCandidates();
  const sameType = (item) => !type || !item?.type || item.type === type;
  return candidates.find(item => sameType(item) && normalizePromptAssetName(item.name) === normalizedLabel)
    || candidates.find(item => {
      if (!sameType(item)) return false;
      const candidateName = normalizePromptAssetName(item.name);
      return candidateName.includes(normalizedLabel) || normalizedLabel.includes(candidateName);
    })
    || null;
}

export function normalizeImageRefAssets(imageRefAssets = {}, imageRefMap = {}) {
  const normalized = {};
  const keys = new Set([
    ...Object.keys(imageRefMap || {}),
    ...Object.keys(imageRefAssets || {}),
  ]);
  keys.forEach((key) => {
    const baseAsset = imageRefAssets?.[key] || {};
    const fallbackLabel = imageRefMap?.[key] || '';
    const candidate = findPromptAssetCandidateByLabel(baseAsset.label || fallbackLabel || key);
    normalized[key] = {
      label: baseAsset.label || fallbackLabel || candidate?.name || key,
      type: baseAsset.type || candidate?.type || 'reference',
      path: baseAsset.path || candidate?.path || candidate?.image || '',
    };
  });
  return normalized;
}

export function findPromptAssetEntry(stateAssets = {}, item = {}) {
  const targetName = normalizePromptAssetName(item?.name || '');
  return Object.entries(stateAssets || {}).find(([, value]) => {
    const assetName = normalizePromptAssetName(value?.label || '');
    const sameType = !item?.type || !value?.type || value.type === item.type;
    return sameType && assetName === targetName;
  }) || Object.entries(stateAssets || {}).find(([, value]) => {
    const assetName = normalizePromptAssetName(value?.label || '');
    const sameType = !item?.type || !value?.type || value.type === item.type;
    return sameType && (assetName.includes(targetName) || targetName.includes(assetName));
  }) || null;
}

// ── Prompt candidate building ───────────────────────────────────

export function dedupePromptCandidates(candidates = []) {
  const merged = [];
  const seen = new Set();
  (candidates || []).forEach((item) => {
    const name = (item?.name || item?.label || '').trim();
    if (!name) return;
    const type = item?.type || 'reference';
    const key = `${type}::${normalizePromptAssetName(name)}`;
    if (seen.has(key)) return;
    seen.add(key);
    merged.push({
      type,
      name,
      image: item?.image || '',
      path: item?.path || '',
    });
  });
  return merged;
}

export function buildPromptCandidatesFromRefs(imageRefAssets = {}, imageRefMap = {}) {
  const normalized = normalizeImageRefAssets(imageRefAssets || {}, imageRefMap || {});
  return Object.values(normalized).map((asset) => ({
    type: asset?.type || 'reference',
    name: asset?.label || '',
    image: resolvePromptAssetImageSrc(asset?.path || ''),
    path: asset?.path || '',
  })).filter(item => item.name);
}

export function buildPromptCandidatesForUnit(u, imageRefAssets = {}, imageRefMap = {}) {
  const sceneCandidates = [];
  const pushSceneCandidate = (label, type) => {
    const name = (label || '').trim();
    if (!name) return;
    const candidate = findPromptAssetCandidateByLabelAndType(name, type);
    sceneCandidates.push({
      type: type || candidate?.type || 'reference',
      name,
      image: candidate?.image || '',
      path: candidate?.path || '',
    });
  };

  getStoryboardScenesForUnit(u).forEach((scene) => {
    (scene?.characters_in_scene || []).forEach(name => pushSceneCandidate(name, 'character'));
    pushSceneCandidate(scene?.scene_location || '', 'location');
    (scene?.props_in_scene || []).forEach(name => pushSceneCandidate(name, 'prop'));
  });

  (u?.characters_in_scene || []).forEach(name => pushSceneCandidate(name, 'character'));

  return dedupePromptCandidates([
    ...buildPromptCandidatesFromRefs(imageRefAssets, imageRefMap),
    ...sceneCandidates,
    ...buildPromptAssetCandidates(),
  ]);
}

export function buildPromptImageRefMap(imageRefAssets = {}) {
  return Object.fromEntries(Object.entries(imageRefAssets || {}).map(([key, value]) => [key, value?.label || key]));
}

export function autoEmbedPromptReferences(promptText, candidates = [], imageRefAssets = {}, imageRefMap = {}) {
  const rawPrompt = promptText || '';
  const normalizedAssets = normalizeImageRefAssets(imageRefAssets || {}, imageRefMap || {});
  if (!rawPrompt) {
    return {
      promptText: '',
      imageRefAssets: normalizedAssets,
      imageRefMap: buildPromptImageRefMap(normalizedAssets),
    };
  }

  if (/@(?:图片?|image)\d+/i.test(rawPrompt)) {
    return {
      promptText: rawPrompt,
      imageRefAssets: normalizedAssets,
      imageRefMap: buildPromptImageRefMap(normalizedAssets),
    };
  }

  const matchedCandidates = dedupePromptCandidates(candidates)
    .map((item) => ({
      ...item,
      firstIndex: rawPrompt.indexOf(item.name),
    }))
    .filter(item => item.name && item.firstIndex >= 0)
    .sort((a, b) => a.firstIndex - b.firstIndex || b.name.length - a.name.length);

  if (!matchedCandidates.length) {
    return {
      promptText: rawPrompt,
      imageRefAssets: normalizedAssets,
      imageRefMap: buildPromptImageRefMap(normalizedAssets),
    };
  }

  const assignedRefs = new Map();
  const embeddedAssets = {};
  matchedCandidates.forEach((item) => {
    const key = `${item.type || 'reference'}::${normalizePromptAssetName(item.name)}`;
    if (assignedRefs.has(key)) return;
    const ref = `@图片${assignedRefs.size + 1}`;
    assignedRefs.set(key, ref);
    embeddedAssets[ref] = {
      label: item.name,
      type: item.type || 'reference',
      path: item.path || item.image || '',
    };
  });

  let embeddedPrompt = rawPrompt;
  [...matchedCandidates]
    .sort((a, b) => b.name.length - a.name.length || a.firstIndex - b.firstIndex)
    .forEach((item) => {
      const key = `${item.type || 'reference'}::${normalizePromptAssetName(item.name)}`;
      const ref = assignedRefs.get(key);
      if (!ref) return;
      const marker = `__PROMPT_REF_${String(ref).replace(/\D/g, '')}__`;
      embeddedPrompt = embeddedPrompt.split(item.name).join(marker);
    });
  embeddedPrompt = embeddedPrompt.replace(/__PROMPT_REF_(\d+)__/g, '@图片$1');

  return {
    promptText: embeddedPrompt,
    imageRefAssets: embeddedAssets,
    imageRefMap: buildPromptImageRefMap(embeddedAssets),
  };
}

// ── Attempt prompt state ────────────────────────────────────────

export function getAttemptPromptState(u, idx) {
  const promptText = getAttemptStoredPrompt(u, idx);
  const imageRefAssets = getAttemptBaseImageRefAssets(u, idx);
  const imageRefMap = getAttemptBaseImageRefMap(u, idx);
  return autoEmbedPromptReferences(
    promptText,
    buildPromptCandidatesForUnit(u, imageRefAssets, imageRefMap),
    imageRefAssets,
    imageRefMap,
  );
}

export function getAttemptPrompt(u, idx) {
  return getAttemptPromptState(u, idx).promptText || '';
}

export function getAttemptImageRefAssets(u, idx) {
  return getAttemptPromptState(u, idx).imageRefAssets || {};
}

export function getAttemptImageRefMap(u, idx) {
  return getAttemptPromptState(u, idx).imageRefMap || {};
}

// ── Tokenizer & renderer ────────────────────────────────────────

export function tokenizePrompt(promptText, imageRefAssets = {}) {
  const prompt = promptText || '';
  const assets = normalizeImageRefAssets(imageRefAssets || {});
  const tokens = [];
  const regex = /@(?:图片?|image)(\d+)/gi;
  let lastIndex = 0;
  let match;
  while ((match = regex.exec(prompt)) !== null) {
    if (match.index > lastIndex) {
      tokens.push({ type: 'text', text: prompt.slice(lastIndex, match.index) });
    }
    const refKey = `@图片${match[1]}`;
    const asset = assets[refKey] || {};
    tokens.push({
      type: 'token',
      refKey,
      rawRef: refKey,
      label: asset.label || refKey,
      image: resolvePromptAssetImageSrc(asset.path || ''),
      entityType: asset.type || 'reference',
      stableKey: `${refKey}-${tokens.length}`,
    });
    lastIndex = regex.lastIndex;
  }
  if (lastIndex < prompt.length) {
    tokens.push({ type: 'text', text: prompt.slice(lastIndex) });
  }
  return tokens.length ? tokens : [{ type: 'text', text: '' }];
}

export function tokensToRawPrompt(tokens = []) {
  return (tokens || []).map(token => token.type === 'token' ? (token.rawRef || '') : (token.text || '')).join('');
}

export function renderPromptToken(token, draggable = false) {
  const dragAttrs = draggable
    ? ` draggable="true" data-token-ref="${esc(token.rawRef || '')}" data-token-key="${esc(token.stableKey || token.rawRef || '')}"`
    : '';
  const imageHtml = token.image
    ? `<img class="prompt-token-image" src="${token.image}" loading="lazy" alt="${esc(token.label || token.rawRef || '')}" />`
    : '';
  return `<span class="prompt-token" contenteditable="false"${dragAttrs}>${imageHtml}<span class="prompt-token-label"><strong>@</strong><span class="prompt-token-name">${esc(token.label || token.rawRef || '')}</span></span></span>`;
}

export function renderPromptWithRefs(promptText, imageRefMap, imageRefAssets) {
  const assets = normalizeImageRefAssets(imageRefAssets || {}, imageRefMap || {});
  const tokens = tokenizePrompt(promptText, assets);
  return `<div class="prompt-render-block">${tokens.map(token => token.type === 'token'
    ? renderPromptToken(token, false)
    : `<span class="prompt-render-text">${esc(token.text || '')}</span>`).join('')}</div>`;
}

// ── Prompt editor state management ──────────────────────────────

export function normalizeEntityTypeLabel(type) {
  return type === 'character' ? t('unit.type_character') : type === 'location' ? t('unit.type_location') : type === 'prop' ? t('unit.type_prop') : t('unit.type_ref');
}

export function getNextPromptAssetRef(stateAssets = {}) {
  const nums = Object.keys(stateAssets || {}).map(key => {
    const m = String(key || '').match(/^@图片(\d+)$/);
    return m ? Number(m[1]) : 0;
  }).filter(Boolean);
  return `@图片${(nums.length ? Math.max(...nums) : 0) + 1}`;
}

export function ensurePromptAssetRef(state, item = {}) {
  if (!state) return '';
  const existing = findPromptAssetEntry(state.assets || {}, item);
  if (existing) return existing[0];
  const rawRef = getNextPromptAssetRef(state.assets || {});
  state.assets = {
    ...(state.assets || {}),
    [rawRef]: {
      label: item.name || rawRef,
      type: item.type || 'reference',
      path: item.path || item.image || '',
    },
  };
  return rawRef;
}

export function buildPromptMentionCandidates(query = '', editorId = '') {
  const q = (query || '').trim().toLowerCase();
  const state = editorId ? getPromptEditorState(editorId) : null;
  const scopedCandidates = Object.entries(state?.assets || {}).map(([rawRef, asset]) => {
    const fallback = findPromptAssetCandidateByLabel(asset?.label || rawRef) || {};
    return {
      rawRef,
      type: asset?.type || fallback.type || 'reference',
      name: asset?.label || fallback.name || rawRef,
      image: resolvePromptAssetImageSrc(asset?.path || fallback.image || ''),
      path: asset?.path || fallback.path || '',
    };
  }).filter(item => item.name && item.rawRef);
  const baseCandidates = buildPromptAssetCandidates();
  const merged = [];
  const seen = new Set();
  [...scopedCandidates, ...baseCandidates].forEach(item => {
    const key = `${item.type || 'reference'}::${normalizePromptAssetName(item.name || '')}`;
    if (!item.name || seen.has(key)) return;
    seen.add(key);
    merged.push(item);
  });
  return merged.filter(item => !q || (item.name || '').toLowerCase().includes(q));
}

export function ensurePromptMentionMenu() {
  let menu = document.getElementById('prompt-mention-menu');
  if (menu) return menu;
  menu = document.createElement('div');
  menu.id = 'prompt-mention-menu';
  menu.className = 'prompt-mention-menu';
  menu.addEventListener('mousedown', handlePromptMentionMenuPick);
  document.body.appendChild(menu);
  return menu;
}

export function hidePromptMentionMenu() {
  const menu = document.getElementById('prompt-mention-menu');
  if (!menu) return;
  menu.classList.remove('show');
  menu.innerHTML = '';
  activeMentionIndex = 0;
  if (activePromptEditorId && promptEditorState[activePromptEditorId]) {
    promptEditorState[activePromptEditorId].activeMentionState = null;
  }
}

export function getPromptEditorState(editorId) {
  return promptEditorState[editorId];
}

export function buildPromptEditorState(editorId, rawPrompt, imageRefAssets, placeholder, previewId = '') {
  promptEditorState[editorId] = {
    editorId,
    rawPrompt: rawPrompt || '',
    tokens: tokenizePrompt(rawPrompt || '', imageRefAssets || {}),
    assets: imageRefAssets || {},
    placeholder: placeholder || '',
    mentionQuery: '',
    activeMentionState: null,
    previewId,
  };
  return promptEditorState[editorId];
}

export function mergePromptTokens(tokens = []) {
  const merged = [];
  for (const token of tokens || []) {
    if (!token) continue;
    if (token.type === 'text') {
      const text = token.text || '';
      if (!text) continue;
      const prev = merged[merged.length - 1];
      if (prev && prev.type === 'text') prev.text += text;
      else merged.push({ type: 'text', text });
    } else {
      merged.push(token);
    }
  }
  return merged.length ? merged : [{ type: 'text', text: '' }];
}

export function getPromptTokenVisibleText(token) {
  return `@${token?.label || token?.rawRef || ''}`;
}

export function buildPromptTokenFromAsset(rawRef, asset, fallback = {}) {
  return {
    type: 'token',
    rawRef,
    refKey: rawRef,
    label: asset?.label || fallback.name || rawRef,
    image: resolvePromptAssetImageSrc(asset?.path || fallback.image || ''),
    entityType: asset?.type || fallback.type || 'reference',
    stableKey: `${rawRef}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
  };
}

// ── Editor DOM sync helpers ─────────────────────────────────────

export function buildEditorTextNodesFromRaw(rawText, state) {
  if (!rawText) return [];
  const normalized = [];
  tokenizePrompt(rawText, state?.assets || {}).forEach(part => {
    if (part.type === 'token') {
      normalized.push({
        ...part,
        stableKey: `${part.rawRef || part.refKey}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      });
      return;
    }
    const pieces = (part.text || '').split(/(@[^\s@]*)/g).filter(Boolean);
    pieces.forEach(piece => {
      if (piece.startsWith('@')) {
        const candidates = buildPromptMentionCandidates(piece.slice(1), state?.editorId || '');
        const exact = candidates.find(item => `@${item.name}` === piece);
        if (exact) {
          const refEntry = findPromptAssetEntry(state.assets || {}, exact);
          if (refEntry) {
            const [rawRef, asset] = refEntry;
            normalized.push(buildPromptTokenFromAsset(rawRef, asset, exact));
            return;
          }
        }
      }
      normalized.push({ type: 'text', text: piece });
    });
  });
  return mergePromptTokens(normalized);
}

export function buildPromptEditorTokensFromDom(editorId) {
  const state = getPromptEditorState(editorId);
  const root = document.getElementById(editorId);
  if (!state || !root) return state?.tokens || [];
  const nextTokens = [];
  root.childNodes.forEach(node => {
    if (node.nodeType === Node.TEXT_NODE) {
      nextTokens.push(...buildEditorTextNodesFromRaw(node.textContent || '', state));
      return;
    }
    if (!(node instanceof HTMLElement)) {
      return;
    }
    const tokenEl = node.matches('.prompt-token') ? node : node.querySelector('.prompt-token');
    if (tokenEl) {
      const rawRef = tokenEl.dataset.tokenRef || '';
      const asset = (state.assets || {})[rawRef] || {};
      nextTokens.push(buildPromptTokenFromAsset(rawRef, asset, {
        name: asset.label || rawRef,
        type: asset.type || 'reference',
      }));
      return;
    }
    nextTokens.push(...buildEditorTextNodesFromRaw(node.textContent || '', state));
  });
  return mergePromptTokens(nextTokens);
}

// ── Mention query helpers ───────────────────────────────────────

export function getPromptMentionQuery(text = '') {
  const atPos = (text || '').lastIndexOf('@');
  if (atPos < 0) return null;
  const query = (text || '').slice(atPos + 1);
  if (/\s/.test(query)) return null;
  return {
    query,
    start: atPos,
    end: (text || '').length,
  };
}

export function getPromptEditorMentionState(editorId) {
  const root = document.getElementById(editorId);
  const selection = window.getSelection();
  if (!root || !selection || !selection.rangeCount) return null;

  let node = selection.anchorNode;
  let offset = selection.anchorOffset;
  if (!node || !root.contains(node)) return null;

  if (node.nodeType === Node.ELEMENT_NODE) {
    const el = node;
    const child = el.childNodes[Math.max(0, offset - 1)] || el.childNodes[offset] || null;
    if (child && child.nodeType === Node.TEXT_NODE) {
      node = child;
      offset = child.textContent?.length || 0;
    } else if (child instanceof HTMLElement && !child.closest('.prompt-token')) {
      const textChild = Array.from(child.childNodes).reverse().find(n => n.nodeType === Node.TEXT_NODE);
      if (textChild) {
        node = textChild;
        offset = textChild.textContent?.length || 0;
      }
    }
  }

  if (!node || node.nodeType !== Node.TEXT_NODE) return null;
  if (node.parentElement?.closest('.prompt-token')) return null;

  const textBefore = (node.textContent || '').slice(0, offset);
  const mentionState = getPromptMentionQuery(textBefore);
  if (!mentionState) return null;

  const beforeRange = selection.getRangeAt(0).cloneRange();
  beforeRange.selectNodeContents(root);
  beforeRange.setEnd(node, offset);
  const beforeText = beforeRange.toString();
  return {
    query: mentionState.query,
    start: beforeText.length - mentionState.query.length - 1,
    end: beforeText.length,
  };
}

export function replacePromptMentionWithToken(tokens, mentionState, token) {
  if (!mentionState) return mergePromptTokens([...(tokens || []), token]);
  const next = [];
  let cursor = 0;
  let remainingMention = { ...mentionState };
  for (const part of tokens || []) {
    if (part.type === 'token') {
      next.push(part);
      cursor += getPromptTokenVisibleText(part).length;
      continue;
    }
    const text = part.text || '';
    const textStart = cursor;
    const textEnd = cursor + text.length;
    if (!remainingMention || remainingMention.end <= textStart || remainingMention.start >= textEnd) {
      next.push({ type: 'text', text });
      cursor = textEnd;
      continue;
    }
    const localStart = Math.max(0, remainingMention.start - textStart);
    const localEnd = Math.min(text.length, remainingMention.end - textStart);
    const before = text.slice(0, localStart);
    const after = text.slice(localEnd);
    if (before) next.push({ type: 'text', text: before });
    next.push(token);
    if (after) next.push({ type: 'text', text: after });
    cursor = textEnd;
    remainingMention = null;
  }
  if (remainingMention) next.push(token);
  return mergePromptTokens(next);
}

// ── Selection offset helpers ────────────────────────────────────

export function getPromptEditorSelectionOffset(editorId) {
  const root = document.getElementById(editorId);
  const selection = window.getSelection();
  if (!root || !selection || !selection.rangeCount) return null;
  const range = selection.getRangeAt(0);
  if (!root.contains(range.startContainer)) return null;
  const probe = range.cloneRange();
  probe.selectNodeContents(root);
  probe.setEnd(range.startContainer, range.startOffset);
  return probe.toString().length;
}

export function setPromptEditorSelectionOffset(editorId, offset) {
  const root = document.getElementById(editorId);
  if (!root || typeof offset !== 'number' || offset < 0) return;
  const selection = window.getSelection();
  if (!selection) return;

  let remaining = offset;
  let targetNode = null;
  let targetOffset = 0;

  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
  let node = walker.nextNode();
  while (node) {
    const len = node.textContent?.length || 0;
    if (remaining <= len) {
      targetNode = node;
      targetOffset = remaining;
      break;
    }
    remaining -= len;
    node = walker.nextNode();
  }

  if (!targetNode) {
    const lastText = (() => {
      let cur = null;
      const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
      let n = w.nextNode();
      while (n) {
        cur = n;
        n = w.nextNode();
      }
      return cur;
    })();
    if (lastText) {
      targetNode = lastText;
      targetOffset = lastText.textContent?.length || 0;
    } else {
      targetNode = root;
      targetOffset = root.childNodes.length;
    }
  }

  const range = document.createRange();
  range.setStart(targetNode, targetOffset);
  range.collapse(true);
  selection.removeAllRanges();
  selection.addRange(range);
}

// ── Prompt editor render / sync ──────────────────────────────────

export function renderPromptEditor(editorId, options = {}) {
  const state = getPromptEditorState(editorId);
  const root = document.getElementById(editorId);
  if (!state || !root) return;
  const selectionOffset = options?.preserveSelection ? getPromptEditorSelectionOffset(editorId) : null;
  root.dataset.placeholder = state.placeholder || '';
  root.classList.toggle('is-empty', !tokensToRawPrompt(state.tokens).trim());
  root.innerHTML = state.tokens.map((token, idx) => token.type === 'token'
    ? `<span data-token-idx="${idx}">${renderPromptToken(token, true)}</span>`
    : `<span class="prompt-editor-text" data-text-idx="${idx}">${esc(token.text || '')}</span>`).join('');

  root.querySelectorAll('.prompt-token[draggable="true"]').forEach(el => {
    el.addEventListener('dragstart', onPromptTokenDragStart);
    el.addEventListener('dragend', onPromptTokenDragEnd);
  });
  root.ondragover = onPromptEditorDragOver;
  root.ondrop = onPromptEditorDrop;
  if (options?.preserveSelection && selectionOffset != null) {
    setPromptEditorSelectionOffset(editorId, selectionOffset);
  }
  updatePromptPreview(editorId);
}

export function serializePromptEditor(editorId) {
  syncPromptEditorFromDom(editorId, { rerender: false });
  const state = getPromptEditorState(editorId);
  return state ? tokensToRawPrompt(state.tokens).trim() : '';
}

export function serializePromptEditorAssets(editorId) {
  syncPromptEditorFromDom(editorId, { rerender: false });
  const state = getPromptEditorState(editorId);
  if (!state) return {};
  const usedRefs = new Set((state.tokens || [])
    .filter(token => token?.type === 'token' && token?.rawRef)
    .map(token => token.rawRef));
  const serialized = {};
  usedRefs.forEach((rawRef) => {
    const asset = (state.assets || {})[rawRef];
    if (!asset) return;
    serialized[rawRef] = {
      label: asset.label || rawRef,
      type: asset.type || 'reference',
      path: asset.path || '',
    };
  });
  return serialized;
}

export function serializePromptEditorForSubmit(editorId) {
  const stateBefore = getPromptEditorState(editorId);
  const tokensBefore = stateBefore?.tokens ? [...stateBefore.tokens] : [];
  const rawPromptBefore = stateBefore?.rawPrompt || '';
  const assetsBefore = stateBefore?.assets ? { ...stateBefore.assets } : {};
  syncPromptEditorFromDom(editorId, { rerender: false });
  const state = getPromptEditorState(editorId);
  if (!state) return { prompt: '', assets: {} };
  // Grammarly 등이 DOM 을 수정해서 tokens 가 비어있으면 sync 전 state 복원
  if ((!state.tokens || state.tokens.length === 0) && tokensBefore.length > 0) {
    state.tokens = tokensBefore;
    state.rawPrompt = rawPromptBefore;
    state.assets = assetsBefore;
  }
  return serializePromptEditorStateToSubmit(state);
}

export function serializePromptEditorStateToSubmit(state) {
  if (!state) return { prompt: '', assets: {} };
  const assets = {};
  const parts = [];
  (state.tokens || []).forEach((token) => {
    if (!token) return;
    if (token.type !== 'token') {
      parts.push(token.text || '');
      return;
    }
    const sourceRef = token.rawRef || token.refKey || '';
    if (!sourceRef) return;
    if (!(sourceRef in assets)) {
      const asset = (state.assets || {})[sourceRef] || {};
      assets[sourceRef] = {
        label: asset.label || sourceRef,
        type: asset.type || 'reference',
        path: asset.path || '',
      };
    }
    parts.push(sourceRef);
  });
  return {
    prompt: parts.join('').trim(),
    assets,
  };
}

export function updatePromptPreview(editorId) {
  const state = getPromptEditorState(editorId);
  const previewId = state?.previewId;
  if (!previewId) return;
  const previewEl = document.getElementById(previewId);
  if (previewEl) previewEl.textContent = state ? tokensToRawPrompt(state.tokens).trim() : '';
}

export function syncPromptEditorFromDom(editorId, options = {}) {
  const state = getPromptEditorState(editorId);
  const root = document.getElementById(editorId);
  if (!state || !root) return;
  const nextTokens = buildPromptEditorTokensFromDom(editorId);
  state.tokens = nextTokens;
  state.rawPrompt = tokensToRawPrompt(state.tokens);
  root.classList.toggle('is-empty', !state.rawPrompt.trim());
  if (options.rerender) {
    renderPromptEditor(editorId, { preserveSelection: true });
    return;
  }
  updatePromptPreview(editorId);
}

// ── Prompt token insertion ──────────────────────────────────────

export function insertPromptToken(editorId, item) {
  const state = getPromptEditorState(editorId);
  if (!state) return;
  const mentionState = getPromptEditorMentionState(editorId) || state.activeMentionState;
  if (!state.activeMentionState) {
    syncPromptEditorFromDom(editorId, { rerender: false });
  }
  const root = document.getElementById(editorId);
  if (!root) return;
  let refEntry = item?.rawRef
    ? [item.rawRef, (state.assets || {})[item.rawRef] || {}]
    : findPromptAssetEntry(state.assets || {}, item);
  if ((!refEntry || !refEntry[0]) && item?.name) {
    const rawRef = ensurePromptAssetRef(state, item);
    if (rawRef) refEntry = [rawRef, (state.assets || {})[rawRef] || {}];
  }
  if (!refEntry) return;
  const [rawRef, asset] = refEntry;
  const promptToken = buildPromptTokenFromAsset(rawRef, asset, item);
  state.tokens = replacePromptMentionWithToken(state.tokens, mentionState, promptToken);
  state.rawPrompt = tokensToRawPrompt(state.tokens);
  const caretOffset = (() => {
    let total = 0;
    for (const part of state.tokens || []) {
      if (part === promptToken) break;
      total += part.type === 'token' ? getPromptTokenVisibleText(part).length : (part.text || '').length;
    }
    return total + getPromptTokenVisibleText(promptToken).length;
  })();
  renderPromptEditor(editorId);
  state.activeMentionState = null;
  root.focus();
  setPromptEditorSelectionOffset(editorId, caretOffset);
}

// ── Mention menu ────────────────────────────────────────────────

export function openPromptMentionMenuFromSelection(editorId) {
  const root = document.getElementById(editorId);
  if (!root) return;
  const mentionState = getPromptEditorMentionState(editorId);
  if (!mentionState) {
    hidePromptMentionMenu();
    return;
  }
  const selection = window.getSelection();
  const range = selection?.rangeCount ? selection.getRangeAt(0).cloneRange() : null;
  let rect = range?.getBoundingClientRect();
  if (!rect || (!rect.width && !rect.height)) rect = root.getBoundingClientRect();
  const state = getPromptEditorState(editorId);
  if (state) state.activeMentionState = { ...mentionState };
  openPromptMentionMenu(editorId, rect, mentionState.query);
}

export function renderPromptMentionMenu(editorId, anchorRect, query = '') {
  const menu = ensurePromptMentionMenu();
  const items = buildPromptMentionCandidates(query, editorId);
  const state = getPromptEditorState(editorId);
  if (!state) return;
  state.mentionQuery = query;
  activePromptEditorId = editorId;
  activeMentionIndex = 0;
  if (!items.length) {
    hidePromptMentionMenu();
    return;
  }
  const grouped = {
    character: items.filter(x => x.type === 'character'),
    location: items.filter(x => x.type === 'location'),
    prop: items.filter(x => x.type === 'prop'),
    reference: items.filter(x => !['character', 'location', 'prop'].includes(x.type)),
  };
  menu.innerHTML = Object.entries(grouped).map(([type, list]) => {
    if (!list.length) return '';
    return `<div class="prompt-mention-group">${normalizeEntityTypeLabel(type)}</div>${list.map((item, idx) => `<button type="button" class="prompt-mention-item${idx===0 && activeMentionIndex===0 ? ' active' : ''}" data-editor-id="${editorId}" data-item-raw-ref="${esc(item.rawRef || '')}" data-item-type="${item.type}" data-item-name="${esc(item.name)}" data-item-image="${esc(item.image || '')}" data-item-path="${esc(item.path || '')}" onmousedown="return window.pickPromptMentionFromMenu(event, this)" onclick="return false;">
      ${item.image ? `<img class="prompt-mention-thumb" src="${item.image}" loading="lazy" alt="${esc(item.name)}" />` : `<span class="prompt-token-fallback">@</span>`}
      <span class="prompt-mention-meta"><span class="prompt-mention-name">${esc(item.name)}</span><span class="prompt-mention-type">${normalizeEntityTypeLabel(item.type)}</span></span>
    </button>`).join('')}`;
  }).join('');
  menu.style.left = `${Math.min(anchorRect.left, window.innerWidth - 340)}px`;
  menu.style.top = `${Math.min(anchorRect.bottom + 6, window.innerHeight - 280)}px`;
  menu.style.pointerEvents = 'auto';
  menu.classList.add('show');
}

export function pickPromptMentionFromMenu(event, btn) {
  if (!(btn instanceof HTMLElement)) return false;
  event?.preventDefault?.();
  event?.stopPropagation?.();
  const editorId = btn.dataset.editorId || activePromptEditorId || '';
  if (!editorId) return false;
  insertPromptToken(editorId, {
    rawRef: btn.dataset.itemRawRef,
    type: btn.dataset.itemType,
    name: btn.dataset.itemName,
    image: btn.dataset.itemImage,
    path: btn.dataset.itemPath,
  });
  hidePromptMentionMenu();
  return false;
}

export function handlePromptMentionMenuPick(event) {
  const target = event.target instanceof HTMLElement ? event.target : null;
  const btn = target?.closest('.prompt-mention-item');
  if (!btn) return;
  pickPromptMentionFromMenu(event, btn);
}

export function openPromptMentionMenu(editorId, anchorRect, query = '') {
  renderPromptMentionMenu(editorId, anchorRect, query);
}

export function onPromptEditorInput(editorId) {
  syncPromptEditorFromDom(editorId, { rerender: false });
  openPromptMentionMenuFromSelection(editorId);
}

export function onPromptEditorKeyDown(event, editorId) {
  if (event.key === '@') {
    setTimeout(() => openPromptMentionMenuFromSelection(editorId), 0);
    return;
  }
  if (event.key === 'Escape') {
    hidePromptMentionMenu();
  }
}

export function initPromptEditor(editorId, rawPrompt, imageRefAssets, placeholder, previewId = '') {
  buildPromptEditorState(editorId, rawPrompt, imageRefAssets, placeholder, previewId);
  const root = document.getElementById(editorId);
  if (!root) return;
  // Grammarly 및 기타 브라우저 확장이 DOM 을 수정하지 못하도록 차단
  root.setAttribute('data-gramm', 'false');
  root.setAttribute('data-gramm_editor', 'false');
  root.setAttribute('data-enable-grammarly', 'false');
  root.spellcheck = false;
  renderPromptEditor(editorId);
  root.oninput = () => onPromptEditorInput(editorId);
  root.onkeydown = (e) => onPromptEditorKeyDown(e, editorId);
  root.onfocus = () => { activePromptEditorId = editorId; };
  root.onblur = () => setTimeout(() => {
    syncPromptEditorFromDom(editorId, { rerender: true });
    if (activePromptEditorId === editorId) activePromptEditorId = null;
    hidePromptMentionMenu();
  }, 150);
}

// ── Drag & drop ─────────────────────────────────────────────────

export function onPromptTokenDragStart(event) {
  const tokenEl = event.currentTarget;
  const rawRef = tokenEl?.dataset?.tokenRef || '';
  const tokenKey = tokenEl?.dataset?.tokenKey || rawRef;
  event.dataTransfer?.setData('text/plain', JSON.stringify({ rawRef, tokenKey }));
  tokenEl.classList.add('dragging');
}

export function onPromptTokenDragEnd(event) {
  event.currentTarget?.classList.remove('dragging');
}

export function onPromptEditorDragOver(event) {
  event.preventDefault();
}

export function onPromptEditorDrop(event) {
  event.preventDefault();
  const root = event.currentTarget;
  const editorId = root?.id;
  const state = getPromptEditorState(editorId);
  if (!state) return;
  let payload = null;
  try {
    payload = JSON.parse(event.dataTransfer?.getData('text/plain') || '{}');
  } catch {
    payload = null;
  }
  const rawRef = payload?.rawRef || '';
  const tokenKey = payload?.tokenKey || rawRef;
  if (!rawRef) return;
  const fromIdx = state.tokens.findIndex(t => t.type === 'token' && (t.stableKey === tokenKey || t.rawRef === rawRef));
  if (fromIdx < 0) return;
  const [moved] = state.tokens.splice(fromIdx, 1);
  const dropTokenEl = event.target instanceof HTMLElement ? event.target.closest('.prompt-token') : null;
  const dropTokenKey = dropTokenEl?.dataset?.tokenKey || '';
  const dropIdx = dropTokenKey
    ? state.tokens.findIndex(t => t.type === 'token' && (t.stableKey === dropTokenKey || t.rawRef === (dropTokenEl?.dataset?.tokenRef || '')))
    : -1;
  if (dropIdx >= 0) state.tokens.splice(dropIdx + 1, 0, moved);
  else state.tokens.push(moved);
  state.tokens = mergePromptTokens(state.tokens);
  state.rawPrompt = tokensToRawPrompt(state.tokens);
  renderPromptEditor(editorId, { preserveSelection: true });
}

// ── Critique HTML builders ──────────────────────────────────────

export function buildCritiqueHTML(attempt) {
  if (!attempt) return '';
  if (!attempt.critique_result) {
    if (attempt.critique_error) {
      return `<div class="unit-critique"><div style="font-size:11px;color:var(--warning);line-height:1.6">评审失败：${esc(attempt.critique_error)}</div></div>`;
    }
    if (attempt.output_path) {
      return `<div class="unit-critique"><div style="font-size:11px;color:var(--text-muted);line-height:1.6">该尝试有视频，但没有可展示的评审结果。</div></div>`;
    }
    return '';
  }
  const cr = attempt.critique_result, sc = cr.overall_score||0;
  const cls = sc>=7?'score-high':sc>=5?'score-mid':'score-low';
  const strengths = (cr.strengths||[]).slice(0,2).map(s => `<li>+ ${esc(s)}</li>`).join('');
  const issues = (cr.critical_issues||[]).slice(0,2).map(s => `<li>- ${esc(s)}</li>`).join('');
  return `<div class="unit-critique"><div class="critique-score"><div class="score-circle ${cls}">${sc}</div><div><div class="critique-rec ${cr.recommendation||''}">${cr.recommendation||''}</div><div style="font-size:11px;color:var(--text-muted)">Quality Score</div></div></div><ul class="critique-items">${strengths}${issues}</ul></div>`;
}

export function buildFullCritiqueHTML(attempt) {
  if (!attempt) return '';
  if (!attempt.critique_result) {
    if (attempt.critique_error) {
      return `<div class="modal-critique-block" style="max-height:200px;overflow-y:auto"><div style="font-size:12px;color:var(--warning);line-height:1.7">评审失败：${esc(attempt.critique_error)}</div></div>`;
    }
    if (attempt.output_path) {
      return `<div class="modal-critique-block" style="max-height:200px;overflow-y:auto"><div style="font-size:12px;color:var(--text-muted);line-height:1.7">该尝试有视频，但没有可展示的评审结果。</div></div>`;
    }
    return '';
  }
  const cr = attempt.critique_result, sc = cr.overall_score||0;
  const cls = sc>=7?'score-high':sc>=5?'score-mid':'score-low';
  const strengths = (cr.strengths||[]).map(s => `<li style="color:var(--success)">+ ${esc(s)}</li>`).join('');
  const issues = (cr.critical_issues||[]).map(s => `<li style="color:var(--error)">- ${esc(s)}</li>`).join('');
  const cols = (strengths || issues) ? `<div style="display:flex;gap:12px;margin-top:6px">
    ${strengths?`<div style="flex:1;min-width:0"><div class="modal-section-label">Strengths</div><ul class="critique-items">${strengths}</ul></div>`:''}
    ${issues?`<div style="flex:1;min-width:0"><div class="modal-section-label">Issues</div><ul class="critique-items">${issues}</ul></div>`:''}
  </div>` : '';
  return `<div class="modal-critique-block" style="max-height:200px;overflow-y:auto">
    <div class="critique-score"><div class="score-circle ${cls}">${sc}</div><div><div class="critique-rec ${cr.recommendation||''}">${cr.recommendation||''}</div><div style="font-size:10px;color:var(--text-muted)">Score</div></div></div>
    ${cols}
    ${cr.feedback?`<div class="modal-section-label" style="margin-top:8px">Feedback</div><div style="font-size:11px;color:var(--text-secondary);line-height:1.6">${esc(cr.feedback)}</div>`:''}
  </div>`;
}

// ── Prompt editor IDs & blocks ──────────────────────────────────

export function getPromptEditorIds(unitId, mode = 'modal') {
  const safeMode = mode === 'browse' ? 'browse' : 'modal';
  return {
    editorId: `prompt-editor-${safeMode}-${unitId}`,
    previewId: `prompt-preview-${safeMode}-${unitId}`,
  };
}

export function clearPromptEditorState(editorId) {
  if (activePromptEditorId === editorId) {
    hidePromptMentionMenu();
    activePromptEditorId = null;
  }
  delete promptEditorState[editorId];
}

export function getDisplayAttemptIndex(u, attempt) {
  const attemptId = Number(attempt?.attempt_id);
  if (!Number.isFinite(attemptId)) return -1;
  return getDisplayAttempts(u).findIndex(item => Number(item?.attempt_id) === attemptId);
}

export function buildReadonlyPromptBlock(u, viewingAttempt) {
  const attemptIdx = getDisplayAttemptIndex(u, viewingAttempt);
  const promptText = getAttemptPrompt(u, attemptIdx);
  const imageRefMap = getAttemptImageRefMap(u, attemptIdx);
  const imageRefAssets = getAttemptImageRefAssets(u, attemptIdx);
  return `
    <div class="unit-actions">
      <div class="prompt-readonly">${renderPromptWithRefs(promptText, imageRefMap, imageRefAssets)}</div>
    </div>
  `;
}

export function buildPromptEditorBlock(u, viewingAttempt, mode = 'modal') {
  if (!isEditableDraftAttempt(viewingAttempt)) {
    return buildReadonlyPromptBlock(u, viewingAttempt);
  }
  const { editorId } = getPromptEditorIds(u.unit_id, mode);
  return `
    <div class="unit-actions">
      <div
        id="${editorId}"
        class="prompt-editor"
        contenteditable="true"
        spellcheck="false"
        data-gramm="false"
        data-gramm_editor="false"
        data-enable-grammarly="false"
        data-mode="${mode}"
        data-unit-id="${u.unit_id}"
      ></div>
      <div class="unit-action-row">
        <button class="vj-btn secondary" onclick="window.saveUnitDraft('${u.unit_id}','${mode}')">保存草稿</button>
        <button class="vj-btn resume" onclick="window.startUnitRegenerate('${u.unit_id}','${mode}')">开始生成</button>
      </div>
      <div class="unit-action-hint">${t('unit.draft_mode_hint')}</div>
    </div>
  `;
}

export function getPromptAssetPickerCategory(type = 'prop') {
  return type === 'character' ? 'characters' : type === 'location' ? 'locations' : 'props';
}

export function getPromptAssetPickerEntityType(category = 'props') {
  return category === 'characters' ? 'character' : category === 'locations' ? 'location' : 'prop';
}

export function getCurrentPromptEditorIdForMode(unitId, mode = 'modal') {
  return getPromptEditorIds(unitId, mode).editorId;
}

export function insertAssetIntoPromptEditor(editorId, item, options = {}) {
  const state = getPromptEditorState(editorId);
  if (!state) return false;
  const root = document.getElementById(editorId);
  if (!root) return false;
  root.focus();
  if (!options.skipMentionInsertion) {
    insertPromptToken(editorId, item);
    return true;
  }
  const rawRef = ensurePromptAssetRef(state, item);
  if (!rawRef) return false;
  state.rawPrompt = tokensToRawPrompt(state.tokens);
  renderPromptEditor(editorId, { preserveSelection: true });
  return true;
}

export function buildPromptAssetControls(unitId, attempt, mode = 'modal') {
  const currentLang = getCurrentLang();
  const editable = isEditableDraftAttempt(attempt);
  const disabledAttr = editable ? '' : ' disabled';
  const titleAttr = editable ? '' : ` title="${esc(t('assets.only_draft_editable'))}"`;
  return `
    <div class="unit-media-actions">
      <button class="unit-media-action-btn primary" id="prompt-asset-trigger-${mode}-${unitId}" onclick="window.togglePromptAssetMenu(event, '${unitId}', '${mode}')"${disabledAttr}${titleAttr}>
        + ${t('assets.select_for_prompt')}
      </button>
      <div class="unit-media-action-hint">${editable ? (currentLang === 'zh' ? '上传到素材库后可立即插入当前 Prompt，也可以从素材库中选一张加入并通过 @ 引用。' : 'Upload to the library and insert it into the current prompt immediately, or choose an existing asset and reference it with @.') : t('assets.only_draft_editable')}</div>
    </div>
  `;
}

export function mountPromptEditorForUnit(u, attemptIdx, mode = 'modal') {
  if (!u) return;
  const { editorId, previewId } = getPromptEditorIds(u.unit_id, mode);
  const displayAttempts = getDisplayAttempts(u);
  const attempt = displayAttempts?.[attemptIdx];
  if (!isEditableDraftAttempt(attempt)) {
    clearPromptEditorState(editorId);
    return;
  }
  const promptText = getAttemptPrompt(u, attemptIdx);
  const imageRefAssets = normalizeImageRefAssets(
    getAttemptImageRefAssets(u, attemptIdx),
    getAttemptImageRefMap(u, attemptIdx),
  );
  initPromptEditor(
    editorId,
    promptText,
    imageRefAssets,
    t('unit.edit_prompt_hint'),
    previewId,
  );
}

// ── switchAttemptVideo (DOM-heavy, uses window.xxx for cross-module) ──

export function switchAttemptVideo(uid, idx) {
  const info = unitDataMap[uid];
  if (!info) return;
  const videoSrc = info.attemptVideos[idx];
  const attempts = info.displayAttempts || getDisplayAttempts(info.unit);
  const attempt = attempts[idx];

  info.bestIdx = idx;

  // Swap video in card
  const wrap = document.getElementById(uid + '-video');
  if (wrap) {
    if (videoSrc) {
      wrap.innerHTML = `<video src="${videoSrc}" preload="metadata" muted></video><div class="play-overlay"><div class="play-btn"><svg width="20" height="20" viewBox="0 0 24 24" fill="white"><polygon points="5,3 19,12 5,21"/></svg></div></div>`;
      wrap.onclick = () => window.playVideo(videoSrc);
    } else {
      const placeholderMessage = buildAttemptPlaceholderMessage(info.unit, attempt, info.status);
      wrap.innerHTML = `<div class="unit-video-placeholder"><svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="opacity:0.3"><rect x="2" y="2" width="20" height="20" rx="2.18"/><polygon points="10,8 16,12 10,16"/></svg><div class="unit-placeholder-pending">${placeholderMessage}</div></div>`;
      wrap.onclick = null;
    }
  }

  // Update card viewing dots
  const card = document.getElementById(uid + '-card');
  if (card) {
    card.querySelectorAll('.attempt-dot').forEach(d => d.classList.remove('viewing'));
    card.querySelector(`.attempt-dot[data-idx="${idx}"]`)?.classList.add('viewing');
  }

  // Update card critique
  const critiqueEl = document.getElementById(uid + '-critique');
  if (critiqueEl) critiqueEl.innerHTML = buildCritiqueHTML(attempt);

  // Update card prompt
  const promptEl = document.getElementById(uid + '-prompt');
  if (promptEl) promptEl.innerHTML = renderPromptWithRefs(
    getAttemptPrompt(info.unit, idx),
    getAttemptImageRefMap(info.unit, idx),
    getAttemptImageRefAssets(info.unit, idx),
  );

  // If modal is open for this unit, update it too
  if (window.currentModalUid === uid) window.updateModalAttempt(uid, idx);
}
