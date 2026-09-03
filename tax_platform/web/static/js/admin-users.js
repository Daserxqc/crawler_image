(() => {
  const createBtn = qs("#one-click-create");
  const createStatus = qs("#create-status");
  const inviteResult = qs("#invite-result");
  const usersTable = qs("#users-table");
  const userCount = qs("#user-count");
  const publicBaseInput = qs("#public-base");
  const publicBaseHint = qs("#public-base-hint");
  const STORAGE_KEY = "tax_hr_public_base";

  let publicBase = "";

  const setStatus = (el, msg, kind = "") => {
    if (!el) return;
    el.textContent = msg || "";
    el.className = `status${kind ? ` ${kind}` : ""}`;
  };

  const normalizeBase = (raw) => String(raw || "").trim().replace(/\/+$/, "");

  const readPublicBase = () => normalizeBase(publicBaseInput?.value || publicBase || window.location.origin);

  const inviteAbsolute = (path) => {
    if (!path) return "";
    try {
      return new URL(path, `${readPublicBase()}/`).href;
    } catch {
      return path;
    }
  };

  const persistPublicBase = () => {
    const value = normalizeBase(publicBaseInput?.value || "");
    publicBase = value;
    if (value) {
      try {
        localStorage.setItem(STORAGE_KEY, value);
      } catch {
        /* ignore */
      }
    }
  };

  const showInviteCard = (data) => {
    const url = inviteAbsolute(data.invite_path);
    inviteResult.hidden = false;
    inviteResult.innerHTML = `
      <p><strong>邀请已生成</strong></p>
      <p>用户名：<code>${escapeHtml(data.username || "")}</code></p>
      <p>昵称：<code>${escapeHtml(data.nickname || "税务用户")}</code></p>
      <p>初始密码：<code>${escapeHtml(data.default_password || "")}</code></p>
      <p>邀请链接：</p>
      <p><input class="invite-link-input" id="invite-link-copy" readonly value="${escapeHtml(url)}" /></p>
      <button type="button" class="btn secondary" id="copy-invite-btn">复制邀请链接</button>
    `;
    qs("#copy-invite-btn")?.addEventListener("click", async () => {
      const input = qs("#invite-link-copy");
      try {
        await navigator.clipboard.writeText(input.value);
        setStatus(createStatus, "邀请链接已复制。", "ok");
      } catch {
        input.select();
        setStatus(createStatus, "请手动复制链接。", "ok");
      }
    });
  };

  const renderUsers = (items) => {
    userCount.textContent = `（${items.length}）`;
    if (!items.length) {
      usersTable.innerHTML = `<p class="muted">暂无账号</p>`;
      return;
    }
    const rows = items
      .map((u) => {
        const inviteCell = u.invite_path
          ? `<button type="button" class="link-btn" data-copy="${escapeHtml(inviteAbsolute(u.invite_path))}">复制邀请</button>`
          : `<span class="muted">已激活</span>`;
        return `<tr>
          <td>${escapeHtml(u.username || "")}</td>
          <td>${escapeHtml(u.display_name || "")}</td>
          <td>${u.is_admin ? "管理员" : "用户"}</td>
          <td>${u.watch_count ?? 0}</td>
          <td>${escapeHtml(formatLocalDateTime(u.last_login_at) || "—")}</td>
          <td>${inviteCell}</td>
          <td class="table-actions">
            <button type="button" class="link-btn" data-reset="${u.id}">重置邀请</button>
            <button type="button" class="link-btn danger" data-del="${u.id}" ${u.is_admin ? "disabled" : ""}>删除</button>
          </td>
        </tr>`;
      })
      .join("");
    usersTable.innerHTML = `
      <table class="data-table">
        <thead>
          <tr>
            <th>用户名</th><th>昵称</th><th>角色</th><th>关注</th><th>上次登录</th><th>邀请</th><th></th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    `;
  };

  const loadUsers = async () => {
    const data = await apiGet("/api/admin/users");
    renderUsers(data.items || []);
  };

  const loadPublicBase = async () => {
    let saved = "";
    try {
      saved = normalizeBase(localStorage.getItem(STORAGE_KEY) || "");
    } catch {
      saved = "";
    }
    let suggested = "";
    try {
      const meta = await apiGet("/api/meta/public-base");
      suggested = normalizeBase(meta.base || meta.detected || "");
      if (publicBaseHint) {
        publicBaseHint.textContent = suggested
          ? `建议使用：${suggested}（可用环境变量 TAX_HR_PUBLIC_BASE 固定）`
          : "请填写本机局域网地址，例如 http://192.168.x.x:8000";
      }
    } catch {
      if (publicBaseHint) {
        publicBaseHint.textContent = "请填写本机局域网地址，例如 http://192.168.x.x:8000";
      }
    }
    const origin = normalizeBase(window.location.origin);
    const loopback = /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/i.test(origin);
    publicBase = saved || (!loopback ? origin : "") || suggested || origin;
    if (publicBaseInput) {
      publicBaseInput.value = publicBase;
      publicBaseInput.addEventListener("change", persistPublicBase);
      publicBaseInput.addEventListener("blur", persistPublicBase);
    }
  };

  createBtn?.addEventListener("click", async () => {
    persistPublicBase();
    setStatus(createStatus, "正在生成…");
    createBtn.disabled = true;
    try {
      const data = await apiPost("/api/admin/users", { auto: true });
      setStatus(createStatus, "账号已创建。", "ok");
      showInviteCard(data);
      await loadUsers();
    } catch (err) {
      setStatus(createStatus, err.message || String(err), "error");
    } finally {
      createBtn.disabled = false;
    }
  });

  usersTable?.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("button");
    if (!btn) return;
    if (btn.dataset.copy) {
      try {
        await navigator.clipboard.writeText(btn.dataset.copy);
        setStatus(createStatus, "邀请链接已复制。", "ok");
      } catch {
        setStatus(createStatus, "复制失败，请手动复制。", "error");
      }
      return;
    }
    if (btn.dataset.reset) {
      if (!confirm("重置后将生成新邀请链接与初始密码，旧密码立即失效。继续？")) return;
      try {
        persistPublicBase();
        const data = await apiPost(`/api/admin/users/${btn.dataset.reset}/reset-invite`, {});
        showInviteCard(data);
        setStatus(createStatus, "已重置邀请。", "ok");
        await loadUsers();
      } catch (err) {
        setStatus(createStatus, err.message || String(err), "error");
      }
      return;
    }
    if (btn.dataset.del) {
      if (!confirm("确定删除该账号？关注与通知记录将一并删除。")) return;
      try {
        await apiDelete(`/api/admin/users/${btn.dataset.del}`);
        setStatus(createStatus, "已删除。", "ok");
        await loadUsers();
      } catch (err) {
        setStatus(createStatus, err.message || String(err), "error");
      }
    }
  });

  (async () => {
    const me = await fetchAuthState();
    if (!me.authenticated) {
      window.location.href = "/login?next=/admin/users";
      return;
    }
    if (!me.user?.is_admin) {
      window.location.href = "/account";
      return;
    }
    try {
      await loadPublicBase();
      await loadUsers();
    } catch (err) {
      usersTable.innerHTML = `<p class="status error">${escapeHtml(err.message || err)}</p>`;
    }
  })();
})();
