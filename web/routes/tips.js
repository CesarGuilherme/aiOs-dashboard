import { api, fmt } from '/web/app.js';

export default async function (root) {
  const tips = await api('/api/tips');
  root.innerHTML = `
    <div class="card">
      <h2>Suggestions</h2>
      ${tips.length === 0
        ? '<p class="muted">No waste patterns right now. Check back after more sessions — new-memory suggestions live on Brain.</p>'
        : `<p class="muted" style="margin:-8px 0 14px">Waste and behavior patterns from recent sessions (7–30 days depending on the rule). Dismissed tips re-appear after 14 days. New-memory suggestions live on Brain.</p>`}
      ${tips.map(t => `
        <div class="tip">
          <div class="tip-head">
            <span class="badge">${fmt.htmlSafe(t.project ?? t.category)}</span>
            <strong>${fmt.htmlSafe(t.title)}</strong>
            <span class="spacer"></span>
            ${t.prompt ? `<button data-copy="${fmt.htmlSafe(t.prompt)}">copy prompt</button>` : ''}
            <button class="ghost" data-key="${fmt.htmlSafe(t.key)}">dismiss</button>
          </div>
          <p class="tip-body">${fmt.htmlSafe(t.body)}</p>
        </div>`).join('')}
    </div>`;
  root.querySelectorAll('button[data-copy]').forEach(b => {
    b.addEventListener('click', async () => {
      await navigator.clipboard.writeText(b.dataset.copy);
      b.textContent = 'copied ✓';
      setTimeout(() => { b.textContent = 'copy prompt'; }, 1500);
    });
  });
  root.querySelectorAll('button[data-key]').forEach(b => {
    b.addEventListener('click', async () => {
      b.disabled = true;
      try {
        const res = await fetch('/api/tips/dismiss', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key: b.dataset.key }),
        });
        if (!res.ok) throw new Error('dismiss failed');
        b.closest('.tip')?.remove();
        if (!root.querySelector('.tip')) {
          const card = root.querySelector('.card');
          if (card) {
            card.innerHTML = `
              <h2>Suggestions</h2>
              <p class="muted">No waste patterns right now. Check back after more sessions — new-memory suggestions live on Brain.</p>`;
          }
        }
      } catch {
        b.disabled = false;
      }
    });
  });
}
