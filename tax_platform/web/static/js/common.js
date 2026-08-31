async function apiGet(path, params = {}) {
  const url = new URL(path, window.location.origin);
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || String(value).trim() === "") return;
    url.searchParams.set(key, value);
  });
  const res = await fetch(url, { credentials: "same-origin" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail;
    const msg = typeof detail === "string" ? detail : res.statusText || "请求失败";
    throw new Error(msg);
  }
  return data;
}

async function apiPost(path, body = {}) {
  const res = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail;
    const msg = typeof detail === "string" ? detail : res.statusText || "请求失败";
    throw new Error(msg);
  }
  return data;
}

async function apiDelete(path) {
  const res = await fetch(path, { method: "DELETE", credentials: "same-origin" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail;
    const msg = typeof detail === "string" ? detail : res.statusText || "请求失败";
    throw new Error(msg);
  }
  return data;
}

function qs(sel, root = document) {
  return root.querySelector(sel);
}

function qsa(sel, root = document) {
  return [...root.querySelectorAll(sel)];
}

function escapeHtml(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

const LEVEL_LABELS = {
  headquarters: "总局层面",
  province: "省局层面",
  city: "市局层面",
  district: "区县层面",
  unknown: "其他",
};

const LEVEL_SHORT = {
  headquarters: "总局",
  province: "省级",
  city: "市局",
  district: "区县",
};

const CATEGORY_LABELS = {
  headquarters: "总局",
  municipality: "直辖市",
  province: "省份",
  autonomous: "自治区",
  internal: "内设",
  direct: "直属",
  dispatched: "派出",
};

const HQ_CATEGORIES = ["internal", "direct", "dispatched"];
const PROVINCE_CATEGORIES = ["municipality", "province", "autonomous"];

const CHANGE_LABELS = {
  appoint: "任职",
  dismiss: "免职",
  promote: "晋升",
  transfer: "调任",
  retire: "退休",
  probation_confirm: "试用期满",
  unknown: "其他",
};

const PAGE_TABS = [
  { id: "search", href: "/", label: "人员查询" },
  { id: "changes", href: "/changes", label: "变动流" },
  { id: "departments", href: "/departments", label: "科室穿透" },
  { id: "anomalies", href: "/anomalies", label: "异常修正" },
];

function levelLabel(id) {
  return LEVEL_LABELS[id] || id || "—";
}

function changeLabel(code) {
  return CHANGE_LABELS[code] || code || "—";
}

function formatCurrent(current) {
  if (!current) return "—";
  const bits = [current.title, current.department].filter(Boolean);
  return bits.join(" · ") || "—";
}

/** Strip 国家税务总局…税务局 prefix/suffix for dropdown display. */
function shortBureauName(site, allBureaus = []) {
  if (!site) return "—";
  let name = String(site.name || site.code || "")
    .replace(/^国家税务总局/, "")
    .replace(/税务局$/, "")
    .trim();
  if (site.level === "district" && site.parent_code && allBureaus.length) {
    const parent = allBureaus.find((b) => b.code === site.parent_code);
    if (parent) {
      const parentShort = String(parent.name || "")
        .replace(/^国家税务总局/, "")
        .replace(/税务局$/, "")
        .trim();
      if (parentShort && name.startsWith(parentShort)) {
        name = name.slice(parentShort.length).trim();
      }
    }
  }
  return name || site.code || "—";
}

/** Macro region bucket — only for province-level sites (not Shanghai parent rows). */
function bureauCategory(site) {
  if (site.level === "headquarters") return "headquarters";
  if (site.level !== "province") return "";
  const name = site.name || "";
  if (/北京市|天津市|上海市|重庆市/.test(name)) return "municipality";
  if (/自治区/.test(name)) return "autonomous";
  return "province";
}

function categoryLabel(cat) {
  return CATEGORY_LABELS[cat] || cat;
}

function categoriesForLevel(level) {
  if (level === "headquarters") return HQ_CATEGORIES;
  if (level === "province") return PROVINCE_CATEGORIES;
  if (!level) return [...HQ_CATEGORIES, ...PROVINCE_CATEGORIES];
  return PROVINCE_CATEGORIES;
}

function bureauDisplayName(site, allBureaus = []) {
  return shortBureauName(site, allBureaus);
}

/** Remove level suffix from title input before API search. */
function parseTitleQuery(raw) {
  return String(raw || "")
    .replace(/（[^）]+）$/, "")
    .trim();
}

function buildTitleSuggestOptions(items) {
  const seen = new Set();
  const options = [];
  (items || []).forEach((item) => {
    const label = item.display_label || item.canonical_title || item.title || "";
    if (!label || seen.has(label)) return;
    seen.add(label);
    options.push(label);
  });
  return options;
}

function mountAppHeader(activeId, authState) {
  const el = qs("#app-header");
  if (!el) return;
  const tabs = PAGE_TABS.map((tab) => {
    const active = tab.id === activeId;
    return `<a href="${tab.href}" class="app-tab${active ? " is-active" : ""}"${
      active ? ' aria-current="page"' : ""
    }>${escapeHtml(tab.label)}</a>`;
  }).join("");
  let authHtml = "";
  if (authState?.authenticated) {
    authHtml = `
      <a class="app-tab${activeId === "watches" ? " is-active" : ""}" href="/watches">我的关注</a>
      <span class="app-user" title="${escapeHtml(authState.user?.email || "")}">${escapeHtml(
      authState.user?.email || "已登录"
    )}</span>
      <button type="button" class="app-auth-btn" id="logout-btn">退出</button>
    `;
  } else {
    authHtml = `<a class="app-auth-btn" href="/login">登录</a>`;
  }
  el.innerHTML = `
    <div class="app-header-inner">
      <a class="app-brand" href="/">税局人事检索</a>
      <nav class="app-tabs" aria-label="功能导航">${tabs}</nav>
      <div class="app-auth" aria-label="账号">${authHtml}</div>
    </div>
  `;
  const logoutBtn = qs("#logout-btn", el);
  if (logoutBtn) {
    logoutBtn.addEventListener("click", async () => {
      await apiPost("/api/auth/logout", {});
      window.location.reload();
    });
  }
}

function renderStatsStrip(container, stats) {
  if (!container) return;
  const span = stats?.date_span
    ? `${stats.date_span.from || "—"} ~ ${stats.date_span.to || "—"}`
    : "—";
  const cards = [
    { value: stats?.persons ?? "—", label: "收录人员" },
    { value: stats?.appointment_events ?? "—", label: "任免记录" },
    { value: stats?.notices ?? "—", label: "官方公告" },
    { value: stats?.bureaus ?? "—", label: "覆盖地区" },
    { value: stats?.departments ?? "—", label: "覆盖科室" },
    { value: span, label: "数据时间跨度", wide: true },
  ];
  container.innerHTML = cards
    .map(
      (card) => `
        <div class="stat-card${card.wide ? " stat-card-wide" : ""}">
          <div class="stat-value">${escapeHtml(card.value)}</div>
          <div class="stat-label">${escapeHtml(card.label)}</div>
        </div>
      `
    )
    .join("");
}

document.addEventListener("DOMContentLoaded", () => {
  const page = document.body.dataset.page;
  if (!page) return;
  mountAppHeader(page, null);
  apiGet("/api/auth/me")
    .then((me) => mountAppHeader(page, me))
    .catch(() => mountAppHeader(page, { authenticated: false }));
});
