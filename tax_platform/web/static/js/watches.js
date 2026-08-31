(() => {
  const listEl = qs("#watch-list");
  const outboxEl = qs("#outbox-list");
  const status = qs("#watch-status");
  const form = qs("#watch-form");

  async function ensureAuth() {
    const me = await apiGet("/api/auth/me");
    if (!me.authenticated) {
      window.location.href = `/login?next=${encodeURIComponent("/watches")}`;
      return false;
    }
    return true;
  }

  function renderWatches(items) {
    if (!items.length) {
      listEl.innerHTML = `<div class="status muted">暂无关注。可在人员页点击「关注」，或在此手动添加。</div>`;
      return;
    }
    listEl.innerHTML = items
      .map(
        (w) => `
      <article class="result-row">
        <div>
          <strong>${escapeHtml(w.label || w.target_id)}</strong>
          <div class="muted">${escapeHtml(w.target_type)} · ${escapeHtml(w.target_id)}</div>
        </div>
        <button type="button" class="btn secondary" data-del="${w.id}" aria-label="取消关注">取消</button>
      </article>`
      )
      .join("");
    qsa("[data-del]", listEl).forEach((btn) => {
      btn.addEventListener("click", async () => {
        await apiDelete(`/api/watches/${btn.dataset.del}`);
        await reload();
      });
    });
  }

  function renderOutbox(items) {
    if (!items.length) {
      outboxEl.innerHTML = `<div class="status muted">暂无邮件记录</div>`;
      return;
    }
    outboxEl.innerHTML = items
      .map(
        (m) => `
      <article class="result-row">
        <div>
          <strong>${escapeHtml(m.subject)}</strong>
          <div class="muted">${escapeHtml(m.status)} · ${escapeHtml(m.created_at || "")}</div>
          ${m.error ? `<div class="status error">${escapeHtml(m.error)}</div>` : ""}
        </div>
      </article>`
      )
      .join("");
  }

  async function reload() {
    const [w, o] = await Promise.all([
      apiGet("/api/watches"),
      apiGet("/api/notify/outbox"),
    ]);
    renderWatches(w.items || []);
    renderOutbox(o.items || []);
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    status.textContent = "";
    try {
      await apiPost("/api/watches", {
        target_type: qs("#target_type").value,
        target_id: qs("#target_id").value.trim(),
        label: qs("#label").value.trim() || null,
      });
      form.reset();
      status.textContent = "已添加";
      await reload();
    } catch (err) {
      status.classList.add("error");
      status.textContent = err.message || String(err);
    }
  });

  qs("#notify-btn").addEventListener("click", async () => {
    status.textContent = "正在试跑…";
    try {
      const result = await apiPost("/api/notify/run?dry_run=true", {});
      status.textContent = `已排队 ${result.digests_queued} 封，匹配事件 ${result.events_matched}`;
      await reload();
    } catch (err) {
      status.classList.add("error");
      status.textContent = err.message || String(err);
    }
  });

  ensureAuth().then((ok) => {
    if (ok) reload().catch((err) => {
      status.classList.add("error");
      status.textContent = err.message || String(err);
    });
  });
})();
