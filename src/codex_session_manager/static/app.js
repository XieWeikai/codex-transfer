const token = document.body.dataset.token;
const state = { sessions: [], providers: [], selected: new Set(), plan: null, restoreId: null };
const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {"Content-Type": "application/json", "X-CSM-Token": token, ...(options.headers || {})}
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
}

function toast(message, error = false) {
  const node = $("#toast");
  node.textContent = message;
  node.className = error ? "show error" : "show";
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.className = "", 4200);
}

function bytes(value) {
  const units = ["B", "KB", "MB", "GB"];
  let n = value || 0, i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}

function date(value) {
  return new Date(Number(value) * 1000).toLocaleString("zh-CN", {month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit"});
}

async function load() {
  try {
    const [status, sessionData] = await Promise.all([api("/api/status"), api("/api/sessions")]);
    state.sessions = sessionData.sessions;
    state.providers = status.providers;
    $("#homePath").textContent = `${status.codex_home} · 备份 ${status.data_dir}`;
    const healthy = status.databases.length && status.databases.every(db => db.integrity === "ok") && status.audit_chain_valid;
    $("#health").className = `health ${healthy ? "ok" : "bad"}`;
    $("#health").innerHTML = `<span></span>${healthy ? "存储正常" : "需要检查"}`;
    renderProviders();
    renderSessions();
  } catch (error) { toast(error.message, true); }
}

function renderProviders() {
  const currentSource = $("#sourceProvider").value;
  const observed = [...new Set(state.sessions.map(s => s.provider))].sort();
  $("#sourceProvider").innerHTML = observed.map(p => `<option ${p === currentSource ? "selected" : ""}>${escapeHtml(p)}</option>`).join("");
  const currentTarget = $("#targetProvider").value;
  $("#targetProvider").innerHTML = state.providers.map(p => `<option ${p === currentTarget ? "selected" : ""}>${escapeHtml(p)}</option>`).join("");
  avoidSameTarget();
}

function avoidSameTarget() {
  if ($("#targetProvider").value === $("#sourceProvider").value) {
    const next = state.providers.find(p => p !== $("#sourceProvider").value);
    if (next) $("#targetProvider").value = next;
  }
}

function filteredSessions() {
  const query = $("#search").value.trim().toLowerCase();
  const provider = $("#sourceProvider").value;
  return state.sessions.filter(s => s.provider === provider && (!query || `${s.title} ${s.cwd} ${s.id}`.toLowerCase().includes(query)));
}

function renderSessions() {
  const items = filteredSessions();
  $("#sessionList").innerHTML = items.length ? items.map(s => `
    <label class="session-row">
      <input type="checkbox" data-id="${escapeHtml(s.id)}" ${state.selected.has(s.id) ? "checked" : ""}>
      <span class="session-main">
        <span class="session-title"><span>${escapeHtml(s.title)}</span>${s.locked ? '<span class="badge locked">使用中</span>' : ''}${s.archived ? '<span class="badge">已归档</span>' : ''}</span>
        <span class="session-meta"><span>${escapeHtml(s.model || "未知模型")}</span><span>${escapeHtml(s.cwd || "无工作目录")}</span><span>${date(s.updated_at)}</span></span>
      </span>
      <span class="session-id">${escapeHtml(s.id.slice(0, 8))}</span>
    </label>`).join("") : '<div class="empty-state">此 provider 下没有匹配的会话。</div>';
  $("#selectedCount").textContent = `已选 ${state.selected.size} 个`;
  $("#selectAll").checked = items.length > 0 && items.every(s => state.selected.has(s.id));
  $("#sessionList").querySelectorAll("input").forEach(input => input.addEventListener("change", () => {
    input.checked ? state.selected.add(input.dataset.id) : state.selected.delete(input.dataset.id);
    invalidatePlan(); renderSessions();
  }));
}

function invalidatePlan() {
  state.plan = null;
  $("#previewResult").hidden = true;
  $("#previewEmpty").hidden = false;
  $("#migrateAck").value = "";
}

async function preview() {
  try {
    const plan = await api("/api/preview", {method:"POST", body: JSON.stringify({
      session_ids: [...state.selected], source_provider: $("#sourceProvider").value, target_provider: $("#targetProvider").value
    })});
    state.plan = plan;
    $("#previewEmpty").hidden = true;
    $("#previewResult").hidden = false;
    $("#planCount").textContent = plan.sessions.length;
    $("#planSize").textContent = bytes(plan.estimated_backup_bytes);
    $("#riskList").innerHTML = plan.risks.map(r => `<div class="risk ${r.severity}"><strong>${escapeHtml(r.message)}</strong><p>${escapeHtml(r.remediation)}</p></div>`).join("");
    updateMigrateButton();
  } catch (error) { toast(error.message, true); }
}

function updateMigrateButton() {
  $("#migrateButton").disabled = !state.plan?.executable || $("#migrateAck").value !== "MIGRATE";
}

async function migrate() {
  $("#migrateButton").disabled = true;
  try {
    const result = await api("/api/migrate", {method:"POST", body: JSON.stringify({
      session_ids: [...state.selected], source_provider: $("#sourceProvider").value,
      target_provider: $("#targetProvider").value, acknowledgement: $("#migrateAck").value
    })});
    toast(`迁移完成，操作 ${result.operation_id} 已备份`);
    state.selected.clear(); invalidatePlan(); await load();
  } catch (error) { toast(error.message, true); updateMigrateButton(); }
}

async function loadOperations() {
  try {
    const data = await api("/api/operations");
    $("#operations").innerHTML = data.operations.length ? data.operations.map(op => {
      const canRestore = op.kind === "migration" && op.status === "completed" && !op.restored_by;
      return `<article class="operation">
        <time>${escapeHtml(new Date(op.created_at).toLocaleString("zh-CN"))}</time>
        <div><strong>${op.kind === "migration" ? `${escapeHtml(op.source_provider)} → ${escapeHtml(op.target_provider)}` : "恢复快照"}</strong><span class="op-meta">${escapeHtml(op.operation_id)} · ${(op.session_ids || []).length} 个会话</span></div>
        <span class="status ${escapeHtml(op.status)}">${escapeHtml(op.restored_by ? "已恢复" : op.status)}</span>
        ${canRestore ? `<button class="secondary restore" data-id="${escapeHtml(op.operation_id)}">恢复</button>` : "<span></span>"}
      </article>`;
    }).join("") : '<div class="empty-state">还没有操作记录。</div>';
    document.querySelectorAll(".restore").forEach(button => button.addEventListener("click", () => openRestore(button.dataset.id)));
  } catch (error) { toast(error.message, true); }
}

function openRestore(id) {
  state.restoreId = id;
  $("#restoreAck").value = "";
  $("#restoreConfirm").disabled = true;
  $("#restoreDialog").showModal();
}

async function restore() {
  $("#restoreConfirm").disabled = true;
  try {
    const result = await api(`/api/operations/${encodeURIComponent(state.restoreId)}/restore`, {method:"POST", body: JSON.stringify({acknowledgement: $("#restoreAck").value})});
    $("#restoreDialog").close();
    toast(`恢复完成，恢复操作 ${result.operation_id} 已留档`);
    await Promise.all([load(), loadOperations()]);
  } catch (error) { toast(error.message, true); $("#restoreConfirm").disabled = false; }
}

document.querySelectorAll(".tab").forEach(tab => tab.addEventListener("click", () => {
  document.querySelectorAll(".tab, .view").forEach(node => node.classList.remove("active"));
  tab.classList.add("active"); $(`#${tab.dataset.view}`).classList.add("active");
  if (tab.dataset.view === "history") loadOperations();
}));
$("#refreshButton").addEventListener("click", load);
$("#historyRefresh").addEventListener("click", loadOperations);
$("#search").addEventListener("input", renderSessions);
$("#sourceProvider").addEventListener("change", () => { state.selected.clear(); avoidSameTarget(); invalidatePlan(); renderSessions(); });
$("#targetProvider").addEventListener("change", invalidatePlan);
$("#selectAll").addEventListener("change", event => { filteredSessions().forEach(s => event.target.checked ? state.selected.add(s.id) : state.selected.delete(s.id)); invalidatePlan(); renderSessions(); });
$("#previewButton").addEventListener("click", preview);
$("#migrateAck").addEventListener("input", updateMigrateButton);
$("#migrateButton").addEventListener("click", migrate);
$("#restoreAck").addEventListener("input", () => $("#restoreConfirm").disabled = $("#restoreAck").value !== "RESTORE");
$("#restoreConfirm").addEventListener("click", restore);
load();
