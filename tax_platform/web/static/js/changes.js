(() => {
  const form = qs("#changes-form");
  const rows = qs("#rows");
  const meta = qs("#meta");
  const status = qs("#status");
  const levelSelect = qs("#org_level");
  const categorySelect = qs("#region_category");
  const bureauSelect = qs("#bureau_code");
  const deptSelect = qs("#department");
  const typeSelect = qs("#change_type");
  const watchOnly = qs("#watch_only");
  const watchHint = qs("#watch-only-hint");
  const watchBar = qs("#changes-watch-bar");

  let allBureaus = [];
  let staUnits = [];
  let userWatches = [];
  let regionSeq = 0;
  let offset = 0;
  const limit = 50;

  function isHeadquartersLevel() {
    return levelSelect.value === "headquarters";
  }

  function isHqCategory() {
    return HQ_CATEGORIES.includes(categorySelect.value);
  }

  function selectedBureauForWatch() {
    if (isHeadquartersLevel() || isHqCategory()) {
      const unit = bureauSelect.value;
      if (!unit) return null;
      return { code: "sta", label: "国家税务总局" };
    }
    const code = bureauSelect.value;
    if (!code) return null;
    const site = allBureaus.find((b) => b.code === code);
    return {
      code,
      label: site ? bureauDisplayName(site, allBureaus) : code,
    };
  }

  async function refreshChangesWatchBar() {
    if (!watchBar) return;
    watchBar.innerHTML = "";
    const bureau = selectedBureauForWatch();
    if (!bureau) {
      watchBar.hidden = true;
      return;
    }
    watchBar.hidden = false;
    watchBar.innerHTML = `<span class="watch-bar-label">当前地区：${escapeHtml(bureau.label)}</span>`;
    const slot = document.createElement("span");
    watchBar.appendChild(slot);
    await mountWatchButton(slot, {
      target_type: "bureau",
      target_id: bureau.code,
      label: bureau.label,
      idleText: "关注此单位",
      activeText: "已关注单位",
      loginText: "登录后关注单位",
    });
  }

  async function refreshWatchOnlyHint() {
    if (!watchHint) return;
    const auth = await fetchAuthState();
    if (!auth.authenticated) {
      watchHint.textContent = "（需登录）";
      if (watchOnly) watchOnly.disabled = false;
      return;
    }
    userWatches = await fetchUserWatches();
    watchHint.textContent = userWatches.length
      ? `（已关注 ${userWatches.length} 项）`
      : "（暂无关注，请先在人员/单位页添加）";
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
        // 区县：用上级省局分类（如上海市 → 直辖市）
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
    if (isHeadquartersLevel() || isHqCategory()) {
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

  async function refreshDeptOptions() {
    const level = levelSelect.value || undefined;
    try {
      const data = await apiGet("/api/departments/suggest", {
        org_level: level,
        limit: 200,
      });
      fillSelect(
        deptSelect,
        (data.items || [])
          .map((d) => ({
            value: d.canonical_name || d.name || "",
            label: d.canonical_name || d.name || "",
          }))
          .filter((d) => d.value),
        "全部科室"
      );
    } catch (err) {
      console.warn("科室下拉加载失败", err);
      fillSelect(deptSelect, [], "全部科室");
    }
  }

  async function onRegionFiltersChange() {
    const seq = ++regionSeq;
    rebuildCategoryOptions();
    if ((isHeadquartersLevel() || isHqCategory()) && !staUnits.length) {
      await loadStaUnits();
    }
    if (seq !== regionSeq) return;
    rebuildBureauOptions();
    await refreshDeptOptions();
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

  function bureauLabel(code) {
    if (!code) return "—";
    if (code === "sta" || String(code).startsWith("sta:")) {
      const unit = staUnits.find((u) => u.code === code);
      return unit ? unit.name : code === "sta" ? "总局" : code;
    }
    const site = allBureaus.find((b) => b.code === code);
    return site ? bureauDisplayName(site, allBureaus) : code;
  }

  function queryParams() {
    const hqMode = isHeadquartersLevel() || isHqCategory();
    const params = {
      org_level: hqMode && !levelSelect.value ? "headquarters" : levelSelect.value || undefined,
      unit_category: categorySelect.value || undefined,
      department: deptSelect.value.trim() || undefined,
      change_type: typeSelect.value || undefined,
      limit,
      offset,
    };
    if (hqMode) {
      const unit = bureauSelect.value;
      if (unit === "sta" || (unit && unit !== "sta")) {
        params.bureau_code = "sta";
      }
    } else if (bureauSelect.value) {
      params.bureau_code = bureauSelect.value;
    }
    return params;
  }

  async function load(event) {
    event?.preventDefault();
    status.hidden = true;
    if (event?.type === "submit") offset = 0;
    qs("#changes-btn").disabled = true;
    meta.textContent = "加载中…";
    try {
      const data = await apiGet("/api/changes", queryParams());
      let items = data.items || [];
      // 总局选了具体司局时，用科室名再收窄（变动流无独立 unit 参数）
      if (isHeadquartersLevel()) {
        const unit = bureauSelect.value;
        if (unit && unit !== "sta") {
          const unitMeta = staUnits.find((u) => u.code === unit);
          const hint = unitMeta?.name || "";
          if (hint) {
            items = items.filter((item) => {
              const blob = `${item.department || ""}${item.department_raw || ""}${item.title || ""}${item.title_raw || ""}${item.bureau_name || ""}`;
              return blob.includes(hint.replace(/司$|中心$|报社$|出版社$|杂志社$/, "")) || blob.includes(hint);
            });
          }
        }
      }
      if (watchOnly?.checked) {
        const auth = await fetchAuthState();
        if (!auth.authenticated) {
          status.hidden = false;
          status.className = "status error";
          status.textContent = "请先登录后再使用「仅看我的关注」";
          rows.innerHTML = "";
          meta.textContent = "需要登录";
          mountSimplePager(qs("#changes-pager"), { total: 0, offset: 0, limit, onPage: () => {} });
          return;
        }
        userWatches = await fetchUserWatches(true);
        if (!userWatches.length) {
          items = [];
        } else {
          items = items.filter((item) => changeItemMatchesWatches(item, userWatches));
        }
      }
      const watchNote = watchOnly?.checked ? " · 仅关注" : "";
      const total = data.total ?? 0;
      const pageNote =
        total > items.length && !watchOnly?.checked
          ? `（本页 ${items.length} 条）`
          : watchOnly?.checked && items.length !== (data.items || []).length
            ? `（关注筛选后 ${items.length} 条）`
            : "";
      meta.textContent = `共 ${total} 条${pageNote}${watchNote}`;
      writeListQuery({
        org_level: levelSelect.value,
        unit_category: categorySelect.value,
        bureau_code: bureauSelect.value,
        department: qs("#department")?.value,
        change_type: qs("#change_type")?.value,
        q: qs("#q")?.value,
        offset: offset > 0 ? String(offset) : "",
        watch_only: watchOnly?.checked ? "1" : "",
      });
      rows.innerHTML =
        items
          .map((item) => {
            const source = item.source_url ? noticeSourceLinks(item.source_url) : "";
            const personHref =
              item.bureau_code && item.person_name
                ? personProfileHref(`${item.bureau_code}:${item.person_name}`)
                : null;
            const name = personHref
              ? `<a href="${personHref}">${escapeHtml(item.person_name)}</a>`
              : escapeHtml(item.person_name || "—");
            return `
            <div class="change-row">
              <div>${escapeHtml(item.effective_on || "—")}</div>
              <div>${escapeHtml(changeLabel(item.change_type || item.action))}</div>
              <div>${name}<div class="muted">${escapeHtml(bureauLabel(item.bureau_code))}</div></div>
              <div>${escapeHtml(item.title || item.title_raw || "—")} · ${escapeHtml(
                item.department || item.department_raw || "—"
              )}<div class="muted">${source}</div></div>
            </div>
          `;
          })
          .join("") || `<div class="results-empty">暂无变动，请调整筛选后重试</div>`;

      // Client-side watch filter can't page server totals — hide pager when narrowed.
      mountSimplePager(qs("#changes-pager"), {
        total: watchOnly?.checked ? 0 : total,
        offset,
        limit,
        unitLabel: "条",
        onPage: (nextOffset) => {
          offset = nextOffset;
          load().catch(() => {});
        },
      });
    } catch (err) {
      status.hidden = false;
      status.className = "status error";
      status.textContent = err.message || String(err);
      meta.textContent = "加载失败";
      mountSimplePager(qs("#changes-pager"), { total: 0, offset: 0, limit, onPage: () => {} });
    } finally {
      qs("#changes-btn").disabled = false;
    }
  }

  function resetForm() {
    form.reset();
    offset = 0;
    clearListQuery();
    onRegionFiltersChange().then(load).catch(() => load());
  }

  form.addEventListener("submit", load);
  qs("#reset-btn").addEventListener("click", resetForm);
  watchOnly?.addEventListener("change", () => {
    offset = 0;
    load().catch(() => {});
  });
  levelSelect.addEventListener("change", () => {
    offset = 0;
    onRegionFiltersChange()
      .then(refreshChangesWatchBar)
      .then(load)
      .catch(() => {});
  });
  categorySelect.addEventListener("change", () => {
    offset = 0;
    rebuildBureauOptions();
    refreshDeptOptions().catch(() => {});
    refreshChangesWatchBar().catch(() => {});
    load().catch(() => {});
  });
  bureauSelect.addEventListener("change", () => {
    offset = 0;
    refreshDeptOptions().catch(() => {});
    refreshChangesWatchBar().catch(() => {});
    load().catch(() => {});
  });

  Promise.all([loadLevels(), loadBureaus(), loadStaUnits()])
    .then(async () => {
      const boot = readListRestore("/changes");
      if (boot.get("org_level")) setSelectValue(levelSelect, boot.get("org_level"));
      await onRegionFiltersChange();
      if (boot.get("unit_category")) {
        setSelectValue(categorySelect, boot.get("unit_category"));
        rebuildBureauOptions();
      }
      if (boot.get("bureau_code")) setSelectValue(bureauSelect, boot.get("bureau_code"));
      if (boot.get("department") && qs("#department")) {
        setSelectValue(qs("#department"), boot.get("department"));
      }
      if (boot.get("change_type") && qs("#change_type")) {
        setSelectValue(qs("#change_type"), boot.get("change_type"));
      }
      if (boot.get("q") && qs("#q")) qs("#q").value = boot.get("q");
      if (boot.get("watch_only") === "1" && watchOnly) watchOnly.checked = true;
      const off = Number.parseInt(boot.get("offset") || "0", 10);
      if (Number.isFinite(off) && off > 0) offset = off;
      return Promise.all([refreshWatchOnlyHint(), refreshChangesWatchBar(), load()]);
    })
    .catch((err) => {
      status.hidden = false;
      status.className = "status error";
      status.textContent = `初始化失败：${err.message || err}`;
    });
})();
