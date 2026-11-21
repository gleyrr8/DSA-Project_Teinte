const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
let publishedShades = [];

async function call(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }
  });
  let data = {};
  try { data = await response.json(); } catch {}
  if (!response.ok) throw Error(data.error || 'Request failed. Please try again.');
  return data;
}

function renderPublishedShades() {
  const query = $('#shade-filter').value.trim().toLocaleLowerCase();
  const matches = publishedShades.filter(shade =>
    [shade.brand_name, shade.product_name, shade.name, shade.code]
      .some(value => String(value || '').toLocaleLowerCase().includes(query))
  );
  $('#shade-count').textContent = query
    ? `Showing ${matches.length} of ${publishedShades.length} published shades.`
    : `${publishedShades.length} published shades.`;
  if (!matches.length) {
    $('#published-list').innerHTML = '<p class="admin-muted" style="padding:14px">No published shades match that search.</p>';
    return;
  }
  $('#published-list').innerHTML = `<table class="admin-table"><thead><tr><th>Brand</th><th>Product</th><th>Shade</th><th>Depth</th><th>Undertone</th></tr></thead><tbody>${matches.map(shade => `<tr><td>${esc(shade.brand_name)}</td><td>${esc(shade.product_name)}</td><td>${esc(shade.name)}${shade.code ? ` · ${esc(shade.code)}` : ''}</td><td>${esc(shade.depth || 'Not listed')}</td><td>${esc(shade.undertone || 'Not listed')}</td></tr>`).join('')}</tbody></table>`;
}

function renderCorrections(reports) {
  const target = $('#correction-list');
  if (!reports.length) {
    target.innerHTML = '<p class="admin-muted">No feedback reports yet. New reports from the public form will appear here.</p>';
    return;
  }
  target.innerHTML = reports.map(report => `<article class="queue-item" data-report="${esc(report.id)}">
    <strong>${esc(report.category)} · ${esc(report.status)}</strong>
    <p>${esc(report.description)}</p>
    <p class="admin-muted">Submitted: ${esc(report.created_at)} · Contact: ${esc(report.contact || 'Not provided')} · Retain until: ${esc(report.retain_until || 'Not set')}</p>
    <div class="admin-actions">
      <label>Status <select aria-label="Feedback status"><option ${report.status === 'Under Review' ? 'selected' : ''}>Under Review</option><option ${report.status === 'Resolved' ? 'selected' : ''}>Resolved</option><option ${report.status === 'Rejected' ? 'selected' : ''}>Rejected</option></select></label>
      <input aria-label="Review note" placeholder="Short note required">
      <button class="button secondary" type="button" data-save-report>Save</button>
    </div>
  </article>`).join('');
  target.querySelectorAll('[data-save-report]').forEach(button => {
    button.addEventListener('click', async () => {
      const item = button.closest('[data-report]');
      const reason = item.querySelector('input').value.trim();
      if (reason.length < 5) {
        item.querySelector('input').focus();
        item.querySelector('input').setCustomValidity('Please enter at least five characters.');
        item.querySelector('input').reportValidity();
        item.querySelector('input').setCustomValidity('');
        return;
      }
      button.disabled = true;
      try {
        await call('/api/admin/review-correction', {
          method: 'POST',
          body: JSON.stringify({ id: item.dataset.report, status: item.querySelector('select').value, reason })
        });
        await load();
      } catch (error) {
        alert(error.message);
        button.disabled = false;
      }
    });
  });
}

async function load() {
  const [overview, shadeData] = await Promise.all([
    call('/api/admin/overview'),
    call('/api/admin/shades')
  ]);
  publishedShades = shadeData.items.filter(shade => shade.publication_state === 'Published');
  $('#stats').innerHTML = `<div class="admin-stat"><strong>${overview.corrections}</strong><span>feedback reports under review</span></div><div class="admin-stat"><strong>${publishedShades.length}</strong><span>published shades</span></div>`;
  renderCorrections(overview.reports);
  renderPublishedShades();
}

async function init() {
  try {
    await load();
    $('#login-view').classList.add('admin-hidden');
    $('#dashboard').classList.remove('admin-hidden');
  } catch (error) {
    if (!String(error.message).toLowerCase().includes('authentication')) $('#login-error').textContent = error.message;
  }
}

$('#login-form').addEventListener('submit', async event => {
  event.preventDefault();
  try {
    await call('/api/admin/login', { method: 'POST', body: JSON.stringify({ password: $('#password').value }) });
    $('#login-error').textContent = '';
    await init();
  } catch (error) {
    $('#login-error').textContent = error.message;
  }
});
$('#logout').addEventListener('click', async () => {
  await call('/api/admin/logout', { method: 'POST' });
  location.reload();
});
$('#shade-filter').addEventListener('input', renderPublishedShades);
init();
