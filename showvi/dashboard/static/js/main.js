// main.js — Entry point: imports all modules, registers on window for HTML onclick handlers
import * as state from './state.js';
import * as store from './store.js';
import * as api from './api.js';
import { initRouter, registerActions, dispatch } from './router.js';
import * as utils from './utils.js';
import * as i18n from './i18n.js';
import * as auth from './auth.js';
import * as ws from './ws.js';
import * as nav from './nav.js';
import * as unitHelpers from './unit-helpers.js';
import * as promptEditor from './prompt-editor.js';
import * as monitor from './monitor.js';
import * as repository from './repository.js';
import * as assets from './assets.js';
import * as unitModal from './unit-modal.js';
import * as editor from './editor.js';
import * as videoJobs from './video-jobs.js';
import * as create from './create.js';
import * as settingsModal from './settings-modal.js';

/* ── 0. Initialize new infrastructure ── */
store.init({
  promptEditors: {},
});
initRouter();

// Expose store and router on window for debugging and gradual migration
window.__store = store;
window.__dispatch = dispatch;
window.__registerActions = registerActions;

/* ── 1. Register all exported functions on window ── */
const modules = [
  state, utils, i18n, auth, ws, nav, unitHelpers, promptEditor,
  monitor, repository, assets, unitModal, editor, videoJobs, create, settingsModal,
];
for (const mod of modules) {
  for (const [key, value] of Object.entries(mod)) {
    if (typeof value === 'function') {
      window[key] = value;
    }
  }
}

/* ── 2. Live-binding getters/setters for state.js variables ── */
// ⚠️ 开发注意：所有 state.js / create.js 的 export let 变量都通过
// defineProperty 活绑定到 window。新增状态变量时必须在此处添加对应绑定，
// 否则其他模块通过 window.xxx 读取会拿到 stale 值。
//
// state.js variables — live getter/setter so window.xxx always reflects module state
const stateBindings = [
  ['currentData',              state, 'setCurrentData'],
  ['ws',                       state, 'setWs'],
  ['reconnectTimer',           state, 'setReconnectTimer'],
  ['repoData',                 state, 'setRepoData'],
  ['projectList',              state, 'setProjectList'],
  ['activeProject',            state, 'setActiveProject'],
  ['videoJobsData',            state, 'setVideoJobsData'],
  ['selectedVideoJobId',       state, 'setSelectedVideoJobId'],
  ['currentTab',               state, 'setCurrentTab'],
  ['monitorBrowseMode',        state, 'setMonitorBrowseMode'],
  ['currentAssetTab',          state, 'setCurrentAssetTab'],
  ['assetData',                state, 'setAssetData'],
  ['assetDataLoadingPromise',  state, 'setAssetDataLoadingPromise'],
  ['voiceData',                state, 'setVoiceData'],
  ['currentAudioEl',           state, 'setCurrentAudioEl'],
  ['assetUploadFile',          state, 'setAssetUploadFile'],
  ['currentUser',              state, 'setCurrentUser'],
  ['assetUploadContext',       state, 'setAssetUploadContext'],
  ['promptAssetPickerContext', state, 'setPromptAssetPickerContext'],
  ['monitorActionState',       state, 'setMonitorActionState'],
  ['concatMode',               state, 'setConcatMode'],
  ['concatFadeSeconds',        state, 'setConcatFadeSeconds'],
  ['monitorJobsPanelCollapsed', state, 'setMonitorJobsPanelCollapsed'],
];
for (const [name, mod, setter] of stateBindings) {
  Object.defineProperty(window, name, {
    get()  { return mod[name]; },
    set(v) { mod[setter](v); },
    configurable: true,
  });
}
// nativeFetch — read-only (it's a const)
Object.defineProperty(window, 'nativeFetch', {
  get() { return utils.nativeFetch; },
  configurable: true,
});

// create.js state variables
const createBindings = [
  ['createMode',                    create, 'setCreateMode'],
  ['createJobId',                   create, 'setCreateJobId'],
  ['createPhase',                   create, 'setCreatePhase'],
  ['createdStoryboard',             create, 'setCreatedStoryboard'],
  ['createdStoryboardPath',         create, 'setCreatedStoryboardPath'],
  ['uploadedNovelPath',             create, 'setUploadedNovelPath'],
  ['uploadedVideoPath',             create, 'setUploadedVideoPath'],
  ['uploadedVideoDurationSeconds',  create, 'setUploadedVideoDurationSeconds'],
  ['activeCreateJobMeta',           create, 'setActiveCreateJobMeta'],
];
for (const [name, mod, setter] of createBindings) {
  Object.defineProperty(window, name, {
    get()  { return mod[name]; },
    set(v) { mod[setter](v); },
    configurable: true,
  });
}

// monitor.js — browseSelectedUnit
Object.defineProperty(window, 'browseSelectedUnit', {
  get() { return monitor.browseSelectedUnit; },
  set(v) { monitor.setBrowseSelectedUnit(v); },
  configurable: true,
});

// unit-modal.js state variables
const unitModalBindings = [
  ['currentModalUid',      unitModal, 'setCurrentModalUid'],
  ['currentModalAttemptIdx', unitModal, 'setCurrentModalAttemptIdx'],
  ['unitUidList',           unitModal, 'setUnitUidList'],
];
for (const [name, mod, setter] of unitModalBindings) {
  Object.defineProperty(window, name, {
    get()  { return mod[name]; },
    set(v) { mod[setter](v); },
    configurable: true,
  });
}

// unit-helpers.js — unitDataMap
Object.defineProperty(window, 'unitDataMap', {
  get()  { return unitHelpers.unitDataMap; },
  set(v) { unitHelpers.setUnitDataMap(v); },
  configurable: true,
});

// nav.js — drawerOpen
Object.defineProperty(window, 'drawerOpen', {
  get() { return nav.drawerOpen; },
  set(v) { nav.setDrawerOpen(v); },
  configurable: true,
});

// video-jobs.js — no state variables need window binding (all internal)

/* ── 3. Bootstrap ── */
utils.installFetchMonkeyPatch();
i18n.applyI18n();

/* ── 4. initializeDashboard — called once on page load ── */
async function initializeDashboard() {
  await auth.ensureAuthenticated();
  utils.setupCreateFormValidation();
  ws.connectWS();
  videoJobs.loadBackendSetting();
  create.loadStoryboardList();
  create.initVideoUploadDrop();
  create.initNovelUploadDrop();
  create.initSplitButton();
  editor.initEntityImageUpload();
  create.switchMode('quickchat');
  utils.apiFetch('/api/repository').then(({ response, data }) => {
    if (response.ok) {
      state.setRepoData(data);
      nav.loadProjectList();
    } else {
      nav.loadProjectList();
    }
  }).catch(() => nav.loadProjectList());
  videoJobs.loadVideoJobs();
  create.loadDemoGallery();
}
window.initializeDashboard = initializeDashboard;
initializeDashboard();
