// The Feedback button in the top bar (and its twin on the profile page's
// Settings card, for phones): a short note to whoever runs the app, sent
// with the page it was written on.
import { fetchJson, qs, qsa, showToast, openModal, closeModal } from './app.js';

const modal = qs('#feedback-modal');
const form = qs('#feedback-form');

function open() {
  openModal(modal);
  qs('#feedback-message').focus();
}

async function send(event) {
  event.preventDefault();
  const message = qs('#feedback-message').value.trim();
  if (!message) {
    qs('#feedback-message').focus();
    return;
  }
  const button = qs('button[type=submit]', form);
  button.disabled = true;
  try {
    await fetchJson('/api/feedback', {
      method: 'POST',
      body: { message, page: location.pathname.slice(0, 200), contact_ok: qs('#feedback-contact').checked },
    });
    form.reset();
    closeModal(modal);
    showToast('Thanks for the note');
  } catch (error) {
    showToast(error.message, 'error');
  } finally {
    button.disabled = false;
  }
}

qsa('[data-feedback]').forEach((button) => button.addEventListener('click', open));
form.addEventListener('submit', send);
