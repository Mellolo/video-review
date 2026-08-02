/**
 * features/creation/creation-controller.js
 *
 * Facade for creation (storyboard generation) functionality.
 * Re-exports from create.js during migration; new features should be
 * added here directly.
 */

export {
  loadDemoGallery,
} from '../../create.js';

import { registerActions } from '../../router.js';
import * as store from '../../store.js';

registerActions('creation', {
  // New actions go here. Example:
  // 'start-generation': (data) => { ... },
});
