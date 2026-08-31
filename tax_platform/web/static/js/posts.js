(() => {
  const form = qs("#posts-form");
  const out = qs("#posts-out");
  const watchBar = qs("#posts-watch-bar");
  const params = new URLSearchParams(window.location.search);

  ["bureau_code", "department", "title"].forEach((key) => {
    if (params.get(key) && qs(`#${key}`)) qs(`#${key}`).value = params.get(key);
  });

  async function refreshPostWatchBar(bureauCode, department, title) {
    if (!watchBar || !bureauCode || !department) {
      if (watchBar) watchBar.hidden = true;
      return;
    }
    watchBar.hidden = false;
    watchBar.innerHTML = `<span class="watch-bar-label">${escapeHtml(department)}${
      title ? ` · ${escapeHtml(title)}` : ""
    }</span>`;
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

  async function load(event) {
    event?.preventDefault();
    const bureauCode = qs("#bureau_code").value.trim();
    const department = qs("#department").value.trim();
    const title = qs("#title").value.trim();
    if (!bureauCode || !department) return;

    out.innerHTML = `<div class="status">加载中…</div>`;
    try {
      const data = await apiGet("/api/posts", {
        bureau_code: bureauCode,
        department,
        title: title || undefined,
      });
      await refreshPostWatchBar(bureauCode, department, title);

      const incumbents = (data.incumbents || [])
        .map(
          (row) =>
            `<li><strong>${escapeHtml(row.person_name)}</strong>
              <span class="muted">${escapeHtml(row.title || "")} · 自 ${escapeHtml(row.since || "—")}</span>
              ${row.source_url ? `<a href="${escapeHtml(row.source_url)}" target="_blank" rel="noopener">原文</a>` : ""}
            </li>`
        )
        .join("");
      const past = (data.past || [])
        .map(
          (row) =>
            `<li>${escapeHtml(row.person_name)} · ${escapeHtml(row.title || "")}
              <span class="muted">${escapeHtml(row.since || "—")} → ${escapeHtml(row.ended_on || "—")}</span></li>`
        )
        .join("");
      const history = (data.history || [])
        .map((row) => {
          const personHref = row.person_name
            ? `/people/${encodeURIComponent(`${bureauCode}:${row.person_name}`)}`
            : null;
          const name = personHref
            ? `<a href="${personHref}">${escapeHtml(row.person_name)}</a>`
            : escapeHtml(row.person_name || "—");
          return `<li><time>${escapeHtml(row.effective_on || "—")}</time> ${name}
            · ${escapeHtml(changeLabel(row.change_type || row.action))}
            · ${escapeHtml(row.title || "")}
            ${row.source_url ? `<a href="${escapeHtml(row.source_url)}" target="_blank" rel="noopener">原文</a>` : ""}
          </li>`;
        })
        .join("");

      out.innerHTML = `
        <div class="results-summary">
          ${escapeHtml(data.region || bureauCode)} · ${escapeHtml(data.department || department)}
          · 现任 ${data.incumbent_count ?? 0} · 履历 ${data.history_count ?? 0}
        </div>
        <h2 class="results-group-title">现任</h2>
        <ul>${incumbents || "<li class='muted'>暂无现任记录</li>"}</ul>
        <h2 class="results-group-title">历任（已结束任期）</h2>
        <ul>${past || "<li class='muted'>暂无</li>"}</ul>
        <h2 class="results-group-title">任免时间线</h2>
        <ul class="timeline">${history || "<li class='muted'>暂无</li>"}</ul>
      `;
    } catch (err) {
      out.innerHTML = `<div class="status error">${escapeHtml(err.message || err)}</div>`;
      if (watchBar) watchBar.hidden = true;
    }
  }

  form.addEventListener("submit", load);
  if (params.get("bureau_code") && params.get("department")) {
    load().catch(() => {});
  }
})();
