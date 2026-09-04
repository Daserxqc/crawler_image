(() => {
  const form = qs("#regions-form");
  const levelSelect = qs("#org_level");
  const qInput = qs("#q");
  const sortSelect = qs("#sort");
  const body = qs("#regions-body");
  const meta = qs("#regions-meta");
  const status = qs("#regions-status");

  let allBureaus = [];

  const depthForLevel = (level) =>
    ({ headquarters: 0, province: 1, city: 2, district: 3 }[level] ?? 0);

  const compareZh = (a, b) =>
    String(a).localeCompare(String(b), "zh-CN", { numeric: true });

  const flattenTree = (items) => {
    const byCode = new Map(items.map((item) => [item.code, item]));
    const children = new Map();
    for (const item of items) {
      const parentOk = item.parent_code && byCode.has(item.parent_code);
      const key = parentOk ? item.parent_code : "__root__";
      if (!children.has(key)) children.set(key, []);
      children.get(key).push(item);
    }
    for (const list of children.values()) {
      list.sort((a, b) => {
        const ld = depthForLevel(a.level) - depthForLevel(b.level);
        if (ld) return ld;
        return compareZh(shortBureauName(a, items), shortBureauName(b, items));
      });
    }
    const out = [];
    const walk = (code, depth) => {
      for (const child of children.get(code) || []) {
        out.push({ item: child, depth });
        walk(child.code, depth + 1);
      }
    };
    walk("__root__", 0);
    return out;
  };

  const filterItems = (items) => {
    const level = levelSelect.value;
    const q = String(qInput.value || "").trim().toLowerCase();
    return items.filter((item) => {
      if (level && item.level !== level) return false;
      if (q) {
        const name = String(item.name || "").toLowerCase();
        const short = shortBureauName(item, allBureaus).toLowerCase();
        const region = String(item.region || "").toLowerCase();
        const code = String(item.code || "").toLowerCase();
        if (![name, short, region, code].some((s) => s.includes(q))) return false;
      }
      return true;
    });
  };

  const buildRows = (items) => {
    const sort = sortSelect.value;
    if (sort === "tree") return flattenTree(items);
    const rows = items.map((item) => ({
      item,
      depth: depthForLevel(item.level),
    }));
    if (sort === "name") {
      rows.sort((a, b) =>
        compareZh(shortBureauName(a.item, allBureaus), shortBureauName(b.item, allBureaus))
      );
    } else if (sort === "date_desc" || sort === "date_asc") {
      const dir = sort === "date_desc" ? -1 : 1;
      rows.sort((a, b) => {
        const da = a.item.latest_notice_on || "";
        const db = b.item.latest_notice_on || "";
        if (!da && !db) {
          return compareZh(
            shortBureauName(a.item, allBureaus),
            shortBureauName(b.item, allBureaus)
          );
        }
        if (!da) return 1;
        if (!db) return -1;
        if (da === db) {
          return compareZh(
            shortBureauName(a.item, allBureaus),
            shortBureauName(b.item, allBureaus)
          );
        }
        return da < db ? -dir : dir;
      });
    }
    return rows;
  };

  const render = () => {
    const filtered = filterItems(allBureaus);
    const rows = buildRows(filtered);
    meta.textContent = `共 ${filtered.length} 个地区`;

    if (!rows.length) {
      body.innerHTML =
        `<tr><td colspan="4" class="muted">没有符合条件的地区。</td></tr>`;
      return;
    }

    body.innerHTML = rows
      .map(({ item, depth }, index) => {
        const label = shortBureauName(item, allBureaus);
        const day = item.latest_notice_on || "";
        const dayCell = day
          ? `<span class="regions-day">${escapeHtml(day)}</span>`
          : `<span class="muted">暂无日期</span>`;
        const pad = Math.max(0, Math.min(depth, 4)) * 1.15;
        const noticesHref = item.latest_notice_on
          ? `/notices/view?bureau_code=${encodeURIComponent(item.code)}&from=regions`
          : "";
        const level = item.level || "";
        const prev = index > 0 ? rows[index - 1].item : null;
        const isBranch =
          level === "headquarters" || level === "province" || (level === "city" && depth <= 1);
        const groupStart =
          isBranch &&
          prev &&
          (prev.level === "city" || prev.level === "district" || prev.level === "province");
        const rowClass = [
          `regions-row regions-row-${level || "other"}`,
          isBranch ? "is-branch" : "is-child",
          groupStart ? "is-group-start" : "",
        ]
          .filter(Boolean)
          .join(" ");
        const noticeAction = noticesHref
          ? `<a href="${noticesHref}">最新公告</a>`
          : `<span class="muted">暂无公告</span>`;
        return `
          <tr class="${rowClass}" data-code="${escapeHtml(item.code)}" data-level="${escapeHtml(level)}">
            <td>
              <span class="regions-name" style="padding-left:${pad}rem">
                ${escapeHtml(label)}
              </span>
            </td>
            <td><span class="regions-level-tag">${escapeHtml(levelLabel(level))}</span></td>
            <td>${dayCell}</td>
            <td class="table-actions">
              ${noticeAction}
            </td>
          </tr>
        `;
      })
      .join("");
  };

  const showError = (err) => {
    status.hidden = false;
    status.className = "status error";
    status.textContent = `加载失败：${err.message || err}`;
    meta.textContent = "加载失败";
  };

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    render();
  });
  qs("#reset-btn").addEventListener("click", () => {
    form.reset();
    render();
  });
  [levelSelect, sortSelect].forEach((el) => {
    el.addEventListener("change", render);
  });
  let qTimer = 0;
  qInput.addEventListener("input", () => {
    window.clearTimeout(qTimer);
    qTimer = window.setTimeout(render, 180);
  });

  apiGet("/api/meta/bureaus")
    .then((data) => {
      allBureaus = data.items || [];
      render();
    })
    .catch(showError);
})();
