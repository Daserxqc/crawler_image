(() => {
  const form = qs("#dept-form");
  const out = qs("#out");
  const status = qs("#status");
  const levelSelect = qs("#org_level");
  const categorySelect = qs("#region_category");
  const bureauSelect = qs("#bureau_code");
  const deptSelect = qs("#department");
  const watchBar = qs("#dept-watch-bar");

  let allBureaus = [];
  let lastBureauCode = null;
  let lastDepartment = null;
  let staUnits = [];
  let staffOffset = 0;
  const STAFF_PAGE = 100;

  function isHeadquartersLevel() {
    return levelSelect.value === "headquarters";
  }

  function resolveBureauCodeForWatch() {
    if (isHeadquartersLevel()) {
      const unit = bureauSelect.value;
      if (!unit) return null;
      return "sta";
    }
    return bureauSelect.value || null;
  }

  async function refreshDeptWatchBar(bureauCode, department) {
    if (!watchBar) return;
    watchBar.innerHTML = "";
    if (!bureauCode) {
      watchBar.hidden = true;
      return;
    }
    watchBar.hidden = false;
    const bureauLabelText = bureauLabel(bureauCode);
    watchBar.innerHTML = `<span class="watch-bar-label">${escapeHtml(bureauLabelText)}${
      department ? ` · ${escapeHtml(department)}` : ""
    }</span>`;
    const actions = document.createElement("span");
    actions.className = "watch-bar-actions";
    watchBar.appendChild(actions);

    const bureauSlot = document.createElement("span");
    actions.appendChild(bureauSlot);
    await mountWatchButton(bureauSlot, {
      target_type: "bureau",
      target_id: bureauCode,
      label: bureauLabelText,
      idleText: "关注此单位",
      activeText: "已关注单位",
      loginText: "登录后关注单位",
    });

    if (department) {
      const deptSlot = document.createElement("span");
      actions.appendChild(deptSlot);
      await mountWatchButton(deptSlot, {
        target_type: "department",
        target_id: buildDepartmentTargetId(bureauCode, department),
        label: `${bureauLabelText} · ${department}`,
        idleText: "关注此科室",
        activeText: "已关注科室",
        loginText: "登录后关注科室",
      });
      const link = document.createElement("a");
      link.className = "btn secondary";
      link.href = `/posts/view?bureau_code=${encodeURIComponent(bureauCode)}&department=${encodeURIComponent(
        department
      )}`;
      link.textContent = "岗位档案";
      actions.appendChild(link);
    }
  }

  function fillSelect(select, options, emptyLabel) {
    const prev = select.value;
    const seen = new Set();
    const unique = [];
    options.forEach((opt) => {
      const value = String(opt.value || "").trim();
      if (!value || seen.has(value)) return;
      seen.add(value);
      unique.push({ value, label: opt.label || value });
    });
    select.innerHTML =
      `<option value="">${escapeHtml(emptyLabel)}</option>` +
      unique
        .map((opt) => `<option value="${escapeHtml(opt.value)}">${escapeHtml(opt.label)}</option>`)
        .join("");
    select.value = unique.some((opt) => opt.value === prev) ? prev : "";
  }

  function rebuildCategoryOptions() {
    const prev = categorySelect.value;
    const sorted = categoriesForLevel(levelSelect.value);
    categorySelect.innerHTML =
      '<option value="">全部分类</option>' +
      sorted.map((cat) => `<option value="${escapeHtml(cat)}">${escapeHtml(categoryLabel(cat))}</option>`).join("");
    categorySelect.value = sorted.includes(prev) ? prev : "";
  }

  function filteredBureaus() {
    const level = levelSelect.value;
    const cat = categorySelect.value;
    return allBureaus
      .filter((site) => {
        if (level && site.level !== level) return false;
        if (!cat) return true;
        if (level === "headquarters") return true;
        if (site.level === "province") return bureauCategory(site) === cat;
        if (site.level === "district" && site.parent_code) {
          const parent = allBureaus.find((b) => b.code === site.parent_code);
          return parent ? bureauCategory(parent) === cat : false;
        }
        return false;
      })
      .sort((a, b) =>
        bureauDisplayName(a, allBureaus).localeCompare(bureauDisplayName(b, allBureaus), "zh-CN")
      );
  }

  function filteredStaUnits() {
    const cat = categorySelect.value;
    if (!cat) return staUnits;
    return staUnits.filter((u) => u.category === cat);
  }

  function rebuildBureauOptions() {
    if (isHeadquartersLevel()) {
      fillSelect(
        bureauSelect,
        filteredStaUnits().map((u) => ({ value: u.code, label: u.name })),
        "全部地区"
      );
      return;
    }
    fillSelect(
      bureauSelect,
      filteredBureaus().map((site) => ({
        value: site.code,
        label: bureauDisplayName(site, allBureaus),
      })),
      "全部地区"
    );
  }

  async function loadStaUnits() {
    const data = await apiGet("/api/meta/units");
    staUnits = data.items || [];
  }

  async function loadLevels() {
    const data = await apiGet("/api/meta/levels");
    (data.levels || []).forEach((level) => {
      const opt = document.createElement("option");
      opt.value = level.id;
      opt.textContent = level.label;
      levelSelect.appendChild(opt);
    });
  }

  async function loadBureaus() {
    allBureaus = await apiGet("/api/meta/bureaus").then((data) => data.items || []);
    rebuildCategoryOptions();
    rebuildBureauOptions();
  }

  async function refreshDeptOptions() {
    const data = await apiGet("/api/departments/suggest", {
      org_level: levelSelect.value || undefined,
      limit: 200,
    });
    fillSelect(
      deptSelect,
      (data.items || [])
        .map((d) => {
          const name = d.canonical_name || d.name || "";
          return { value: name, label: name };
        })
        .filter((d) => d.value),
      "全部科室"
    );
  }

  async function onRegionFiltersChange() {
    rebuildCategoryOptions();
    if (isHeadquartersLevel() && !staUnits.length) {
      await loadStaUnits();
    }
    rebuildBureauOptions();
    await refreshDeptOptions();
  }

  function bureauParam() {
    if (isHeadquartersLevel()) {
      const unit = bureauSelect.value;
      if (unit === "sta" || (unit && unit.startsWith("sta"))) return "sta";
      return undefined;
    }
    return bureauSelect.value || undefined;
  }

  function bureauLabel(code) {
    if (!code) return "—";
    if (code === "sta" || String(code).startsWith("sta:")) {
      const unit = staUnits.find((u) => u.code === code);
      return unit ? unit.name : code === "sta" ? "总局" : code;
    }
    const site = allBureaus.find((b) => b.code === code);
    return site ? bureauDisplayName(site, allBureaus) : code;
  }

  async function runQuery(event, { resetOffset = false } = {}) {
    event?.preventDefault();
    status.hidden = true;
    if (resetOffset || event?.type === "submit") staffOffset = 0;
    const department = deptSelect.value.trim();
    qs("#dept-btn").disabled = true;
    out.innerHTML = `<div class="status">查询中…</div>`;
    try {
      const data = await apiGet("/api/departments/penetrate", {
        department: department || undefined,
        org_level: levelSelect.value || undefined,
        bureau_code: bureauParam(),
        staff_limit: STAFF_PAGE,
        staff_offset: staffOffset,
      });
      const leaderItems = data.supervising_leaders || [];
      const staffItems = data.staff || [];
      const upwardItems = data.upward || [];
      const leaderCount = data.supervisor_count ?? leaderItems.length;
      const staffCount = data.staff_count ?? staffItems.length;
      const pageLimit = data.staff_limit ?? STAFF_PAGE;
      const pageOffset = data.staff_offset ?? staffOffset;

      const deptLabel = data.browse_all ? "全部科室" : data.department || department || "全部";
      lastBureauCode = bureauParam() || bureauSelect.value || null;
      lastDepartment = data.browse_all ? null : deptLabel;

      const upwardByKey = new Map();
      upwardItems.forEach((u) => {
        const lead = u.leader || {};
        const key = lead.id || `${u.bureau_code}:${lead.name || ""}`;
        upwardByKey.set(key, u);
      });

      function hierarchyMeta(u) {
        if (!u) return [];
        const bureauName = bureauLabel(u.bureau_code);
        const region = (u.region || "").trim();
        const parentName = u.parent_bureau_code ? bureauLabel(u.parent_bureau_code) : "";
        const parts = [];
        if (u.org_level) parts.push(levelLabel(u.org_level));
        if (bureauName) parts.push(bureauName);
        if (region && region !== bureauName) parts.push(region);
        if (parentName && parentName !== region && parentName !== bureauName) {
          parts.push(`上级 ${parentName}`);
        }
        return parts;
      }

      const leaders = leaderItems
        .map((l) => {
          const id = l.id || (l.bureau_code && l.name ? `${l.bureau_code}:${l.name}` : null);
          const label = l.name || l.person_name || "—";
          const name = id
            ? `<a href="/people/${encodeURIComponent(id)}">${escapeHtml(label)}</a>`
            : escapeHtml(label);
          const key = id || `${l.bureau_code}:${label}`;
          const chain = hierarchyMeta(upwardByKey.get(key) || {
            bureau_code: l.bureau_code,
            org_level: l.org_level,
          });
          const title = (l.title_raw || "").trim();
          const line2 = [title, ...chain].filter(Boolean).join(" · ");
          return `<li class="dept-hit">
            <div class="dept-hit-name">${name}</div>
            ${
              line2
                ? `<div class="dept-hit-meta muted">${escapeHtml(line2)}</div>`
                : ""
            }
          </li>`;
        })
        .join("");

      const staff = staffItems
        .map((s) => {
          return `<li class="dept-hit">
            <div class="dept-hit-name"><a href="/people/${encodeURIComponent(s.id)}">${escapeHtml(
              s.name
            )}</a></div>
            <div class="dept-hit-meta muted">${escapeHtml(formatCurrent(s.current))} · ${escapeHtml(
              bureauLabel(s.bureau_code)
            )}</div>
          </li>`;
        })
        .join("");

      const leadersEmpty = data.browse_all
        ? "浏览全体时不按科室聚合分管；选定科室后可查看分管领导"
        : "暂无（源站未公布或未采集到分管）";
      const postsLink =
        !data.browse_all && lastBureauCode && lastDepartment
          ? `<p class="dept-panel-foot"><a class="btn secondary" href="/posts/view?bureau_code=${encodeURIComponent(
              lastBureauCode
            )}&department=${encodeURIComponent(lastDepartment)}">查看该科室岗位档案 →</a></p>`
          : "";

      await refreshDeptWatchBar(lastBureauCode, lastDepartment);

      const pageNote =
        staffCount > pageLimit
          ? `（第 ${Math.floor(pageOffset / pageLimit) + 1} 页 · 每页 ${pageLimit} 条）`
          : staffCount
            ? `（共 ${staffCount} 条）`
            : "";

      out.innerHTML = `
        <section class="panel-card dept-results" aria-label="科室穿透结果">
          <div class="results-summary">科室：${escapeHtml(deptLabel)}${escapeHtml(pageNote)}</div>
          <p class="dept-chain-hint muted">科室 → 分管领导 → 所在地区层级</p>

          <h2 class="results-group-title">分管领导（向上穿透）${
            leaderCount ? ` · ${escapeHtml(leaderCount)}` : ""
          }</h2>
          <ul class="dept-hit-list">${
            leaders || `<li class="dept-hit muted">${escapeHtml(leadersEmpty)}</li>`
          }</ul>

          <details class="dept-staff-block"${staffCount ? " open" : ""}>
            <summary>本科室任职人员${staffCount ? ` · ${escapeHtml(staffCount)}` : ""}</summary>
            <ul class="dept-hit-list">${
              staff || `<li class="dept-hit muted">暂无匹配任职</li>`
            }</ul>
            <nav class="pagination" id="dept-staff-pager" aria-label="任职人员分页" hidden></nav>
            ${postsLink}
          </details>
        </section>
      `;

      mountSimplePager(qs("#dept-staff-pager"), {
        total: staffCount,
        offset: pageOffset,
        limit: pageLimit,
        unitLabel: "人",
        onPage: (nextOffset) => {
          staffOffset = nextOffset;
          runQuery(null).catch(() => {});
        },
      });
    } catch (err) {
      out.innerHTML = "";
      status.hidden = false;
      status.className = "status error";
      status.textContent = err.message || String(err);
    } finally {
      qs("#dept-btn").disabled = false;
    }
  }

  function resetForm() {
    form.reset();
    staffOffset = 0;
    out.innerHTML = "";
    status.hidden = true;
    onRegionFiltersChange().catch(() => {});
  }

  async function runLeaderLookup(event) {
    event?.preventDefault();
    const nameInput = qs("#leader_name");
    const name = (nameInput?.value || "").trim();
    status.hidden = true;
    if (!name) {
      status.hidden = false;
      status.className = "status error";
      status.textContent = "请填写领导姓名后再反查分管科室";
      return;
    }
    const btn = qs("#leader-lookup-btn");
    if (btn) btn.disabled = true;
    out.innerHTML = `<div class="status">反查中…</div>`;
    try {
      const data = await apiGet(`/api/leaders/${encodeURIComponent(name)}/departments`, {
        bureau_code: bureauParam(),
      });
      const items = data.items || [];
      if (!items.length) {
        out.innerHTML = `<section class="panel-card"><p class="muted">未找到「${escapeHtml(
          name
        )}」的分管科室（源站未公布或未采集）。</p></section>`;
        return;
      }
      const rows = items
        .map((it) => {
          const depts = (it.departments || []).join("、") || "—";
          const bureau = bureauLabel(it.bureau_code);
          const pid = it.id || it.person_id;
          const personLink = pid
            ? `<a href="/people/${encodeURIComponent(pid)}">${escapeHtml(it.name || name)}</a>`
            : escapeHtml(it.name || name);
          return `<li class="dept-hit">
            <div class="dept-hit-name">${personLink}</div>
            <div class="muted">${escapeHtml(bureau)} · ${escapeHtml(depts)}</div>
          </li>`;
        })
        .join("");
      out.innerHTML = `
        <section class="panel-card" aria-label="领导分管科室">
          <h2 class="results-group-title">「${escapeHtml(name)}」分管科室 · ${items.length}</h2>
          <ul class="dept-hit-list">${rows}</ul>
        </section>
      `;
    } catch (err) {
      out.innerHTML = "";
      status.hidden = false;
      status.className = "status error";
      status.textContent = err.message || String(err);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  form.addEventListener("submit", runQuery);
  qs("#reset-btn").addEventListener("click", resetForm);
  qs("#leader-lookup-btn")?.addEventListener("click", runLeaderLookup);
  levelSelect.addEventListener("change", () => onRegionFiltersChange().catch(() => {}));
  categorySelect.addEventListener("change", () => {
    rebuildBureauOptions();
    refreshDeptOptions().catch(() => {});
  });
  bureauSelect.addEventListener("change", () => refreshDeptOptions().catch(() => {}));

  Promise.all([loadLevels(), loadBureaus(), loadStaUnits()])
    .then(() => onRegionFiltersChange())
    .catch((err) => {
      status.hidden = false;
      status.className = "status error";
      status.textContent = `初始化失败：${err.message || err}`;
    });
})();
