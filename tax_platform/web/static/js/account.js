(() => {
  const facts = qs("#account-facts");
  const avatar = qs("#account-avatar");
  const subtitle = qs("#account-subtitle");

  async function boot() {
    const me = await apiGet("/api/auth/me");
    if (!me.authenticated) {
      window.location.href = `/login?next=${encodeURIComponent("/account")}`;
      return;
    }
    const user = me.user || {};
    const display = user.display_name || user.account_masked || "已登录";
    const method = user.login_method || (user.channel === "phone" ? "手机号" : "邮箱");
    avatar.textContent = method === "手机号" ? "手" : "邮";
    subtitle.textContent = `通过${method}登录`;

    facts.innerHTML = `
      <div>
        <dt>登录方式</dt>
        <dd>${escapeHtml(method)}</dd>
      </div>
      <div>
        <dt>${method === "手机号" ? "手机号" : "邮箱"}</dt>
        <dd>${escapeHtml(display)}</dd>
      </div>
      <div>
        <dt>隐私说明</dt>
        <dd class="muted">联系方式已脱敏显示，完整号码不会出现在页面或截图中。</dd>
      </div>
    `;
  }

  qs("#account-logout").addEventListener("click", async () => {
    await apiPost("/api/auth/logout", {});
    window.location.href = "/";
  });

  boot().catch((err) => {
    facts.innerHTML = `<div class="status error">${escapeHtml(err.message || err)}</div>`;
  });
})();
