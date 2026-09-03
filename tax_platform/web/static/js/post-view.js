(() => {
  const out = qs("#post-view-out");
  const watchBar = qs("#posts-watch-bar");
  const backLink = qs("#back-to-search");
  const params = new URLSearchParams(window.location.search);
  const bureauCode = (params.get("bureau_code") || "").trim();
  const department = (params.get("department") || "").trim();
  const title = (params.get("title") || "").trim();

  let allBureaus = [];

  function bureauLabel(code) {
    if (!code) return "—";
    const site = allBureaus.find((b) => b.code === code);
    return site ? bureauDisplayName(site, allBureaus) : code;
  }

  function personLink(code, name) {
    if (!code || !name) return escapeHtml(name || "—");
    return `<a href="/people/${encodeURIComponent(`${code}:${name}`)}">${escapeHtml(name)}</a>`;
  }

  function buildSearchHref() {
    // Prefer the exact filter state from the list page — never invent a 科室 filter
    // from the post being viewed.
    const ret = (params.get("ret") || "").trim();
    if (ret) return `/posts?${ret}`;
    const url = new URL("/posts", window.location.origin);
    if (bureauCode) url.searchParams.set("bureau_code", bureauCode);
    if (title) url.searchParams.set("title", title);
    return `${url.pathname}${url.search}`;
  }

  async function refreshPostWatchBar() {
    if (!watchBar || !bureauCode || !department) {
      if (watchBar) watchBar.hidden = true;
      return;
    }
    watchBar.hidden = false;
    watchBar.innerHTML = `<span class="watch-bar-label">${escapeHtml(bureauLabel(bureauCode))} · ${escapeHtml(
      department
    )}${title ? ` · ${escapeHtml(title)}` : ""}</span>`;
    const actions = document.createElement("span");
    actions.className = "watch-bar-actions";
    watchBar.appendChild(actions);

    const deptSlot = document.createElement("span");
    actions.appendChild(deptSlot);
    await mountWatchButton(deptSlot, {
      target_type: "department",
      target_id: buildDepartmentTargetId(bureauCode, department),
      label: department,
      idleText: "关注此科室",
      activeText: "已关注科室",
      loginText: "登录后关注科室",
    });

    const postSlot = document.createElement("span");
    actions.appendChild(postSlot);
    await mountWatchButton(postSlot, {
      target_type: "post",
      target_id: buildPostTargetId(bureauCode, department, title || ""),
      label: title ? `${department} · ${title}` : department,
      idleText: "关注此岗位",
      activeText: "已关注岗位",
      loginText: "登录后关注岗位",
    });
  }

  function renderTenureRow(row, { ended = false } = {}) {
    const dates = ended
      ? `${escapeHtml(row.since || "—")} → ${escapeHtml(row.ended_on || "—")}`
      : `自 ${escapeHtml(row.since || "—")}`;
    const source = row.source_url
      ? `<a href="${escapeHtml(row.source_url)}" target="_blank" rel="noopener">原文</a>`
      : "";
    return `<li class="post-tenure-row">
      <div class="post-tenure-main">${personLink(bureauCode, row.person_name)} · ${escapeHtml(row.title || "")}</div>
      <div class="muted post-tenure-meta">${dates} ${source}</div>
    </li>`;
  }

  function renderArchive(data) {
    const incumbents = (data.incumbents || [])
      .map((row) => renderTenureRow(row))
      .join("");
    const past = (data.past || [])
      .map((row) => renderTenureRow(row, { ended: true }))
      .join("");
    const history = (data.history || [])
      .map((row) => {
        const source = row.source_url
          ? `<a href="${escapeHtml(row.source_url)}" target="_blank" rel="noopener">原文</a>`
          : "";
        return `<li class="timeline-item">
          <time class="timeline-date">${escapeHtml(row.effective_on || "—")}</time>
          <div class="timeline-body">
            <div class="timeline-title">
              <strong>${personLink(bureauCode, row.person_name)} · ${escapeHtml(changeLabel(row.change_type || row.action))} · ${escapeHtml(row.title || "")}</strong>
            </div>
            <div class="muted">${escapeHtml(row.notice_title || "")} ${source}</div>
          </div>
        </li>`;
      })
      .join("");

    const heading = qs("#post-view-heading");
    if (heading) {
      heading.textContent = `${data.department || department}${
        title || data.title ? ` · ${title || data.title}` : ""
      }`;
    }
    document.title = `${heading?.textContent || "岗位档案"} · 税局人事检索`;

    out.innerHTML = `
      <div class="results-summary">
        ${escapeHtml(bureauLabel(bureauCode))}
        · 现任 ${data.incumbent_count ?? 0}
        · 历任 ${data.past?.length ?? 0}
        · 履历 ${data.history_count ?? 0}
      </div>
      <h2 class="results-group-title">现任</h2>
      <ul class="post-tenure-list">${incumbents || "<li class='muted'>暂无现任记录</li>"}</ul>
      <h2 class="results-group-title">历任（已结束任期）</h2>
      <ul class="post-tenure-list">${past || "<li class='muted'>暂无</li>"}</ul>
      <h2 class="results-group-title">任免时间线</h2>
      <ul class="timeline profile-timeline">${history || "<li class='timeline-item'><div class='muted'>暂无</div></li>"}</ul>
    `;
  }

  async function load() {
    if (backLink) backLink.href = buildSearchHref();

    if (!bureauCode || !department) {
      out.innerHTML = `<div class="status error">缺少单位或科室参数。<a href="/posts">返回检索</a></div>`;
      return;
    }

    try {
      allBureaus = await apiGet("/api/meta/bureaus").then((data) => data.items || []);
      const data = await apiGet("/api/posts", {
        bureau_code: bureauCode,
        department,
        title: title || undefined,
      });
      await refreshPostWatchBar();
      renderArchive(data);
    } catch (err) {
      out.innerHTML = `<div class="status error">${escapeHtml(err.message || err)}</div>`;
      if (watchBar) watchBar.hidden = true;
    }
  }

  load().catch(() => {});
})();
