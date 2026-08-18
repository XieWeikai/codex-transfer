const token = document.body.dataset.token;
const state = {
  sessions: [],
  providers: [],
  operations: [],
  selected: new Set(),
  activeProvider: null,
  filter: "all",
  action: "fork",
  plan: null,
  restoreId: null,
  restorePlan: null,
  draggingId: null,
  pointerDrag: null,
  mouseDrag: null,
  suppressCardClick: false,
  popoverSessionId: null,
  popoverPinned: false,
  popoverHideTimer: null,
};

const providerColors = ["#86d39a", "#e7b85c", "#7bb7d7", "#c797d8", "#dc8d69", "#a8c66c"];
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, char => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
})[char]);

function applyTheme(theme) {
  const allowed = ["graphite", "cloud", "contrast"];
  const selected = allowed.includes(theme) ? theme : "graphite";
  document.documentElement.dataset.theme = selected;
  localStorage.setItem("codex-relay-theme", selected);
  if ($("#themeSelect")) $("#themeSelect").value = selected;
}

applyTheme(localStorage.getItem("codex-relay-theme") || "graphite");

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

function projectLabel(path) {
  if (!path) return "无 Project";
  const normalized = path.replace(/[\\/]+$/, "");
  return normalized.split(/[\\/]/).pop() || path;
}

function renderProjects() {
  const select = $("#projectFilter");
  const current = select.value;
  const counts = new Map();
  state.sessions
    .filter(session => !state.activeProvider || session.provider === state.activeProvider)
    .forEach(session => counts.set(session.cwd || "", (counts.get(session.cwd || "") || 0) + 1));
  const projects = [...counts.keys()].sort((a, b) => projectLabel(a).localeCompare(projectLabel(b), "zh-CN"));
  const labelCounts = new Map();
  projects.forEach(project => labelCounts.set(projectLabel(project), (labelCounts.get(projectLabel(project)) || 0) + 1));
  select.innerHTML = '<option value="__all__">全部 Project</option>' + projects.map(project => {
    const parts = project.replace(/[\\/]+$/, "").split(/[\\/]/).filter(Boolean);
    const label = labelCounts.get(projectLabel(project)) > 1 ? parts.slice(-2).join("/") : projectLabel(project);
    return `<option value="${escapeHtml(project)}">${escapeHtml(label)} · ${counts.get(project)}</option>`;
  }
  ).join("");
  select.value = projects.includes(current) ? current : "__all__";
  select.title = select.value === "__all__" ? "全部 Project" : select.value || "无 Project";
}

function renderAll() {
  renderActionMode();
  renderProviders();
  renderProjects();
  renderTargets();
  renderSessions();
  renderQueue();
  renderOperations();
}

