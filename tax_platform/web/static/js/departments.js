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
      link.href = `/posts?bureau_code=${encodeURIComponent(bureauCode)}&department=${encodeURIComponent(
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

  async function runQuery(event) {
    event?.preventDefault();
    status.hidden = true;
    const department = deptSelect.value.trim();
    qs("#dept-btn").disabled = true;
    out.innerHTML = `<div class="status">查询中…</div>`;
    try {
      const data = await apiGet("/api/departments/penetrate", {
        department: department || undefined,
        org_level: levelSelect.value || undefined,
        bureau_code: bureauParam(),
      });
      const leaders = (data.supervising_leaders || [])
        .map((l) => {
          const id = l.id || (l.bureau_code && l.name ? `${l.bureau_code}:${l.name}` : null);
          const label = l.name || l.person_name || "—";
          const name = id
            ? `<a href="/people/${encodeURIComponent(id)}">${escapeHtml(label)}</a>`
            : escapeHtml(label);
          return `<li>${name} <span class="muted">${escapeHtml(l.title_raw || "")} · ${escapeHtml(
            bureauLabel(l.bureau_code)
          )}</span></li>`;
        })
        .join("");
      const staff = (data.staff || [])
        .map((s) => {
          return `<li><a href="/people/${encodeURIComponent(s.id)}">${escapeHtml(s.name)}</a>
            <span class="muted">${escapeHtml(formatCurrent(s.current))} · ${escapeHtml(
              bureauLabel(s.bureau_code)
            )}</span></li>`;
        })
        .join("");
      const upward = (data.upward || [])
        .map((u) => {
          const lead = u.leader || {};
          const id = lead.id;
          const label = lead.name || "—";
          const name = id
            ? `<a href="/people/${encodeURIComponent(id)}">${escapeHtml(label)}</a>`
            : escapeHtml(label);
          return `<li>${escapeHtml(levelLabel(u.org_level))} · ${escapeHtml(bureauLabel(u.bureau_code))} · ${name}
            <span class="muted">${escapeHtml(u.region || "")}${
              u.parent_bureau_code ? ` · 上级 ${escapeHtml(bureauLabel(u.parent_bureau_code))}` : ""
            }</span></li>`;
        })
        .join("");
      const deptLabel = data.browse_all ? "全部科室" : data.department || department || "全部";
      lastBureauCode = bureauParam() || bureauSelect.value || null;
      lastDepartment = data.browse_all ? null : deptLabel;
      await refreshDeptWatchBar(lastBureauCode, lastDepartment);
      out.innerHTML = `
        <div class="results-summary">科室：${escapeHtml(deptLabel)}
          · 分管 ${escapeHtml(data.supervisor_count ?? 0)} · 任职 ${escapeHtml(data.staff_count ?? 0)}${
            data.browse_all ? "（本页最多 100 条）" : ""
          }</div>
        <h2 class="results-group-title">分管领导</h2>
        <ul>${
          leaders ||
          (data.browse_all
            ? "<li class='muted'>浏览全体时不按科室聚合分管；选定科室后可查看分管领导</li>"
            : "<li class='muted'>暂无（源站未公布或未采集到分管）</li>")
        }</ul>
        <h2 class="results-group-title">任职人员</h2>
        <ul>${staff || "<li class='muted'>暂无匹配任职</li>"}</ul>
        ${
          !data.browse_all && lastBureauCode && lastDepartment
            ? `<p><a class="btn secondary" href="/posts?bureau_code=${encodeURIComponent(
                lastBureauCode
              )}&department=${encodeURIComponent(lastDepartment)}">查看该科室岗位档案 →</a></p>`
            : ""
        }
        ${upward ? `<h2 class="results-group-title">层级穿透</h2><ul>${upward}</ul>` : ""}
      `;
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
    out.innerHTML = "";
    status.hidden = true;
    onRegionFiltersChange().catch(() => {});
  }

  form.addEventListener("submit", runQuery);
  qs("#reset-btn").addEventListener("click", resetForm);
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
