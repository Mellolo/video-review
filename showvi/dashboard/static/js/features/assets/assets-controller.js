/**
 * features/assets/assets-controller.js
 *
 * Facade for asset management (characters, locations, props).
 * Re-exports from assets.js during migration.
 */

export {
  hidePromptAssetMenu,
  getAssetTabLabel,
} from '../../assets.js';

import { registerActions } from '../../router.js';

registerActions('assets', {
  // New actions go here.
});
