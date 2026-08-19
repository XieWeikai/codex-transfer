(function () {
  "use strict";

  const STORAGE_KEY = "codex-transfer-locale";
  const DEFAULT_LOCALE = "zh-CN";
  const SUPPORTED = new Set([DEFAULT_LOCALE, "en"]);
  const originalText = new WeakMap();
  const originalAttributes = new WeakMap();
  let locale = SUPPORTED.has(localStorage.getItem(STORAGE_KEY))
    ? localStorage.getItem(STORAGE_KEY)
    : DEFAULT_LOCALE;
  let scheduled = false;

  const exact = new Map(Object.entries({
    "跳到会话工作台": "Skip to session workspace",
    "正在连接本地 Codex 存储": "Connecting to local Codex storage",
    "连接实时状态": "Connecting live status",
    "检查中": "Checking",
    "主题": "Theme",
    "石墨": "Graphite",
    "云白": "Cloud",
    "高对比": "High contrast",
    "语言": "Language",
    "文档": "Docs",
    "操作记录": "Activity",
    "Provider 分类": "Provider categories",
    "来源主机": "Source host",
    "本机控制平面": "Local control plane",
    "凭据不会复制到其他主机": "Credentials are never copied to another host",
    "全部会话": "All sessions",
    "Session 工作台": "Session workspace",
    "条可见": "visible",
    "条待 Fork": "queued for fork",
    "条待Fork": "queued for fork",
    "搜索标题、工作目录或 Session ID": "Search title, working directory, or Session ID",
    "搜索会话": "Search sessions",
    "按 Project 筛选": "Filter by project",
    "全部 Project": "All projects",
    "排序": "Sort",
    "会话排序": "Session sorting",
    "最近更新": "Recent",
    "最早更新": "Oldest",
    "标题 A–Z": "Title A–Z",
    "文件大小": "Size",
    "会话状态筛选": "Session status filter",
    "全部": "All",
    "可迁移": "Available",
    "占用": "In use",
    "已归档": "Archived",
    "选择当前结果": "Select current results",
    "Session 卡片列表": "Session card list",
    "Fork 工作区": "Fork workspace",
    "Fork工作区": "Fork workspace",
    "清空": "Clear",
    "Session 操作模式": "Session operation mode",
    "Fork 副本": "Fork copy",
    "移动原会话": "Move original",
    "目标主机": "Target host",
    "目标 provider": "Target provider",
    "目标 Project 绝对路径": "Target project absolute path",
    "Session 拖拽投放区": "Session drop zone",
    "拖拽一个 Session 创建 Fork": "Drag a session here to fork it",
    "支持多选；每个 Fork 独立备份和审计": "Multi-select supported; every fork is backed up and audited independently",
    "待 Fork": "Fork queue",
    "待Fork": "Fork queue",
    "来源": "Source",
    "尚未选择": "Not selected",
    "预计备份": "Estimated backup",
    "检查并 Fork": "Review and fork",
    "检查并Fork": "Review and fork",
    "Fork 通过 Codex 官方 app-server 创建，并保留原会话。": "Forks are created through the official Codex app-server and preserve the source session.",
    "Session 完整信息": "Full session details",
    "完整信息": "Full details",
    "关闭 Session 完整信息": "Close full session details",
    "操作与恢复": "Operations and recovery",
    "关闭操作记录": "Close activity",
    "每次迁移、Fork、归档和恢复都保留完整快照、SHA-256 清单与哈希链事件。": "Every move, fork, archive, and restore keeps a full snapshot, SHA-256 manifest, and hash-chained event.",
    "审计链检查中": "Checking audit chain",
    "确认 Fork 风险": "Confirm fork risks",
    "关闭迁移确认": "Close operation confirmation",
    "正在运行预检": "Running preflight checks",
    "我已确认目标 provider 已配置，并理解新 Fork 的加密推理可能无法继续使用。": "I confirm the target provider is configured and understand that encrypted reasoning may not remain usable in the new fork.",
    "确认": "to confirm",
    "输入": "Enter",
    "预计备份 —": "Estimated backup —",
    "取消": "Cancel",
    "创建备份并 Fork": "Back up and fork",
    "恢复迁移前状态": "Restore pre-move state",
    "关闭恢复确认": "Close restore confirmation",
    "正在检查快照与当前状态": "Checking snapshot and current state",
    "我已关闭相关 Codex 任务，并理解恢复限制。": "I have closed the related Codex tasks and understand the recovery limitations.",
    "备份当前状态并恢复": "Back up current state and restore",
    "未命名 Session": "Untitled session",
    "独立 Web Search": "Standalone web search",
    "Codex 默认 / 未公开": "Codex default / undisclosed",
    "尚未从 Session 观察到": "Not observed from sessions",
    "未声明扩展能力": "No extended capabilities declared",
    "使用 Codex 默认值": "Use Codex defaults",
    "请求元数据名称": "Request metadata names",
    "仅显示路由元数据。凭据、Token、Header 值和查询参数值不会进入浏览器响应。": "Only routing metadata is shown. Credentials, tokens, header values, and query parameter values never enter the browser response.",
    "存储正常": "Storage healthy",
    "需要检查": "Needs attention",
    "审计哈希链完整": "Audit hash chain valid",
    "审计哈希链异常": "Audit hash chain invalid",
    "连接失败": "Connection failed",
    "等待重新同步": "Waiting to resync",
    "焦点同步": "Focus sync",
    "实时": "Live",
    "重新连接": "Reconnecting",
    "浏览器不支持 EventSource；返回页面和手动刷新时更新状态": "This browser does not support EventSource; status updates when the page regains focus or is refreshed manually",
    "本机 Session 状态通过原生文件事件自动更新": "Local session state updates through native file events",
    "当前系统没有原生文件事件支持；返回页面和手动刷新时更新状态": "Native file events are unavailable; status updates when the page regains focus or is refreshed manually",
    "实时状态连接中断，浏览器会自动重连": "The live-status connection was interrupted; the browser will reconnect automatically",
    "无 Project": "No project",
    "正在读取": "Loading",
    "移动": "Move",
    "移动工作区": "Move workspace",
    "拖拽 Session 到这里Fork": "Drag sessions here to fork",
    "拖拽 Session 到这里移动": "Drag sessions here to move",
    "原会话将切换归属；点击卡片或复选框多选": "The original session will change ownership; click cards or checkboxes to multi-select",
    "待移动": "Move queue",
    "条待移动": "queued to move",
    "检查并移动": "Review and move",
    "移动会改写原会话归属；执行前会完整备份并要求确认。": "Moving rewrites the source session owner; a complete backup and confirmation are required first.",
    "选择当前筛选结果中的可操作 Session": "Select operable sessions in the current results",
    "还原归档": "Unarchive",
    "归档": "Archive",
    "查看完整信息": "View full details",
    "没有匹配的 Session": "No matching sessions",
    "调整 provider、Project、状态筛选或搜索关键词。": "Adjust the provider, project, status filter, or search terms.",
    "被 Codex 占用的 Session 不能修改；关闭任务后等待 writer lock 释放。": "A session in use by Codex cannot be changed. Close the task and wait for its writer lock to be released.",
    "可操作": "Available",
    "无工作目录": "No working directory",
    "Codex 持有 writer lock": "Codex holds the writer lock",
    "这表示该进程拥有独占写入权；它可能正在运行，也可能只是已加载并等待 30 分钟后卸载。为避免两个 writer，Codex Transfer 不会强制抢占。": "This process has exclusive write ownership. It may be active or merely loaded and waiting for the 30-minute unload timeout. Codex Transfer never forces a takeover because that could create two writers.",
    "完整标题": "Full title",
    "Session 已加入迁移队列。": "Session added to the transfer queue.",
    "一次迁移只能包含同一来源 provider。": "One transfer can contain sessions from only one source provider.",
    "Session 被 Codex 占用；关闭任务并等待 writer lock 释放后才能操作。": "The session is in use by Codex. Close the task and wait for its writer lock to be released.",
    "这个 Session 已归档；请先使用卡片上的还原归档按钮。": "This session is archived. Use the unarchive button on its card first.",
    "队列为空\n拖入或点击卡片开始": "Queue is empty\nDrag or click a card to begin",
    "队列为空": "Queue is empty",
    "拖入或点击卡片开始": "Drag or click a card to begin",
    "归档 Session": "Archive session",
    "还原归档 Session": "Unarchive session",
    "恢复": "Restore",
    "已恢复": "Restored",
    "还没有操作记录": "No operations yet",
    "完成一次迁移后，审计记录会显示在这里。": "Audit records appear here after an operation completes.",
    "确认归档风险": "Confirm archive risks",
    "确认还原归档风险": "Confirm unarchive risks",
    "确认移动风险": "Confirm move risks",
    "我已关闭相关 Codex 任务，并理解归档会从默认活动列表隐藏 Session。": "I have closed the related Codex tasks and understand that archiving hides sessions from the default active list.",
    "我已关闭相关 Codex 任务，并理解批量还原归档是逐条执行的。": "I have closed the related Codex tasks and understand that batch unarchive runs one session at a time.",
    "我已确认目标 provider 已配置，并理解原会话归属将被改写。": "I confirm the target provider is configured and understand that the original session ownership will be rewritten.",
    "活动列表": "Active list",
    "预检通过，可以创建备份": "Preflight passed; backup can be created",
    "撤销 Fork 副本": "Remove fork copy",
    "当前历史已分叉，无法无损恢复": "History has diverged; lossless recovery is unavailable",
    "快照与迁移后状态一致": "Snapshot matches the post-operation state"
  }));

  const patterns = [
    [/^已同步 (.+)$/, "Synced $1"],
    [/^已更新 (\d+) 个占用状态$/, "Updated $1 in-use states"],
    [/^(.+) · 正在读取$/, "$1 · loading"],
    [/^请求 (\d+)$/, "$1 request retries"],
    [/^流 (\d+)$/, "$1 stream retries"],
    [/^空闲 (.+)$/, "$1 idle timeout"],
    [/^(\d+) 总计 · (\d+) 活动 · (\d+) 归档 · (\d+) 占用$/, "$1 total · $2 active · $3 archived · $4 in use"],
    [/^查看 (.+) 的完整信息$/, "View full details for $1"],
    [/^选择 (.+)$/, "Select $1"],
    [/^从队列移除 (.+)$/, "Remove $1 from queue"],
    [/^预检发现 (\d+) 项阻断问题$/, "Preflight found $1 blocking issue(s)"],
    [/^预计备份 (.+)$/, "Estimated backup $1"],
    [/^正在 Fork (\d+)\/(\d+)$/, "Forking $1/$2"],
    [/^完整标题加载失败：(.+)$/, "Failed to load full title: $1"],
    [/^至少 (.+)$/, "At least $1"],
    [/^一次迁移只能包含同一来源 provider。当前来源是 (.+)。$/, "One transfer can contain sessions from only one source provider. The current source is $1."],
    [/^(.+) · 归档 Session$/, "$1 · Archive session"],
    [/^(.+) · 还原归档 Session$/, "$1 · Unarchive session"],
    [/^批量(.+)已完成 (\d+)\/(\d+)；失败于 (.+)：(.+)$/, "Batch $1 completed $2/$3; failed at $4: $5"],
    [/^已完成 (\d+) 条(.+)；每条均已独立备份并审计。$/, "Completed $1 $2 operation(s); each was backed up and audited independently."],
    [/^跨主机操作完成 (\d+)\/(\d+)；失败于 (.+)：(.+)$/, "Cross-host operation completed $1/$2; failed at $3: $4"],
    [/^已完成 (\d+) 条跨主机(.+)，均已备份并审计。$/, "Completed $1 cross-host $2 operation(s); all were backed up and audited."],
    [/^批量 Fork 已完成 (\d+)\/(\d+)；失败于 (.+)：(.+)$/, "Batch fork completed $1/$2; failed at $3: $4"],
    [/^已完成 (\d+) 个 Fork；每个操作均已独立备份并写入审计账本。$/, "Completed $1 fork(s); each was backed up independently and added to the audit ledger."],
    [/^移动完成。操作 (.+) 已备份并写入审计账本。$/, "Move complete. Operation $1 was backed up and added to the audit ledger."],
    [/^恢复完成。恢复操作 (.+) 已留档。$/, "Restore complete. Recovery operation $1 was archived."],
    [/^(.+)（远端清单摘要）$/, "$1 (remote inventory summary)"],
    [/^恢复 (.+)$/, "Restore $1"],
    [/^创建备份并(.+)$/, "Back up and $1"],
    [/^确认(.+)风险$/, "Confirm $1 risks"],
    [/^(\d+) 个 Session$/, "$1 sessions"]
  ];
  const attributeNames = ["aria-label", "title", "placeholder"];

  function translate(value) {
    if (locale !== "en" || !value) return value;
    const leading = value.match(/^\s*/)?.[0] || "";
    const trailing = value.match(/\s*$/)?.[0] || "";
    const core = value.slice(leading.length, value.length - trailing.length);
    if (!core) return value;
    if (exact.has(core)) return `${leading}${exact.get(core)}${trailing}`;
    for (const [pattern, replacement] of patterns) {
      if (pattern.test(core)) return `${leading}${core.replace(pattern, replacement)}${trailing}`;
    }
    return value;
  }

  function translateNode(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      if (!originalText.has(node)) originalText.set(node, node.nodeValue);
      const source = originalText.get(node);
      const target = locale === DEFAULT_LOCALE ? source : translate(source);
      if (node.nodeValue !== target) node.nodeValue = target;
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE || node.closest?.("[data-i18n-skip]")) return;
    let attributes = originalAttributes.get(node);
    if (!attributes) {
      attributes = {};
      originalAttributes.set(node, attributes);
    }
    attributeNames.forEach(name => {
      if (!node.hasAttribute(name)) return;
      if (!(name in attributes)) attributes[name] = node.getAttribute(name);
      const source = attributes[name];
      const target = locale === DEFAULT_LOCALE ? source : translate(source);
      if (node.getAttribute(name) !== target) node.setAttribute(name, target);
    });
    [...node.childNodes].forEach(translateNode);
  }

  function apply(root = document.documentElement) {
    document.documentElement.lang = locale;
    translateNode(root);
    const selector = document.querySelector("#languageSelect");
    if (selector) selector.value = locale;
    const docsLink = document.querySelector("#docsLink");
    if (docsLink) docsLink.href = locale === "en" ? "/docs/en" : "/docs";
  }

  function setLocale(next) {
    locale = SUPPORTED.has(next) ? next : DEFAULT_LOCALE;
    localStorage.setItem(STORAGE_KEY, locale);
    apply();
    document.dispatchEvent(new CustomEvent("codex-transfer:locale", {detail: {locale}}));
  }

  const observer = new MutationObserver(records => {
    const nodes = new Set();
    records.forEach(record => {
      if (record.type === "characterData") nodes.add(record.target);
      record.addedNodes?.forEach(node => nodes.add(node));
    });
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      nodes.forEach(translateNode);
    });
  });

  window.CodexTransferI18n = {
    apply,
    getLocale: () => locale,
    localeCode: () => locale === "en" ? "en-US" : "zh-CN",
    setLocale,
    translate,
  };

  document.addEventListener("DOMContentLoaded", () => {
    apply();
    observer.observe(document.body, {subtree: true, childList: true, characterData: true});
  });
})();
