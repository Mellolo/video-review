/**
 * features/admin/admin-controller.js
 *
 * Facade for admin functionality (usage, accounts).
 * Re-exports from admin-usage.js and admin-accounts.js during migration.
 */

export * from '../../admin-usage.js';
export * from '../../admin-accounts.js';

import { registerActions } from '../../router.js';

registerActions('admin', {
  // New actions go here.
});
