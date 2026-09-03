(() => {
  const loginForm = qs("#login-form");
  const changeForm = qs("#change-password-form");
  const panelChange = qs("#panel-change-password");
  const loginStatus = qs("#login-status");
  const changeStatus = qs("#change-status");
  const usernameInput = qs("#username");
  const passwordInput = qs("#password");
  const wantChange = qs("#want-change-password");
  const oldPasswordField = qs("#old-password-field");
  const oldPasswordInput = qs("#old-password");
  const newPasswordInput = qs("#new-password");
  const newPassword2Input = qs("#new-password2");
  const changeHint = qs("#change-password-hint");

  const params = new URLSearchParams(window.location.search);
  const next = params.get("next") || "/account";
  let currentUser = null;
  let forceChange = false;

  const setStatus = (el, message, kind = "") => {
    if (!el) return;
    el.textContent = message || "";
    el.className = `login-status${kind ? ` ${kind}` : ""}`;
  };

  const showChangePanel = (forced) => {
    forceChange = !!forced;
    if (loginForm) loginForm.hidden = true;
    if (panelChange) {
      panelChange.hidden = false;
      panelChange.style.display = "block";
    }
    if (oldPasswordField) {
      const needOld = !forceChange;
      oldPasswordField.hidden = !needOld;
      oldPasswordField.style.display = needOld ? "grid" : "none";
      if (oldPasswordInput) oldPasswordInput.required = needOld;
    }
    if (changeHint) {
      changeHint.textContent = forceChange
        ? "管理员要求首次登录修改密码，请设置新密码后继续。"
        : "请输入当前密码并设置新密码。";
    }
    newPasswordInput?.focus();
  };

  const finishLogin = () => {
    window.location.href = next.startsWith("/") ? next : "/account";
  };

  if (params.get("username")) {
    usernameInput.value = params.get("username");
  }

  loginForm?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    setStatus(loginStatus, "");
    const username = usernameInput.value.trim();
    const password = passwordInput.value;
    if (!username || !password) {
      setStatus(loginStatus, "请输入用户名和密码。", "error");
      return;
    }
    try {
      const data = await apiPost("/api/auth/login", { username, password });
      currentUser = data.user;
      _authCache = { authenticated: true, user: currentUser };
      const must = !!currentUser?.must_change_password;
      const want = !!wantChange?.checked;
      if (must || want) {
        showChangePanel(must);
        setStatus(changeStatus, must ? "请先修改初始密码。" : "", must ? "ok" : "");
        return;
      }
      finishLogin();
    } catch (err) {
      setStatus(loginStatus, err.message || String(err), "error");
    }
  });

  changeForm?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    setStatus(changeStatus, "");
    const newPassword = newPasswordInput.value;
    const newPassword2 = newPassword2Input.value;
    if (newPassword.length < 6) {
      setStatus(changeStatus, "新密码至少 6 位。", "error");
      return;
    }
    if (newPassword !== newPassword2) {
      setStatus(changeStatus, "两次输入的新密码不一致。", "error");
      return;
    }
    const body = { new_password: newPassword };
    if (!forceChange) {
      body.old_password = oldPasswordInput.value;
    } else if (oldPasswordInput?.value) {
      body.old_password = oldPasswordInput.value;
    }
    try {
      const data = await apiPost("/api/auth/change-password", body);
      currentUser = data.user;
      _authCache = { authenticated: true, user: currentUser };
      setStatus(changeStatus, "密码已更新。", "ok");
      finishLogin();
    } catch (err) {
      setStatus(changeStatus, err.message || String(err), "error");
    }
  });
})();
