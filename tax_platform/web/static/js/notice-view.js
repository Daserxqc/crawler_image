(() => {
  const panel = qs("#notice-panel");
  const params = new URLSearchParams(window.location.search);
  const url = (params.get("url") || "").trim();
  const bureauCode = (params.get("bureau_code") || "").trim();
  const backHref = params.get("from") === "regions" ? "/regions" : "/notices";
  const backLabel = params.get("from") === "regions" ? "← 返回地区更新" : "← 返回公告列表";

  function eventLine(ev) {
    const bits = [
      changeLabel(ev.action),
      [ev.department_raw, ev.title_raw].filter(Boolean).join(" · "),
      ev.bureau_name || "",
    ].filter(Boolean);
    const name = escapeHtml(ev.person_name || "—");
    const href = ev.person_id
      ? `/people/${encodeURIComponent(ev.person_id)}`
      : "";
    const nameHtml = href
      ? `<a href="${href}">${name}</a>`
      : `<strong>${name}</strong>`;
    return `
      <li class="notice-event-item">
        ${nameHtml}
        <span class="muted">${escapeHtml(bits.join(" · "))}</span>
      </li>
    `;
  }

  function renderNotice(data, fallbackUrl) {
    const title = data.title || "（无标题）";
    document.title = `${title} · 公告存档`;
    const metaBits = [
      data.issued_on ? `任免日 ${escapeHtml(data.issued_on)}` : "",
      data.doc_no ? `文号 ${escapeHtml(data.doc_no)}` : "",
      data.issuer ? escapeHtml(data.issuer) : "",
    ].filter(Boolean);
    const text = (data.raw_text || "").trim();
    const body = text
      ? `<pre class="notice-archive-text">${escapeHtml(text)}</pre>`
      : `<p class="muted">库内未保存可用正文。可尝试打开原链接。</p>`;
    const events = Array.isArray(data.events) ? data.events : [];
    const peopleBlock = events.length
      ? `<section class="notice-events" aria-label="本条任免人员">
           <h2 class="notice-events-title">本条任免人员</h2>
           <ul class="notice-events-list">${events.map(eventLine).join("")}</ul>
         </section>`
      : "";
    const original = data.source_url
      ? `<a class="btn secondary" href="${escapeHtml(
          data.source_url
        )}" target="_blank" rel="noopener">打开原链接</a>`
      : fallbackUrl
        ? `<a class="btn secondary" href="${escapeHtml(
            fallbackUrl
          )}" target="_blank" rel="noopener">打开原链接</a>`
        : "";
    panel.innerHTML = `
      <p class="muted"><a href="${backHref}">${backLabel}</a></p>
      <h1 class="panel-heading">${escapeHtml(title)}</h1>
      <p class="muted">${metaBits.join(" · ") || "—"}</p>
      <div class="filter-actions" style="margin: 0.75rem 0 1rem">${original}</div>
      ${body}
      ${peopleBlock}
    `;
  }

  function renderError(err, fallbackUrl) {
    panel.innerHTML = `
      <p class="muted"><a href="${backHref}">${backLabel}</a></p>
      <p class="status error">${escapeHtml(err.message || String(err))}</p>
      ${
        fallbackUrl
          ? `<p class="muted">若原链接仍可用：
          <a href="${escapeHtml(fallbackUrl)}" target="_blank" rel="noopener">打开原链接</a>
        </p>`
          : ""
      }
    `;
  }

  async function load() {
    if (!panel) return;
    if (!url && !bureauCode) {
      panel.innerHTML = `<p class="muted">缺少公告参数（url 或 bureau_code）。</p>`;
      return;
    }
    try {
      const data = bureauCode
        ? await apiGet("/api/notices/latest", { bureau_code: bureauCode })
        : await apiGet("/api/notices/by-url", { url });
      renderNotice(data, url || data.source_url || "");
    } catch (err) {
      renderError(err, url);
    }
  }

  load();
})();
