const token = document.body.dataset.token;
const state = {
  sessions: [],
  providers: [],
  operations: [],
  selected: new Set(),
  activeProvider: null,
  filter: "all",
  plan: null,
  restoreId: null,
  draggingId: null,
  pointerDrag: null,
  mouseDrag: null,
};

const providerColors = ["#86d39a", "#e7b85c", "#7bb7d7", "#c797d8", "#dc8d69", "#a8c66c"];
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, char => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
})[char]);

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {"Content-Type": "application/json", "X-CSM-Token": token, ...(options.headers || {})},
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
}

function toast(message, error = false) {
  const node = $("#toast");
  node.textContent = message;
  node.className = error ? "toast show error" : "toast show";
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.className = "toast", 4200);
}

function debounce(callback, delay) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => callback(...args), delay);
  };
}

function formatBytes(value) {
  const units = ["B", "KB", "MB", "GB"];
  let amount = Number(value || 0);
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1; }
  return `${amount.toFixed(unit ? 1 : 0)} ${units[unit]}`;
}

function formatDate(value) {
  return new Date(Number(value) * 1000).toLocaleString("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

function selectedSessions() {
  return state.sessions.filter(session => state.selected.has(session.id));
}

function selectedSource() {
  return selectedSessions()[0]?.provider || null;
}

function providerColor(provider) {
  const index = Math.max(0, state.providers.indexOf(provider));
  return providerColors[index % providerColors.length];
}

async function load() {
  try {
    const [status, sessionData, operationData] = await Promise.all([
      api("/api/status"), api("/api/sessions"), api("/api/operations"),
    ]);
    state.sessions = sessionData.sessions;
    state.providers = status.providers;
    state.operations = operationData.operations;
    if (!state.activeProvider || !state.sessions.some(session => session.provider === state.activeProvider)) {
      state.activeProvider = observedProviders()[0] || null;
    }
    $("#homePath").textContent = `${status.codex_home}  ·  backups ${status.data_dir}`;
    const healthy = status.databases.length > 0
      && status.databases.every(database => database.integrity === "ok")
      && status.audit_chain_valid;
    $("#health").className = `health-pill ${healthy ? "ok" : "bad"}`;
    $("#health").innerHTML = `<span></span><strong>${healthy ? "存储正常" : "需要检查"}</strong>`;
    $("#auditHealth").className = `audit-health ${status.audit_chain_valid ? "ok" : "bad"}`;
    $("#auditHealth").innerHTML = `<span></span><strong>${status.audit_chain_valid ? "审计哈希链完整" : "审计哈希链异常"}</strong>`;
    $("#operationCount").textContent = state.operations.length;
    renderAll();
  } catch (error) {
    $("#health").className = "health-pill bad";
    $("#health").innerHTML = "<span></span><strong>连接失败</strong>";
    toast(error.message, true);
  }
}

function observedProviders() {
  const counts = new Map();
  state.sessions.forEach(session => counts.set(session.provider, (counts.get(session.provider) || 0) + 1));
  return [...counts.keys()].sort((a, b) => (counts.get(b) - counts.get(a)) || a.localeCompare(b));
}

function renderAll() {
  renderProviders();
  renderTargets();
  renderSessions();
  renderQueue();
  renderOperations();
}

function renderProviders() {
  const providers = observedProviders();
  $("#providerCount").textContent = providers.length;
  $("#providerList").innerHTML = providers.map(provider => {
    const count = state.sessions.filter(session => session.provider === provider).length;
    const active = provider === state.activeProvider;
    return `<button type="button" class="provider-item ${active ? "active" : ""}" data-provider="${escapeHtml(provider)}" aria-pressed="${active}">
      <span class="provider-dot" style="--provider-color:${providerColor(provider)}"></span>
      <span class="provider-name">${escapeHtml(provider)}</span>
      <small>${count}</small>
    </button>`;
  }).join("");
  $$(".provider-item").forEach(button => button.addEventListener("click", () => {
    state.activeProvider = button.dataset.provider;
    renderAll();
  }));
  $("#currentProviderLabel").textContent = state.activeProvider || "全部会话";
  $("#currentProviderDot").style.background = providerColor(state.activeProvider);
}

function renderTargets() {
  const source = selectedSource() || state.activeProvider;
  const current = $("#targetProvider").value;
  const options = state.providers.filter(provider => provider !== source);
  $("#targetProvider").innerHTML = options.map(provider =>
    `<option value="${escapeHtml(provider)}">${escapeHtml(provider)}</option>`
  ).join("");
  if (options.includes(current)) $("#targetProvider").value = current;
}

function visibleSessions() {
  const query = $("#search").value.trim().toLocaleLowerCase();
  const sessions = state.sessions.filter(session => {
    if (state.activeProvider && session.provider !== state.activeProvider) return false;
    if (state.filter === "ready" && (session.locked || session.archived)) return false;
    if (state.filter === "locked" && !session.locked) return false;
    if (state.filter === "archived" && !session.archived) return false;
    return !query || `${session.title} ${session.cwd} ${session.id} ${session.model || ""}`.toLocaleLowerCase().includes(query);
  });
  const sort = $("#sortSessions").value;
  sessions.sort((a, b) => {
    if (sort === "oldest") return a.updated_at - b.updated_at;
    if (sort === "title") return a.title.localeCompare(b.title, "zh-CN");
    if (sort === "size") return b.size_bytes - a.size_bytes;
    return b.updated_at - a.updated_at;
  });
  return sessions;
}

function renderSessions() {
  const sessions = visibleSessions();
  $("#visibleCount").textContent = sessions.length;
  $("#selectedCount").textContent = state.selected.size;
  $("#selectAll").checked = sessions.length > 0 && sessions.every(session => state.selected.has(session.id));
  $("#selectAll").indeterminate = sessions.some(session => state.selected.has(session.id)) && !$("#selectAll").checked;

  $("#sessionList").innerHTML = sessions.length ? sessions.map(session => {
    const selected = state.selected.has(session.id);
    const chips = [
      session.locked ? '<span class="status-chip locked">使用中</span>' : "",
      session.archived ? '<span class="status-chip archived">已归档</span>' : "",
    ].join("");
    return `<article class="session-row ${selected ? "selected" : ""} ${session.locked ? "locked" : ""}" data-draggable="${!session.locked}" data-id="${escapeHtml(session.id)}">
      <label class="session-check" aria-label="选择 ${escapeHtml(session.title)}"><input type="checkbox" ${selected ? "checked" : ""} ${session.locked ? "disabled" : ""}></label>
      <div class="session-primary"><div class="session-title-line"><span class="session-title" title="${escapeHtml(session.title)}">${escapeHtml(session.title)}</span>${chips}</div><span class="session-id">${escapeHtml(session.id)}</span></div>
      <div class="session-workspace-meta"><span class="session-model">${escapeHtml(session.model || "model unknown")}</span><span class="session-cwd" title="${escapeHtml(session.cwd)}">${escapeHtml(session.cwd || "无工作目录")}</span></div>
      <time class="session-time">${formatDate(session.updated_at)}</time>
      <span class="session-size">${formatBytes(session.size_bytes)}</span>
      <button class="add-button ${selected ? "added" : ""}" type="button" ${session.locked ? "disabled" : ""}>${selected ? "移除" : "加入"}</button>
    </article>`;
  }).join("") : '<div class="empty-state"><div><strong>没有匹配的 Session</strong><p>调整 provider、状态筛选或搜索关键词。</p></div></div>';

  $$(".session-row").forEach(row => {
    const id = row.dataset.id;
    row.querySelector("input")?.addEventListener("change", event => setSessionSelected(id, event.target.checked));
    row.querySelector(".add-button")?.addEventListener("click", () => setSessionSelected(id, !state.selected.has(id)));
    setupPointerDrag(row, id);
  });
}

function setupPointerDrag(row, id) {
  if (row.dataset.draggable !== "true") return;
  row.addEventListener("pointerdown", event => {
    if (event.pointerType === "mouse" || event.button !== 0 || event.target.closest("button, input, label")) return;
    state.pointerDrag = {
      id,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      active: false,
      row,
      ghost: null,
    };
    row.setPointerCapture(event.pointerId);
  });
  row.addEventListener("pointermove", event => {
    const drag = state.pointerDrag;
    if (!drag || drag.pointerId !== event.pointerId || drag.row !== row) return;
    const distance = Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY);
    if (!drag.active && distance < 8) return;
    if (!drag.active) {
      drag.active = true;
      state.draggingId = id;
      row.classList.add("dragging");
      const session = state.sessions.find(item => item.id === id);
      drag.ghost = document.createElement("div");
      drag.ghost.className = "drag-ghost";
      drag.ghost.textContent = session?.title || id;
      document.body.append(drag.ghost);
    }
    event.preventDefault();
    drag.ghost.style.transform = `translate(${event.clientX + 12}px, ${event.clientY + 12}px)`;
    $("#dropZone").classList.toggle("drag-over", pointInside($("#dropZone"), event.clientX, event.clientY));
  });
  row.addEventListener("pointerup", event => finishPointerDrag(event));
  row.addEventListener("pointercancel", event => finishPointerDrag(event));
  row.addEventListener("mousedown", event => {
    if (event.button !== 0 || event.target.closest("button, input, label")) return;
    state.mouseDrag = {
      id,
      startX: event.clientX,
      startY: event.clientY,
      active: false,
      row,
      ghost: null,
    };
  });
}

function pointInside(element, x, y) {
  const rect = element.getBoundingClientRect();
  return x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
}

function finishPointerDrag(event) {
  const drag = state.pointerDrag;
  if (!drag || drag.pointerId !== event.pointerId) return;
  const dropped = drag.active && pointInside($("#dropZone"), event.clientX, event.clientY);
  drag.row.classList.remove("dragging");
  drag.ghost?.remove();
  $("#dropZone").classList.remove("drag-over");
  state.pointerDrag = null;
  state.draggingId = null;
  if (dropped) {
    setSessionSelected(drag.id, true);
    toast("Session 已加入迁移队列。");
  }
}

function moveMouseDrag(event) {
  const drag = state.mouseDrag;
  if (!drag) return;
  const distance = Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY);
  if (!drag.active && distance < 8) return;
  if (!drag.active) activateDragVisuals(drag);
  event.preventDefault();
  updateDragVisuals(drag, event.clientX, event.clientY);
}

