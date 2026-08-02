/**
 * features/editor/editor-controller.js
 *
 * Facade for storyboard/screenplay editor functionality.
 * Re-exports from editor.js during migration.
 */

export {
  showEditorSceneDetail,
  renderEditorSceneModal,
  saveEditorScene,
} from '../../editor.js';

import { registerActions } from '../../router.js';
import * as store from '../../store.js';

registerActions('editor', {
  // New actions go here. Example:
  // 'save-scene': (data) => { ... },
  // 'delete-scene': (data) => { ... },
});
