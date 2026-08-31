(() => {
  const emailForm = qs("#email-form");
  const codeForm = qs("#code-form");
  const emailInput = qs("#email");
  const codeInput = qs("#code");
  const sendBtn = qs("#send-code-btn");
  const verifyBtn = qs("#verify-btn");
  const resendBtn = qs("#resend-btn");
  const changeEmailBtn = qs("#change-email-btn");
  const emailStatus = qs("#email-status");
  const codeStatus = qs("#code-status");
  const sentEmail = qs("#sent-email");
  const stepEls = qsa(".login-step");

  const params = new URLSearchParams(window.location.search);
  const next = params.get("next") || "/watches";
  const RESEND_SECONDS = 60;

  let pendingEmail = "";
  let resendTimer = null;
  let resendLeft = 0;
  let verifying = false;

  const setStatus = (el, message, kind = "") => {
    el.textContent = message || "";
    el.className = `login-status${kind ? ` ${kind}` : ""}`;
  };

  const setStep = (step) => {
    stepEls.forEach((el) => {
      el.classList.toggle("is-active", el.dataset.step === String(step));
      el.classList.toggle("is-done", Number(el.dataset.step) < step);
    });
    if (step === 1) {
      emailForm.hidden = false;
      codeForm.hidden = true;
      emailInput.focus();
    } else {
      emailForm.hidden = true;
      codeForm.hidden = false;
      codeInput.focus();
    }
  };

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
        resendBtn.textContent = "重新发送";
        return;
      }
      resendBtn.textContent = `重新发送（${resendLeft}s）`;
      resendLeft -= 1;
    };
    tick();
    resendTimer = setInterval(tick, 1000);
  };

  const requestCode = async (email) => {
    const data = await apiPost("/api/auth/request-code", { email });
    pendingEmail = email;
    sentEmail.textContent = email;
    codeInput.value = "";
    setStep(2);
    startResendTimer();
    if (data.dev_code) {
      codeInput.value = data.dev_code;
      setStatus(codeStatus, `开发模式：已填入验证码 ${data.dev_code}，可直接点登录或等待自动提交。`, "ok");
      // Auto-submit after brief pause so user sees the message.
      window.setTimeout(() => {
        if (codeInput.value.length === 6 && !verifying) {
          codeForm.requestSubmit();
        }
      }, 400);
    } else {
      setStatus(codeStatus, "验证码已发送，请查收邮箱（约 15 分钟内有效）。", "ok");
    }
    return data;
  };

  emailForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const email = emailInput.value.trim();
    setStatus(emailStatus, "");
    if (!email || !email.includes("@")) {
      setStatus(emailStatus, "请输入有效邮箱地址。", "error");
      emailInput.focus();
      return;
    }
    sendBtn.disabled = true;
    sendBtn.textContent = "发送中…";
    try {
      await requestCode(email);
      setStatus(emailStatus, "");
    } catch (err) {
      setStatus(emailStatus, err.message || String(err), "error");
    } finally {
      sendBtn.disabled = false;
      sendBtn.textContent = "发送验证码";
    }
  });

  const verify = async () => {
    const code = codeInput.value.trim();
    if (!/^\d{6}$/.test(code)) {
      setStatus(codeStatus, "请输入 6 位数字验证码。", "error");
      return;
    }
    if (verifying) return;
    verifying = true;
    verifyBtn.disabled = true;
    verifyBtn.textContent = "登录中…";
    setStatus(codeStatus, "");
    try {
      await apiPost("/api/auth/verify", { email: pendingEmail, code });
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
    // Digits only.
    codeInput.value = codeInput.value.replace(/\D/g, "").slice(0, 6);
    if (codeInput.value.length === 6) {
      verify();
    }
  });

  resendBtn.addEventListener("click", async () => {
    if (!pendingEmail || resendBtn.disabled) return;
    resendBtn.disabled = true;
    setStatus(codeStatus, "正在重新发送…");
    try {
      await requestCode(pendingEmail);
    } catch (err) {
      setStatus(codeStatus, err.message || String(err), "error");
      resendBtn.disabled = false;
      resendBtn.textContent = "重新发送";
    }
  });

  changeEmailBtn.addEventListener("click", () => {
    stopResendTimer();
    pendingEmail = "";
    codeInput.value = "";
    setStatus(codeStatus, "");
    setStep(1);
  });

  // If already logged in, bounce away.
  fetchAuthState()
    .then((me) => {
      if (me?.authenticated) {
        window.location.replace(next);
      }
    })
    .catch(() => {});

  setStep(1);
})();
