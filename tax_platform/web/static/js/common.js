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

/** Persist list filters into the address bar so browser Back restores them. */
const LIST_STATE_PREFIX = "tax_hr_list_state:";

function listStateKey(pathname = window.location.pathname) {
  return LIST_STATE_PREFIX + (pathname || "/");
}

function writeListQuery(fields, { replace = true, persist = true } = {}) {
  const url = new URL(window.location.href);
  const clean = {};
  Object.entries(fields || {}).forEach(([key, value]) => {
    const text = String(value ?? "").trim();
    if (text) clean[key] = text;
  });
  const next = new URLSearchParams(clean);
  const qs = next.toString();
  const target = qs ? `${url.pathname}?${qs}` : url.pathname;
  const current = `${url.pathname}${url.search}`;
  if (persist) {
    try {
      if (Object.keys(clean).length) {
        sessionStorage.setItem(listStateKey(url.pathname), JSON.stringify(clean));
      } else {
        sessionStorage.removeItem(listStateKey(url.pathname));
      }
    } catch {
      /* ignore quota / private mode */
    }
  }
  if (current === target) return;
  // Always replace — never push filter URLs, or browser Back lands on an older search.
  window.history.replaceState({}, "", target);
}

function clearListQuery() {
  const url = new URL(window.location.href);
  try {
    sessionStorage.removeItem(listStateKey(url.pathname));
  } catch {
    /* ignore */
  }
  if (!url.search) return;
  window.history.replaceState({}, "", url.pathname);
}

/** Read filters from the address bar only (no silent session hijack). */
function readListRestore(pathname = window.location.pathname) {
  return new URLSearchParams(window.location.search);
}

function setSelectValue(select, value) {
  if (!select) return false;
  const v = value == null ? "" : String(value);
  if (!v) {
    select.value = "";
    return true;
  }
  if (![...select.options].some((opt) => opt.value === v)) {
    const opt = document.createElement("option");
    opt.value = v;
    opt.textContent = v;
    select.appendChild(opt);
  }
  select.value = v;
  return select.value === v;
}

/** Person profile link that remembers the current list URL for「返回」. */
function personProfileHref(personId, returnTo) {
  const id = String(personId || "").trim();
  if (!id) return "#";
  const url = new URL(`/people/${encodeURIComponent(id)}`, window.location.origin);
  const here = String(
    returnTo || `${window.location.pathname}${window.location.search}` || ""
  ).trim();
  if (here && here.startsWith("/") && !here.startsWith("//") && !here.startsWith("/people")) {
    url.searchParams.set("ret", here);
  }
  return `${url.pathname}${url.search}`;
}

/**
 * 「返回」只认人员页 URL 上的 ret，绝不读全局 session 以免跳到别的旧查询。
 */
function resolveListReturnUrl() {
  const ret = new URLSearchParams(window.location.search).get("ret");
  if (ret && ret.startsWith("/") && !ret.startsWith("//") && !ret.includes("://")) {
    return ret;
  }
  return "";
}

/** Build list URL from filter fields (same rules as writeListQuery). */
function listQueryHref(fields, pathname = window.location.pathname) {
  const next = new URLSearchParams();
  Object.entries(fields || {}).forEach(([key, value]) => {
    const text = String(value ?? "").trim();
    if (text) next.set(key, text);
  });
  const qs = next.toString();
  return qs ? `${pathname}?${qs}` : pathname;
}

/**
 * At click time, stamp ret=current list URL so「返回」不会用到渲染时的旧地址。
 */
function bindPersonReturnLinks(root = document) {
  if (!root || root.__personReturnBound) return;
  root.__personReturnBound = true;
  root.addEventListener("click", (event) => {
    const link = event.target?.closest?.("a[href^='/people/']");
    if (!link) return;
    try {
      const url = new URL(link.getAttribute("href"), window.location.origin);
      if (!url.pathname.startsWith("/people/")) return;
      const here = `${window.location.pathname}${window.location.search}`;
      if (!here || here.startsWith("/people")) return;
      url.searchParams.set("ret", here);
      link.href = `${url.pathname}${url.search}`;
    } catch {
      /* ignore */
    }
  });
}

