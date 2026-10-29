// Shared front-end utilities, loaded on every page via base.html.
//
// These used to be copy-pasted independently into dashboard.html, index.html,
// profile.html and training_calendar.html (with small, accidental drift
// between the copies). Living here once means every page that extends
// base.html gets the same, verified behavior.

// ---- Toast notifications ----
// Expects the shared #toast/#toast-msg markup rendered by base.html and the
// .toast CSS in style.css.
function showToast(msg, type = 'success') {
    const toast = document.getElementById('toast');
    const msgEl = document.getElementById('toast-msg');
    if (!toast || !msgEl) return;
    toast.className = `toast show ${type}`;
    msgEl.textContent = msg;
    setTimeout(() => { toast.classList.remove('show'); }, 3000);
}

// ---- Empty / error state placeholder ----
// Renders a consistent "nothing here yet" illustration + message into any
// container. `ctaText`/`ctaHref` default to the Strava re-auth prompt since
// that's by far the most common reason a section has nothing to show, but
// either can be overridden per call site, or `ctaText` set to null/'' to
// omit the button entirely (e.g. a section with no sensible action).
function showEmptyState(containerId, { title, message, ctaText = 'Sync Strava Now', ctaHref = '/strava-auth' } = {}) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = `
        <div class="empty-state">
            <div class="empty-illustration">
                <svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <path d="M50 20C40 20 30 30 30 45C30 65 50 80 50 80C50 80 70 65 70 45C70 30 60 20 50 20Z" stroke="#3b82f6" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" opacity="0.5"/>
                    <path d="M45 40L55 50M55 40L45 50" stroke="#f43f5e" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
            </div>
            <h4>${title}</h4>
            <p>${message}</p>
            ${ctaText ? `<button onclick="window.location.href='${ctaHref}'">${ctaText}</button>` : ''}
        </div>
    `;
}

// ---- Escape untrusted text before interpolating it into an HTML string ----
// Strava activity/workout names, descriptions, etc. are free-text.
// `preserveNewlines` re-inserts <br> for the one call site (the training
// calendar's workout notes) that used to have its own copy of this function
// with that one behavioral difference baked in.
function escapeHtml(text, { preserveNewlines = false } = {}) {
    const div = document.createElement('div');
    div.textContent = text;
    return preserveNewlines ? div.innerHTML.replace(/\n/g, '<br>') : div.innerHTML;
}
