/**
 * features/monitor/monitor-controller.js
 *
 * Facade for the monitor view (run progress, unit status).
 * Re-exports from monitor.js during migration.
 */

export {
  renderMonitor,
  renderVideoJobsPanel,
} from '../../monitor.js';

import { registerActions } from '../../router.js';
import * as store from '../../store.js';

registerActions('monitor', {
  // New actions go here. Example:
  // 'select-unit': (data) => { ... },
  // 'toggle-jobs-panel': (data) => { ... },
});
