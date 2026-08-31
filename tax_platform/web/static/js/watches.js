(() => {
  const listEl = qs("#watch-list");
  const outboxEl = qs("#outbox-list");
  const status = qs("#watch-status");
  const countEl = qs("#watch-count");
  const personHits = qs("#person-hits");
  const panelPerson = qs("#add-person");
  const panelBureau = qs("#add-bureau");
  const levelSelect = qs("#bureau-level");
  const bureauSelect = qs("#bureau-pick");

  let allBureaus = [];

  const TYPE_LABELS = {
    person: "人员",
    bureau: "单位",
    department: "科室",
    post: "岗位",
  };

  const STATUS_LABELS = {
    pending: "待发送",
    sent: "已发送",
    dry_run: "已生成",
    failed: "发送失败",
  };

  const setStatus = (message, kind = "") => {
    status.textContent = message || "";
    status.className = `login-status${kind ? ` ${kind}` : ""}`;
  };

  async function ensureAuth() {
    const me = await apiGet("/api/auth/me");
    if (!me.authenticated) {
      window.location.href = `/login?next=${encodeURIComponent("/watches")}`;
      return false;
    }
    return true;
  }

  function setMode(mode) {
    const isPerson = mode !== "bureau";
    qsa(".watch-add-tabs .login-method").forEach((btn) => {
      const active = btn.dataset.mode === (isPerson ? "person" : "bureau");
      btn.classList.toggle("is-active", active);
    });
    panelPerson.hidden = !isPerson;
    panelBureau.hidden = isPerson;
    panelPerson.style.display = isPerson ? "block" : "none";
    panelBureau.style.display = isPerson ? "none" : "block";
    personHits.hidden = true;
    setStatus("");
  }

  function renderWatches(items) {
    countEl.textContent = items.length ? `（${items.length}）` : "";
    if (!items.length) {
      listEl.innerHTML = `
        <div class="watch-empty">
          <p>还没有关注任何人。</p>
          <p class="muted">去 <a href="/">人员查询</a> 打开某人履历，点「关注此人」；或在上方搜索添加。</p>
        </div>`;
      return;
    }
    listEl.innerHTML = items
      .map((w) => {
        const typeLabel = TYPE_LABELS[w.target_type] || w.target_type;
        const title = w.label || w.target_id;
        const personHref =
          w.target_type === "person"
            ? `/people/${encodeURIComponent(w.target_id)}`
            : w.target_type === "bureau"
              ? `/?bureau_code=${encodeURIComponent(w.target_id)}`
              : null;
        const nameHtml = personHref
          ? `<a href="${personHref}">${escapeHtml(title)}</a>`
          : escapeHtml(title);
        return `
          <article class="watch-item">
            <div>
              <span class="tag">${escapeHtml(typeLabel)}</span>
              <strong class="watch-item-title">${nameHtml}</strong>
            </div>
            <button type="button" class="btn secondary" data-del="${w.id}" aria-label="取消关注 ${escapeHtml(title)}">取消关注</button>
          </article>`;
      })
      .join("");
    qsa("[data-del]", listEl).forEach((btn) => {
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          await apiDelete(`/api/watches/${btn.dataset.del}`);
          invalidateWatchCache();
          setStatus("已取消关注", "ok");
          await reload();
        } catch (err) {
          setStatus(err.message || String(err), "error");
          btn.disabled = false;
        }
      });
    });
  }

  function renderOutbox(items) {
    if (!items.length) {
      outboxEl.innerHTML = `<div class="muted">暂无通知</div>`;
      return;
    }
    outboxEl.innerHTML = items
      .map((m) => {
        const st = STATUS_LABELS[m.status] || m.status;
        return `
          <article class="watch-item">
            <div>
              <strong>${escapeHtml(m.subject)}</strong>
              <div class="muted">${escapeHtml(st)} · ${escapeHtml((m.created_at || "").slice(0, 16).replace("T", " "))}</div>
            </div>
          </article>`;
      })
      .join("");
  }

  async function reload() {
    const [w, o] = await Promise.all([apiGet("/api/watches"), apiGet("/api/notify/outbox")]);
    renderWatches(w.items || []);
    renderOutbox(o.items || []);
  }

  function rebuildBureauOptions() {
    const level = levelSelect.value;
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

  async function loadBureaus() {
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

  qsa(".watch-add-tabs .login-method").forEach((btn) => {
    btn.addEventListener("click", () => setMode(btn.dataset.mode));
  });

  qs("#person-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = qs("#person-q").value.trim();
    if (!name) {
      setStatus("请输入要搜索的姓名", "error");
      return;
    }
    const btn = qs("#person-search-btn");
    btn.disabled = true;
    personHits.hidden = true;
    setStatus("搜索中…");
    try {
      const data = await apiGet("/api/search", { name, limit: 20 });
      const items = data.items || [];
      setStatus(items.length ? `找到 ${items.length} 人，点击即可关注` : "没有匹配人员", items.length ? "ok" : "");
      if (!items.length) {
        personHits.innerHTML = "";
        return;
      }
      personHits.hidden = false;
      personHits.innerHTML = items
        .map((hit) => {
          const unit = hit.unit_display || hit.bureau_code || "";
          const title = hit.title_display || hit.current?.title || "";
          return `
            <button type="button" class="watch-pick" data-id="${escapeHtml(hit.id)}" data-name="${escapeHtml(hit.name)}">
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
            setStatus(`已关注 ${pick.dataset.name}`, "ok");
            await reload();
          } catch (err) {
            setStatus(err.message || String(err), "error");
            pick.disabled = false;
          }
        });
      });
    } catch (err) {
      setStatus(err.message || String(err), "error");
    } finally {
      btn.disabled = false;
    }
  });

  qs("#bureau-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const code = bureauSelect.value;
    if (!code) {
      setStatus("请选择单位", "error");
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
      setStatus(`已关注 ${label}`, "ok");
      await reload();
    } catch (err) {
      setStatus(err.message || String(err), "error");
    }
  });

  levelSelect.addEventListener("change", rebuildBureauOptions);

  ensureAuth().then(async (ok) => {
    if (!ok) return;
    setMode("person");
    try {
      await loadBureaus();
      await reload();
    } catch (err) {
      setStatus(err.message || String(err), "error");
    }
  });
})();
