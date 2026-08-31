(() => {
  const TYPE_LABELS = {
    person: "人员",
    bureau: "单位",
    department: "科室",
    post: "岗位",
  };

  const NOTIFY_LABELS = {
    pending: "等待发邮件",
    sent: "邮件已发出",
    dry_run: "仅站内提醒",
    failed: "邮件未发出",
  };

  const ORG_TYPES = ["bureau", "department", "post"];
  const NOTIFY_PAGE = 10;
  const VALID_SECTIONS = ["overview", "watches", "notify", "settings", "help"];

  let allBureaus = [];
  let allWatches = [];
  let allNotify = [];
  let notifyLimit = NOTIFY_PAGE;
  let watchFilter = "all";
  let watchQuery = "";
  let notifyFilter = "all";
  let currentUser = null;

  const formatDateTime = (iso) => formatLocalDateTime(iso);

  const formatDate = (iso) => formatLocalDate(iso);

  const countByType = (items, types) =>
    items.filter((item) => types.includes(item.target_type)).length;

  const watchHref = (watch) => {
    if (watch.target_type === "person") {
      return `/people/${encodeURIComponent(watch.target_id)}`;
    }
    if (watch.target_type === "bureau") {
      return `/?bureau_code=${encodeURIComponent(watch.target_id)}`;
    }
    return null;
  };

  const setQuickStatus = (message, kind = "") => {
    const el = qs("#qa-status");
    if (!el) return;
    el.textContent = message || "";
    el.className = `login-status${kind ? ` ${kind}` : ""}`;
  };

  const getSectionFromHash = () => {
    const hash = (window.location.hash || "").replace(/^#/, "");
    return VALID_SECTIONS.includes(hash) ? hash : "overview";
  };

  const showSection = (section, { updateHash = true } = {}) => {
    const id = VALID_SECTIONS.includes(section) ? section : "overview";
    qsa(".account-section").forEach((el) => {
      el.classList.toggle("is-active", el.id === `section-${id}`);
    });
    qsa(".account-nav-item").forEach((btn) => {
      const active = btn.dataset.section === id;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-current", active ? "page" : "false");
    });
    if (updateHash && window.location.hash !== `#${id}`) {
      history.replaceState(null, "", `#${id}`);
    }
  };

  const filterWatches = (items) => {
    let out = items;
    if (watchFilter === "person") out = out.filter((w) => w.target_type === "person");
    if (watchFilter === "org") out = out.filter((w) => ORG_TYPES.includes(w.target_type));
    const q = watchQuery.trim().toLowerCase();
    if (q) {
      out = out.filter((w) => {
        const label = String(w.label || w.target_id || "").toLowerCase();
        return label.includes(q);
      });
    }
    return out;
  };

  const filterNotify = (items) => {
    if (notifyFilter === "all") return items;
    if (notifyFilter === "pending") return items.filter((n) => n.status === "pending");
    if (notifyFilter === "sent") {
      return items.filter((n) => ["sent", "dry_run"].includes(n.status));
    }
    if (notifyFilter === "failed") return items.filter((n) => n.status === "failed");
    return items;
  };

  const renderTimeline = () => {
    const el = qs("#account-timeline");
    const events = [
      ...allWatches.map((w) => ({
        kind: "watch",
        at: w.created_at,
        watch: w,
      })),
      ...allNotify.map((n) => ({
        kind: "notify",
        at: n.created_at,
        notify: n,
      })),
    ]
      .sort((a, b) => String(b.at || "").localeCompare(String(a.at || "")))
      .slice(0, 8);

    if (!events.length) {
      el.innerHTML = `<div class="account-feed-empty">
        <p>暂无动态。</p>
        <p class="muted">添加关注后，这里会显示最近操作与通知。</p>
        <button type="button" class="btn secondary account-inline-btn" data-goto="watches">去添加关注</button>
      </div>`;
      bindGotoButtons(el);
      return;
    }

    el.innerHTML = events
      .map((ev) => {
        if (ev.kind === "watch") {
          const w = ev.watch;
          const title = w.label || w.target_id;
          const typeLabel = TYPE_LABELS[w.target_type] || w.target_type;
          return `<article class="account-timeline-item">
            <span class="account-timeline-dot watch" aria-hidden="true"></span>
            <div>
              <p class="account-timeline-text">关注了${escapeHtml(typeLabel)} <strong>${escapeHtml(title)}</strong></p>
              <time class="muted">${escapeHtml(formatDateTime(ev.at))}</time>
            </div>
          </article>`;
        }
        const n = ev.notify;
        const status = NOTIFY_LABELS[n.status] || n.status;
        return `<article class="account-timeline-item">
          <span class="account-timeline-dot notify" aria-hidden="true"></span>
          <div>
            <p class="account-timeline-text">收到通知 <strong>${escapeHtml(n.subject)}</strong> <span class="tag">${escapeHtml(status)}</span></p>
            <time class="muted">${escapeHtml(formatDateTime(ev.at))}</time>
          </div>
        </article>`;
      })
      .join("");
  };

  const renderWatchList = () => {
    const el = qs("#account-watch-list");
    if (!el) return;
    const items = filterWatches(allWatches);
    setText("#watch-list-count", items.length ? `（${items.length}）` : "");

    if (!items.length) {
      el.innerHTML = `<div class="account-feed-empty">
        <p>${allWatches.length ? "没有符合筛选条件的关注。" : "还没有关注对象。"}</p>
        <p class="muted">${allWatches.length ? "试试切换筛选或清空搜索。" : "在上方添加关注，或去人员查询打开履历点关注。"}</p>
      </div>`;
      return;
    }

    el.innerHTML = items
      .map((watch) => {
        const title = watch.label || watch.target_id;
        const typeLabel = TYPE_LABELS[watch.target_type] || watch.target_type;
        const href = watchHref(watch);
        const nameHtml = href
          ? `<a href="${href}">${escapeHtml(title)}</a>`
          : escapeHtml(title);
        return `<article class="account-watch-row">
          <div class="account-watch-main">
            <span class="tag">${escapeHtml(typeLabel)}</span>
            <strong class="account-feed-title">${nameHtml}</strong>
            <time class="muted account-feed-time">关注于 ${escapeHtml(formatDateTime(watch.created_at))}</time>
          </div>
          <button type="button" class="btn secondary account-unwatch" data-id="${watch.id}" aria-label="取消关注 ${escapeHtml(title)}">取消关注</button>
        </article>`;
      })
      .join("");

    qsa(".account-unwatch", el).forEach((btn) => {
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          await apiDelete(`/api/watches/${btn.dataset.id}`);
          invalidateWatchCache();
          await reloadData();
          setQuickStatus("已取消关注", "ok");
        } catch (err) {
          setQuickStatus(err.message || String(err), "error");
          btn.disabled = false;
        }
      });
    });
  };

  const renderNotifyList = () => {
    const el = qs("#account-notify-list");
    const items = filterNotify(allNotify);

    if (!items.length) {
      el.innerHTML = `<div class="account-feed-empty">
        <p class="muted">${allNotify.length ? "当前筛选下暂无通知。" : "暂无变动通知。"}</p>
        <p class="muted">添加关注后，有新任免时会在这里出现摘要。</p>
      </div>`;
      return;
    }

    el.innerHTML = items
      .map((item) => {
        const status = NOTIFY_LABELS[item.status] || item.status;
        const preview = String(item.body_preview || "").trim();
        const error = item.error ? `<p class="account-notify-error">${escapeHtml(item.error)}</p>` : "";
        return `<details class="account-notify-item">
          <summary>
            <span class="account-notify-subject">${escapeHtml(item.subject)}</span>
            <span class="tag account-notify-status">${escapeHtml(status)}</span>
            <time class="muted">${escapeHtml(formatDateTime(item.created_at))}</time>
          </summary>
          ${preview ? `<pre class="account-notify-body">${escapeHtml(preview)}</pre>` : ""}
          ${error}
        </details>`;
      })
      .join("");
  };

  const updateStats = () => {
    setText("#stat-watches-total", String(allWatches.length));
    setText("#stat-watches-person", String(countByType(allWatches, ["person"])));
    setText("#stat-watches-org", String(countByType(allWatches, ORG_TYPES)));
    setText("#stat-notify", String(allNotify.length));

    const watchBadge = qs("#nav-badge-watches");
    if (watchBadge) {
      watchBadge.textContent = allWatches.length ? String(allWatches.length) : "";
      watchBadge.hidden = !allWatches.length;
    }

    const pending = allNotify.filter((n) => n.status === "pending").length;
    const notifyBadge = qs("#nav-badge-notify");
    if (notifyBadge) {
      notifyBadge.textContent = pending ? String(pending) : "";
      notifyBadge.hidden = !pending;
    }

    const loadMoreBtn = qs("#notify-load-more");
    if (!loadMoreBtn) return;
    loadMoreBtn.hidden = allNotify.length < notifyLimit;
    loadMoreBtn.disabled = false;
    loadMoreBtn.textContent = "加载更多";
  };

  const renderAll = () => {
    updateStats();
    renderTimeline();
    renderWatchList();
    renderNotifyList();
  };

  async function reloadData() {
    const [watchesRes, outboxRes] = await Promise.all([
      apiGet("/api/watches"),
      apiGet("/api/notify/outbox", { limit: notifyLimit }),
    ]);
    allWatches = watchesRes.items || [];
    allNotify = outboxRes.items || [];
    renderAll();
  }

  function bindGotoButtons(root = document) {
    qsa("[data-goto]", root).forEach((btn) => {
      btn.addEventListener("click", () => {
        const section = btn.dataset.goto;
        if (btn.dataset.filter) {
          watchFilter = btn.dataset.filter;
          qsa("[data-watch-filter]").forEach((chip) => {
            chip.classList.toggle("is-active", chip.dataset.watchFilter === watchFilter);
          });
          renderWatchList();
        }
        showSection(section);
      });
    });
  }

  function setQuickAddMode(mode) {
    const isPerson = mode !== "bureau";
    qsa(".watch-add-tabs .login-method").forEach((btn) => {
      const active = btn.dataset.mode === (isPerson ? "person" : "bureau");
      btn.classList.toggle("is-active", active);
    });
    qs("#qa-panel-person").hidden = !isPerson;
    qs("#qa-panel-bureau").hidden = isPerson;
    qs("#qa-panel-person").style.display = isPerson ? "block" : "none";
    qs("#qa-panel-bureau").style.display = isPerson ? "none" : "block";
    qs("#qa-person-hits").hidden = true;
    setQuickStatus("");
  }

  function rebuildBureauOptions() {
    const level = qs("#qa-bureau-level").value;
    const bureauSelect = qs("#qa-bureau-pick");
    const items = allBureaus
      .filter((b) => !level || b.level === level)
      .sort((a, b) =>
        bureauDisplayName(a, allBureaus).localeCompare(bureauDisplayName(b, allBureaus), "zh-CN")
      );
    const prev = bureauSelect.value;
    bureauSelect.innerHTML =
      `<option value="">请选择单位</option>` +
      items
        .map(
          (b) =>
            `<option value="${escapeHtml(b.code)}">${escapeHtml(
              bureauDisplayName(b, allBureaus)
            )}</option>`
        )
        .join("");
    if (items.some((b) => b.code === prev)) bureauSelect.value = prev;
  }

  async function loadBureauMeta() {
    const levelSelect = qs("#qa-bureau-level");
    const levels = await apiGet("/api/meta/levels");
    (levels.levels || []).forEach((level) => {
      const opt = document.createElement("option");
      opt.value = level.id;
      opt.textContent = level.label;
      levelSelect.appendChild(opt);
    });
    allBureaus = (await apiGet("/api/meta/bureaus")).items || [];
    rebuildBureauOptions();
  }

  function renderProfile(user) {
    currentUser = user;
    const { displayName, defaultNick, customNick, masked, method } = resolveUserDisplay(user);

    applyUserAvatar(qs("#account-avatar"), user);
    setText("#account-display-name", displayName);
    setText("#account-account-line", `${masked} · ${method}登录`);
    setText(
      "#account-meta",
      user.last_login_at ? `上次登录 ${formatLocalDateTime(user.last_login_at)}` : ""
    );

    const nickLabel = customNick ? customNick : `${defaultNick || displayName}（系统默认）`;
    setText("#setting-nickname", nickLabel);
    setText("#setting-account", masked);
    setText("#setting-method", `${method}验证码`);
    setText("#setting-created", formatLocalDate(user.created_at));
    setText("#setting-last-login", formatLocalDateTime(user.last_login_at));
    setText(
      "#setting-session",
      user.session_expires_at ? formatLocalDateTime(user.session_expires_at) : "—"
    );

    const nickInput = qs("#nickname-input");
    const nickHint = qs("#nickname-hint");
    if (nickInput) nickInput.value = customNick;
    if (nickHint) {
      nickHint.textContent = customNick
        ? "您已设置自定义昵称。"
        : `未设置时使用系统昵称「${defaultNick || displayName}」，可随时修改。`;
    }
  }

  async function boot() {
    const me = await apiGet("/api/auth/me");
    if (!me.authenticated) {
      window.location.href = `/login?next=${encodeURIComponent("/account")}`;
      return;
    }
    currentUser = me.user || {};
    renderProfile(currentUser);
    await loadBureauMeta();
    setQuickAddMode("person");
    await reloadData();
    showSection(getSectionFromHash(), { updateHash: false });
    bindGotoButtons();
  }

  qsa(".account-nav-item").forEach((btn) => {
    btn.addEventListener("click", () => showSection(btn.dataset.section));
  });

  window.addEventListener("hashchange", () => showSection(getSectionFromHash(), { updateHash: false }));

  qsa("[data-watch-filter]").forEach((chip) => {
    chip.addEventListener("click", () => {
      watchFilter = chip.dataset.watchFilter;
      qsa("[data-watch-filter]").forEach((c) => c.classList.toggle("is-active", c === chip));
      renderWatchList();
    });
  });

  qs("#watch-filter-q")?.addEventListener("input", (event) => {
    watchQuery = event.target.value;
    renderWatchList();
  });

  qsa("[data-notify-filter]").forEach((chip) => {
    chip.addEventListener("click", () => {
      notifyFilter = chip.dataset.notifyFilter;
      qsa("[data-notify-filter]").forEach((c) => c.classList.toggle("is-active", c === chip));
      renderNotifyList();
    });
  });

  qsa(".watch-add-tabs .login-method").forEach((btn) => {
    btn.addEventListener("click", () => setQuickAddMode(btn.dataset.mode));
  });

  qs("#qa-person-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = qs("#qa-person-q").value.trim();
    const personHits = qs("#qa-person-hits");
    const btn = qs("#qa-person-search-btn");
    if (!name) {
      setQuickStatus("请输入要搜索的姓名", "error");
      return;
    }
    btn.disabled = true;
    personHits.hidden = true;
    setQuickStatus("搜索中…");
    try {
      const data = await apiGet("/api/search", { name, limit: 20 });
      const items = data.items || [];
      if (!items.length) {
        personHits.innerHTML = "";
        setQuickStatus("没有匹配人员", "error");
        return;
      }
      setQuickStatus(`找到 ${items.length} 人，点击即可关注`, "ok");
      personHits.hidden = false;
      personHits.innerHTML = items
        .map((hit) => {
          const unit = hit.unit_display || hit.bureau_code || "";
          const title = hit.title_display || hit.current?.title || "";
          return `<button type="button" class="watch-pick" data-id="${escapeHtml(hit.id)}" data-name="${escapeHtml(hit.name)}">
              <strong>${escapeHtml(hit.name)}</strong>
              <span class="muted">${escapeHtml(unit)}${title ? " · " + escapeHtml(title) : ""}</span>
            </button>`;
        })
        .join("");
      qsa(".watch-pick", personHits).forEach((pick) => {
        pick.addEventListener("click", async () => {
          pick.disabled = true;
          try {
            await apiPost("/api/watches", {
              target_type: "person",
              target_id: pick.dataset.id,
              label: pick.dataset.name,
            });
            invalidateWatchCache();
            setQuickStatus(`已关注 ${pick.dataset.name}`, "ok");
            qs("#qa-person-q").value = "";
            personHits.hidden = true;
            await reloadData();
          } catch (err) {
            setQuickStatus(err.message || String(err), "error");
            pick.disabled = false;
          }
        });
      });
    } catch (err) {
      setQuickStatus(err.message || String(err), "error");
    } finally {
      btn.disabled = false;
    }
  });

  qs("#qa-bureau-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const code = qs("#qa-bureau-pick").value;
    if (!code) {
      setQuickStatus("请选择单位", "error");
      return;
    }
    const site = allBureaus.find((b) => b.code === code);
    const label = site ? bureauDisplayName(site, allBureaus) : code;
    try {
      await apiPost("/api/watches", {
        target_type: "bureau",
        target_id: code,
        label,
      });
      invalidateWatchCache();
      setQuickStatus(`已关注 ${label}`, "ok");
      await reloadData();
    } catch (err) {
      setQuickStatus(err.message || String(err), "error");
    }
  });

  qs("#qa-bureau-level").addEventListener("change", rebuildBureauOptions);

  qs("#notify-load-more").addEventListener("click", async () => {
    const btn = qs("#notify-load-more");
    btn.disabled = true;
    btn.textContent = "加载中…";
    notifyLimit += NOTIFY_PAGE;
    try {
      await reloadData();
    } catch (err) {
      btn.disabled = false;
      btn.textContent = "加载更多";
    }
  });

  qs("#account-rebind").addEventListener("click", async () => {
    const btn = qs("#account-rebind");
    btn.disabled = true;
    try {
      await apiPost("/api/auth/logout", {});
      window.location.href = "/login?rebind=1&next=/account";
    } catch (err) {
      btn.disabled = false;
      alert(err.message || String(err));
    }
  });

  qs("#account-logout").addEventListener("click", async () => {
    await apiPost("/api/auth/logout", {});
    window.location.href = "/";
  });

  qs("#nickname-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const status = qs("#nickname-status");
    const btn = qs("#nickname-save-btn");
    const nickname = qs("#nickname-input").value.trim();
    status.textContent = "";
    btn.disabled = true;
    try {
      let res;
      try {
        res = await apiPatch("/api/auth/profile", { nickname });
      } catch (patchErr) {
        if (!String(patchErr.message || "").includes("Not Found")) throw patchErr;
        res = await apiPost("/api/auth/profile", { nickname });
      }
      currentUser = res.user || currentUser;
      renderProfile(currentUser);
      await fetchAuthState(true);
      status.textContent = nickname ? "昵称已保存" : "已恢复系统默认昵称";
      status.className = "login-status ok";
    } catch (err) {
      status.textContent = err.message || String(err);
      status.className = "login-status error";
    } finally {
      btn.disabled = false;
    }
  });

  boot().catch((err) => {
    const timeline = qs("#account-timeline");
    if (timeline) {
      timeline.innerHTML = `<div class="status error">${escapeHtml(err.message || err)}</div>`;
    }
  });
})();
