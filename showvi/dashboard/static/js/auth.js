/**
 * auth.js — single-user mode (no login/credits/payment)
 */

import { showToast } from './utils.js';
import { t } from './i18n.js';
import { currentUser, setCurrentUser } from './state.js';

// ── Helpers ─────────────────────────────────────────────────────

export function getUserInitial(user) {
  const u = user ?? currentUser;
  const source = u?.username || u?.role || 'U';
  return String(source).trim().charAt(0).toUpperCase() || 'U';
}

// ── User UI ─────────────────────────────────────────────────────

export function updateUserUi() {
  const navAvatar = document.getElementById('nav-user-avatar');
  const labelEl = document.getElementById('nav-user-label');
  const navCreditsBadge = document.getElementById('nav-credits-badge');
  if (navAvatar) navAvatar.textContent = getUserInitial();
  if (labelEl) labelEl.textContent = currentUser?.username || 'admin';
  if (navCreditsBadge) navCreditsBadge.classList.add('hidden');

  const avatarEls = [document.getElementById('user-menu-avatar')];
  avatarEls.forEach(el => { if (el) el.textContent = getUserInitial(); });
  const nameEl = document.getElementById('user-menu-name');
  if (nameEl) nameEl.textContent = currentUser?.username || 'admin';
  const nameMetaEl = document.getElementById('user-menu-name-meta');
  if (nameMetaEl) nameMetaEl.textContent = currentUser?.username || 'admin';
  const roleEl = document.getElementById('user-menu-role');
  if (roleEl) roleEl.textContent = t('auth.admin');
  const roleMetaEl = document.getElementById('user-menu-role-meta');
  if (roleMetaEl) roleMetaEl.textContent = t('auth.admin');
  const sessionUserEl = document.getElementById('user-session-owner');
  if (sessionUserEl) sessionUserEl.textContent = currentUser?.username || 'admin';
  const adminPanel = document.getElementById('admin-panel');
  if (adminPanel) adminPanel.classList.remove('hidden');
}

// ── Authentication ──────────────────────────────────────────────

export async function ensureAuthenticated() {
  const user = { id: 1, username: 'admin', role: 'admin' };
  setCurrentUser(user);
  updateUserUi();
  return user;
}

// ── User menu ───────────────────────────────────────────────────

export function handleUserEntryClick() {
  openUserMenu();
}

export function openUserMenu() {
  document.getElementById('user-menu-modal')?.classList.add('show');
  updateUserUi();
}

export function closeUserMenu() {
  document.getElementById('user-menu-modal')?.classList.remove('show');
}

export function setUserMenuStatus(message = '', type = '') {
  const el = document.getElementById('user-menu-status');
  if (!el) return;
  el.textContent = message;
  el.style.color = type === 'error' ? 'var(--error)' : type === 'success' ? 'var(--success)' : 'var(--text-muted)';
}

// ── Tutorial modal ──────────────────────────────────────────────

export function openTutorialModal() {
  document.getElementById('tutorial-modal')?.classList.add('show');
}

export function closeTutorialModal() {
  document.getElementById('tutorial-modal')?.classList.remove('show');
}

// ── Stubs for removed features (prevent HTML onclick errors) ────

export function openAuthModal() {}
export function closeAuthModal() {}
export function logout() { showToast('单用户模式，无需登出', 'info'); }
export function openCreditRulesModal() {}
export function closeCreditRulesModal() {}
export function openCreditsPurchase() {}
export function closeCreditsPurchase() {}
export function loadUserCredits() {}
export function redeemCode() {}
export function toggleCreditsHistory() {}
export function loadCreditsHistory() {}
export function loadMoreCreditsHistory() {}
export function renderCreditsHistory() {}
export function generateRedeemCodes() {}
export function loadRedeemCodes() {}
export function copyRedeemCode() {}
export function loadAdminCreditsOverview() {}
export function loadAdminUsers() {}
export function renderAdminUsers() {}
export function createAdminUser() {}
export function disableAdminUser() {}
export function enableAdminUser() {}
export function resetAdminUserPassword() {}
export function sendRegistrationOTP() {}
export function finishRegistration() {}
export function openRegisterModal() {}
export function closeRegisterModal() {}
export function openLoginModal() {}
export function submitInlineLogin() {}
export function selectCreditPackage() {}
export function submitCreditsPurchase() {}
export function confirmPaymentDone() {}
