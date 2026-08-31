(() => {
  const form = qs("#login-form");
  const status = qs("#login-status");
  const codeField = qs("#code-field");
  const submit = qs("#login-submit");
  let step = "request";

  const params = new URLSearchParams(window.location.search);
  const next = params.get("next") || "/watches";

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const email = qs("#email").value.trim();
    status.textContent = "";
    status.classList.remove("error");
    submit.disabled = true;
    try {
      if (step === "request") {
        const data = await apiPost("/api/auth/request-code", { email });
        codeField.hidden = false;
        step = "verify";
        submit.textContent = "验证并登录";
        if (data.dev_code) {
          qs("#code").value = data.dev_code;
          status.textContent = `开发模式：验证码已自动填入（${data.dev_code}）`;
        } else {
          status.textContent = "验证码已发送（若未配置邮件，请开启 TAX_HR_DEV_LOGIN=1）";
        }
      } else {
        const code = qs("#code").value.trim();
        await apiPost("/api/auth/verify", { email, code });
        status.textContent = "登录成功，正在跳转…";
        window.location.href = next;
      }
    } catch (err) {
      status.classList.add("error");
      status.textContent = err.message || String(err);
    } finally {
      submit.disabled = false;
    }
  });
})();
