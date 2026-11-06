// The admin page: the sign-up cohorts by week with what each did next, and
// the latest feedback notes, from /api/admin/stats.
import { fetchJson, qs, escapeHtml, fmtDate, renderEmpty } from '../app.js';

const COUNTS = ['signed_up', 'connected', 'added_race', 'course_file', 'returned'];
const NOTE_DATE = { year: 'numeric', month: 'short', day: 'numeric' };

function countCells(row) {
  return COUNTS.map((key) => `<td class="num">${row[key]}</td>`).join('');
}

function renderCohorts(report) {
  const rows = report.weeks.map((week) => `<tr><td class="primary nowrap">${fmtDate(week.start)}</td>${countCells(week)}</tr>`);
  rows.push(`<tr class="total"><td class="primary">All time</td>${countCells(report.totals)}</tr>`);
  qs('#cohorts tbody').innerHTML = rows.join('');
  const before = report.totals.before_tracking;
  qs('#cohorts-foot').textContent = before
    ? `${before} ${before === 1 ? 'account is' : 'accounts are'} from before sign-up dates were kept: counted in All time, in no week.`
    : '';
}

function renderFeedback(notes) {
  const body = qs('#feedback-table tbody');
  if (!notes.length) {
    body.innerHTML = '<tr><td colspan="5" class="muted">No notes yet.</td></tr>';
    return;
  }
  body.innerHTML = notes.map((note) => `<tr>
    <td class="nowrap">${fmtDate(note.created_at, NOTE_DATE)}</td>
    <td class="primary">${escapeHtml(note.user_name)}<span class="cell-sub">${escapeHtml(note.user_email)}</span></td>
    <td>${escapeHtml(note.page || '–')}</td>
    <td class="note">${escapeHtml(note.message)}</td>
    <td>${note.contact_ok ? '<span class="badge badge-good">Yes</span>' : '<span class="faint">No</span>'}</td>
  </tr>`).join('');
}

async function load() {
  try {
    const report = await fetchJson('/api/admin/stats');
    renderCohorts(report);
    renderFeedback(report.feedback);
  } catch (error) {
    renderEmpty(qs('#cohorts').closest('.table-wrap'), 'Could not load the numbers', error.message);
  }
}

load();
