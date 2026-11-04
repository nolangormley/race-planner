// Dashboard, the recovery card below its energy block (energy.js): the
// HRV and resting heart rate tiles and chart from COROS, and today's call.
import { parseDate, renderEmpty, qs } from '../app.js';
import { panelChart } from '../charts/panels.js';
import { dateTicks, fmtDateTick } from '../charts/core.js';

const charts = {};

function hoursMinutes(minutes) {
  if (minutes == null) return '–';
  return `${Math.floor(minutes / 60)}h ${String(Math.round(minutes % 60)).padStart(2, '0')}m`;
}

function recoveryTile(label, value, unit, foot) {
  const node = document.createElement('div');
  node.className = 'recovery-tile';
  node.innerHTML = `<div class="stat-label">${label}</div><div class="stat-value">${value}${unit ? `<small>${unit}</small>` : ''}</div><div class="stat-foot">${foot}</div>`;
  return node;
}

export function renderRecovery(data) {
  const container = qs('#recovery-chart');
  const tiles = qs('#recovery-tiles');
  charts.recovery?.destroy();
  container.replaceChildren();
  tiles.replaceChildren();
  if (!data || !data.days.length) {
    renderEmpty(container, 'No COROS data yet', 'Connect COROS on your profile to see your energy, overnight HRV and resting heart rate here.');
    return;
  }
  const latest = data.latest;
  tiles.append(
    recoveryTile('Overnight HRV', latest.hrv ?? '–', 'ms', latest.hrv_baseline ? `baseline ${latest.hrv_baseline} ms` : ''),
    recoveryTile('Resting heart rate', latest.rhr ?? '–', 'bpm', data.resting_hr_7d ? `7-night average ${data.resting_hr_7d} bpm` : ''),
  );
  if (latest.time_in_bed_min || data.sleep_7d_min) {
    tiles.append(recoveryTile('Time in bed', hoursMinutes(latest.time_in_bed_min), '', data.sleep_7d_min ? `7-night average ${hoursMinutes(data.sleep_7d_min)}` : ''));
  }
  qs('#recovery-sub').textContent = `Energy over the last week, then ${data.days.length} nights of HRV and resting heart rate from COROS. HRV at or above its baseline with a steady resting heart rate means you are absorbing the training.`;
  charts.recovery = panelChart(container, {
    x: data.days.map((night) => parseDate(night.day).getTime()),
    xTicks: (min, max) => dateTicks(min, max, 6),
    xFormat: fmtDateTick,
    xTitle: (ts) => new Date(ts).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }),
    tableXLabel: 'Night',
    ariaLabel: 'Overnight HRV and resting heart rate by night',
    legend: true,
    table: true,
    panels: [
      {
        kind: 'line', height: 140, yTicks: 3, yFormat: (value) => `${Math.round(value)} ms`,
        series: [
          { key: 'hrv', label: 'HRV', color: 'series-1', values: data.days.map((night) => night.hrv), points: true },
          { key: 'baseline', label: 'Baseline', color: 'series-muted', values: data.days.map((night) => night.hrv_baseline), dash: true },
        ],
      },
      {
        kind: 'line', height: 110, yTicks: 3, yFormat: (value) => `${Math.round(value)} bpm`,
        series: [{ key: 'rhr', label: 'Resting heart rate', color: 'series-2', values: data.days.map((night) => night.rhr), points: true }],
      },
    ],
  });
}

export function renderReadiness(call) {
  const container = qs('#readiness');
  container.replaceChildren();
  if (!call?.available) return;
  const badgeNode = document.createElement('span');
  badgeNode.className = `badge badge-${call.badge}`;
  badgeNode.textContent = `Today: ${call.label}`;
  const advice = document.createElement('div');
  advice.className = 'small faint';
  advice.textContent = call.advice;
  container.append(badgeNode, advice);
}