function finishMouseDrag(event) {
  const drag = state.mouseDrag;
  if (!drag) return;
  const dropped = drag.active && pointInside($("#dropZone"), event.clientX, event.clientY);
  clearDragVisuals(drag);
  state.mouseDrag = null;
  if (dropped) {
    setSessionSelected(drag.id, true);
    toast("Session 已加入迁移队列。");
  }
}

function activateDragVisuals(drag) {
  drag.active = true;
  state.draggingId = drag.id;
  drag.row.classList.add("dragging");
  const session = state.sessions.find(item => item.id === drag.id);
  drag.ghost = document.createElement("div");
  drag.ghost.className = "drag-ghost";
  drag.ghost.textContent = session?.title || drag.id;
  document.body.append(drag.ghost);
}

function updateDragVisuals(drag, x, y) {
  drag.ghost.style.transform = `translate(${x + 12}px, ${y + 12}px)`;
  $("#dropZone").classList.toggle("drag-over", pointInside($("#dropZone"), x, y));
}

function clearDragVisuals(drag) {
  drag.row.classList.remove("dragging");
  drag.ghost?.remove();
  $("#dropZone").classList.remove("drag-over");
  state.draggingId = null;
}

function setSessionSelected(id, selected) {
  const session = state.sessions.find(item => item.id === id);
  if (!session || session.locked) {
    toast("正在使用的 Session 必须先关闭后才能加入迁移。", true);
    return;
  }
  const source = selectedSource();
  if (selected && source && source !== session.provider) {
    toast(`一次迁移只能包含同一来源 provider。当前来源是 ${source}。`, true);
    return;
  }
  selected ? state.selected.add(id) : state.selected.delete(id);
  state.plan = null;
  renderTargets();
  renderSessions();
  renderQueue();
}

