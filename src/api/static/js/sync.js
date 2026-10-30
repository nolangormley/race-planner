// The Strava sync button in the top bar and the floating progress panel.
// POST /api/sync-strava/{id} starts a background job; this polls GET on the
// same path until the job finishes, then tells the page with a
// 'strava:synced' event so it can reload its data.
import { config, fetchJson, qs, showToast, relativeTime } from './app.js';

const POLL_MS = 800;
const SYNC_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>';

let pollTimer = null;
let panel = null;
let hideTimer = null;

function url() {
  return `/api/sync-strava/${config.userId}`;
}

function setButtonState(running) {
  const button = qs('#sync-btn');
  if (!button) return;
  button.disabled = running;
  button.innerHTML = `<span class="${running ? 'spin' : ''}" style="display:inline-flex">${SYNC_ICON}</span>${running ? 'Syncing' : 'Sync Strava'}`;
}

function setStatusText(lastSyncedAt, job) {
  const status = qs('#sync-status');
  if (!status) return;
  if (job && job.status === 'running') status.textContent = '';
  else if (lastSyncedAt) status.textContent = `Synced ${relativeTime(lastSyncedAt)}`;
  else status.textContent = 'Never synced';
}

function ensurePanel() {
  if (panel) return panel;
  panel = document.createElement('div');
  panel.className = 'card sync-panel';
  panel.setAttribute('role', 'status');
  panel.setAttribute('aria-live', 'polite');
  panel.innerHTML = `
    <div class="sync-panel-head"><span class="label"></span><span class="pct"></span></div>
    <div class="progress"><span></span></div>
    <div class="sync-panel-detail"><span class="truncate detail"></span><span class="count"></span></div>
    <div class="sync-panel-actions hidden">
      <a class="btn btn-sm btn-strava hidden reconnect" href="/strava-auth">Reconnect Strava</a>
      <button class="btn btn-sm dismiss" type="button">Dismiss</button>
    </div>`;
  panel.querySelector('.dismiss').addEventListener('click', hidePanel);
  document.body.appendChild(panel);
  return panel;
}

function hidePanel() {
  clearTimeout(hideTimer);
  panel?.remove();
  panel = null;
}

function renderJob(job) {
  const node = ensurePanel();
  const bar = node.querySelector('.progress');
  node.querySelector('.label').textContent = job.label;
  node.querySelector('.pct').textContent = job.status === 'error' ? '' : `${job.percent}%`;
  bar.querySelector('span').style.width = `${job.percent}%`;
  bar.classList.toggle('indeterminate', job.status === 'running' && !job.total && job.phase !== 'listing');
  bar.classList.toggle('is-done', job.status === 'done');
  bar.classList.toggle('is-error', job.status === 'error');

  const detail = node.querySelector('.detail');
  const count = node.querySelector('.count');
  if (job.status === 'error') {
    detail.textContent = job.error || 'Something went wrong.';
    count.textContent = '';
  } else if (job.status === 'done') {
    const n = job.new_activities ?? 0;
    if (job.kind === 'recompute') detail.textContent = 'Zones, training load and VO2 max recomputed for every activity.';
    else detail.textContent = n === 0 ? 'Everything was already up to date.' : `${n} new ${n === 1 ? 'activity' : 'activities'} added.`;
    count.textContent = '';
  } else {
    detail.textContent = job.detail || '';
    count.textContent = job.total ? `${job.done} of ${job.total}` : '';
  }
  node.querySelector('.sync-panel-actions').classList.toggle('hidden', job.status === 'running');
  node.querySelector('.reconnect').classList.toggle('hidden', job.error_kind !== 'auth');
}

async function poll() {
  let state;
  try {
    state = await fetchJson(url());
  } catch (error) {
    stopPolling();
    showToast(error.message, 'error');
    setButtonState(false);
    return;
  }
  apply(state);
}

function apply(state, started = false) {
  const job = state.job;
  setStatusText(state.last_synced_at, job);
  if (!job) {
    setButtonState(false);
    return;
  }
  if (job.status === 'running') {
    setButtonState(true);
    renderJob(job);
    if (!pollTimer) pollTimer = setInterval(poll, POLL_MS);
    return;
  }
  // Finished: show the result once, only if this page saw it running or
  // started it (a short recompute can finish before the first read).
  if (pollTimer || started) {
    stopPolling();
    renderJob(job);
    if (job.status === 'done') {
      if (job.kind === 'recompute') showToast('Metrics updated');
      else showToast(job.new_activities ? `Synced ${job.new_activities} new ${job.new_activities === 1 ? 'activity' : 'activities'}` : 'Strava is up to date');
      document.dispatchEvent(new CustomEvent('strava:synced', { detail: job }));
      hideTimer = setTimeout(hidePanel, 6000);
    } else {
      showToast(job.kind === 'recompute' ? 'Recomputing your metrics failed' : 'Strava sync failed', 'error');
    }
  }
  setButtonState(false);
}

function stopPolling() {
  clearInterval(pollTimer);
  pollTimer = null;
}

async function start() {
  setButtonState(true);
  try {
    const state = await fetchJson(url(), { method: 'POST' });
    hidePanel();
    apply(state);
  } catch (error) {
    setButtonState(false);
    showToast(error.message, 'error');
  }
}

// Re-reads the job state. A page that started a job another way (a profile
// change that triggers a recompute) passes started=true, so the panel shows
// the result even when the job is already done.
export function refreshSync(started = false) {
  if (!config.userId) return;
  hidePanel();
  fetchJson(url()).then((state) => apply(state, started)).catch(() => setStatusText(null, null));
}

export function initSync() {
  const button = qs('#sync-btn');
  if (!button || !config.userId) return;
  setButtonState(false);
  button.addEventListener('click', start);
  // Picks up a sync that is already running (page reload mid-sync) and the
  // last-synced time.
  refreshSync();
}
