(() => {
  const body = qs("#invite-body");
  const status = qs("#invite-status");
  const params = new URLSearchParams(window.location.search);
  const token = params.get("token") || "";

  const setError = (msg) => {
    if (status) {
      status.textContent = msg;
      status.className = "login-status error";
    }
  };

  if (!token) {
    setError("邀请链接缺少 token。");
    return;
  }

  (async () => {
    try {
      const data = await apiGet("/api/auth/invite", { token });
      const loginUrl = `/login?username=${encodeURIComponent(data.username || "")}`;
      body.innerHTML = `
        <div class="field">
          <label>用户名</label>
          <p class="invite-value" id="invite-username">${escapeHtml(data.username || "")}</p>
        </div>
        <div class="field">
          <label>初始密码</label>
          <p class="invite-value" id="invite-password">${escapeHtml(data.default_password || "")}</p>
        </div>
        <p class="field-hint">请勿转发给无关人员。首次登录将被要求修改密码。</p>
        <p class="login-status ok" role="status">账号已就绪</p>
        <a class="btn login-primary" href="${escapeHtml(loginUrl)}">去登录</a>
      `;
    } catch (err) {
      setError(err.message || String(err));
    }
  })();
})();