function renderQueue() {
  const sessions = selectedSessions();
  $("#queueCount").textContent = sessions.length;
  $("#selectedCount").textContent = sessions.length;
  $("#sourceSummary").textContent = sessions[0]?.provider || "尚未选择";
  $("#backupSummary").textContent = state.plan
    ? formatBytes(state.plan.estimated_backup_bytes)
    : sessions.length ? `至少 ${formatBytes(sessions.reduce((sum, session) => sum + session.size_bytes, 0))}` : "—";
  $("#previewButton").disabled = sessions.length === 0 || !$("#targetProvider").value;
  $("#transferQueue").innerHTML = sessions.length ? sessions.map(session => `
    <div class="queue-item"><span></span><div><strong>${escapeHtml(session.title)}</strong><small>${escapeHtml(session.id.slice(0, 13))} · ${formatBytes(session.size_bytes)}</small></div><button type="button" class="queue-remove" data-id="${escapeHtml(session.id)}" aria-label="从队列移除 ${escapeHtml(session.title)}">×</button></div>
  `).join("") : '<div class="queue-empty">队列为空<br>拖入或点按“加入”开始</div>';
  $$(".queue-remove").forEach(button => button.addEventListener("click", () => setSessionSelected(button.dataset.id, false)));
}

function renderOperations() {
  $("#operationCount").textContent = state.operations.length;
  $("#operations").innerHTML = state.operations.length ? state.operations.map(operation => {
    const canRestore = operation.kind === "migration" && operation.status === "completed" && !operation.restored_by;
    const route = operation.kind === "migration"
      ? `${escapeHtml(operation.source_provider)} → ${escapeHtml(operation.target_provider)}`
      : `恢复 ${escapeHtml(operation.restores_operation || "snapshot")}`;
    const status = operation.restored_by ? "已恢复" : operation.status;
    return `<article class="operation"><div class="operation-top"><strong class="operation-route">${route}</strong><span class="operation-status ${escapeHtml(operation.status)}">${escapeHtml(status)}</span></div><div class="operation-bottom"><span>${escapeHtml(new Date(operation.created_at).toLocaleString("zh-CN"))} · ${(operation.session_ids || []).length} sessions</span>${canRestore ? `<button class="restore-button" type="button" data-id="${escapeHtml(operation.operation_id)}">恢复</button>` : `<code>${escapeHtml(operation.operation_id)}</code>`}</div></article>`;
  }).join("") : '<div class="empty-state"><div><strong>还没有操作记录</strong><p>完成一次迁移后，审计记录会显示在这里。</p></div></div>';
  $$(".restore-button").forEach(button => button.addEventListener("click", () => openRestore(button.dataset.id)));
}

