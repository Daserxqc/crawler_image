(() => {
  const form = qs("#search-form");
  const results = qs("#results");
  const meta = qs("#result-meta");
  const status = qs("#status");
  const pagination = qs("#pagination");
  const levelSelect = qs("#org_level");
  const categorySelect = qs("#region_category");
  const bureauSelect = qs("#bureau_code");
  const deptSelect = qs("#department");
  const titleSelect = qs("#title");
  const nameInput = qs("#name");
  const dateFromInput = qs("#date_from");
  const dateToInput = qs("#date_to");
  const watchBar = qs("#search-watch-bar");

  let allBureaus = [];
  let staUnits = [];
  let lastHits = [];
  let regionSeq = 0;
  let searchTotal = 0;
  let currentPage = 1;
  const PAGE_SIZE = 50;

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

  async function refreshSearchWatchBar() {
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

  function bureauParam() {
    if (!(isHeadquartersLevel() || isHqCategory())) return bureauSelect.value || undefined;
    const unit = bureauSelect.value;
    return unit === "sta" ? "sta" : undefined;
  }

  function filteredBureaus() {
    const level = levelSelect.value;
    return allBureaus
      .filter((site) => !level || site.level === level)
      .sort((a, b) =>
        bureauDisplayName(a, allBureaus).localeCompare(bureauDisplayName(b, allBureaus), "zh-CN")
      );
  }

  function filteredStaUnits() {
    const cat = categorySelect.value;
    if (!cat) return staUnits;
    return staUnits.filter((u) => u.category === cat);
  }

  function fillSelect(select, options, emptyLabel) {
    const prev = select.value;
    const html =
      `<option value="">${escapeHtml(emptyLabel)}</option>` +
      options
        .map((opt) => `<option value="${escapeHtml(opt.value)}">${escapeHtml(opt.label)}</option>`)
        .join("");
    select.innerHTML = html;
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

  async function loadStaUnits() {
    const data = await apiGet("/api/meta/units");
    staUnits = data.items || [];
  }

  function rebuildBureauOptions() {
    // 内设/直属/派出 are STA-only; keep the district dropdown on STA units.
    if (isHeadquartersLevel() || isHqCategory()) {
      const items = filteredStaUnits();
      fillSelect(
        bureauSelect,
        items.map((u) => ({ value: u.code, label: u.name })),
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
    const bureau = bureauParam();
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
      buildTitleSuggestOptions(titles.items || []).map((label) => ({ value: label, label })),
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

  // filterHitsByCategory removed — category is applied server-side.

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

  async function loadStats() {
    const strip = qs("#stats-strip");
    try {
      const stats = await apiGet("/api/meta/summary");
      renderStatsStrip(strip, stats);
    } catch {
      if (strip) strip.hidden = true;
    }
  }

  function renderCard(hit, index) {
    const current = hit.current || {};
    const levelTag = `<span class="tag tag-level">${escapeHtml(levelLabel(hit.org_level))}</span>`;
    const currentTag = current.is_current
      ? '<span class="tag tag-current">现任</span>'
      : '<span class="tag">非现任/未知</span>';
    const catTag = hit.category_label
      ? `<span class="tag tag-cat">${escapeHtml(hit.category_label)}</span>`
      : "";
    const count = hit.appointment_count || 0;
    const detailId = `person-detail-${index}`;
    return `
      <article class="person-card" role="listitem">
        <div class="person-card-head">
          <div class="person-card-title">
            <h3 class="person-name">
              <a href="/people/${encodeURIComponent(hit.id)}">${escapeHtml(hit.name)}</a>
            </h3>
            <div class="person-tags">${currentTag}${levelTag}${catTag}</div>
          </div>
          <button
            class="person-expand"
            type="button"
            aria-expanded="false"
            aria-controls="${detailId}"
            data-target="${detailId}"
            data-count="${escapeHtml(count)}"
          >任职记录 ${escapeHtml(count)} 条</button>
        </div>
        <div class="person-meta-row">
          <div class="person-meta-item">
            <span class="meta-k">任职单位</span>
            <span class="meta-v">${escapeHtml(hit.unit_display || "—")}</span>
          </div>
          <div class="person-meta-item">
            <span class="meta-k">所属科室</span>
            <span class="meta-v">${escapeHtml(
              current.department ||
                (hit.org_level === "headquarters" ? "本机关" : "—")
            )}</span>
          </div>
          <div class="person-meta-item">
            <span class="meta-k">现任职务</span>
            <span class="meta-v">${escapeHtml(hit.title_display || current.title || "—")}</span>
          </div>
          <div class="person-meta-item">
            <span class="meta-k">分管科室</span>
            <span class="meta-v">${escapeHtml(
              (hit.supervised_departments || current.departments || []).join("、") || "—"
            )}</span>
          </div>
          <div class="person-meta-item">
            <span class="meta-k">地区</span>
            <span class="meta-v">${escapeHtml(hit.region_display || "—")}</span>
          </div>
        </div>
        <div class="person-detail" id="${detailId}" hidden>
          ${renderAppointmentRows(hit.appointments || [])}
          <p><a href="/people/${encodeURIComponent(hit.id)}">查看完整履历 →</a></p>
        </div>
      </article>
    `;
  }

  function renderAppointmentRows(appointments) {
    if (!appointments?.length) {
      return '<p class="muted">暂无任免记录</p>';
    }
    return `<ul class="appointment-mini">
      ${appointments
        .map((ev) => {
          const source = ev.source_url
            ? `<a href="${escapeHtml(ev.source_url)}" target="_blank" rel="noopener">公告原文</a>`
            : "";
          const bits = [
            changeLabel(ev.action),
            ev.title_raw,
            ev.department_raw,
            ev.bureau_code && ev.bureau_code !== "sta" ? ev.bureau_code : null,
          ].filter((x) => {
            const s = String(x || "").trim();
            return s && s !== "—" && s !== "-" && s !== "–" && s !== "一";
          });
          return `<li>
            <time>${escapeHtml(ev.effective_on || "—")}</time>
            <span>${bits.map((b) => escapeHtml(b)).join(" · ") || "—"}</span>
            ${source}
          </li>`;
        })
        .join("")}
    </ul>`;
  }

  function bindExpanders() {
    qsa(".person-expand", results).forEach((btn) => {
      btn.addEventListener("click", () => {
        const panel = qs(`#${btn.dataset.target}`);
        const open = btn.getAttribute("aria-expanded") === "true";
        const count = btn.dataset.count || "0";
        btn.setAttribute("aria-expanded", open ? "false" : "true");
        panel.hidden = open;
        btn.textContent = open ? `任职记录 ${count} 条` : "收起";
      });
    });
  }

  function renderHits(items) {
    if (!items.length) {
      results.innerHTML =
        '<div class="results-empty" id="results-empty">没有匹配结果，请调整筛选条件后重试</div>';
      return;
    }
    const currentHits = items.filter((h) => h.current?.is_current);
    const otherHits = items.filter((h) => !h.current?.is_current);
    let html = "";
    let idx = 0;
    const renderGroup = (hits, title) => {
      if (!hits.length) return "";
      let block = `<h3 class="results-group-title">${escapeHtml(title)}（本页 ${hits.length} 人）</h3>`;
      let lastUnit = null;
      hits.forEach((hit) => {
        const unit = hit.unit_display || hit.region_display || "其他单位";
        if (unit !== lastUnit) {
          block += `<h4 class="results-unit-title">${escapeHtml(levelLabel(hit.org_level))} · ${escapeHtml(unit)}</h4>`;
          lastUnit = unit;
        }
        block += renderCard(hit, idx++);
      });
      return block;
    };
    html += renderGroup(currentHits, "现任匹配");
    html += renderGroup(otherHits, "其他匹配");
    results.innerHTML = html;
    bindExpanders();
  }

  function hasSearchCriteria(params) {
    return (
      [params.department, params.title, params.name, params.date_from, params.date_to].some(
        (v) => String(v || "").trim()
      ) ||
      [params.org_level, params.bureau_code, params.unit, params.unit_category].some(
        (v) => String(v || "").trim()
      )
    );
  }

  function searchParams(page = currentPage) {
    const hqMode = isHeadquartersLevel() || isHqCategory();
    const unit = hqMode ? bureauSelect.value : "";
    return {
      org_level: hqMode && !levelSelect.value ? "headquarters" : levelSelect.value,
      bureau_code: hqMode ? (unit === "sta" ? "sta" : "") : bureauSelect.value,
      unit: hqMode && unit && unit !== "sta" ? unit : unit === "sta" ? "sta" : "",
      unit_category: categorySelect.value,
      department: deptSelect.value,
      title: parseTitleQuery(titleSelect.value),
      name: nameInput.value.trim(),
      date_from: dateFromInput?.value || "",
      date_to: dateToInput?.value || "",
      limit: PAGE_SIZE,
      offset: (page - 1) * PAGE_SIZE,
    };
  }

  function totalPages(total = searchTotal) {
    return Math.max(1, Math.ceil(total / PAGE_SIZE));
  }

  function renderPagination(total, offset) {
    if (!pagination) return;
    if (!total || total <= PAGE_SIZE) {
      pagination.hidden = true;
      pagination.innerHTML = "";
      return;
    }
    const page = Math.floor(offset / PAGE_SIZE) + 1;
    const pages = totalPages(total);
    const prevDisabled = page <= 1;
    const nextDisabled = page >= pages;
    pagination.hidden = false;
    pagination.innerHTML = `
      <button class="btn secondary pager-btn" type="button" id="pager-first" ${
        prevDisabled ? "disabled" : ""
      }>首页</button>
      <button class="btn secondary pager-btn" type="button" id="pager-prev" ${
        prevDisabled ? "disabled" : ""
      }>上一页</button>
      <span class="pagination-info">第 ${page} / ${pages} 页（共 ${total} 人）</span>
      <label class="pagination-jump" for="pager-input">
        <span class="sr-only">跳转到页码</span>
        <input
          class="pager-input"
          id="pager-input"
          type="number"
          min="1"
          max="${pages}"
          value="${page}"
          inputmode="numeric"
          aria-label="页码"
        />
      </label>
      <button class="btn secondary pager-btn" type="button" id="pager-go">跳转</button>
      <button class="btn secondary pager-btn" type="button" id="pager-next" ${
        nextDisabled ? "disabled" : ""
      }>下一页</button>
      <button class="btn secondary pager-btn" type="button" id="pager-last" ${
        nextDisabled ? "disabled" : ""
      }>尾页</button>
    `;
    if (!prevDisabled) {
      qs("#pager-first", pagination).addEventListener("click", () => goToPage(1));
      qs("#pager-prev", pagination).addEventListener("click", () => goToPage(page - 1));
    }
    const jumpInput = qs("#pager-input", pagination);
    const jumpBtn = qs("#pager-go", pagination);
    const handleJump = () => {
      const target = Number.parseInt(jumpInput.value, 10);
      if (!Number.isFinite(target)) return;
      goToPage(Math.min(pages, Math.max(1, target)));
    };
    jumpBtn.addEventListener("click", handleJump);
    jumpInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        handleJump();
      }
    });
    if (!nextDisabled) {
      qs("#pager-next", pagination).addEventListener("click", () => goToPage(page + 1));
      qs("#pager-last", pagination).addEventListener("click", () => goToPage(pages));
    }
  }

  function clearPagination() {
    if (!pagination) return;
    pagination.hidden = true;
    pagination.innerHTML = "";
  }

  async function goToPage(page) {
    const pages = totalPages();
    if (page < 1 || page > pages) return;
    currentPage = page;
    await runSearch(null, page, { scroll: true });
  }

  async function runSearch(event, page = 1, options = {}) {
    event?.preventDefault();
    status.hidden = true;
    currentPage = page;
    const params = searchParams(page);
    qs("#search-btn").disabled = true;
    if (pagination) {
      qsa("button", pagination).forEach((btn) => {
        btn.disabled = true;
      });
    }
    meta.textContent = "检索中…";
    try {
      const data = await apiGet("/api/search", params);
      lastHits = data.items || [];
      searchTotal = data.total ?? 0;
      const currentCount = Number(data.current_count ?? 0);
      const pageNum = Math.floor((data.offset ?? 0) / PAGE_SIZE) + 1;
      meta.textContent = `共检索到 ${searchTotal} 位人员（第 ${pageNum} 页，本页 ${lastHits.length} 条）`;
      renderHits(lastHits);
      renderPagination(searchTotal, data.offset ?? 0);
      if (!lastHits.length) {
        status.hidden = false;
        status.className = "status";
        status.textContent = searchTotal
          ? "当前页没有结果，请翻页或调整筛选"
          : "没有匹配结果";
      } else {
        meta.textContent += ` · 现任 ${currentCount} 人`;
      }
      if (options.scroll) {
        qs(".results-section")?.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    } catch (err) {
      status.hidden = false;
      status.className = "status error";
      status.textContent = err.message || String(err);
      results.innerHTML = "";
      meta.textContent = "检索失败";
      clearPagination();
    } finally {
      qs("#search-btn").disabled = false;
    }
  }

  function resetForm() {
    form.reset();
    lastHits = [];
    searchTotal = 0;
    currentPage = 1;
    onRegionFiltersChange().catch(() => {});
    results.innerHTML =
      '<div class="results-empty" id="results-empty">全部留空 = 列出库内所有人员（分页）；也可先选地区/科室/职务/姓名缩小范围</div>';
    meta.textContent = "点击「查询」浏览人员，默认每页 50 条";
    status.hidden = true;
    clearPagination();
  }

  function exportSearch(fmt) {
    const params = new URLSearchParams();
    const map = { ...searchParams(), fmt, limit: 500 };
    Object.entries(map).forEach(([k, v]) => {
      if (String(v || "").trim()) params.set(k, v);
    });
    window.location.href = `/api/export/search?${params.toString()}`;
  }

  form.addEventListener("submit", runSearch);
  qs("#reset-btn").addEventListener("click", resetForm);
  qs("#export-btn").addEventListener("click", () => exportSearch("csv"));
  qs("#export-xlsx-btn")?.addEventListener("click", () => exportSearch("xlsx"));
  levelSelect.addEventListener("change", () => {
    onRegionFiltersChange()
      .then(refreshSearchWatchBar)
      .catch(() => {});
  });
  categorySelect.addEventListener("change", () => {
    rebuildBureauOptions();
    if (lastHits.length) renderHits(lastHits);
    refreshFilterOptions().catch(() => {});
    refreshSearchWatchBar().catch(() => {});
  });
  bureauSelect.addEventListener("change", () => {
    refreshFilterOptions().catch(() => {});
    refreshSearchWatchBar().catch(() => {});
  });

  const boot = new URLSearchParams(window.location.search);
  ["name", "department", "title", "org_level", "bureau_code", "date_from", "date_to"].forEach((key) => {
    if (boot.get(key) && qs(`#${key}`)) qs(`#${key}`).value = boot.get(key);
  });

  Promise.all([loadLevels(), loadBureaus(), loadStaUnits()])
    .then(refreshFilterOptions)
    .then(() => {
      loadStats().catch(() => {});
      refreshSearchWatchBar().catch(() => {});
      if (boot.get("name") || boot.get("department") || boot.get("title")) {
        runSearch();
      }
    })
    .catch((err) => {
      status.hidden = false;
      status.className = "status error";
      status.textContent = `初始化失败：${err.message || err}`;
    });
})();
