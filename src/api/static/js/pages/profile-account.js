// The Delete account modal on the profile page: asks for the password, calls
// DELETE /api/account and sends the browser to the login page.
import { fetchJson, openModal, qs } from '../app.js';

export function initDeleteAccount() {
  const modal = qs('#delete-modal');
  const form = qs('#delete-form');
  const status = qs('.status', form);
  const button = qs('#delete-confirm');

  qs('#delete-account').addEventListener('click', () => {
    form.reset();
    status.textContent = '';
    openModal(modal);
    qs('#delete-password').focus();
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    button.disabled = true;
    status.textContent = 'Deleting…';
    try {
      await fetchJson('/api/account', { method: 'DELETE', body: { password: qs('#delete-password').value } });
      window.location.assign('/login?deleted=1');
    } catch (error) {
      status.textContent = error.message;
      button.disabled = false;
    }
  });
}