/** Local notice archive link + optional live original URL. */
function noticeSourceLinks(url, { archiveLabel = "本地存档", originalLabel = "原链接" } = {}) {
  const href = String(url || "").trim();
  if (!href) return "";
  const archive = `/notices/view?url=${encodeURIComponent(href)}`;
  return (
    `<span class="notice-source-links">` +
    `<a href="${escapeHtml(archive)}">${escapeHtml(archiveLabel)}</a>` +
    `<span class="notice-source-sep" aria-hidden="true"> · </span>` +
    `<a href="${escapeHtml(href)}" target="_blank" rel="noopener">${escapeHtml(originalLabel)}</a>` +
    `</span>`
  );
}

function formatLocalDateTime(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function formatLocalDate(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso).slice(0, 10);
  return date.toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

async function apiPatch(path, body = {}) {
  const res = await fetch(path, {
    method: "PATCH",
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
  { id: "notices", href: "/notices", label: "最新公告" },
  { id: "changes", href: "/changes", label: "变动流" },
  { id: "departments", href: "/departments", label: "科室穿透" },
  { id: "posts", href: "/posts", label: "岗位历任" },
  { id: "regions", href: "/regions", label: "地区更新" },
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
  return resolveUserDisplay(user).displayName;
}

function resolveUserDisplay(user) {
  if (!user) {
    return { displayName: "已登录", defaultNick: "", customNick: "", masked: "—", method: "" };
  }
  const customNick = (user.nickname || "").trim();
  const masked = user.account_masked || "—";
  const method = user.login_method || (user.channel === "phone" ? "手机号" : "邮箱");
  let defaultNick = (user.default_nickname || "").trim();
  if (!defaultNick && !customNick) {
    const id = Number(user.id) || 0;
    if (user.channel === "phone" || method.includes("手机")) {
      const digits = String(masked).replace(/\D/g, "");
      defaultNick =
        digits.length >= 4 ? `用户${digits.slice(-4)}` : `用户${String(id).padStart(4, "0")}`;
    } else {
      defaultNick = `税务用户${String(id).padStart(4, "0")}`;
    }
  }
  const displayName = customNick || defaultNick || masked || "已登录";
  return { displayName, defaultNick, customNick, masked, method };
}

function setText(sel, value) {
  const el = qs(sel);
  if (el) el.textContent = value ?? "";
}

function applyUserAvatar(el, user) {
  if (!el) return;
  el.classList.remove("is-person-icon");
  const { displayName, customNick, defaultNick, masked, method } = resolveUserDisplay(user);
  const label = customNick || defaultNick || displayName;
  if (label && !String(label).includes("*")) {
    el.textContent = label.slice(0, 1);
    return;
  }
  const digits = String(masked).replace(/\D/g, "");
  if ((user?.channel || "") === "phone" || method.includes("手机")) {
    if (digits.length >= 4) {
      el.textContent = digits.slice(-4);
      return;
    }
  }
  el.textContent = "";
  el.classList.add("is-person-icon");
}

function userAvatarText(user) {
  const { displayName, customNick, defaultNick, masked, method } = resolveUserDisplay(user);
  const label = customNick || defaultNick || displayName;
  if (label && !String(label).includes("*")) return label.slice(0, 1);
  const digits = String(masked).replace(/\D/g, "");
  if ((user?.channel || "") === "phone" || method.includes("手机")) {
    if (digits.length >= 4) return digits.slice(-4);
  }
  return "";
}

function userAvatarIsIcon(user) {
  return !userAvatarText(user);
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
    const avatarClass = userAvatarIsIcon(authState.user) ? " user-menu-avatar is-person-icon" : " user-menu-avatar";
    const avatarInner = userAvatarIsIcon(authState.user)
      ? ""
      : escapeHtml(userAvatarText(authState.user));
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
          <span class="${avatarClass.trim()}" aria-hidden="true">${avatarInner}</span>
          <span class="user-menu-label">${escapeHtml(label)}</span>
          <span class="user-menu-caret" aria-hidden="true">▾</span>
        </button>
        <div class="user-menu-panel" id="user-menu-panel" role="menu" hidden>
          <a role="menuitem" href="/account">个人中心</a>
          <a role="menuitem" href="/account#watches">我的关注</a>
          ${authState.user?.is_admin ? `<a role="menuitem" href="/admin/users">账号管理</a>` : ""}
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
      <nav class="app-tabs" aria-label="功能导航">
        ${tabs}
      </nav>
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

/**
 * Shared prev/next/jump pager for list pages.
 * @param {HTMLElement|null} container
 * @param {{ total: number, offset: number, limit: number, onPage: (offset: number) => void, unitLabel?: string }} opts
 */
function mountSimplePager(container, opts) {
  if (!container) return;
  const total = Math.max(0, Number(opts.total) || 0);
  const limit = Math.max(1, Number(opts.limit) || 50);
  const offset = Math.max(0, Number(opts.offset) || 0);
  const unitLabel = opts.unitLabel || "条";
  const onPage = typeof opts.onPage === "function" ? opts.onPage : () => {};

  if (total <= limit) {
    container.hidden = true;
    container.innerHTML = "";
    return;
  }

  const page = Math.floor(offset / limit) + 1;
  const pages = Math.max(1, Math.ceil(total / limit));
  const prevDisabled = page <= 1;
  const nextDisabled = page >= pages;
  container.hidden = false;
  container.innerHTML = `
    <button class="btn secondary pager-btn" type="button" data-pager="first" ${
      prevDisabled ? "disabled" : ""
    }>首页</button>
    <button class="btn secondary pager-btn" type="button" data-pager="prev" ${
      prevDisabled ? "disabled" : ""
    }>上一页</button>
    <span class="pagination-info">第 ${page} / ${pages} 页（共 ${total} ${unitLabel}）</span>
    <label class="pagination-jump">
      <span class="sr-only">跳转到页码</span>
      <input class="pager-input" type="number" min="1" max="${pages}" value="${page}" inputmode="numeric" aria-label="页码" data-pager="jump" />
    </label>
    <button class="btn secondary pager-btn" type="button" data-pager="go">跳转</button>
    <button class="btn secondary pager-btn" type="button" data-pager="next" ${
      nextDisabled ? "disabled" : ""
    }>下一页</button>
    <button class="btn secondary pager-btn" type="button" data-pager="last" ${
      nextDisabled ? "disabled" : ""
    }>尾页</button>
  `;

  const goTo = (targetPage) => {
    const p = Math.min(pages, Math.max(1, Number(targetPage) || 1));
    onPage((p - 1) * limit);
  };

  container.querySelector('[data-pager="first"]')?.addEventListener("click", () => {
    if (!prevDisabled) goTo(1);
  });
  container.querySelector('[data-pager="prev"]')?.addEventListener("click", () => {
    if (!prevDisabled) goTo(page - 1);
  });
  container.querySelector('[data-pager="next"]')?.addEventListener("click", () => {
    if (!nextDisabled) goTo(page + 1);
  });
  container.querySelector('[data-pager="last"]')?.addEventListener("click", () => {
    if (!nextDisabled) goTo(pages);
  });
  const jumpInput = container.querySelector('[data-pager="jump"]');
  const handleJump = () => goTo(jumpInput?.value);
  container.querySelector('[data-pager="go"]')?.addEventListener("click", handleJump);
  jumpInput?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      handleJump();
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  const page = document.body.dataset.page;
  if (!page) return;
  mountAppHeader(page, null);
  apiGet("/api/auth/me")
    .then((me) => mountAppHeader(page, me))
    .catch(() => mountAppHeader(page, { authenticated: false }));
  bindPersonReturnLinks(document);
});

// Focus often stays on top filter <select>s; End/Home then change the option
// (and may reload) instead of scrolling. Prefer page scroll unless typing.
document.addEventListener(
  "keydown",
  (event) => {
    if (event.key !== "End" && event.key !== "Home") return;
    if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
    const target = event.target;
    if (!(target instanceof Element)) return;
    const tag = target.tagName;
    if (tag === "TEXTAREA" || target.isContentEditable) return;
    if (tag === "INPUT") {
      const type = String(target.getAttribute("type") || "text").toLowerCase();
      if (!["button", "submit", "reset", "checkbox", "radio", "file", "hidden"].includes(type)) {
        return; // caret move in text/search/number fields
      }
    }
    event.preventDefault();
    const top = event.key === "Home" ? 0 : Math.max(
      document.documentElement.scrollHeight,
      document.body.scrollHeight
    );
    window.scrollTo({ top, left: 0, behavior: "smooth" });
  },
  true
);
