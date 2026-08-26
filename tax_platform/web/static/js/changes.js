(() => {
  const form = qs("#changes-form");
  const rows = qs("#rows");
  const meta = qs("#meta");
  const status = qs("#status");
  const levelSelect = qs("#org_level");
  const categorySelect = qs("#region_category");
  const bureauSelect = qs("#bureau_code");
  const deptInput = qs("#department");
  const typeSelect = qs("#change_type");

  let allBureaus = [];
  let staUnits = [];

  function isHeadquartersLevel() {
    return levelSelect.value === "headquarters";
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
    const params = {
      org_level: levelSelect.value || undefined,
      department: deptInput.value.trim() || undefined,
      change_type: typeSelect.value || undefined,
      limit: 50,
    };
    if (isHeadquartersLevel()) {
      const unit = bureauSelect.value;
      if (unit === "sta" || (unit && unit !== "sta")) {
        params.bureau_code = "sta";
      }
    } else if (bureauSelect.value) {
      params.bureau_code = bureauSelect.value;
    }
    return params;
  }

  async function onRegionFiltersChange() {
    rebuildCategoryOptions();
    if (isHeadquartersLevel() && !staUnits.length) {
      await loadStaUnits();
    }
    rebuildBureauOptions();
  }

  async function load(event) {
    event?.preventDefault();
    status.hidden = true;
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
      // 地区分类：非具体局时，按站点分类过滤本页结果
      const cat = categorySelect.value;
      if (cat && !bureauSelect.value && !isHeadquartersLevel()) {
        items = items.filter((item) => {
          const site = allBureaus.find((b) => b.code === item.bureau_code);
          if (!site) return false;
          if (site.level === "province") return bureauCategory(site) === cat;
          if (site.level === "district" && site.parent_code) {
            const parent = allBureaus.find((b) => b.code === site.parent_code);
            return parent ? bureauCategory(parent) === cat : false;
          }
          return false;
        });
      }
      meta.textContent = `共 ${data.total ?? 0} 条${items.length !== (data.items || []).length ? `（本页筛选后 ${items.length} 条）` : ""}`;
      rows.innerHTML =
        items
          .map((item) => {
            const source = item.source_url
              ? `<a href="${escapeHtml(item.source_url)}" target="_blank" rel="noopener">原文</a>`
              : "";
            const personHref =
              item.bureau_code && item.person_name
                ? `/people/${encodeURIComponent(`${item.bureau_code}:${item.person_name}`)}`
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
    } catch (err) {
      status.hidden = false;
      status.className = "status error";
      status.textContent = err.message || String(err);
      meta.textContent = "加载失败";
    } finally {
      qs("#changes-btn").disabled = false;
    }
  }

  function resetForm() {
    form.reset();
    onRegionFiltersChange().then(load).catch(() => load());
  }

  form.addEventListener("submit", load);
  qs("#reset-btn").addEventListener("click", resetForm);
  levelSelect.addEventListener("change", () => onRegionFiltersChange().catch(() => {}));
  categorySelect.addEventListener("change", () => {
    rebuildBureauOptions();
  });

  Promise.all([loadLevels(), loadBureaus(), loadStaUnits()])
    .then(() => {
      rebuildCategoryOptions();
      rebuildBureauOptions();
      return load();
    })
    .catch((err) => {
      status.hidden = false;
      status.className = "status error";
      status.textContent = `初始化失败：${err.message || err}`;
    });
})();
