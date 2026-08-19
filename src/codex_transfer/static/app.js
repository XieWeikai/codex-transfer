const token = document.body.dataset.token;
const state = {
  sessions: [],
  hosts: [],
  providers: [],
  operations: [],
  selected: new Set(),
  activeProvider: null,
  activeHost: "local",
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
  popoverLoadTimer: null,
  refreshTimer: null,
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
  localStorage.setItem("codex-transfer-theme", selected);
  if ($("#themeSelect")) $("#themeSelect").value = selected;
}

applyTheme(localStorage.getItem("codex-transfer-theme") || "graphite");

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {"Content-Type": "application/json", "X-Codex-Transfer-Token": token, ...(options.headers || {})},
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

function compactText(value, limit = 120) {
  const normalized = String(value || "未命名 Session").replace(/\s+/g, " ").trim();
  return normalized.length > limit ? `${normalized.slice(0, limit - 1)}…` : normalized;
}

function prepareSession(session) {
  const displayTitle = compactText(session.title);
  return {
    ...session,
    displayTitle,
    fullTitle: session.title_truncated ? null : session.title,
    searchText: `${session.title} ${session.cwd} ${session.id} ${session.model || ""}`.toLocaleLowerCase(),
  };
}

function selectedSessions() {
  return state.sessions.filter(session => session.host_id === state.activeHost && state.selected.has(session.id));
}

function selectedSource() {
  return selectedSessions()[0]?.provider || null;
}

function isArchiveAction() {
  return state.action === "archive" || state.action === "unarchive";
}

function isCrossHost() {
  return !isArchiveAction() && Boolean($("#targetHost")?.value)
    && (state.activeHost !== "local" || $("#targetHost").value !== "local");
}

function sessionSelectable(session) {
  if (session.locked) return false;
  if (isArchiveAction() && session.host_id !== "local") return false;
  if (state.action === "archive") return !session.archived;
  if (state.action === "unarchive") return session.archived;
  return !session.archived;
}

function providerColor(provider) {
  const index = Math.max(0, state.providers.indexOf(provider));
  return providerColors[index % providerColors.length];
}

