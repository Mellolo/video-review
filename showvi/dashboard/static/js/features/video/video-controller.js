/**
 * features/video/video-controller.js
 *
 * Facade for video generation job management.
 * Re-exports from video-jobs.js during migration.
 */

export {
  loadVideoJobs,
  renderVideoJobsPanel,
} from '../../video-jobs.js';

import { registerActions } from '../../router.js';
import * as store from '../../store.js';

registerActions('video', {
  // New actions go here. Example:
  // 'start-gen': (data) => { ... },
  // 'stop-gen': (data) => { ... },
});