async function openMigrationDialog() {
  const sessions = selectedSessions();
  const target = $("#targetProvider").value;
  if (!sessions.length || !target) return;
  const dialog = $("#migrationDialog");
  $("#dialogSource").textContent = sessions[0].provider;
  $("#dialogTarget").textContent = target;
  $("#dialogCount").textContent = sessions.length;
  $("#riskList").innerHTML = "";
  $("#preflightStatus").className = "preflight-status";
  $("#preflightStatus").innerHTML = "<span></span><strong>正在运行预检</strong>";
  $("#compatibilityAck").checked = false;
  $("#migrateAck").value = "";
  $("#migrateButton").disabled = true;
  dialog.showModal();
  try {
    state.plan = await api("/api/preview", {method: "POST", body: JSON.stringify({
      session_ids: sessions.map(session => session.id),
      source_provider: sessions[0].provider,
      target_provider: target,
    })});
    const critical = state.plan.risks.filter(risk => risk.severity === "critical").length;
    $("#preflightStatus").className = `preflight-status ${critical ? "bad" : "ok"}`;
    $("#preflightStatus").innerHTML = `<span></span><strong>${critical ? `预检发现 ${critical} 项阻断问题` : "预检通过，可以创建备份"}</strong>`;
    $("#riskList").innerHTML = state.plan.risks.map(risk => `
      <div class="risk ${escapeHtml(risk.severity)}"><span class="risk-marker">${risk.severity === "critical" ? "×" : risk.severity === "warning" ? "!" : "i"}</span><div><strong>${escapeHtml(risk.message)}</strong><p>${escapeHtml(risk.remediation)}</p></div></div>
    `).join("");
    $("#dialogBackupSize").textContent = `预计备份 ${formatBytes(state.plan.estimated_backup_bytes)}`;
    renderQueue();
    updateMigrateButton();
  } catch (error) {
    dialog.close();
    toast(error.message, true);
  }
}

function updateMigrateButton() {
  $("#migrateButton").disabled = !state.plan?.executable
    || !$("#compatibilityAck").checked
    || $("#migrateAck").value !== "MIGRATE";
}

async function migrate() {
  $("#migrateButton").disabled = true;
  const sessions = selectedSessions();
  try {
    const result = await api("/api/migrate", {method: "POST", body: JSON.stringify({
      session_ids: sessions.map(session => session.id),
      source_provider: sessions[0].provider,
      target_provider: $("#targetProvider").value,
      acknowledgement: $("#migrateAck").value,
    })});
    $("#migrationDialog").close();
    state.selected.clear();
    state.plan = null;
    toast(`迁移完成。操作 ${result.operation_id} 已备份并写入审计账本。`);
    await load();
  } catch (error) {
    toast(error.message, true);
    updateMigrateButton();
  }
}