async function load() {
  try {
    const workspace = await api("/api/workspace");
    const status = workspace.status;
    state.sessions = workspace.sessions.map(prepareSession);
    state.hosts = workspace.hosts || [{id: "local", label: "This Mac", kind: "local", connected: true, providers: status.providers}];
    if (!state.hosts.some(host => host.id === state.activeHost && host.connected)) state.activeHost = "local";
    state.providers = (state.hosts.find(host => host.id === state.activeHost)?.providers || status.providers);
    state.operations = workspace.operations;
    if (!state.activeProvider || !state.sessions.some(session => session.host_id === state.activeHost && session.provider === state.activeProvider)) {
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
    clearTimeout(state.refreshTimer);
    if (state.hosts.some(host => host.loading)) {
      state.refreshTimer = setTimeout(pollHosts, 1200);
    }
  } catch (error) {
    $("#health").className = "health-pill bad";
    $("#health").innerHTML = "<span></span><strong>连接失败</strong>";
    toast(error.message, true);
  }
}

async function pollHosts() {
  try {
    const snapshot = await api("/api/hosts");
    if (!snapshot.ready) {
      state.refreshTimer = setTimeout(pollHosts, 1200);
      return;
    }
    state.hosts = snapshot.hosts;
    state.sessions = [
      ...state.sessions.filter(session => session.host_id === "local"),
      ...snapshot.sessions.filter(session => session.host_id !== "local").map(prepareSession),
    ];
    state.providers = state.hosts.find(host => host.id === state.activeHost)?.providers || [];
    renderAll();
  } catch (_error) {
    state.refreshTimer = setTimeout(pollHosts, 2500);
  }
}

function observedProviders() {
  const counts = new Map();
  state.sessions.filter(session => session.host_id === state.activeHost).forEach(session => counts.set(session.provider, (counts.get(session.provider) || 0) + 1));
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
    .filter(session => session.host_id === state.activeHost && (!state.activeProvider || session.provider === state.activeProvider))
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
  renderHosts();
  renderProviders();
  renderProjects();
  renderTargets();
  renderSessions();
  renderQueue();
  renderOperations();
}

function renderHosts() {
  const source = $("#sourceHost");
  source.innerHTML = state.hosts.map(host => {
    const disabled = !host.connected || host.loading;
    const suffix = host.loading ? " · 正在读取" : host.connected ? "" : " · unavailable";
    return `<option value="${escapeHtml(host.id)}" ${disabled ? "disabled" : ""}>${escapeHtml(host.label)}${suffix}</option>`;
  }).join("");
  source.value = state.activeHost;
  const current = state.hosts.find(host => host.id === state.activeHost);
  $("#hostState").textContent = current?.kind === "local" ? "LOCAL" : "SSH";
}

function renderActionMode() {
  const fork = state.action === "fork";
  const archive = state.action === "archive";
  const unarchive = state.action === "unarchive";
  $$(".action-mode").forEach(button => {
    const active = button.dataset.action === state.action;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  const label = archive ? "归档" : unarchive ? "还原归档" : fork ? "Fork" : "移动";
  $("#actionKicker").textContent = `${label.toLocaleUpperCase()} QUEUE`;
  $("#actionHeading").textContent = `${label}工作区`;
  $("#dropTitle").textContent = `拖拽 Session 到这里${label}`;
  $("#dropHint").textContent = isArchiveAction()
    ? "支持跨 Provider 多选；每条操作独立备份和审计"
    : fork ? "支持多选；每个 Fork 独立备份和审计" : "原会话将切换归属；点击卡片或复选框多选";
  $("#queueLabel").textContent = `待${label}`;
  $("#selectedLabel").textContent = `条待${label}`;
  $("#previewButtonLabel").textContent = `检查并${label}`;
  $("#actionNote").textContent = archive
    ? "归档通过 Codex 官方 app-server 隐藏 Session，不删除聊天记录。"
    : unarchive
      ? "还原归档通过官方 app-server 恢复可见状态，不改 Provider。"
      : fork
        ? "Fork 通过 Codex 官方 app-server 创建，并保留原会话。"
        : "移动会改写原会话归属；执行前会完整备份并要求确认。";
  $("#targetProviderLabel").hidden = isArchiveAction();
  $("#targetProviderField").hidden = isArchiveAction();
  $("#targetHostLabel").hidden = isArchiveAction();
  $("#targetHostField").hidden = isArchiveAction();
  $("#targetCwdField").hidden = !isCrossHost();
  $("#selectAll").disabled = false;
  $("#selectAll").closest("label").title = "选择当前筛选结果中的可操作 Session";
}

function renderProviders() {
  const providers = observedProviders();
  $("#providerCount").textContent = providers.length;
  $("#providerList").innerHTML = providers.map(provider => {
    const count = state.sessions.filter(session => session.host_id === state.activeHost && session.provider === provider).length;
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
  if (isArchiveAction()) {
    $("#targetProvider").innerHTML = "";
    return;
  }
  const hostSelect = $("#targetHost");
  const previousHost = hostSelect.value;
  const availableHosts = state.hosts.filter(host => host.connected && !host.loading && (state.activeHost === "local" || host.id !== state.activeHost));
  hostSelect.innerHTML = availableHosts.map(host =>
    `<option value="${escapeHtml(host.id)}">${escapeHtml(host.label)}</option>`
  ).join("");
  hostSelect.value = availableHosts.some(host => host.id === previousHost)
    ? previousHost
    : (availableHosts[0]?.id || "");
  const targetHost = state.hosts.find(host => host.id === hostSelect.value);
  const source = selectedSource() || state.activeProvider;
  const current = $("#targetProvider").value;
  const options = (targetHost?.providers || []).filter(provider => hostSelect.value !== state.activeHost || provider !== source);
  $("#targetProvider").innerHTML = options.map(provider =>
    `<option value="${escapeHtml(provider)}">${escapeHtml(provider)}</option>`
  ).join("");
  if (options.includes(current)) $("#targetProvider").value = current;
  $("#targetCwdField").hidden = !isCrossHost();
  if (isCrossHost() && !$("#targetCwd").value) $("#targetCwd").value = selectedSessions()[0]?.cwd || "";
}

function visibleSessions() {
  const query = $("#search").value.trim().toLocaleLowerCase();
  const project = $("#projectFilter").value;
  const sessions = state.sessions.filter(session => {
    if (session.host_id !== state.activeHost) return false;
    if (state.activeProvider && session.provider !== state.activeProvider) return false;
    if (project !== "__all__" && session.cwd !== project) return false;
    if (state.filter === "ready" && (session.locked || session.archived)) return false;
    if (state.filter === "locked" && !session.locked) return false;
    if (state.filter === "archived" && !session.archived) return false;
    return !query || session.searchText.includes(query);
  });
  const sort = $("#sortSessions").value;
  sessions.sort((a, b) => {
    if (sort === "oldest") return a.updated_at - b.updated_at;
    if (sort === "title") return a.displayTitle.localeCompare(b.displayTitle, "zh-CN");
    if (sort === "size") return b.size_bytes - a.size_bytes;
    return b.updated_at - a.updated_at;
  });
  return sessions;
}

function renderSessions() {
  hideSessionPopover();
  const sessions = visibleSessions();
  const selectableSessions = sessions.filter(sessionSelectable);
  const selectedVisible = selectableSessions.filter(session => state.selected.has(session.id)).length;
  $("#visibleCount").textContent = sessions.length;
  $("#selectedCount").textContent = state.selected.size;
  $("#selectAll").checked = selectableSessions.length > 0 && selectedVisible === selectableSessions.length;
  $("#selectAll").indeterminate = selectedVisible > 0 && selectedVisible < selectableSessions.length;

  $("#sessionList").innerHTML = sessions.length ? sessions.map(session => {
    const selected = state.selected.has(session.id);
    const selectable = sessionSelectable(session);
    const chips = [
      session.locked ? '<span class="status-chip locked">使用中</span>' : "",
      session.archived ? '<span class="status-chip archived">已归档</span>' : "",
    ].join("");
    return `<article class="session-card ${selected ? "selected" : ""} ${!selectable ? "locked" : ""}" data-draggable="${selectable}" data-id="${escapeHtml(session.id)}" tabindex="0" aria-label="${escapeHtml(session.displayTitle)}, Project ${escapeHtml(projectLabel(session.cwd))}">
      <div class="session-card-top">
        <label class="session-check" aria-label="选择 ${escapeHtml(session.displayTitle)}"><input type="checkbox" ${selected ? "checked" : ""} ${!selectable ? "disabled" : ""}></label>
        <div class="session-card-actions">${chips}<button class="info-button" type="button" aria-label="查看 ${escapeHtml(session.displayTitle)} 的完整信息" aria-controls="sessionPopover" aria-expanded="false" title="查看完整信息">i</button></div>
      </div>
      <strong class="session-card-title">${escapeHtml(session.displayTitle)}</strong>
      <div class="session-project" title="${escapeHtml(session.cwd || "无 Project")}"><span aria-hidden="true">⌂</span>${escapeHtml(projectLabel(session.cwd))}</div>
      <div class="session-card-foot"><span>${escapeHtml(session.model || "model unknown")}</span><time>${formatDate(session.updated_at)}</time></div>
    </article>`;
  }).join("") : '<div class="empty-state"><div><strong>没有匹配的 Session</strong><p>调整 provider、Project、状态筛选或搜索关键词。</p></div></div>';

  $$(".session-card").forEach(card => {
    const id = card.dataset.id;
    const session = state.sessions.find(item => item.host_id === state.activeHost && item.id === id);
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
  clearTimeout(state.popoverLoadTimer);
  state.popoverSessionId = session.id;
  state.popoverPinned = pinned;
  const status = session.locked ? "使用中" : session.archived ? "已归档" : "可操作";
  $("#sessionPopoverContent").innerHTML = `
    <dl class="popover-details">
      <div><dt>Session ID</dt><dd>${escapeHtml(session.id)}</dd></div>
      <div><dt>Host</dt><dd>${escapeHtml(session.host_id)}</dd></div>
      <div><dt>Provider</dt><dd>${escapeHtml(session.provider)}</dd></div>
      <div><dt>Model</dt><dd>${escapeHtml(session.model || "unknown")}</dd></div>
      <div><dt>Project</dt><dd>${escapeHtml(session.cwd || "无工作目录")}</dd></div>
      <div><dt>Updated</dt><dd>${formatDate(session.updated_at)}</dd></div>
      <div><dt>Size / Status</dt><dd>${formatBytes(session.size_bytes)} · ${status}</dd></div>
    </dl>
    <section class="popover-title"><span>完整标题</span><p id="sessionPopoverTitle">${escapeHtml(session.fullTitle || session.title || "未命名 Session")}${session.title_truncated && !session.fullTitle ? "…" : ""}</p></section>`;
  $$(".info-button").forEach(button => button.setAttribute("aria-expanded", "false"));
  if (pinned) card.querySelector(".info-button")?.setAttribute("aria-expanded", "true");
  const popover = $("#sessionPopover");
  popover.classList.toggle("pinned", pinned);
  popover.classList.add("open");
  popover.setAttribute("aria-hidden", "false");
  positionSessionPopover(card);
  if (session.title_truncated && !session.fullTitle) {
    state.popoverLoadTimer = setTimeout(
      () => loadFullSessionTitle(session),
      pinned ? 0 : 250,
    );
  }
}

async function loadFullSessionTitle(session) {
  if (session.host_id !== "local") {
    const title = $("#sessionPopoverTitle");
    if (title) title.textContent = `${session.title}（远端清单摘要）`;
    return;
  }
  session.titlePromise ||= api(`/api/sessions/${encodeURIComponent(session.id)}`);
  try {
    const detail = await session.titlePromise;
    session.fullTitle = detail.title;
    session.title_truncated = false;
    if (state.popoverSessionId !== session.id) return;
    const title = $("#sessionPopoverTitle");
    if (title) title.textContent = detail.title || "未命名 Session";
    const card = $$(".session-card").find(item => item.dataset.id === session.id);
    if (card) positionSessionPopover(card);
  } catch (error) {
    session.titlePromise = null;
    if (state.popoverSessionId === session.id) {
      const title = $("#sessionPopoverTitle");
      if (title) title.textContent = `完整标题加载失败：${error.message}`;
    }
  }
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
  clearTimeout(state.popoverLoadTimer);
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
      state.suppressCardClick = true;
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
    event.preventDefault();
    state.mouseDrag = {
      id,
      startX: event.clientX,
      startY: event.clientY,
      active: false,
      row,
      ghost: null,
    };
  });
  row.addEventListener("dragstart", event => event.preventDefault());
}

function pointInside(element, x, y) {
  const rect = element.getBoundingClientRect();
  return x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
}

function finishPointerDrag(event) {
  const drag = state.pointerDrag;
  if (!drag || drag.pointerId !== event.pointerId) return;
  const wasActive = drag.active;
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
  if (wasActive) setTimeout(() => { state.suppressCardClick = false; }, 0);
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
  const wasActive = drag.active;
  const dropped = drag.active && pointInside($("#dropZone"), event.clientX, event.clientY);
  clearDragVisuals(drag);
  state.mouseDrag = null;
  if (dropped) {
    setSessionSelected(drag.id, true);
    toast("Session 已加入迁移队列。");
  }
  if (wasActive) setTimeout(() => { state.suppressCardClick = false; }, 0);
}

function activateDragVisuals(drag) {
  drag.active = true;
  state.suppressCardClick = true;
  state.draggingId = drag.id;
  drag.row.classList.add("dragging");
  const session = state.sessions.find(item => item.id === drag.id);
  drag.ghost = document.createElement("div");
  drag.ghost.className = "drag-ghost";
  drag.ghost.textContent = session?.displayTitle || drag.id;
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
  const session = state.sessions.find(item => item.host_id === state.activeHost && item.id === id);
  if (!session || !sessionSelectable(session)) {
    const message = session?.locked
      ? "正在使用的 Session 必须先关闭后才能操作。"
      : isArchiveAction() && session?.host_id !== "local"
        ? "远程 Session 当前只支持 Fork 或移动；归档仍限定本机。"
        : state.action === "archive" ? "这个 Session 已经归档。" : "这个 Session 当前没有归档。";
    toast(message, true);
    return;
  }
  const source = selectedSource();
  if (!isArchiveAction() && selected && source && source !== session.provider) {
    toast(`一次迁移只能包含同一来源 provider。当前来源是 ${source}。`, true);
    return;
  }
  if (state.selected.has(id) === selected) return;
  selected ? state.selected.add(id) : state.selected.delete(id);
  state.plan = null;
  renderTargets();
  updateSessionCardSelection(id);
  updateSelectionIndicators();
  renderQueue();
}

function updateSessionCardSelection(id) {
  const card = $$(".session-card").find(item => item.dataset.id === id);
  if (!card) return;
  const selected = state.selected.has(id);
  card.classList.toggle("selected", selected);
  const checkbox = card.querySelector('input[type="checkbox"]');
  if (checkbox) checkbox.checked = selected;
}

function updateSelectionIndicators() {
  const cards = $$(".session-card");
  const selectable = cards.filter(card => card.dataset.draggable === "true");
  const selectedCount = selectable.filter(card => state.selected.has(card.dataset.id)).length;
  $("#visibleCount").textContent = cards.length;
  $("#selectedCount").textContent = state.selected.size;
  $("#selectAll").checked = selectable.length > 0 && selectedCount === selectable.length;
  $("#selectAll").indeterminate = selectedCount > 0 && selectedCount < selectable.length;
}

function renderQueue() {
  const sessions = selectedSessions();
  $("#queueCount").textContent = sessions.length;
  $("#selectedCount").textContent = sessions.length;
  $("#sourceSummary").textContent = isArchiveAction()
    ? `${new Set(sessions.map(session => session.provider)).size} 个 Provider`
    : sessions[0] ? `${state.activeHost} · ${sessions[0].provider}` : "尚未选择";
  $("#backupSummary").textContent = state.plan
    ? formatBytes(state.plan.estimated_backup_bytes)
    : sessions.length ? `至少 ${formatBytes(sessions.reduce((sum, session) => sum + session.size_bytes, 0))}` : "—";
  $("#previewButton").disabled = sessions.length === 0
    || (!isArchiveAction() && !$("#targetProvider").value)
    || (isCrossHost() && !$("#targetCwd").value.trim());
  $("#transferQueue").innerHTML = sessions.length ? sessions.map(session => `
    <div class="queue-item"><span></span><div><strong>${escapeHtml(session.displayTitle)}</strong><small>${escapeHtml(session.id.slice(0, 13))} · ${formatBytes(session.size_bytes)}</small></div><button type="button" class="queue-remove" data-id="${escapeHtml(session.id)}" aria-label="从队列移除 ${escapeHtml(session.displayTitle)}">×</button></div>
  `).join("") : '<div class="queue-empty">队列为空<br>拖入或点击卡片开始</div>';
  $$(".queue-remove").forEach(button => button.addEventListener("click", () => setSessionSelected(button.dataset.id, false)));
}

function renderOperations() {
  $("#operationCount").textContent = state.operations.length;
  $("#operations").innerHTML = state.operations.length ? state.operations.map(operation => {
    const canRestore = ["migration", "fork", "cross_host_fork", "cross_host_move"].includes(operation.kind) && operation.status === "completed" && !operation.restored_by;
    const route = operation.kind === "migration"
      ? `${escapeHtml(operation.source_provider)} → ${escapeHtml(operation.target_provider)}`
      : operation.kind === "fork"
        ? `${escapeHtml(operation.source_provider)} ↗ ${escapeHtml(operation.target_provider)} · Fork`
        : operation.kind === "cross_host_fork" || operation.kind === "cross_host_move"
          ? `${escapeHtml(operation.source_host)} → ${escapeHtml(operation.target_host)} · ${operation.kind.endsWith("fork") ? "Fork" : "Move"}`
        : operation.kind === "archive"
          ? "归档 Session"
          : operation.kind === "unarchive"
            ? "还原归档 Session"
            : `恢复 ${escapeHtml(operation.restores_operation || "snapshot")}`;
    const status = operation.restored_by ? "已恢复" : operation.status;
    return `<article class="operation"><div class="operation-top"><strong class="operation-route">${route}</strong><span class="operation-status ${escapeHtml(operation.status)}">${escapeHtml(status)}</span></div><div class="operation-bottom"><span>${escapeHtml(new Date(operation.created_at).toLocaleString("zh-CN"))} · ${(operation.session_ids || []).length} sessions</span>${canRestore ? `<button class="restore-button" type="button" data-id="${escapeHtml(operation.operation_id)}">恢复</button>` : `<code>${escapeHtml(operation.operation_id)}</code>`}</div></article>`;
  }).join("") : '<div class="empty-state"><div><strong>还没有操作记录</strong><p>完成一次迁移后，审计记录会显示在这里。</p></div></div>';
  $$(".restore-button").forEach(button => button.addEventListener("click", () => openRestore(button.dataset.id)));
}

async function openMigrationDialog() {
  const sessions = selectedSessions();
  const target = $("#targetProvider").value;
  if (!sessions.length || (!isArchiveAction() && !target)) return;
  const dialog = $("#migrationDialog");
  const fork = state.action === "fork";
  const archive = state.action === "archive";
  const unarchive = state.action === "unarchive";
  const acknowledgement = archive ? "ARCHIVE" : unarchive ? "UNARCHIVE" : fork ? "FORK" : "MIGRATE";
  const label = archive ? "归档" : unarchive ? "还原归档" : fork ? "Fork" : "移动";
  $("#dialogKicker").textContent = `${acknowledgement} CHECK`;
  $("#dialogTitle").textContent = `确认${label}风险`;
  $("#compatibilityText").textContent = archive
    ? "我已关闭相关 Codex 任务，并理解归档会从默认活动列表隐藏 Session。"
    : unarchive
      ? "我已关闭相关 Codex 任务，并理解批量还原归档是逐条执行的。"
      : fork
        ? "我已确认目标 provider 已配置，并理解新 Fork 的加密推理可能无法继续使用。"
        : "我已确认目标 provider 已配置，并理解原会话归属将被改写。";
  $("#actionAckCode").textContent = acknowledgement;
  $("#migrateAck").placeholder = acknowledgement;
  $("#migrateButton").textContent = `创建备份并${label}`;
  $("#dialogSource").textContent = isArchiveAction() ? `${sessions.length} 个 Session` : `${state.activeHost} · ${sessions[0].provider}`;
  $("#dialogTarget").textContent = archive ? "已归档" : unarchive ? "活动列表" : `${$("#targetHost").value} · ${target}`;
  $("#dialogCount").textContent = sessions.length;
  $("#riskList").innerHTML = "";
  $("#preflightStatus").className = "preflight-status";
  $("#preflightStatus").innerHTML = "<span></span><strong>正在运行预检</strong>";
  $("#compatibilityAck").checked = false;
  $("#migrateAck").value = "";
  $("#migrateButton").disabled = true;
  dialog.showModal();
  try {
    const previewPath = isArchiveAction() ? "/api/archive/preview" : isCrossHost() ? "/api/transfer/preview" : fork ? "/api/forks/preview" : "/api/preview";
    const payload = isArchiveAction()
      ? {session_ids: sessions.map(session => session.id), archived: archive}
      : isCrossHost()
        ? {session_ids: sessions.map(session => session.id), source_host: state.activeHost, target_host: $("#targetHost").value, target_provider: target, target_cwd: $("#targetCwd").value, move: !fork}
      : fork
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
  const acknowledgement = state.action === "archive"
    ? "ARCHIVE"
    : state.action === "unarchive" ? "UNARCHIVE" : state.action === "fork" ? "FORK" : "MIGRATE";
  $("#migrateButton").disabled = !state.plan?.executable
    || !$("#compatibilityAck").checked
    || $("#migrateAck").value !== acknowledgement;
}

async function migrate() {
  $("#migrateButton").disabled = true;
  const sessions = selectedSessions();
  try {
    const fork = state.action === "fork";
    if (isArchiveAction()) {
      const archived = state.action === "archive";
      const result = await api("/api/archive", {
        method: "POST",
        body: JSON.stringify({
          session_ids: sessions.map(session => session.id),
          archived,
          acknowledgement: $("#migrateAck").value,
        }),
      });
      $("#migrationDialog").close();
      result.completed.forEach(item => (item.session_ids || []).forEach(id => state.selected.delete(id)));
      state.plan = null;
      await load();
      if (result.failed) {
        toast(`批量${archived ? "归档" : "还原归档"}已完成 ${result.completed.length}/${sessions.length}；失败于 ${result.failed.session_id}：${result.failed.error}`, true);
      } else {
        state.selected.clear();
        toast(`已完成 ${result.completed.length} 条${archived ? "归档" : "还原归档"}；每条均已独立备份并审计。`);
      }
      return;
    }
    if (isCrossHost()) {
      const result = await api("/api/transfer", {
        method: "POST",
        body: JSON.stringify({
          session_ids: sessions.map(session => session.id),
          source_host: state.activeHost,
          target_host: $("#targetHost").value,
          target_provider: $("#targetProvider").value,
          target_cwd: $("#targetCwd").value,
          move: !fork,
          acknowledgement: $("#migrateAck").value,
        }),
      });
      $("#migrationDialog").close();
      state.selected.clear();
      state.plan = null;
      await load();
      if (result.failed) toast(`跨主机操作完成 ${result.completed.length}/${sessions.length}；失败于 ${result.failed.session_id}：${result.failed.error}`, true);
      else toast(`已完成 ${result.completed.length} 条跨主机${fork ? " Fork" : "移动"}，均已备份并审计。`);
      return;
    }
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
          toast(`批量 Fork 已完成 ${completed.length}/${sessions.length}；失败于 ${sessions[index].displayTitle}：${error.message}`, true);
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
  $("#sourceHost").addEventListener("change", event => {
    state.activeHost = event.target.value;
    state.providers = state.hosts.find(host => host.id === state.activeHost)?.providers || [];
    state.activeProvider = observedProviders()[0] || null;
    state.selected.clear();
    state.plan = null;
    $("#targetCwd").value = "";
    renderAll();
  });
  $("#targetHost").addEventListener("change", () => {
    state.plan = null;
    $("#targetCwd").value = isCrossHost() ? (selectedSessions()[0]?.cwd || "") : "";
    renderTargets();
    renderQueue();
  });
  $("#targetCwd").addEventListener("input", () => { state.plan = null; renderQueue(); });
  $("#clearSelection").addEventListener("click", () => {
    const selectedIds = [...state.selected];
    state.selected.clear();
    state.plan = null;
    selectedIds.forEach(updateSessionCardSelection);
    renderTargets();
    updateSelectionIndicators();
    renderQueue();
  });
  $$(".action-mode").forEach(button => button.addEventListener("click", () => {
    state.selected.clear();
    state.action = button.dataset.action;
    state.plan = null;
    renderAll();
  }));
  $("#selectAll").addEventListener("change", event => {
    const source = selectedSource();
    const sessions = visibleSessions();
    sessions.forEach(session => {
      if (sessionSelectable(session) && (isArchiveAction() || !source || source === session.provider)) {
        event.target.checked ? state.selected.add(session.id) : state.selected.delete(session.id);
      }
    });
    state.plan = null;
    sessions.forEach(session => updateSessionCardSelection(session.id));
    renderTargets(); updateSelectionIndicators(); renderQueue();
  });
  $$(".filter-row .segment").forEach(button => button.addEventListener("click", () => {
    state.filter = button.dataset.filter;
    $$(".filter-row .segment").forEach(item => { item.classList.toggle("active", item === button); item.setAttribute("aria-pressed", String(item === button)); });
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
