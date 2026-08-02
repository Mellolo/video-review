/**
 * prompt-editor.js — Re-export layer for backward compatibility.
 *
 * All prompt editor logic now lives in unit-helpers.js (single source of truth).
 * This file re-exports everything so that existing imports from './prompt-editor.js'
 * continue to work without changes, while ensuring there is only ONE
 * promptEditorState Map in the entire application.
 *
 * Previously this file contained a full duplicate of the prompt editor
 * implementation with its own private promptEditorState, causing state
 * divergence bugs (e.g. edits not persisting when different modules
 * read/write different Maps).
 */

export {
  // Attempt prompt / ref helpers
  getAttemptStoredPrompt,
  getAttemptRefSource,
  getAttemptBaseImageRefAssets,
  getAttemptMaxAttempts,
  getAttemptBaseImageRefMap,
  getStoryboardScenesForUnit,
  findPromptAssetCandidateByLabelAndType,
  dedupePromptCandidates,
  buildPromptCandidatesFromRefs,
  buildPromptCandidatesForUnit,
  buildPromptImageRefMap,
  autoEmbedPromptReferences,

  // Attempt prompt state accessors
  getAttemptPromptState,
  getAttemptPrompt,
  getAttemptImageRefAssets,
  getAttemptImageRefMap,

  // Asset name / image helpers
  normalizePromptAssetName,
  resolvePromptAssetImageSrc,
  findPromptAssetCandidateByLabel,
  normalizeImageRefAssets,
  findPromptAssetEntry,

  // Editor ID / state management
  getPromptEditorIds,
  clearPromptEditorState,
  getDisplayAttemptIndex,

  // Build blocks
  buildReadonlyPromptBlock,
  buildPromptEditorBlock,
  getPromptAssetPickerCategory,
  getPromptAssetPickerEntityType,
  getCurrentPromptEditorIdForMode,
  insertAssetIntoPromptEditor,
  buildPromptAssetControls,
  mountPromptEditorForUnit,

  // Asset data loading
  ensureAssetDataLoaded,
  buildPromptAssetCandidates,

  // Tokenization
  tokenizePrompt,
  tokensToRawPrompt,
  renderPromptToken,
  renderPromptWithRefs,
  normalizeEntityTypeLabel,
  getNextPromptAssetRef,
  ensurePromptAssetRef,
  buildPromptMentionCandidates,

  // Mention menu
  ensurePromptMentionMenu,
  hidePromptMentionMenu,
  getPromptEditorState,
  buildPromptEditorState,
  mergePromptTokens,
  getPromptTokenVisibleText,
  buildPromptTokenFromAsset,
  buildEditorTextNodesFromRaw,
  buildPromptEditorTokensFromDom,
  getPromptMentionQuery,
  getPromptEditorMentionState,
  replacePromptMentionWithToken,
  getPromptEditorSelectionOffset,
  setPromptEditorSelectionOffset,
  renderPromptEditor,
  serializePromptEditor,
  serializePromptEditorAssets,
  serializePromptEditorForSubmit,
  serializePromptEditorStateToSubmit,
  updatePromptPreview,
  syncPromptEditorFromDom,
  insertPromptToken,
  openPromptMentionMenuFromSelection,
  renderPromptMentionMenu,
  pickPromptMentionFromMenu,
  handlePromptMentionMenuPick,
  openPromptMentionMenu,
  onPromptEditorInput,
  onPromptEditorKeyDown,
  initPromptEditor,

  // Drag and drop
  onPromptTokenDragStart,
  onPromptTokenDragEnd,
  onPromptEditorDragOver,
  onPromptEditorDrop,
} from './unit-helpers.js';