function renderActionMode() {
  const fork = state.action === "fork";
  $$(".action-mode").forEach(button => {
    const active = button.dataset.action === state.action;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  $("#actionKicker").textContent = fork ? "FORK QUEUE" : "MOVE QUEUE";
  $("#actionHeading").textContent = fork ? "Fork 工作区" : "移动工作区";
  $("#dropTitle").textContent = fork ? "拖拽 Session 创建 Fork" : "拖拽 Session 到这里移动";
  $("#dropHint").textContent = fork ? "支持多选；每个 Fork 独立备份和审计" : "原会话将切换归属；点击卡片或复选框多选";
  $("#queueLabel").textContent = fork ? "待 Fork" : "待移动";
  $("#selectedLabel").textContent = fork ? "条待 Fork" : "条待移动";
  $("#previewButtonLabel").textContent = fork ? "检查并 Fork" : "检查并移动";
  $("#actionNote").textContent = fork
    ? "Fork 通过 Codex 官方 app-server 创建，并保留原会话。"
    : "移动会改写原会话归属；执行前会完整备份并要求确认。";
  $("#selectAll").disabled = false;
  $("#selectAll").closest("label").title = "选择当前筛选结果中的可操作 Session";
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
  const project = $("#projectFilter").value;
  const sessions = state.sessions.filter(session => {
    if (state.activeProvider && session.provider !== state.activeProvider) return false;
    if (project !== "__all__" && session.cwd !== project) return false;
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
  hideSessionPopover();
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
    const status = session.locked ? "使用中" : session.archived ? "已归档" : "可操作";
    return `<article class="session-card ${selected ? "selected" : ""} ${session.locked ? "locked" : ""}" data-draggable="${!session.locked}" data-id="${escapeHtml(session.id)}" tabindex="0" aria-label="${escapeHtml(session.title)}, Project ${escapeHtml(projectLabel(session.cwd))}">
      <div class="session-card-top">
        <label class="session-check" aria-label="选择 ${escapeHtml(session.title)}"><input type="checkbox" ${selected ? "checked" : ""} ${session.locked ? "disabled" : ""}></label>
        <div class="session-card-actions">${chips}<button class="info-button" type="button" aria-label="查看 ${escapeHtml(session.title)} 的完整信息" aria-controls="sessionPopover" aria-expanded="false" title="查看完整信息">i</button></div>
      </div>
      <strong class="session-card-title">${escapeHtml(session.title || "未命名 Session")}</strong>
      <div class="session-project" title="${escapeHtml(session.cwd || "无 Project")}"><span aria-hidden="true">⌂</span>${escapeHtml(projectLabel(session.cwd))}</div>
      <div class="session-card-foot"><span>${escapeHtml(session.model || "model unknown")}</span><time>${formatDate(session.updated_at)}</time></div>
    </article>`;
  }).join("") : '<div class="empty-state"><div><strong>没有匹配的 Session</strong><p>调整 provider、Project、状态筛选或搜索关键词。</p></div></div>';

  $$(".session-card").forEach(card => {
    const id = card.dataset.id;
    const session = state.sessions.find(item => item.id === id);
    card.querySelector("input")?.addEventListener("change", event => setSessionSelected(id, event.target.checked));
    card.querySelector(".info-button")?.addEventListener("click", event => {
      const shouldClose = state.popoverPinned && state.popoverSessionId === id;
      shouldClose ? hideSessionPopover() : showSessionPopover(card, session, true);
    });
    card.addEventListener("pointerenter", event => {
      if (event.pointerType === "mouse" && !state.popoverPinned) showSessionPopover(card, session, false);
    });
    card.addEventListener("pointerleave", event => {
      if (event.pointerType === "mouse" && !state.popoverPinned) scheduleSessionPopoverHide();
    });
    card.addEventListener("focus", () => {
      if (!state.popoverPinned) showSessionPopover(card, session, false);
    });
    card.addEventListener("blur", () => {
      if (!state.popoverPinned) scheduleSessionPopoverHide();
    });
    card.addEventListener("keydown", event => {
      if ((event.key === " " || event.key === "Enter") && event.target === card) {
        event.preventDefault();
        setSessionSelected(id, !state.selected.has(id));
      }
    });
    card.addEventListener("click", event => {
      if (event.target.closest("button, input, label")) return;
      if (state.suppressCardClick) {
        state.suppressCardClick = false;
        return;
      }
      setSessionSelected(id, !state.selected.has(id));
    });
    setupPointerDrag(card, id);
  });
}

function showSessionPopover(card, session, pinned) {
  if (!card || !session) return;
  clearTimeout(state.popoverHideTimer);
  state.popoverSessionId = session.id;
  state.popoverPinned = pinned;
  const status = session.locked ? "使用中" : session.archived ? "已归档" : "可操作";
  $("#sessionPopoverContent").innerHTML = `
    <dl class="popover-details">
      <div><dt>Session ID</dt><dd>${escapeHtml(session.id)}</dd></div>
      <div><dt>Provider</dt><dd>${escapeHtml(session.provider)}</dd></div>
      <div><dt>Model</dt><dd>${escapeHtml(session.model || "unknown")}</dd></div>
      <div><dt>Project</dt><dd>${escapeHtml(session.cwd || "无工作目录")}</dd></div>
      <div><dt>Updated</dt><dd>${formatDate(session.updated_at)}</dd></div>
      <div><dt>Size / Status</dt><dd>${formatBytes(session.size_bytes)} · ${status}</dd></div>
    </dl>
    <section class="popover-title"><span>完整标题</span><p>${escapeHtml(session.title || "未命名 Session")}</p></section>`;
  $$(".info-button").forEach(button => button.setAttribute("aria-expanded", "false"));
  if (pinned) card.querySelector(".info-button")?.setAttribute("aria-expanded", "true");
  const popover = $("#sessionPopover");
  popover.classList.toggle("pinned", pinned);
  popover.classList.add("open");
  popover.setAttribute("aria-hidden", "false");
  positionSessionPopover(card);
}

function positionSessionPopover(card) {
  const popover = $("#sessionPopover");
  const cardRect = card.getBoundingClientRect();
  const popoverRect = popover.getBoundingClientRect();
  const gap = 12;
  const margin = 12;
  let left;
  let top;
  if (window.innerWidth <= 640) {
    left = Math.max(margin, Math.min(cardRect.left, window.innerWidth - popoverRect.width - margin));
    const below = cardRect.bottom + gap;
    top = below + popoverRect.height <= window.innerHeight - margin
      ? below
      : Math.max(margin, cardRect.top - popoverRect.height - gap);
  } else {
    const right = cardRect.right + gap;
    left = right + popoverRect.width <= window.innerWidth - margin
      ? right
      : Math.max(margin, cardRect.left - popoverRect.width - gap);
    top = Math.max(margin, Math.min(cardRect.top, window.innerHeight - popoverRect.height - margin));
  }
  popover.style.left = `${Math.round(left)}px`;
  popover.style.top = `${Math.round(top)}px`;
}

function scheduleSessionPopoverHide() {
  clearTimeout(state.popoverHideTimer);
  state.popoverHideTimer = setTimeout(() => {
    if (!state.popoverPinned) hideSessionPopover();
  }, 120);
}

function hideSessionPopover() {
  clearTimeout(state.popoverHideTimer);
  const returnFocus = Boolean(document.activeElement?.closest("#sessionPopover"));
  const sessionId = state.popoverSessionId;
  state.popoverSessionId = null;
  state.popoverPinned = false;
  const popover = $("#sessionPopover");
  if (!popover) return;
  popover.classList.remove("open", "pinned");
  popover.setAttribute("aria-hidden", "true");
  $$(".info-button").forEach(button => button.setAttribute("aria-expanded", "false"));
  if (returnFocus && sessionId) {
    requestAnimationFrame(() => {
      $$(".session-card").find(card => card.dataset.id === sessionId)?.querySelector(".info-button")?.focus();
    });
  }
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
  state.suppressCardClick = true;
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
  if (selected && window.matchMedia("(max-width: 1024px)").matches) {
    requestAnimationFrame(() => $(".transfer-panel").scrollIntoView({
      behavior: "auto",
      block: "start",
    }));
  }
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
  `).join("") : '<div class="queue-empty">队列为空<br>拖入或点击卡片开始</div>';
  $$(".queue-remove").forEach(button => button.addEventListener("click", () => setSessionSelected(button.dataset.id, false)));
}

function renderOperations() {
  $("#operationCount").textContent = state.operations.length;
  $("#operations").innerHTML = state.operations.length ? state.operations.map(operation => {
    const canRestore = ["migration", "fork"].includes(operation.kind) && operation.status === "completed" && !operation.restored_by;
    const route = operation.kind === "migration"
      ? `${escapeHtml(operation.source_provider)} → ${escapeHtml(operation.target_provider)}`
      : operation.kind === "fork"
        ? `${escapeHtml(operation.source_provider)} ↗ ${escapeHtml(operation.target_provider)} · Fork`
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
  const fork = state.action === "fork";
  const acknowledgement = fork ? "FORK" : "MIGRATE";
  $("#dialogKicker").textContent = fork ? "FORK CHECK" : "MOVE CHECK";
  $("#dialogTitle").textContent = fork ? "确认 Fork 风险" : "确认移动风险";
  $("#compatibilityText").textContent = fork
    ? "我已确认目标 provider 已配置，并理解新 Fork 的加密推理可能无法继续使用。"
    : "我已确认目标 provider 已配置，并理解原会话归属将被改写。";
  $("#actionAckCode").textContent = acknowledgement;
  $("#migrateAck").placeholder = acknowledgement;
  $("#migrateButton").textContent = fork ? "创建备份并 Fork" : "创建备份并移动";
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
    const previewPath = fork ? "/api/forks/preview" : "/api/preview";
    const payload = fork
      ? {session_ids: sessions.map(session => session.id), target_provider: target}
      : {session_ids: sessions.map(session => session.id), source_provider: sessions[0].provider, target_provider: target};
    state.plan = await api(previewPath, {method: "POST", body: JSON.stringify(payload)});
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
  const acknowledgement = state.action === "fork" ? "FORK" : "MIGRATE";
  $("#migrateButton").disabled = !state.plan?.executable
    || !$("#compatibilityAck").checked
    || $("#migrateAck").value !== acknowledgement;
}

async function migrate() {
  $("#migrateButton").disabled = true;
  const sessions = selectedSessions();
  try {
    const fork = state.action === "fork";
    if (fork) {
      const completed = [];
      for (let index = 0; index < sessions.length; index += 1) {
        $("#migrateButton").textContent = `正在 Fork ${index + 1}/${sessions.length}`;
        try {
          const result = await api("/api/fork", {
            method: "POST",
            body: JSON.stringify({
              session_id: sessions[index].id,
              target_provider: $("#targetProvider").value,
              acknowledgement: $("#migrateAck").value,
            }),
          });
          completed.push({session: sessions[index], operationId: result.operation_id});
        } catch (error) {
          completed.forEach(item => state.selected.delete(item.session.id));
          $("#migrationDialog").close();
          state.plan = null;
          await load();
          toast(`批量 Fork 已完成 ${completed.length}/${sessions.length}；失败于 ${sessions[index].title}：${error.message}`, true);
          return;
        }
      }
      $("#migrationDialog").close();
      state.selected.clear();
      state.plan = null;
      toast(`已完成 ${completed.length} 个 Fork；每个操作均已独立备份并写入审计账本。`);
      await load();
      return;
    }
    const result = await api("/api/migrate", {
      method: "POST",
      body: JSON.stringify({session_ids: sessions.map(session => session.id), source_provider: sessions[0].provider, target_provider: $("#targetProvider").value, acknowledgement: $("#migrateAck").value}),
    });
    $("#migrationDialog").close();
    state.selected.clear();
    state.plan = null;
    toast(`移动完成。操作 ${result.operation_id} 已备份并写入审计账本。`);
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

async function openRestore(operationId) {
  state.restoreId = operationId;
  state.restorePlan = null;
  $("#restoreOperationLabel").textContent = operationId;
  $("#restorePreflight").className = "preflight-status";
  $("#restorePreflight").innerHTML = "<span></span><strong>正在检查快照与当前状态</strong>";
  $("#restoreRiskList").innerHTML = "";
  $("#restoreRiskAck").checked = false;
  $("#restoreAck").value = "";
  $("#restoreConfirm").disabled = true;
  closeHistory();
  $("#restoreDialog").showModal();
  try {
    state.restorePlan = await api(`/api/operations/${encodeURIComponent(operationId)}/restore-preview`, {
      method: "POST", body: "{}",
    });
    const blocked = !state.restorePlan.executable;
    $("#restoreDialog h2").textContent = state.restorePlan.kind === "fork" ? "撤销 Fork 副本" : "恢复迁移前状态";
    $("#restorePreflight").className = `preflight-status ${blocked ? "bad" : "ok"}`;
    $("#restorePreflight").innerHTML = `<span></span><strong>${blocked ? "当前历史已分叉，无法无损恢复" : "快照与迁移后状态一致"}</strong>`;
    $("#restoreRiskList").innerHTML = state.restorePlan.risks.map(risk => `
      <div class="risk ${escapeHtml(risk.severity)}"><span class="risk-marker">${risk.severity === "critical" ? "×" : "!"}</span><div><strong>${escapeHtml(risk.message)}</strong><p>${escapeHtml(risk.remediation)}</p></div></div>
    `).join("");
    updateRestoreButton();
  } catch (error) {
    $("#restoreDialog").close();
    toast(error.message, true);
  }
}

function updateRestoreButton() {
  $("#restoreConfirm").disabled = !state.restorePlan?.executable
    || !$("#restoreRiskAck").checked
    || $("#restoreAck").value !== "RESTORE";
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
  $("#sessionPopover").addEventListener("pointerenter", () => clearTimeout(state.popoverHideTimer));
  $("#sessionPopover").addEventListener("pointerleave", () => { if (!state.popoverPinned) scheduleSessionPopoverHide(); });
  $("#closeSessionPopover").addEventListener("click", hideSessionPopover);
  $(".session-table").addEventListener("scroll", hideSessionPopover, {passive: true});
  window.addEventListener("resize", hideSessionPopover);
  document.addEventListener("pointerdown", event => {
    if (state.popoverPinned && !event.target.closest("#sessionPopover, .info-button")) hideSessionPopover();
  });
  $("#refreshButton").addEventListener("click", load);
  $("#themeSelect").addEventListener("change", event => applyTheme(event.target.value));
  $("#search").addEventListener("input", debounce(renderSessions, 120));
  $("#projectFilter").addEventListener("change", event => { event.target.title = event.target.value === "__all__" ? "全部 Project" : event.target.value || "无 Project"; renderSessions(); });
  $("#sortSessions").addEventListener("change", renderSessions);
  $("#targetProvider").addEventListener("change", () => { state.plan = null; renderQueue(); });
  $("#clearSelection").addEventListener("click", () => { state.selected.clear(); state.plan = null; renderAll(); });
  $$(".action-mode").forEach(button => button.addEventListener("click", () => {
    state.action = button.dataset.action;
    state.plan = null;
    renderAll();
  }));
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
    if (event.key === "Escape" && $("#sessionPopover").classList.contains("open")) hideSessionPopover();
    if (event.key === "Escape" && $("#historyDrawer").classList.contains("open")) closeHistory();
  });
}

setupEvents();
load();
