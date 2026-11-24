(() => {
  "use strict";

  const CFG = window.APP_CONFIG || {
    statuses: [],
    conditions: [],
    marketplaces: [],
    cargos: [],
    reasons: [],
  };

  const STATUS_BY_KEY = Object.fromEntries(CFG.statuses.map((s) => [s.key, s]));
  const CONDITION_BY_KEY = Object.fromEntries(CFG.conditions.map((c) => [c.key, c.label]));

  const IMPORT_FIELD_LABELS = [
    { key: "order_no", label: "Sipariş No" },
    { key: "tracking_no", label: "Kargo Takip No" },
    { key: "barcode", label: "Barkod" },
    { key: "cargo_company", label: "Kargo Firması" },
    { key: "marketplace", label: "Pazaryeri" },
    { key: "customer_name", label: "Müşteri Adı" },
    { key: "customer_phone", label: "Telefon" },
    { key: "product_name", label: "Ürün Adı *" },
    { key: "sku", label: "Stok Kodu" },
    { key: "quantity", label: "Adet" },
    { key: "refund_amount", label: "İade Tutarı" },
    { key: "return_reason", label: "İade Nedeni" },
    { key: "status", label: "Durum" },
    { key: "return_date", label: "İade Tarihi" },
    { key: "notes", label: "Notlar" },
  ];

  const state = {
    returns: [],
    selectedIds: new Set(),
    activeReturnId: null,
    importToken: null,
    importHeaders: [],
    cameraStream: null,
    scanTimer: null,
  };

  const $ = (id) => document.getElementById(id);

  function esc(str) {
    if (str === null || str === undefined) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function formatMoney(val) {
    const num = Number(val || 0);
    return num.toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " ₺";
  }

  function toast(message, type = "info") {
    const box = $("toast-container");
    const el = document.createElement("div");
    el.className = `toast ${type === "error" ? "toast-error" : type === "success" ? "toast-success" : ""}`;
    el.textContent = message;
    box.appendChild(el);
    setTimeout(() => {
      el.remove();
    }, 3600);
  }

  async function api(url, options = {}) {
    const resp = await fetch(url, options);
    const isJson = (resp.headers.get("content-type") || "").includes("application/json");
    const data = isJson ? await resp.json() : null;
    if (!resp.ok) {
      throw new Error((data && data.error) || `Sunucu hatası (${resp.status})`);
    }
    return data;
  }

  function statusBadgeHtml(statusKey) {
    const meta = STATUS_BY_KEY[statusKey] || { label: statusKey, color: "#334155", bg: "#f1f5f9" };
    return `<span class="status-badge" style="color:${esc(meta.color)};background:${esc(meta.bg)}">${esc(meta.label)}</span>`;
  }

  function buildFilterQuery() {
    const params = new URLSearchParams();
    const pairs = [
      ["search", $("filter-search").value.trim()],
      ["status", $("filter-status").value],
      ["marketplace", $("filter-marketplace").value],
      ["reason", $("filter-reason").value],
      ["has_evidence", $("filter-evidence").value],
      ["date_from", $("filter-date-from").value],
      ["date_to", $("filter-date-to").value],
    ];
    for (const [k, v] of pairs) {
      if (v && v !== "all") params.set(k, v);
    }
    return params.toString();
  }

  async function loadReturns() {
    const qs = buildFilterQuery();
    const data = await api(`/api/returns${qs ? `?${qs}` : ""}`);
    state.returns = data.items || [];
    $("nav-count-badge").textContent = String(state.returns.length);
    renderReturnsTable();
    renderMobileCards();
    syncBulkBar();
  }

  async function loadStats() {
    const data = await api("/api/stats");
    const s = data.summary || {};
    $("kpi-total").textContent = String(s.total_count || 0);
    $("kpi-units").textContent = `${s.total_units || 0} Adet Ürün`;
    $("kpi-pending").textContent = String(s.pending_count || 0);
    $("kpi-review").textContent = String(s.review_count || 0);
    $("kpi-approved").textContent = String(s.approved_count || 0);
    $("kpi-amount").textContent = formatMoney(s.total_amount || 0);
    $("kpi-rejected").textContent = String(s.rejected_count || 0);

    renderBarChart(
      "chart-status",
      (data.by_status || []).map((row) => {
        const meta = STATUS_BY_KEY[row.status] || { label: row.status, color: "#4f46e5" };
        return { label: meta.label, count: row.count, color: meta.color };
      })
    );

    renderBarChart(
      "chart-marketplace",
      (data.by_marketplace || []).map((row) => ({
        label: row.marketplace || "Diğer",
        count: row.count,
        color: "#4f46e5",
      }))
    );

    renderBarChart(
      "chart-reasons",
      (data.by_reason || []).map((row) => ({
        label: row.reason || "Belirtilmedi",
        count: row.count,
        color: "#0ea5e9",
      }))
    );

    renderRecentActivity(data.recent_history || []);
  }

  function renderBarChart(containerId, items) {
    const el = $(containerId);
    if (!items.length) {
      el.innerHTML = `<p class="muted-text">Henüz veri bulunmuyor.</p>`;
      return;
    }
    const maxCount = Math.max(...items.map((i) => i.count), 1);
    el.innerHTML = items
      .map((item) => {
        const pct = Math.round((item.count / maxCount) * 100);
        return `
          <div>
            <div class="bar-item-head">
              <span>${esc(item.label)}</span>
              <span>${item.count}</span>
            </div>
            <div class="bar-track">
              <div class="bar-fill" style="width:${pct}%;background:${esc(item.color)}"></div>
            </div>
          </div>
        `;
      })
      .join("");
  }

  function renderRecentActivity(rows) {
    const el = $("recent-activity");
    if (!rows.length) {
      el.innerHTML = `<p class="muted-text">Henüz işlem kaydı yok.</p>`;
      return;
    }
    el.innerHTML = rows
      .map((r) => {
        const statusMeta = STATUS_BY_KEY[r.new_status] || { label: r.new_status };
        return `
          <div class="activity-item" data-open-id="${r.return_id}">
            <div><strong>${esc(r.return_code)}</strong> — ${esc(r.product_name)}</div>
            <div class="sub-line">${esc(statusMeta.label)} • ${esc(r.note || "")} (${esc(r.created_at)})</div>
          </div>
        `;
      })
      .join("");
  }

  function renderReturnsTable() {
    const tbody = $("returns-tbody");
    if (!state.returns.length) {
      tbody.innerHTML = `<tr><td colspan="10" class="empty-cell">Kriterlere uygun iade kaydı bulunamadı.</td></tr>`;
      return;
    }

    tbody.innerHTML = state.returns
      .map((item) => {
        const checked = state.selectedIds.has(item.id) ? "checked" : "";
        const evCount = Number(item.evidence_count || 0);
        return `
          <tr data-row-id="${item.id}">
            <td class="col-check" data-no-open="1">
              <input type="checkbox" class="row-checkbox" data-id="${item.id}" ${checked} aria-label="Seç" />
            </td>
            <td>
              <span class="code-chip">${esc(item.return_code)}</span>
              <div class="sub-line">${esc(item.return_date || item.created_at.slice(0, 10))}</div>
            </td>
            <td>
              <div><strong>${esc(item.customer_name || "Müşteri Belirtilmedi")}</strong></div>
              <div class="sub-line">
                <span class="market-pill">${esc(item.marketplace)}</span>
                ${item.order_no ? ` • ${esc(item.order_no)}` : ""}
              </div>
            </td>
            <td>
              <div><strong>${esc(item.product_name)}</strong></div>
              <div class="sub-line">
                ${item.sku ? `SKU: ${esc(item.sku)}` : ""}
                ${item.return_reason ? ` • ${esc(item.return_reason)}` : ""}
              </div>
            </td>
            <td>
              <div>${esc(item.tracking_no || "-")}</div>
              <div class="sub-line">${esc(item.cargo_company || "")}</div>
            </td>
            <td class="text-center"><strong>${item.quantity}</strong></td>
            <td class="text-right"><strong>${formatMoney(item.refund_amount)}</strong></td>
            <td>${statusBadgeHtml(item.status)}</td>
            <td class="text-center">
              <span class="evidence-badge ${evCount > 0 ? "has-files" : ""}">📎 ${evCount}</span>
            </td>
            <td class="text-right" data-no-open="1">
              <button type="button" class="btn btn-outline btn-sm" data-open-id="${item.id}">Detay</button>
            </td>
          </tr>
        `;
      })
      .join("");
  }

  function renderMobileCards() {
    const box = $("mobile-cards");
    if (!state.returns.length) {
      box.innerHTML = `<div class="empty-state">Kriterlere uygun iade kaydı bulunamadı.</div>`;
      return;
    }

    box.innerHTML = state.returns
      .map((item) => {
        const evCount = Number(item.evidence_count || 0);
        return `
          <div class="m-card" data-open-id="${item.id}">
            <div class="m-card-top">
              <span class="code-chip">${esc(item.return_code)}</span>
              ${statusBadgeHtml(item.status)}
            </div>
            <div class="m-card-title">${esc(item.product_name)}</div>
            <div class="sub-line">${esc(item.customer_name || "-")} • ${esc(item.marketplace)}</div>
            <div class="m-card-meta">
              <span>Takip: ${esc(item.tracking_no || "-")}</span>
              <span><strong>${item.quantity} Adet • ${formatMoney(item.refund_amount)}</strong></span>
              <span>📎 ${evCount}</span>
            </div>
          </div>
        `;
      })
      .join("");
  }

  function syncBulkBar() {
    const count = state.selectedIds.size;
    $("bulk-count").textContent = String(count);
    $("bulk-bar").classList.toggle("hidden", count === 0);
    const allCheckbox = $("select-all-rows");
    allCheckbox.checked = state.returns.length > 0 && state.returns.every((r) => state.selectedIds.has(r.id));
  }

  function switchTab(tabName) {
    document.querySelectorAll(".nav-tab, .mobile-nav-btn[data-tab]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.tab === tabName);
    });
    document.querySelectorAll(".tab-panel").forEach((panel) => {
      panel.classList.toggle("active", panel.id === `tab-${tabName}`);
    });
    if (tabName === "dashboard") {
      loadStats().catch((e) => toast(e.message, "error"));
    }
    if (tabName === "scanner") {
      setTimeout(() => $("scanner-input").focus(), 60);
    }
  }

  function openModal(id) {
    $(id).classList.remove("hidden");
  }

  function closeModal(id) {
    $(id).classList.add("hidden");
  }

  function setSelectValue(selectId, value) {
    const select = $(selectId);
    if (value && !Array.from(select.options).some((o) => o.value === value)) {
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = value;
      select.appendChild(opt);
    }
    select.value = value;
  }

  function openReturnModal(item = null, prefill = {}) {
    $("return-modal-title").textContent = item ? `İade Düzenle (${item.return_code})` : "Yeni İade Kaydı";
    $("form-return-id").value = item ? item.id : "";
    const targetMarket = (item && item.marketplace) || prefill.marketplace || CFG.marketplaces[0] || "";
    setSelectValue("form-marketplace", targetMarket);
    $("form-order-no").value = (item && item.order_no) || prefill.order_no || "";
    $("form-tracking-no").value = (item && item.tracking_no) || prefill.tracking_no || "";
    setSelectValue("form-cargo", (item && item.cargo_company) || "");
    $("form-product-name").value = (item && item.product_name) || "";
    $("form-sku").value = (item && item.sku) || "";
    $("form-barcode").value = (item && item.barcode) || prefill.barcode || "";
    $("form-customer-name").value = (item && item.customer_name) || "";
    $("form-customer-phone").value = (item && item.customer_phone) || "";
    $("form-quantity").value = (item && item.quantity) || 1;
    $("form-amount").value = (item && item.refund_amount) || 0;
    $("form-status").value = (item && item.status) || "beklemede";
    $("form-condition").value = (item && item.condition_grade) || "belirtilmedi";
    $("form-reason").value = (item && item.return_reason) || "";
    $("form-date").value = (item && item.return_date) || new Date().toISOString().slice(0, 10);
    $("form-notes").value = (item && item.notes) || "";
    openModal("return-modal");
    setTimeout(() => $("form-product-name").focus(), 50);
  }

  async function openDetailDrawer(returnId) {
    state.activeReturnId = returnId;
    const data = await api(`/api/returns/${returnId}`);
    renderDetailDrawer(data.item);
    openModal("detail-drawer");
  }

  function renderDetailDrawer(item) {
    $("drawer-code").textContent = item.return_code;
    $("drawer-title").textContent = item.product_name;

    // Quick status pills
    $("drawer-status-pills").innerHTML = CFG.statuses
      .map((s) => {
        const isActive = s.key === item.status;
        return `
          <button
            type="button"
            class="status-pill-btn ${isActive ? "active" : ""}"
            style="color:${esc(s.color)};background:${esc(s.bg)}"
            data-quick-status="${esc(s.key)}"
          >
            ${esc(s.label)}
          </button>
        `;
      })
      .join("");

    // Info grid
    const infos = [
      ["Pazaryeri", item.marketplace],
      ["Sipariş No", item.order_no || "-"],
      ["Kargo Takip No", item.tracking_no || "-"],
      ["Kargo Firması", item.cargo_company || "-"],
      ["Müşteri", item.customer_name || "-"],
      ["Telefon", item.customer_phone || "-"],
      ["Stok Kodu (SKU)", item.sku || "-"],
      ["Barkod", item.barcode || "-"],
      ["Adet / Tutar", `${item.quantity} Adet / ${formatMoney(item.refund_amount)}`],
      ["Kondisyon", CONDITION_BY_KEY[item.condition_grade] || item.condition_grade],
      ["İade Nedeni", item.return_reason || "-"],
      ["İade Tarihi", item.return_date || "-"],
      ["Notlar", item.notes || "-"],
    ];
    $("drawer-info-grid").innerHTML = infos
      .map(([k, v]) => `<div class="info-item"><small>${esc(k)}</small><strong>${esc(v)}</strong></div>`)
      .join("");

    // Evidence gallery
    const evidence = item.evidence || [];
    $("drawer-evidence-count").textContent = `${evidence.length} dosya`;
    if (!evidence.length) {
      $("drawer-evidence-grid").innerHTML = `<p class="muted-text" style="grid-column:1/-1">Henüz fotoğraf veya kanıt yüklenmemiş.</p>`;
    } else {
      $("drawer-evidence-grid").innerHTML = evidence
        .map((ev) => {
          const url = `/api/evidence/${ev.id}/file`;
          const isPdf = (ev.mime_type || "").includes("pdf") || ev.file_name.toLowerCase().endsWith(".pdf");
          return `
            <div class="evidence-card">
              ${
                isPdf
                  ? `<a class="evidence-pdf" href="${url}" target="_blank" rel="noopener">PDF Kanıt</a>`
                  : `<img class="evidence-thumb" src="${url}" alt="${esc(ev.caption || ev.file_name)}" data-lightbox-src="${url}" data-lightbox-cap="${esc(ev.caption || ev.file_name)}" />`
              }
              <div class="evidence-meta">
                <span title="${esc(ev.caption || ev.file_name)}">${esc(ev.caption || ev.file_name)}</span>
                <button type="button" class="evidence-del" data-delete-evidence="${ev.id}" title="Sil">🗑</button>
              </div>
            </div>
          `;
        })
        .join("");
    }

    // History timeline
    const history = item.history || [];
    $("drawer-history").innerHTML = history
      .map((h) => {
        const toLabel = (STATUS_BY_KEY[h.new_status] || {}).label || h.new_status;
        const fromLabel = h.old_status ? ((STATUS_BY_KEY[h.old_status] || {}).label || h.old_status) : "";
        const transition = fromLabel && fromLabel !== toLabel ? `${fromLabel} → ${toLabel}` : toLabel;
        return `
          <div class="timeline-item">
            <div><strong>${esc(transition)}</strong>${h.note ? ` — ${esc(h.note)}` : ""}</div>
            <div class="timeline-time">${esc(h.created_at)}</div>
          </div>
        `;
      })
      .join("");
  }

  async function uploadEvidenceFiles(files) {
    if (!state.activeReturnId || !files || !files.length) return;
    const fd = new FormData();
    for (const f of files) {
      fd.append("files", f);
    }
    fd.append("caption", $("evidence-caption-input").value.trim());

    try {
      const data = await api(`/api/returns/${state.activeReturnId}/evidence`, {
        method: "POST",
        body: fd,
      });
      $("evidence-caption-input").value = "";
      $("evidence-files-input").value = "";
      renderDetailDrawer(data.item);
      await Promise.all([loadReturns(), loadStats()]);
      toast(data.message, "success");
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function performScanLookup(rawCode, switchToScannerTab = true) {
    const code = rawCode.trim();
    if (!code) return;
    if (switchToScannerTab) switchTab("scanner");
    $("scanner-input").value = code;

    try {
      const data = await api(`/api/lookup?code=${encodeURIComponent(code)}`);
      const resultsBox = $("scanner-results");
      if (!data.items.length) {
        resultsBox.innerHTML = `
          <div class="empty-state">
            <div class="empty-icon">🔍</div>
            <p><strong>"${esc(code)}"</strong> koduyla eşleşen bir iade kaydı bulunamadı.</p>
            <button type="button" class="btn btn-primary" id="btn-create-from-scan" data-scan-code="${esc(code)}">
              ＋ Bu Kod ile Yeni İade Oluştur
            </button>
          </div>
        `;
        return;
      }

      resultsBox.innerHTML = data.items
        .map(
          (item) => `
          <div class="scan-match-card">
            <div>
              <div><span class="code-chip">${esc(item.return_code)}</span> ${statusBadgeHtml(item.status)}</div>
              <div style="margin-top:6px"><strong>${esc(item.product_name)}</strong></div>
              <div class="sub-line">
                ${esc(item.customer_name || "-")} • ${esc(item.marketplace)}
                ${item.tracking_no ? ` • Takip: ${esc(item.tracking_no)}` : ""}
                ${item.barcode ? ` • Barkod: ${esc(item.barcode)}` : ""}
              </div>
            </div>
            <div style="display:flex;gap:8px;flex-wrap:wrap">
              <button type="button" class="btn btn-outline btn-sm" data-scan-mark="${item.id}" data-next-status="teslim_alindi">Teslim Alındı</button>
              <button type="button" class="btn btn-primary btn-sm" data-open-id="${item.id}">Detay & Fotoğraf</button>
            </div>
          </div>
        `
        )
        .join("");
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function toggleCamera() {
    if (state.cameraStream) {
      stopCamera();
      return;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      toast("Bu tarayıcıda kamera erişimi desteklenmiyor.", "error");
      return;
    }
    try {
      state.cameraStream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment" },
      });
      const video = $("camera-video");
      video.srcObject = state.cameraStream;
      $("camera-box").classList.remove("hidden");
      $("btn-toggle-camera").textContent = "📷 Kamerayı Kapat";

      if ("BarcodeDetector" in window) {
        const detector = new window.BarcodeDetector();
        state.scanTimer = setInterval(async () => {
          if (!state.cameraStream || video.readyState < 2) return;
          try {
            const barcodes = await detector.detect(video);
            if (barcodes && barcodes.length > 0 && barcodes[0].rawValue) {
              const code = barcodes[0].rawValue;
              stopCamera();
              performScanLookup(code, false);
              toast(`Kod okundu: ${code}`, "success");
            }
          } catch (_) {
            // Ignore frame decode errors
          }
        }, 450);
      } else {
        toast("Kamera açık; otomatik barkod tanıma desteklenmiyorsa kodu yukarıya yazabilirsiniz.", "info");
      }
    } catch (err) {
      toast("Kamera açılamadı: " + err.message, "error");
    }
  }

  function stopCamera() {
    if (state.scanTimer) {
      clearInterval(state.scanTimer);
      state.scanTimer = null;
    }
    if (state.cameraStream) {
      state.cameraStream.getTracks().forEach((t) => t.stop());
      state.cameraStream = null;
    }
    $("camera-box").classList.add("hidden");
    $("btn-toggle-camera").textContent = "📷 Kamera Aç";
  }

  function renderImportPreview(data) {
    state.importToken = data.import_token;
    state.importHeaders = data.headers || [];
    $("import-file-summary").textContent = `Dosya: ${data.filename} — Toplam ${data.total_rows} satır algılandı.`;

    const mappingGrid = $("import-mapping-grid");
    mappingGrid.innerHTML = IMPORT_FIELD_LABELS.map((f) => {
      const selectedIdx = data.mapping[f.key] !== undefined ? String(data.mapping[f.key]) : "";
      const options = [`<option value="">-- Eşleştirme Yok --</option>`]
        .concat(
          state.importHeaders.map(
            (h, idx) => `<option value="${idx}" ${String(idx) === selectedIdx ? "selected" : ""}>${esc(h || `Sütun ${idx + 1}`)}</option>`
          )
        )
        .join("");
      return `
        <div class="field">
          <label>${esc(f.label)}</label>
          <select class="import-map-select" data-field="${esc(f.key)}">${options}</select>
        </div>
      `;
    }).join("");

    const thead = `<thead><tr>${state.importHeaders.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead>`;
    const tbody = `<tbody>${(data.preview_rows || [])
      .map((row) => `<tr>${state.importHeaders.map((_, idx) => `<td>${esc(row[idx] || "")}</td>`).join("")}</tr>`)
      .join("")}</tbody>`;
    $("import-preview-table").innerHTML = thead + tbody;

    $("import-step-2").classList.remove("hidden");
    $("btn-import-commit").classList.remove("hidden");
  }

  function bindEvents() {
    // Tab switching
    document.querySelectorAll(".nav-tab, .mobile-nav-btn[data-tab]").forEach((btn) => {
      btn.addEventListener("click", () => switchTab(btn.dataset.tab));
    });

    // KPI click filters
    document.querySelectorAll(".kpi-card").forEach((card) => {
      card.addEventListener("click", () => {
        const st = card.dataset.filterStatus || "all";
        $("filter-status").value = st;
        switchTab("records");
        loadReturns().catch((e) => toast(e.message, "error"));
      });
    });

    // Filter changes
    let searchDebounce = null;
    $("filter-search").addEventListener("input", () => {
      clearTimeout(searchDebounce);
      searchDebounce = setTimeout(() => loadReturns().catch((e) => toast(e.message, "error")), 220);
    });
    ["filter-status", "filter-marketplace", "filter-reason", "filter-evidence", "filter-date-from", "filter-date-to"].forEach((id) => {
      $(id).addEventListener("change", () => loadReturns().catch((e) => toast(e.message, "error")));
    });

    $("btn-reset-filters").addEventListener("click", () => {
      $("filter-search").value = "";
      $("filter-status").value = "all";
      $("filter-marketplace").value = "all";
      $("filter-reason").value = "all";
      $("filter-evidence").value = "";
      $("filter-date-from").value = "";
      $("filter-date-to").value = "";
      loadReturns().catch((e) => toast(e.message, "error"));
    });

    // Quick scan & scanner tab
    $("quick-scan-form").addEventListener("submit", (e) => {
      e.preventDefault();
      performScanLookup($("quick-scan-input").value, true);
    });
    $("scanner-form").addEventListener("submit", (e) => {
      e.preventDefault();
      performScanLookup($("scanner-input").value, false);
    });
    $("btn-toggle-camera").addEventListener("click", toggleCamera);
    $("btn-stop-camera").addEventListener("click", stopCamera);

    // Scanner dynamic actions
    $("scanner-results").addEventListener("click", async (e) => {
      const createBtn = e.target.closest("#btn-create-from-scan");
      if (createBtn) {
        const code = createBtn.dataset.scanCode || "";
        openReturnModal(null, { tracking_no: code });
        return;
      }
      const markBtn = e.target.closest("[data-scan-mark]");
      if (markBtn) {
        const rid = Number(markBtn.dataset.scanMark);
        try {
          await api(`/api/returns/${rid}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              status: markBtn.dataset.nextStatus,
              history_note: "Barkod kontrol ekranından teslim alındı",
            }),
          });
          toast("Kayıt 'Teslim Alındı' olarak güncellendi.", "success");
          await Promise.all([loadReturns(), loadStats()]);
          performScanLookup($("scanner-input").value, false);
        } catch (err) {
          toast(err.message, "error");
        }
      }
    });

    // Open new modal
    $("btn-new-return").addEventListener("click", () => openReturnModal(null));
    $("mobile-btn-add").addEventListener("click", () => openReturnModal(null));

    // Export Excel
    $("btn-export-excel").addEventListener("click", () => {
      const qs = buildFilterQuery();
      window.location.href = `/api/export/excel${qs ? `?${qs}` : ""}`;
    });

    // Save return form
    $("return-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const id = $("form-return-id").value;
      const payload = {
        marketplace: $("form-marketplace").value,
        order_no: $("form-order-no").value.trim(),
        tracking_no: $("form-tracking-no").value.trim(),
        cargo_company: $("form-cargo").value,
        product_name: $("form-product-name").value.trim(),
        sku: $("form-sku").value.trim(),
        barcode: $("form-barcode").value.trim(),
        customer_name: $("form-customer-name").value.trim(),
        customer_phone: $("form-customer-phone").value.trim(),
        quantity: Number($("form-quantity").value || 1),
        refund_amount: Number($("form-amount").value || 0),
        status: $("form-status").value,
        condition_grade: $("form-condition").value,
        return_reason: $("form-reason").value,
        return_date: $("form-date").value,
        notes: $("form-notes").value.trim(),
      };
      try {
        if (id) {
          await api(`/api/returns/${id}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          });
          toast("İade kaydı güncellendi.", "success");
          if (state.activeReturnId === Number(id) && !$("detail-drawer").classList.contains("hidden")) {
            await openDetailDrawer(Number(id));
          }
        } else {
          await api("/api/returns", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          });
          toast("Yeni iade kaydı eklendi.", "success");
        }
        closeModal("return-modal");
        await Promise.all([loadReturns(), loadStats()]);
      } catch (err) {
        toast(err.message, "error");
      }
    });

    // Row click / checkboxes / open detail
    document.addEventListener("click", (e) => {
      const closeTarget = e.target.closest("[data-close-modal]");
      if (closeTarget) {
        closeModal(closeTarget.dataset.closeModal);
        return;
      }

      const openTarget = e.target.closest("[data-open-id]");
      if (openTarget) {
        openDetailDrawer(Number(openTarget.dataset.openId)).catch((err) => toast(err.message, "error"));
        return;
      }

      const row = e.target.closest("tr[data-row-id]");
      if (row && !e.target.closest("[data-no-open]")) {
        openDetailDrawer(Number(row.dataset.rowId)).catch((err) => toast(err.message, "error"));
      }
    });

    $("returns-tbody").addEventListener("change", (e) => {
      const cb = e.target.closest(".row-checkbox");
      if (!cb) return;
      const id = Number(cb.dataset.id);
      if (cb.checked) state.selectedIds.add(id);
      else state.selectedIds.delete(id);
      syncBulkBar();
    });

    $("select-all-rows").addEventListener("change", (e) => {
      if (e.target.checked) {
        state.returns.forEach((r) => state.selectedIds.add(r.id));
      } else {
        state.selectedIds.clear();
      }
      renderReturnsTable();
      syncBulkBar();
    });

    $("btn-bulk-clear").addEventListener("click", () => {
      state.selectedIds.clear();
      renderReturnsTable();
      syncBulkBar();
    });

    $("btn-bulk-apply").addEventListener("click", async () => {
      const ids = Array.from(state.selectedIds);
      const status = $("bulk-status-select").value;
      if (!ids.length) return;
      try {
        const res = await api("/api/returns/bulk-status", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ids, status }),
        });
        state.selectedIds.clear();
        toast(res.message, "success");
        await Promise.all([loadReturns(), loadStats()]);
      } catch (err) {
        toast(err.message, "error");
      }
    });

    // Drawer quick status
    $("drawer-status-pills").addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-quick-status]");
      if (!btn || !state.activeReturnId) return;
      try {
        const data = await api(`/api/returns/${state.activeReturnId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            status: btn.dataset.quickStatus,
            history_note: $("drawer-status-note").value.trim(),
          }),
        });
        $("drawer-status-note").value = "";
        renderDetailDrawer(data.item);
        await Promise.all([loadReturns(), loadStats()]);
        toast("Durum güncellendi.", "success");
      } catch (err) {
        toast(err.message, "error");
      }
    });

    $("drawer-btn-edit").addEventListener("click", async () => {
      if (!state.activeReturnId) return;
      const data = await api(`/api/returns/${state.activeReturnId}`);
      openReturnModal(data.item);
    });

    $("drawer-btn-delete").addEventListener("click", async () => {
      if (!state.activeReturnId) return;
      if (!confirm("Bu iade kaydını ve ekli kanıtları kalıcı olarak silmek istiyor musunuz?")) return;
      try {
        await api(`/api/returns/${state.activeReturnId}`, { method: "DELETE" });
        closeModal("detail-drawer");
        state.selectedIds.delete(state.activeReturnId);
        state.activeReturnId = null;
        await Promise.all([loadReturns(), loadStats()]);
        toast("Kayıt silindi.", "success");
      } catch (err) {
        toast(err.message, "error");
      }
    });

    // Evidence upload & delete & lightbox
    const pickTrigger = () => $("evidence-files-input").click();
    $("evidence-drop-trigger").addEventListener("click", pickTrigger);
    $("btn-pick-evidence").addEventListener("click", pickTrigger);
    $("evidence-files-input").addEventListener("change", (e) => uploadEvidenceFiles(e.target.files));

    const dropzone = $("evidence-upload-form");
    dropzone.addEventListener("dragover", (e) => {
      e.preventDefault();
      dropzone.classList.add("dragover");
    });
    dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
    dropzone.addEventListener("drop", (e) => {
      e.preventDefault();
      dropzone.classList.remove("dragover");
      uploadEvidenceFiles(e.dataTransfer.files);
    });

    $("drawer-evidence-grid").addEventListener("click", async (e) => {
      const delBtn = e.target.closest("[data-delete-evidence]");
      if (delBtn) {
        const evId = Number(delBtn.dataset.deleteEvidence);
        try {
          await api(`/api/evidence/${evId}`, { method: "DELETE" });
          await openDetailDrawer(state.activeReturnId);
          await loadReturns();
          toast("Kanıt silindi.", "success");
        } catch (err) {
          toast(err.message, "error");
        }
        return;
      }

      const thumb = e.target.closest("[data-lightbox-src]");
      if (thumb) {
        $("lightbox-img").src = thumb.dataset.lightboxSrc;
        $("lightbox-caption").textContent = thumb.dataset.lightboxCap || "";
        $("lightbox").classList.remove("hidden");
      }
    });

    $("lightbox-close").addEventListener("click", () => $("lightbox").classList.add("hidden"));
    $("lightbox-x").addEventListener("click", () => $("lightbox").classList.add("hidden"));

    // Import modal
    $("btn-open-import").addEventListener("click", () => {
      state.importToken = null;
      $("import-file-input").value = "";
      $("import-step-2").classList.add("hidden");
      $("btn-import-commit").classList.add("hidden");
      openModal("import-modal");
    });

    $("btn-upload-preview").addEventListener("click", async () => {
      const file = $("import-file-input").files[0];
      if (!file) {
        toast("Lütfen önce bir Excel veya CSV dosyası seçin.", "error");
        return;
      }
      const fd = new FormData();
      fd.append("file", file);
      try {
        const data = await api("/api/import/preview", { method: "POST", body: fd });
        renderImportPreview(data);
        toast("Dosya başarıyla analiz edildi.", "success");
      } catch (err) {
        toast(err.message, "error");
      }
    });

    $("btn-import-commit").addEventListener("click", async () => {
      if (!state.importToken) return;
      const mapping = {};
      document.querySelectorAll(".import-map-select").forEach((sel) => {
        if (sel.value !== "") mapping[sel.dataset.field] = Number(sel.value);
      });
      try {
        const res = await api("/api/import/commit", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            import_token: state.importToken,
            mapping,
            duplicate_mode: $("import-dup-mode").value,
            default_marketplace: $("import-def-marketplace").value,
            default_status: $("import-def-status").value,
          }),
        });
        toast(res.message, "success");
        closeModal("import-modal");
        await Promise.all([loadReturns(), loadStats()]);
      } catch (err) {
        toast(err.message, "error");
      }
    });

    // Keyboard shortcuts
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        ["lightbox", "return-modal", "import-modal", "detail-drawer"].forEach(closeModal);
      }
    });
  }

  async function init() {
    bindEvents();
    try {
      await Promise.all([loadReturns(), loadStats()]);
    } catch (err) {
      toast(err.message, "error");
    }
  }

  init();
})();

