// The What to work on card: the rule-based notes from /api/insights (src/insights.py), serious first.
import { escapeHtml, qs } from '../app.js';

export function renderInsights(data) {
  const card = qs('#insights-card');
  // A failed request hides the card rather than saying there is nothing to flag.
  card.classList.toggle('hidden', !data);
  if (!data) return;
  const list = qs('#insights');
  if (!data.insights.length) {
    list.innerHTML = '<li class="empty"><strong>Nothing to flag</strong>Your last weeks of training show nothing to change. The checks look again after every sync.</li>';
    return;
  }
  list.innerHTML = data.insights.map((item) => `
    <li class="insight">
      <div class="insight-head"><span class="badge badge-${item.tone}">${escapeHtml(item.label)}</span><span class="insight-title">${escapeHtml(item.title)}</span></div>
      <p class="insight-text">${escapeHtml(item.text)}</p>
      <p class="insight-evidence">${escapeHtml(item.evidence)}</p>
    </li>`).join('');
}
