(() => {
  const form = qs("#posts-form");
  const listEl = qs("#posts-list");
  const metaEl = qs("#posts-meta");
  const statusEl = qs("#posts-status");
  const levelSelect = qs("#org_level");
  const categorySelect = qs("#region_category");
  const bureauSelect = qs("#bureau_code");
  const deptSelect = qs("#department");
  const titleSelect = qs("#title");
  const params = new URLSearchParams(window.location.search);

  let allBureaus = [];
  let staUnits = [];
  let regionSeq = 0;
  let offset = 0;
  const limit = 50;

  function isHeadquartersLevel() {
    return levelSelect.value === "headquarters";
  }

  function isHqCategory() {
    return HQ_CATEGORIES.includes(categorySelect.value);
  }

  function fillSelect(select, options, emptyLabel) {
    const prev = select.value;
    select.innerHTML =
      `<option value="">${escapeHtml(emptyLabel)}</option>` +
      options
        .map((opt) => `<option value="${escapeHtml(opt.value)}">${escapeHtml(opt.label)}</option>`)
        .join("");
    select.value = options.some((opt) => opt.value === prev) ? prev : "";
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
    // 内设/直属/派出 are STA-only; keep the district dropdown on STA units.
    if (isHeadquartersLevel() || isHqCategory()) {
      fillSelect(
        bureauSelect,
        filteredStaUnits().map((u) => ({ value: u.code, label: u.name })),
        "全部地区"
      );
      return;
    }
    let items = filteredBureaus();
    const cat = categorySelect.value;
    if (PROVINCE_CATEGORIES.includes(cat)) {
      items = items.filter((site) => {
        if (bureauCategory(site) === cat) return true;
        if (!site.parent_code) return false;
        const parent = allBureaus.find((b) => b.code === site.parent_code);
        return parent ? bureauCategory(parent) === cat : false;
      });
    }
    fillSelect(
      bureauSelect,
      items.map((site) => ({
        value: site.code,
        label: bureauDisplayName(site, allBureaus),
      })),
      "全部地区"
    );
  }

  async function refreshFilterOptions() {
    const level = levelSelect.value || undefined;
    const [depts, titles] = await Promise.all([
      apiGet("/api/departments/suggest", { org_level: level, limit: 200 }),
      apiGet("/api/titles/suggest", { org_level: level, limit: 200 }),
    ]);
    fillSelect(
      deptSelect,
      (depts.items || [])
        .map((d) => ({
          value: d.canonical_name || d.name || "",
          label: d.canonical_name || d.name || "",
        }))
        .filter((d) => d.value),
      "全部科室"
    );
    fillSelect(
      titleSelect,
      (titles.items || [])
        .map((t) => ({
          value: t.canonical_title || t.title || "",
          label: t.display_label || t.canonical_title || t.title || "",
        }))
        .filter((t) => t.value)
        .filter((t, idx, arr) => arr.findIndex((x) => x.value === t.value && x.label === t.label) === idx),
      "全部职务"
    );
  }

  async function onRegionFiltersChange() {
    const seq = ++regionSeq;
    rebuildCategoryOptions();
    if ((isHeadquartersLevel() || isHqCategory()) && !staUnits.length) {
      await loadStaUnits();
    }
    if (seq !== regionSeq) return;
    rebuildBureauOptions();
    await refreshFilterOptions();
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

  function resolveBureauCode() {
    if (isHeadquartersLevel() || isHqCategory()) {
      const unit = bureauSelect.value;
      if (unit) return "sta";
      return null;
    }
    return bureauSelect.value || null;
  }

  function currentSearchQuery() {
    const u = new URLSearchParams();
    if (levelSelect.value) u.set("org_level", levelSelect.value);
    if (categorySelect.value) u.set("unit_category", categorySelect.value);
    if (bureauSelect.value) u.set("bureau_code", bureauSelect.value);
    if (deptSelect.value) u.set("department", deptSelect.value);
    if (titleSelect.value) u.set("title", titleSelect.value);
    if (offset > 0) u.set("offset", String(offset));
    return u.toString();
  }

  function archiveHref(bureauCode, department, title) {
    const url = new URL("/posts/view", window.location.origin);
    url.searchParams.set("bureau_code", bureauCode);
    url.searchParams.set("department", department);
    if (title) url.searchParams.set("title", title);
    const ret = currentSearchQuery();
    if (ret) url.searchParams.set("ret", ret);
    return `${url.pathname}${url.search}`;
  }

  async function searchPosts(event) {
    event?.preventDefault();
    statusEl.hidden = true;
    if (event?.type === "submit") offset = 0;
    const department = deptSelect.value.trim();
    const title = titleSelect.value.trim();
    const hqMode = isHeadquartersLevel() || isHqCategory();
    const bureauCode = resolveBureauCode();
    const orgLevel = hqMode
      ? levelSelect.value || "headquarters"
      : levelSelect.value || undefined;
    const unitCategory = categorySelect.value || undefined;

    if (!department && !title && !bureauCode && !unitCategory) {
      statusEl.hidden = false;
      statusEl.className = "status error";
      statusEl.textContent = "请至少选择科室、职务、地区分类，或具体地区";
      mountSimplePager(qs("#posts-pager"), { total: 0, offset: 0, limit, onPage: () => {} });
      return;
    }

    qs("#posts-btn").disabled = true;
    listEl.innerHTML = `<div class="status">检索中…</div>`;

    try {
      const data = await apiGet("/api/posts/search", {
        department: department || undefined,
        title: title || undefined,
        bureau_code: bureauCode || undefined,
        org_level: bureauCode ? undefined : orgLevel,
        unit_category: unitCategory,
        limit,
        offset,
      });
      const items = data.items || [];
      const total = data.total ?? 0;
      metaEl.textContent = `共 ${total} 个岗位${
        items.length < total ? `（本页 ${items.length}）` : ""
      }`;

      if (!items.length) {
        listEl.innerHTML = `<div class="results-empty">未找到岗位，请放宽科室/职务条件</div>`;
        mountSimplePager(qs("#posts-pager"), { total: 0, offset: 0, limit, onPage: () => {} });
        return;
      }

      // Only auto-open when the whole result set is a single post.
      if (total === 1 && items.length === 1 && offset === 0) {
        const only = items[0];
        window.location.href = archiveHref(only.bureau_code, only.department, only.title || title);
        return;
      }

      // 已选科室+职务（跨地区扫同一岗）→ 地区作主标题；否则科室职务作主标题
      const emphasizePlace = Boolean(department && title);

      listEl.innerHTML = items
        .map((item) => {
          const postTitle = item.title ? item.title : "（职务未写明）";
          const postLabel = `${item.department} · ${postTitle}`;
          const placeLabel = `${bureauLabel(item.bureau_code)}${
            item.region && item.region !== bureauLabel(item.bureau_code)
              ? ` · ${item.region}`
              : ""
          }`;
          const primary = emphasizePlace ? placeLabel : postLabel;
          const secondary = emphasizePlace ? postLabel : placeLabel;
          const href = archiveHref(item.bureau_code, item.department, item.title || "");
          return `
          <div class="post-row">
            <div class="post-row-main">
              <strong>${escapeHtml(primary)}</strong>
              <div class="muted">${escapeHtml(secondary)}</div>
            </div>
            <div class="post-row-stats">
              <span>现任 ${item.incumbent_count ?? 0}</span>
              <span>历任 ${item.past_count ?? 0}</span>
            </div>
            <div class="post-row-action">
              <a class="btn secondary" href="${escapeHtml(href)}">查看档案</a>
            </div>
          </div>`;
        })
        .join("");

      mountSimplePager(qs("#posts-pager"), {
        total,
        offset,
        limit,
        unitLabel: "个岗位",
        onPage: (nextOffset) => {
          offset = nextOffset;
          searchPosts().catch(() => {});
        },
      });
    } catch (err) {
      listEl.innerHTML = "";
      statusEl.hidden = false;
      statusEl.className = "status error";
      const msg = err.message || String(err);
      statusEl.textContent =
        msg === "Not Found"
          ? "接口未找到：请重启 API 服务后再试（python scripts/run_api.py）"
          : msg;
      metaEl.textContent = "检索失败";
      mountSimplePager(qs("#posts-pager"), { total: 0, offset: 0, limit, onPage: () => {} });
    } finally {
      qs("#posts-btn").disabled = false;
    }
  }

  function resetForm() {
    form.reset();
    offset = 0;
    onRegionFiltersChange()
      .then(() => {
        listEl.innerHTML = "";
        metaEl.textContent = "选择科室、职务或具体地区后检索。";
        statusEl.hidden = true;
        mountSimplePager(qs("#posts-pager"), { total: 0, offset: 0, limit, onPage: () => {} });
        const url = new URL(window.location.href);
        url.search = "";
        window.history.replaceState({}, "", url);
      })
      .catch(() => {});
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
  }

  async function loadStaUnits() {
    const data = await apiGet("/api/meta/units");
    staUnits = data.items || [];
  }

  async function applyUrlParams() {
    const level = params.get("org_level");
    if (level) levelSelect.value = level;
    const code = params.get("bureau_code");
    if (code) {
      const site = allBureaus.find((b) => b.code === code);
      if (site) {
        if (!level) levelSelect.value = site.level || "";
        await onRegionFiltersChange();
        const cat = params.get("unit_category") || params.get("region_category");
        if (cat) {
          categorySelect.value = cat;
          rebuildBureauOptions();
        }
        bureauSelect.value = code;
      } else if (code === "sta") {
        levelSelect.value = "headquarters";
        await onRegionFiltersChange();
        bureauSelect.value = "sta";
      } else {
        await onRegionFiltersChange();
      }
    } else {
      await onRegionFiltersChange();
      const cat = params.get("unit_category") || params.get("region_category");
      if (cat) {
        categorySelect.value = cat;
        rebuildBureauOptions();
      }
    }
    if (params.get("department")) {
      const dept = params.get("department");
      if (![...deptSelect.options].some((o) => o.value === dept)) {
        const opt = document.createElement("option");
        opt.value = dept;
        opt.textContent = dept;
        deptSelect.appendChild(opt);
      }
      deptSelect.value = dept;
    }
    if (params.get("title")) {
      const title = params.get("title");
      if (![...titleSelect.options].some((o) => o.value === title)) {
        const opt = document.createElement("option");
        opt.value = title;
        opt.textContent = title;
        titleSelect.appendChild(opt);
      }
      titleSelect.value = title;
    }
    const off = Number(params.get("offset") || "0");
    if (Number.isFinite(off) && off > 0) offset = off;
  }

  form.addEventListener("submit", (e) => searchPosts(e).catch(() => {}));
  qs("#reset-btn").addEventListener("click", resetForm);
  levelSelect.addEventListener("change", () => onRegionFiltersChange().catch(() => {}));
  categorySelect.addEventListener("change", () => {
    rebuildBureauOptions();
  });

  // 旧深链 /posts?bureau&department → 档案页
  if (params.get("view") === "1" || (params.get("bureau_code") && params.get("department") && params.get("archive") === "1")) {
    window.location.replace(
      archiveHref(params.get("bureau_code"), params.get("department"), params.get("title") || "")
    );
  } else {
    Promise.all([loadLevels(), loadBureaus(), loadStaUnits()])
      .then(async () => {
        await applyUrlParams();
        if (params.get("department") || params.get("title") || params.get("bureau_code")) {
          return searchPosts();
        }
        return null;
      })
      .catch(() => {});
  }
})();