function openHistory() {
  $("#drawerScrim").hidden = false;
  $("#historyDrawer").classList.add("open");
  $("#historyDrawer").setAttribute("aria-hidden", "false");
  $(".app-shell").inert = true;
  $(".app-header").inert = true;
  $("#closeHistory").focus();
}

function closeHistory() {
  $("#historyDrawer").classList.remove("open");
  $("#historyDrawer").setAttribute("aria-hidden", "true");
  $("#drawerScrim").hidden = true;
  $(".app-shell").inert = false;
  $(".app-header").inert = false;
  $("#openHistory").focus();
}

function openRestore(operationId) {
  state.restoreId = operationId;
  $("#restoreOperationLabel").textContent = operationId;
  $("#restoreRiskAck").checked = false;
  $("#restoreAck").value = "";
  $("#restoreConfirm").disabled = true;
  closeHistory();
  $("#restoreDialog").showModal();
}

function updateRestoreButton() {
  $("#restoreConfirm").disabled = !$("#restoreRiskAck").checked || $("#restoreAck").value !== "RESTORE";
}

async function restore() {
  $("#restoreConfirm").disabled = true;
  try {
    const result = await api(`/api/operations/${encodeURIComponent(state.restoreId)}/restore`, {
      method: "POST", body: JSON.stringify({acknowledgement: $("#restoreAck").value}),
    });
    $("#restoreDialog").close();
    toast(`恢复完成。恢复操作 ${result.operation_id} 已留档。`);
    await load();
  } catch (error) {
    toast(error.message, true);
    updateRestoreButton();
  }
}

function setupEvents() {
  document.addEventListener("mousemove", moveMouseDrag);
  document.addEventListener("mouseup", finishMouseDrag);
  $("#refreshButton").addEventListener("click", load);
  $("#search").addEventListener("input", debounce(renderSessions, 120));
  $("#sortSessions").addEventListener("change", renderSessions);
  $("#targetProvider").addEventListener("change", () => { state.plan = null; renderQueue(); });
  $("#clearSelection").addEventListener("click", () => { state.selected.clear(); state.plan = null; renderAll(); });
  $("#selectAll").addEventListener("change", event => {
    const source = selectedSource();
    visibleSessions().forEach(session => {
      if (!session.locked && (!source || source === session.provider)) {
        event.target.checked ? state.selected.add(session.id) : state.selected.delete(session.id);
      }
    });
    state.plan = null;
    renderTargets(); renderSessions(); renderQueue();
  });
  $$(".segment").forEach(button => button.addEventListener("click", () => {
    state.filter = button.dataset.filter;
    $$(".segment").forEach(item => { item.classList.toggle("active", item === button); item.setAttribute("aria-pressed", String(item === button)); });
    renderSessions();
  }));
  $("#dropZone").addEventListener("dragover", event => { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; $("#dropZone").classList.add("drag-over"); });
  $("#dropZone").addEventListener("dragleave", () => $("#dropZone").classList.remove("drag-over"));
  $("#dropZone").addEventListener("drop", event => {
    event.preventDefault();
    $("#dropZone").classList.remove("drag-over");
    const id = event.dataTransfer.getData("text/plain") || state.draggingId;
    if (id) { setSessionSelected(id, true); toast("Session 已加入迁移队列。"); }
  });
  $("#previewButton").addEventListener("click", openMigrationDialog);
  $("#compatibilityAck").addEventListener("change", updateMigrateButton);
  $("#migrateAck").addEventListener("input", updateMigrateButton);
  $("#migrateButton").addEventListener("click", migrate);
  $("#openHistory").addEventListener("click", openHistory);
  $("#closeHistory").addEventListener("click", closeHistory);
  $("#drawerScrim").addEventListener("click", closeHistory);
  $("#restoreRiskAck").addEventListener("change", updateRestoreButton);
  $("#restoreAck").addEventListener("input", updateRestoreButton);
  $("#restoreConfirm").addEventListener("click", restore);
  document.addEventListener("keydown", event => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); $("#search").focus(); }
    if (event.key === "Escape" && $("#historyDrawer").classList.contains("open")) closeHistory();
  });
}

setupEvents();
load();
