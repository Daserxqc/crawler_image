(() => {
  const root = qs("#profile");
  const pathId = decodeURIComponent(window.location.pathname.replace(/^\/people\//, ""));
  const queryId = new URLSearchParams(window.location.search).get("id");
  const personId = pathId && pathId !== "person.html" ? pathId : queryId;

  if (!personId) {
    root.innerHTML = `<div class="status error">缺少人员 id</div>`;
    return;
  }

  function shortName(fullName) {
    return String(fullName || "")
      .replace(/^国家税务总局/, "")
      .replace(/税务局$/, "")
      .trim();
  }

  function handleBack() {
    const ret = resolveListReturnUrl();
    if (ret) {
      window.location.assign(ret);
      return;
    }
    window.location.href = "/departments";
  }

  function tenureRange(row) {
    const start = row.started_on || row.date || "";
    const end = row.ended_on || "";
    const ctype = row.change_type || row.action || "";
    if (["dismiss", "retire", "免职", "退休"].includes(ctype) && !row.started_on) {
      return row.date || "—";
    }
    if (!start) return "—";
    if (end) return `${start} ～ ${end}`;
    if (["appoint", "transfer", "promote", "probation_confirm", "任职", "调任", "晋升"].includes(ctype) || row.started_on) {
      return `${start} ～ 至今`;
    }
    return start;
  }

  function formatHistoryLine(row) {
    const bits = [
      changeLabel(row.change_type || row.action),
      row.title,
      row.department,
      row.unit ? shortName(row.unit) : null,
    ].filter((x) => {
      const s = String(x || "").trim();
      return s && s !== "—" && s !== "-" && s !== "一";
    });
    return bits.map((b) => escapeHtml(b)).join(" · ") || "—";
  }

  apiGet(`/api/people/${encodeURIComponent(personId)}`)
    .then((profile) => {
      const current = profile.current || {};
      const bureauShort = shortName(profile.bureau_name || profile.bureau_code);
      const levelTag = profile.org_level
        ? `<span class="tag tag-level">${escapeHtml(levelLabel(profile.org_level))}</span>`
        : "";
      const deptDisplay =
        current.department ||
        (profile.org_level === "headquarters" ? "本机关" : "—");
      const history = (profile.history || [])
        .map((row) => {
          const source = row.source_url ? noticeSourceLinks(row.source_url) : "";
          return `
            <li class="timeline-item">
              <time class="timeline-date">${escapeHtml(tenureRange(row))}</time>
              <div class="timeline-body">
                <div class="timeline-title">
                  <strong>${formatHistoryLine(row)}</strong>
                </div>
                <div class="muted">${escapeHtml(row.notice_title || "")}</div>
                ${source ? `<div class="timeline-sources">${source}</div>` : ""}
              </div>
            </li>
          `;
        })
        .join("");
      document.title = `${profile.name} · 税局人事检索`;
      root.innerHTML = `
        <div class="profile-toolbar">
          <button type="button" class="btn secondary" id="back-btn" aria-label="返回上一页">← 返回</button>
          <span id="person-watch-slot"></span>
          <a class="btn secondary" id="export-csv" href="/api/export/people/${encodeURIComponent(profile.id || personId)}?fmt=csv">导出 CSV</a>
          <a class="btn secondary" id="export-xlsx" href="/api/export/people/${encodeURIComponent(profile.id || personId)}?fmt=xlsx">导出 Excel</a>
        </div>
        <div class="profile-header">
          <div>
            <h1 class="profile-name">${escapeHtml(profile.name)}</h1>
            <div class="person-tags">
              ${current.is_current ? '<span class="tag tag-current">现任</span>' : '<span class="tag">非现任/未知</span>'}
              ${levelTag}
            </div>
          </div>
          ${profile.leader_intro_url ? `<a class="btn secondary" href="${escapeHtml(profile.leader_intro_url)}" target="_blank" rel="noopener">领导介绍原文</a>` : ""}
        </div>
        <dl class="person-facts profile-facts">
          <div>
            <dt>任职单位</dt>
            <dd>${escapeHtml(bureauShort || "—")}</dd>
          </div>
          <div>
            <dt>工作科室</dt>
            <dd>${escapeHtml(deptDisplay)}</dd>
          </div>
          <div>
            <dt>现任职务</dt>
            <dd>${escapeHtml(current.title || "—")}</dd>
          </div>
          <div>
            <dt>分管科室</dt>
            <dd>${escapeHtml((current.departments || []).join("、") || "暂无（源站未公布或未采集到）")}</dd>
          </div>
        </dl>
        <section class="profile-history">
          <h2 class="panel-heading">履历</h2>
          <ul class="timeline profile-timeline">${history || "<li class='timeline-item'><div class='muted'>暂无任免履历</div></li>"}</ul>
        </section>
      `;
      qs("#back-btn").addEventListener("click", handleBack);
      mountWatchButton(qs("#person-watch-slot"), {
        target_type: "person",
        target_id: profile.id || personId,
        label: profile.name,
        idleText: "关注此人",
        activeText: "已关注",
        loginText: "登录后关注",
        className: "btn watch-btn",
        loginNext: window.location.pathname,
      }).catch(() => {});
    })
    .catch((err) => {
      root.innerHTML = `
        <div class="profile-toolbar">
          <button type="button" class="btn secondary" id="back-btn" aria-label="返回上一页">← 返回</button>
        </div>
        <div class="status error">${escapeHtml(err.message || err)}</div>
      `;
      qs("#back-btn").addEventListener("click", handleBack);
    });
})();
