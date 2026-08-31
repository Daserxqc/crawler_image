(() => {
  const accountForm = qs("#account-form");
  const codeForm = qs("#code-form");
  const panelAccount = qs("#panel-account");
  const panelCode = qs("#panel-code");
  const emailField = qs("#email-field");
  const phoneField = qs("#phone-field");
  const emailInput = qs("#email");
  const phoneInput = qs("#phone");
  const codeInput = qs("#code");
  const sendBtn = qs("#send-btn");
  const verifyBtn = qs("#verify-btn");
  const resendBtn = qs("#resend-btn");
  const changeBtn = qs("#change-account-btn");
  const accountStatus = qs("#account-status");
  const codeStatus = qs("#code-status");
  const sentAccount = qs("#sent-account");
  const methodTabs = qsa(".login-method");

  const params = new URLSearchParams(window.location.search);
  const next = params.get("next") || "/watches";
  const RESEND_SECONDS = 60;

  let channel = "email"; // email | phone
  let pendingAccount = "";
  let resendTimer = null;
  let resendLeft = 0;
  let verifying = false;

  const setStatus = (el, message, kind = "") => {
    el.textContent = message || "";
    el.className = `login-status${kind ? ` ${kind}` : ""}`;
  };

  const showPanel = (name) => {
    const isCode = name === "code";
    panelAccount.hidden = isCode;
    panelCode.hidden = !isCode;
    // CSS display:grid overrides [hidden] — force it.
    panelAccount.style.display = isCode ? "none" : "block";
    panelCode.style.display = isCode ? "block" : "none";
    if (isCode) {
      codeInput.focus();
    } else if (channel === "phone") {
      phoneInput.focus();
    } else {
      emailInput.focus();
    }
  };

  const applyChannel = (nextChannel) => {
    channel = nextChannel === "phone" ? "phone" : "email";
    methodTabs.forEach((tab) => {
      const active = tab.dataset.channel === channel;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });
    emailField.hidden = channel !== "email";
    phoneField.hidden = channel !== "phone";
    emailField.style.display = channel === "email" ? "grid" : "none";
    phoneField.style.display = channel === "phone" ? "grid" : "none";
    emailInput.required = channel === "email";
    phoneInput.required = channel === "phone";
    setStatus(accountStatus, "");
  };

  const currentAccount = () =>
    channel === "phone" ? phoneInput.value.trim() : emailInput.value.trim();

  const stopResendTimer = () => {
    if (resendTimer) {
      clearInterval(resendTimer);
      resendTimer = null;
    }
  };

  const startResendTimer = (seconds = RESEND_SECONDS) => {
    stopResendTimer();
    resendLeft = seconds;
    resendBtn.disabled = true;
    const tick = () => {
      if (resendLeft <= 0) {
        stopResendTimer();
        resendBtn.disabled = false;
        resendBtn.textContent = "重新获取";
        return;
      }
      resendBtn.textContent = `重新获取（${resendLeft}s）`;
      resendLeft -= 1;
    };
    tick();
    resendTimer = setInterval(tick, 1000);
  };

  const requestCode = async (account) => {
    const data = await apiPost("/api/auth/request-code", {
      channel,
      account,
    });
    pendingAccount = data.account || account;
    sentAccount.textContent = pendingAccount;
    codeInput.value = "";
    showPanel("code");
    startResendTimer();
    if (data.dev_code) {
      codeInput.value = data.dev_code;
      setStatus(
        codeStatus,
        `开发模式：验证码 ${data.dev_code} 已填入，正在登录…`,
        "ok"
      );
      window.setTimeout(() => {
        if (codeInput.value.length === 6 && !verifying) {
          codeForm.requestSubmit();
        }
      }, 350);
    } else {
      setStatus(
        codeStatus,
        channel === "phone" ? "验证码已发送，请查看手机短信。" : "验证码已发送，请查收邮箱。",
        "ok"
      );
    }
  };

  methodTabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      if (panelCode.style.display !== "none" && !panelCode.hidden) {
        // Switching method resets to account step.
        stopResendTimer();
        pendingAccount = "";
        codeInput.value = "";
        setStatus(codeStatus, "");
        showPanel("account");
      }
      applyChannel(tab.dataset.channel);
    });
  });

  accountForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const account = currentAccount();
    setStatus(accountStatus, "");
    if (!account) {
      setStatus(
        accountStatus,
        channel === "phone" ? "请输入手机号。" : "请输入邮箱地址。",
        "error"
      );
      return;
    }
    sendBtn.disabled = true;
    sendBtn.textContent = "发送中…";
    try {
      await requestCode(account);
    } catch (err) {
      setStatus(accountStatus, err.message || String(err), "error");
    } finally {
      sendBtn.disabled = false;
      sendBtn.textContent = "获取验证码";
    }
  });

  const verify = async () => {
    const code = codeInput.value.trim();
    if (!/^\d{6}$/.test(code)) {
      setStatus(codeStatus, "请输入 6 位数字验证码。", "error");
      return;
    }
    const account = pendingAccount || currentAccount();
    if (!account) {
      setStatus(codeStatus, "请先获取验证码。", "error");
      showPanel("account");
      return;
    }
    if (verifying) return;
    verifying = true;
    verifyBtn.disabled = true;
    verifyBtn.textContent = "登录中…";
    try {
      await apiPost("/api/auth/verify", { channel, account, code });
      setStatus(codeStatus, "登录成功，正在跳转…", "ok");
      stopResendTimer();
      window.location.href = next;
    } catch (err) {
      setStatus(codeStatus, err.message || String(err), "error");
      codeInput.select();
    } finally {
      verifying = false;
      verifyBtn.disabled = false;
      verifyBtn.textContent = "登录";
    }
  };

  codeForm.addEventListener("submit", (event) => {
    event.preventDefault();
    verify();
  });

  codeInput.addEventListener("input", () => {
    codeInput.value = codeInput.value.replace(/\D/g, "").slice(0, 6);
    if (codeInput.value.length === 6) verify();
  });

  resendBtn.addEventListener("click", async () => {
    if (!pendingAccount || resendBtn.disabled) return;
    resendBtn.disabled = true;
    setStatus(codeStatus, "正在重新获取…");
    try {
      await requestCode(pendingAccount);
    } catch (err) {
      setStatus(codeStatus, err.message || String(err), "error");
      resendBtn.disabled = false;
      resendBtn.textContent = "重新获取";
    }
  });

  changeBtn.addEventListener("click", () => {
    stopResendTimer();
    pendingAccount = "";
    codeInput.value = "";
    setStatus(codeStatus, "");
    showPanel("account");
  });

  fetchAuthState()
    .then((me) => {
      if (me?.authenticated) window.location.replace(next);
    })
    .catch(() => {});

  applyChannel("email");
  showPanel("account");
})();
