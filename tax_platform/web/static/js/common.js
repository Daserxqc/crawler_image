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

function maskPhoneClient(phone) {
  const digits = String(phone || "").replace(/\D/g, "");
  if (digits.length === 11) return `${digits.slice(0, 3)}****${digits.slice(-4)}`;
  return "***";
}

function maskEmailClient(email) {
  const value = String(email || "").trim();
  const at = value.indexOf("@");
  if (at < 1) return "***";
  const local = value.slice(0, at);
  const domain = value.slice(at + 1);
  if (local.length === 1) return `${local}***@${domain}`;
  return `${local[0]}***@${domain}`;
}

function displayUserLabel(user) {
  if (!user) return "我的账号";
  if (user.display_name || user.account_masked) {
    return user.display_name || user.account_masked;
  }
  // Fallback if an older API still returns plaintext.
  if (user.phone) return maskPhoneClient(user.phone);
  if (user.email) return maskEmailClient(user.email);
  if (user.account) {
    return String(user.account).includes("@")
      ? maskEmailClient(user.account)
      : maskPhoneClient(user.account);
  }
  return "我的账号";
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
    const label = displayUserLabel(authState.user);
    const initial = (authState.user?.login_method || "").includes("手机") ? "手" : "我";
    authHtml = `
      <div class="user-menu" id="user-menu">
        <button
          type="button"
          class="user-menu-trigger"
          id="user-menu-btn"
          aria-haspopup="menu"
          aria-expanded="false"
          aria-label="账号菜单，当前 ${escapeHtml(label)}"
        >
          <span class="user-menu-avatar" aria-hidden="true">${escapeHtml(initial)}</span>
          <span class="user-menu-label">${escapeHtml(label)}</span>
          <span class="user-menu-caret" aria-hidden="true">▾</span>
        </button>
        <div class="user-menu-panel" id="user-menu-panel" role="menu" hidden>
          <a role="menuitem" href="/account">个人中心</a>
          <a role="menuitem" href="/watches">我的关注</a>
          <button type="button" role="menuitem" id="logout-btn">退出登录</button>
        </div>
      </div>
    `;
  } else if (activeId === "login") {
    authHtml = `<a class="app-auth-btn" href="/">返回检索</a>`;
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
  const menu = qs("#user-menu", el);
  const menuBtn = qs("#user-menu-btn", el);
  const menuPanel = qs("#user-menu-panel", el);
  const logoutBtn = qs("#logout-btn", el);
  if (menu && menuBtn && menuPanel) {
    const closeMenu = () => {
      menuPanel.hidden = true;
      menu.classList.remove("is-open");
      menuBtn.setAttribute("aria-expanded", "false");
    };
    const openMenu = () => {
      menuPanel.hidden = false;
      menu.classList.add("is-open");
      menuBtn.setAttribute("aria-expanded", "true");
    };
    menuBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      if (menuPanel.hidden) openMenu();
      else closeMenu();
    });
    document.addEventListener("click", (event) => {
      if (!menu.contains(event.target)) closeMenu();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeMenu();
    });
  }
  if (logoutBtn) {
    logoutBtn.addEventListener("click", async () => {
      await apiPost("/api/auth/logout", {});
      window.location.href = "/";
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

/** ── Watch helpers (PR10 UI hooks) ── */

let _authCache = null;
let _watchesCache = null;

async function fetchAuthState(force = false) {
  if (!force && _authCache) return _authCache;
  _authCache = await apiGet("/api/auth/me");
  return _authCache;
}

async function fetchUserWatches(force = false) {
  const auth = await fetchAuthState(force);
  if (!auth.authenticated) {
    _watchesCache = [];
    return _watchesCache;
  }
  if (!force && _watchesCache) return _watchesCache;
  const data = await apiGet("/api/watches");
  _watchesCache = data.items || [];
  return _watchesCache;
}

function invalidateWatchCache() {
  _watchesCache = null;
}

function buildDepartmentTargetId(bureauCode, department) {
  return `${String(bureauCode || "").trim()}::${String(department || "").trim()}`;
}

function buildPostTargetId(bureauCode, department, title = "") {
  const parts = [String(bureauCode || "").trim(), String(department || "").trim()];
  const t = String(title || "").trim();
  if (t) parts.push(t);
  return parts.join("::");
}

function parseWatchTarget(targetType, targetId) {
  const type = String(targetType || "").trim();
  const id = String(targetId || "").trim();
  if (type === "bureau") {
    return { bureau_code: id, department: null, title: null, person_name: null };
  }
  if (type === "person") {
    if (id.includes(":")) {
      const [bureau_code, person_name] = id.split(":", 2);
      return { bureau_code, department: null, title: null, person_name };
    }
    return { bureau_code: null, department: null, title: null, person_name: id };
  }
  if (type === "department" || type === "post") {
    const parts = id.split("::");
    return {
      bureau_code: parts[0] || null,
      department: parts[1] || null,
      title: type === "post" && parts[2] ? parts[2] : null,
      person_name: null,
    };
  }
  return { bureau_code: null, department: null, title: null, person_name: null };
}

function eventMatchesWatch(event, watch) {
  const parsed = parseWatchTarget(watch.target_type, watch.target_id);
  const bureau = event.bureau_code;
  const person = event.person_name;
  const dept = event.department_raw || event.department || "";
  const title = event.title_raw || event.title || "";
  const type = watch.target_type;
  if (type === "bureau") return bureau === parsed.bureau_code;
  if (type === "person") {
    if (parsed.person_name !== person) return false;
    if (parsed.bureau_code) return bureau === parsed.bureau_code;
    return true;
  }
  if (type === "department") {
    if (bureau !== parsed.bureau_code) return false;
    return Boolean(parsed.department && dept.includes(parsed.department));
  }
  if (type === "post") {
    if (bureau !== parsed.bureau_code) return false;
    if (parsed.department && !dept.includes(parsed.department)) return false;
    if (parsed.title && !title.includes(parsed.title)) return false;
    return Boolean(parsed.department || parsed.title);
  }
  return false;
}

function changeItemMatchesWatches(item, watches) {
  if (!watches?.length) return false;
  return watches.some((w) => eventMatchesWatch(item, w));
}

async function mountWatchButton(container, spec) {
  if (!container) return null;
  const {
    target_type: targetType,
    target_id: targetId,
    label,
    loginNext = window.location.pathname + window.location.search,
    idleText = "关注",
    activeText = "已关注",
    loginText = "登录后关注",
    className = "btn secondary watch-btn",
  } = spec;
  if (!targetType || !targetId) {
    container.hidden = true;
    return null;
  }

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = className;
  btn.setAttribute("aria-label", idleText);
  btn.textContent = idleText;
  container.appendChild(btn);

  const refresh = async () => {
    btn.disabled = true;
    try {
      const auth = await fetchAuthState();
      if (!auth.authenticated) {
        btn.textContent = loginText;
        btn.dataset.mode = "login";
        return;
      }
      const check = await apiGet("/api/watches/check", {
        target_type: targetType,
        target_id: targetId,
      });
      if (check.watching) {
        btn.textContent = activeText;
        btn.dataset.mode = "active";
        btn.dataset.watchId = String(check.watch?.id || "");
      } else {
        btn.textContent = idleText;
        btn.dataset.mode = "idle";
        delete btn.dataset.watchId;
      }
    } catch {
      btn.hidden = true;
    } finally {
      btn.disabled = false;
    }
  };

  btn.addEventListener("click", async () => {
    if (btn.dataset.mode === "login") {
      window.location.href = `/login?next=${encodeURIComponent(loginNext)}`;
      return;
    }
    btn.disabled = true;
    try {
      if (btn.dataset.mode === "active" && btn.dataset.watchId) {
        await apiDelete(`/api/watches/${btn.dataset.watchId}`);
      } else {
        await apiPost("/api/watches", {
          target_type: targetType,
          target_id: targetId,
          label: label || null,
        });
      }
      invalidateWatchCache();
      await refresh();
    } catch (err) {
      window.alert(err.message || String(err));
    } finally {
      btn.disabled = false;
    }
  });

  await refresh();
  return btn;
}

document.addEventListener("DOMContentLoaded", () => {
  const page = document.body.dataset.page;
  if (!page) return;
  mountAppHeader(page, null);
  apiGet("/api/auth/me")
    .then((me) => mountAppHeader(page, me))
    .catch(() => mountAppHeader(page, { authenticated: false }));
});
