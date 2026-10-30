// Shared helpers for every page: fetch, formatting, units, theme, toast.
// Pages import what they need; base.html loads this once for the top bar.

export const config = { ...document.body.dataset };

export function qs(selector, root = document) {
  return root.querySelector(selector);
}

export function qsa(selector, root = document) {
  return Array.from(root.querySelectorAll(selector));
}

export async function fetchJson(url, options = {}) {
  const init = { ...options };
  if (init.body !== undefined && typeof init.body !== 'string' && !(init.body instanceof FormData)) {
    init.body = JSON.stringify(init.body);
    init.headers = { 'Content-Type': 'application/json', ...(init.headers || {}) };
  }
  const response = await fetch(url, init);
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const detail = payload && (payload.detail || payload.error);
    const error = new Error(typeof detail === 'string' ? detail : `Request failed (${response.status})`);
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

export function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text == null ? '' : String(text);
  return div.innerHTML;
}

// ---- Toast ----
let toastTimer = null;
export function showToast(message, kind = 'success') {
  const toast = qs('#toast');
  if (!toast) return;
  toast.textContent = message;
  toast.className = `toast show ${kind === 'error' ? 'error' : ''}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('show'), 3200);
}

// ---- Units ----
const METERS_PER_MILE = 1609.344;

export const units = {
  isMetric() {
    return localStorage.getItem('units') === 'metric';
  },
  set(value) {
    localStorage.setItem('units', value);
  },
  distanceLabel() {
    return this.isMetric() ? 'km' : 'mi';
  },
  paceLabel() {
    return this.isMetric() ? '/km' : '/mi';
  },
  elevationLabel() {
    return this.isMetric() ? 'm' : 'ft';
  },
  // Meters to the preferred distance unit, as a number.
  distanceValue(meters) {
    return this.isMetric() ? meters / 1000 : meters / METERS_PER_MILE;
  },
  distance(meters, digits = 1) {
    if (meters == null) return '–';
    return `${this.distanceValue(meters).toFixed(digits)} ${this.distanceLabel()}`;
  },
  // Meters per second to minutes per unit, as a number.
  paceMinutes(metersPerSecond) {
    if (!metersPerSecond || metersPerSecond <= 0) return null;
    const metersPerUnit = this.isMetric() ? 1000 : METERS_PER_MILE;
    return metersPerUnit / metersPerSecond / 60;
  },
  pace(metersPerSecond, withUnit = true) {
    const minutes = this.paceMinutes(metersPerSecond);
    if (minutes == null) return '–';
    return fmtMinutes(minutes) + (withUnit ? this.paceLabel() : '');
  },
  elevation(meters) {
    if (meters == null) return '–';
    const value = this.isMetric() ? meters : meters * 3.28084;
    return `${Math.round(value)} ${this.elevationLabel()}`;
  },
};

// ---- Formatting ----
export function fmtMinutes(minutes) {
  const whole = Math.floor(minutes);
  const seconds = Math.round((minutes - whole) * 60);
  if (seconds === 60) return `${whole + 1}:00`;
  return `${whole}:${String(seconds).padStart(2, '0')}`;
}

export function fmtDuration(seconds) {
  if (seconds == null) return '–';
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (hours > 0) return `${hours}h ${String(minutes).padStart(2, '0')}m`;
  return `${minutes}m ${String(total % 60).padStart(2, '0')}s`;
}

export function fmtClock(seconds) {
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  return `${minutes}:${String(secs).padStart(2, '0')}`;
}

export function fmtNumber(value, digits = 0) {
  if (value == null || Number.isNaN(value)) return '–';
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

// Dates arrive as 'YYYY-MM-DD' or ISO timestamps. A bare date is parsed as
// local midnight so it never shifts a day across time zones.
export function parseDate(value) {
  if (value instanceof Date) return value;
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return new Date(`${value}T00:00:00`);
  return new Date(value);
}

export function fmtDate(value, options = { month: 'short', day: 'numeric' }) {
  if (!value) return '–';
  return parseDate(value).toLocaleDateString(undefined, options);
}

export function isoDate(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

export function relativeTime(value) {
  if (!value) return null;
  const diffMs = Date.now() - parseDate(value).getTime();
  const minutes = Math.round(diffMs / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days} d ago`;
  return fmtDate(value);
}

export function daysUntil(dateString) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((parseDate(dateString) - today) / 86400000);
}

// ---- Theme ----
export function applyTheme(theme) {
  if (theme === 'light') document.documentElement.dataset.theme = 'light';
  else delete document.documentElement.dataset.theme;
  localStorage.setItem('theme', theme);
  qsa('[data-theme-toggle]').forEach((button) => {
    button.setAttribute('aria-label', theme === 'light' ? 'Switch to dark theme' : 'Switch to light theme');
  });
}

export function currentTheme() {
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
}

function initThemeToggle() {
  qsa('[data-theme-toggle]').forEach((button) => {
    button.addEventListener('click', () => applyTheme(currentTheme() === 'light' ? 'dark' : 'light'));
  });
  applyTheme(currentTheme());
}

// ---- Modals ----
// Markup: <div class="modal"><div class="modal-dialog" role="dialog" tabindex="-1">…</div></div>
// Elements with [data-close] inside, a click on the backdrop, and Escape all close it.
export function openModal(modal) {
  modal.returnFocus = document.activeElement;
  modal.classList.add('open');
  modal.setAttribute('aria-hidden', 'false');
  qs('.modal-dialog', modal)?.focus();
}

export function closeModal(modal) {
  modal.classList.remove('open');
  modal.setAttribute('aria-hidden', 'true');
  modal.returnFocus?.focus?.();
  modal.returnFocus = null;
}

function initModals() {
  qsa('.modal').forEach((modal) => {
    modal.addEventListener('click', (event) => {
      if (event.target === modal) closeModal(modal);
    });
    qsa('[data-close]', modal).forEach((button) => button.addEventListener('click', () => closeModal(modal)));
  });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    const open = qs('.modal.open');
    if (open) closeModal(open);
  });
}

// ---- Small DOM helpers ----
export function setLoading(element, loading) {
  element?.classList.toggle('is-loading', loading);
}

export function renderEmpty(element, title, message, action) {
  element.innerHTML = `<div class="empty"><strong>${escapeHtml(title)}</strong>${escapeHtml(message)}${
    action ? `<div><a class="btn btn-sm" href="${action.href}">${escapeHtml(action.label)}</a></div>` : ''
  }</div>`;
}

initThemeToggle();
initModals();
