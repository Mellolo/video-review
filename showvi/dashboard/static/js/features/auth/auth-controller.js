/**
 * features/auth/auth-controller.js
 *
 * Facade for authentication and user management.
 * Re-exports from auth.js during migration.
 */

export {
  logout,
  updateUserUi,
  loadUserCredits,
} from '../../auth.js';

import { registerActions } from '../../router.js';

registerActions('auth', {
  // New actions go here.
});
