(() => {
  const listEl = qs("#anom-list");
  const status = qs("#anom-status");

  async function load() {
    status.textContent = "加载中…";
    status.classList.remove("error");
    const params = {
      status: qs("#status").value,
      kind: qs("#kind").value.trim() || undefined,
      limit: 100,
    };
    try {
      const data = await apiGet("/api/anomalies", params);
      const items = data.items || [];
      status.textContent = `共 ${data.total ?? items.length} 条`;
      if (!items.length) {
        listEl.innerHTML = `<div class="status muted">暂无异常</div>`;
        return;
      }
      listEl.innerHTML = items
        .map((a) => {
          const title = `${a.kind} · ${a.severity || a.message || ""}`;
          return `
          <article class="result-row anom-row">
            <div>
              <strong>${escapeHtml(title)}</strong>
              <div class="muted">
                ${escapeHtml(a.target_type)} #${escapeHtml(a.target_id)}
                ${a.person_name ? " · " + escapeHtml(a.person_name) : ""}
                ${a.bureau_code ? " · " + escapeHtml(a.bureau_code) : ""}
              </div>
              <div class="muted">${escapeHtml(a.message || "")}</div>
            </div>
            <div class="anom-actions">
              ${
                a.status === "open"
                  ? `<button type="button" class="btn secondary" data-ignore="${a.id}">忽略</button>
                     <button type="button" class="btn secondary" data-patch="${a.id}"
                       data-tt="${escapeHtml(a.target_type)}" data-tid="${escapeHtml(a.target_id)}">修正姓名</button>`
                  : `<span class="tag">${escapeHtml(a.status)}</span>`
              }
            </div>
          </article>`;
        })
        .join("");

      qsa("[data-ignore]", listEl).forEach((btn) => {
        btn.addEventListener("click", async () => {
          await apiPost(`/api/anomalies/${btn.dataset.ignore}/ignore`, { note: "ui ignore" });
          await load();
        });
      });
      qsa("[data-patch]", listEl).forEach((btn) => {
        btn.addEventListener("click", async () => {
          const name = window.prompt("新的人员姓名（留空取消）");
          if (!name) return;
          await apiPost("/api/corrections", {
            target_type: btn.dataset.tt,
            target_id: btn.dataset.tid,
            anomaly_id: Number(btn.dataset.patch),
            patch: { person_name: name.trim() },
            note: "ui correction",
          });
          await load();
        });
      });
    } catch (err) {
      status.classList.add("error");
      status.textContent = err.message || String(err);
    }
  }

  qs("#scan-btn").addEventListener("click", async () => {
    status.textContent = "扫描中…";
    try {
      const result = await apiPost("/api/anomalies/scan", {});
      status.textContent = `扫描完成：${JSON.stringify(result)}`;
      await load();
    } catch (err) {
      status.classList.add("error");
      status.textContent = err.message || String(err);
    }
  });
  qs("#reload-btn").addEventListener("click", () => load());
  qs("#status").addEventListener("change", () => load());
  load();
})();
