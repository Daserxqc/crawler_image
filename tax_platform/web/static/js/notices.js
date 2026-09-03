(() => {
  const form = qs("#notices-form");
  const rows = qs("#rows");
  const meta = qs("#meta");
  const status = qs("#status");
  const levelSelect = qs("#org_level");
  const categorySelect = qs("#region_category");
  const bureauSelect = qs("#bureau_code");
  const rangeSelect = qs("#date_range");
  const qInput = qs("#q");

  let allBureaus = [];
  let staUnits = [];
  let offset = 0;
  const limit = 50;

  const RANGE_LABELS = {
    "30": "近 30 天",
    "90": "近 90 天",
    "180": "近半年",
    "365": "近一年",
  };

  function isHeadquartersLevel() {
    return levelSelect.value === "headquarters";
  }

  function isHqCategory() {
    return HQ_CATEGORIES.includes(categorySelect.value);
  }

  function dateFromForRange() {
    const days = Number(rangeSelect.value);
    if (!Number.isFinite(days) || days <= 0) return undefined;
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    d.setDate(d.getDate() - days);
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  }

  function rangeLabel() {
    return RANGE_LABELS[rangeSelect.value] || "全部时间";
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

  function resolveBureauCode() {
    if (isHeadquartersLevel() || isHqCategory()) {
      return bureauSelect.value ? "sta" : null;
    }
    return bureauSelect.value || null;
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

  function cleanTitle(raw) {
    return String(raw || "")
      .replace(/<br\s*\/?>/gi, "")
      .replace(/<[^>]+>/g, "")
      .replace(/【\s*字体\s*[：:][^】]*】/g, "")
      .replace(/打印本页|关闭本页/g, "")
      .replace(/[\s\u3000]*[（(]\s*(?:20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|20\d{2}\s*年\s*\d{1,2}\s*月|\d{1,2}\s*月\s*\d{1,2}\s*日|20\d{2}-\d{1,2}-\d{1,2})\s*[）)]\s*$/g, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function cleanDocNo(raw) {
    const text = String(raw || "").replace(/\s+/g, "").trim();
    if (!text) return "";
    const match = text.match(/[\u4e00-\u9fa5A-Za-z0-9、]{1,24}[〔\[]\d{4}[〕\]]\d+号/);
    if (match) return match[0];
    if (
      text.length > 40 ||
      /字号|打印|微信|扫一扫|分享|发布[日时]|发文[日时机]|来源|有效性|\[大\]|任免工作人员/.test(text)
    ) {
      return "";
    }
    return /号$/.test(text) ? text : "";
  }

  function placeLabel(item) {
    const bureau = bureauLabel(item.bureau_code);
    const region = String(item.region || "").trim();
    if (!region || region === bureau || bureau.includes(region) || region.includes(bureau.replace(/税务局$/, ""))) {
      return bureau;
    }
    return `${bureau} · ${region}`;
  }

  function showBootError(err) {
    rows.innerHTML = "";
    status.hidden = false;
    status.className = "status error";
    status.textContent = err?.message || String(err) || "加载失败";
    meta.textContent = "加载失败";
    qs("#notices-btn").disabled = false;
  }

  async function load(event) {
    event?.preventDefault();
    status.hidden = true;
    qs("#notices-btn").disabled = true;
    meta.textContent = "加载中…";
    // Only reset on form submit — pager calls load() without an event.
    if (event?.type === "submit") offset = 0;

    try {
      const data = await apiGet("/api/notices", {
        org_level:
          isHeadquartersLevel() || isHqCategory()
            ? levelSelect.value || "headquarters"
            : levelSelect.value || undefined,
        bureau_code: resolveBureauCode() || undefined,
        unit_category: categorySelect.value || undefined,
        q: qInput.value.trim() || undefined,
        date_from: dateFromForRange(),
        limit,
        offset,
      });
      const items = data.items || [];
      const total = data.total ?? 0;
      const scope = rangeLabel();
      meta.textContent = `${scope}共 ${total} 条公告${
        items.length < total ? `（本页 ${items.length}）` : ""
      }`;

      rows.innerHTML =
        items
          .map((item) => {
            const date = item.sort_date || item.issued_on || (item.published_at || "").slice(0, 10) || "—";
            const source = item.source_url
              ? `<a href="${escapeHtml(item.source_url)}" target="_blank" rel="noopener">原文</a>`
              : "";
            const place = placeLabel(item);
            const docNo = cleanDocNo(item.doc_no);
            return `
            <div class="notice-row">
              <div class="notice-row-date">${escapeHtml(date)}</div>
              <div class="notice-row-main">
                <strong>${escapeHtml(cleanTitle(item.title) || "—")}</strong>
                <div class="muted">${escapeHtml(place)}
                  ${docNo ? ` · ${escapeHtml(docNo)}` : ""}
                  · 解析 ${item.event_count ?? 0} 条任免
                  ${source ? ` · ${source}` : ""}
                </div>
              </div>
            </div>`;
          })
          .join("") || `<div class="results-empty">该时间范围内暂无公告，可扩大时间范围或换地区再试。</div>`;

      mountSimplePager(qs("#notices-pager"), {
        total,
        offset,
        limit,
        unitLabel: "条",
        onPage: (nextOffset) => {
          offset = nextOffset;
          load().catch(() => {});
        },
      });
    } catch (err) {
      rows.innerHTML = "";
      status.hidden = false;
      status.className = "status error";
      status.textContent = err.message || String(err);
      meta.textContent = "加载失败";
      mountSimplePager(qs("#notices-pager"), { total: 0, offset: 0, limit, onPage: () => {} });
    } finally {
      qs("#notices-btn").disabled = false;
    }
  }

  function resetForm() {
    form.reset();
    rebuildCategoryOptions();
    rebuildBureauOptions();
    offset = 0;
    load().catch(() => {});
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

  form.addEventListener("submit", (e) => load(e).catch(() => {}));
  qs("#reset-btn").addEventListener("click", resetForm);
  levelSelect.addEventListener("change", () => {
    rebuildCategoryOptions();
    rebuildBureauOptions();
  });
  categorySelect.addEventListener("change", rebuildBureauOptions);
  rangeSelect.addEventListener("change", () => {
    offset = 0;
    load().catch(() => {});
  });

  Promise.all([
    loadLevels(),
    apiGet("/api/meta/bureaus").then((d) => {
      allBureaus = d.items || [];
    }),
    apiGet("/api/meta/units").then((d) => {
      staUnits = d.items || [];
    }),
  ])
    .then(() => {
      rebuildCategoryOptions();
      rebuildBureauOptions();
      return load();
    })
    .catch(showBootError);
})();
