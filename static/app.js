"use strict";

const $ = (s) => document.querySelector(s);
const state = {
  config: null,
  skills: [],
  session: null,
  activeFile: "SKILL.md",
  view: "edit",
  viewVersion: null,   // null = 当前版本；数字 = 查看历史版本
  versionFiles: null,
  edits: {},           // 手动编辑未保存的内容 {path: text}
  showExisting: false, // 是否展开原版中已存在的格式问题
  streaming: null,     // {controller, text}
};

// ------------------------------------------------------------ 工具

async function api(method, url, body) {
  const opt = { method, headers: {} };
  if (body instanceof FormData) opt.body = body;
  else if (body !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const res = await fetch(url, opt);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.detail || `请求失败（${res.status}）`);
    err.status = res.status;
    throw err;
  }
  return data;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, "");
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c != null) e.append(c instanceof Node ? c : document.createTextNode(c));
  return e;
}

let toastTimer;
function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (err ? " err" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), err ? 6000 : 3000);
}

function showDialog(title, body, actions = []) {
  const d = $("#dialog");
  $("#dialogTitle").textContent = title;
  $("#dialogBody").textContent = body;
  const box = $("#dialogActions");
  box.replaceChildren(...actions.map((a) =>
    el("button", { class: "btn" + (a.primary ? " primary" : ""), type: "button", onclick: () => { d.close(); a.onClick?.(); } }, a.label)));
  d.showModal();
}

const fmtTime = (ts) => new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false });
const sid = () => state.session?.id;

// ------------------------------------------------------------ 配置 / skill 库 / 会话列表

async function loadConfig() {
  state.config = await api("GET", "/api/config");
  const c = state.config;
  $("#meta").replaceChildren(
    c.llm_configured ? el("span", {}, `模型：${c.model}`) : el("span", { class: "bad" }, "未配置模型：请在 .env 填写 LLM_BASE_URL / LLM_MODEL"),
    el("span", {}, `skill 库：${c.skills_dir}`),
  );
}

async function loadSkills() {
  state.skills = await api("GET", "/api/skills");
  $("#skillCount").textContent = `(${state.skills.length})`;
  $("#skillList").replaceChildren(...state.skills.map((s) =>
    el("li", { title: s.description, onclick: () => previewSkill(s.name) },
      el("div", { class: "name" }, s.name, el("span", { class: "desc" }, s.description || "")))));
  renderTarget();
}

async function previewSkill(name) {
  const data = await api("GET", `/api/skills/${encodeURIComponent(name)}`);
  const body = Object.entries(data.files).map(([p, c]) => `===== ${p} =====\n${c}`).join("\n\n");
  showDialog(name, body, [{ label: "下载", onClick: () => (location.href = `/api/skills/${encodeURIComponent(name)}/download`) }]);
}

async function loadSessions() {
  const list = await api("GET", "/api/sessions");
  $("#sessionList").replaceChildren(...list.map((s) =>
    el("li", { class: s.id === sid() ? "active" : "", onclick: () => openSession(s.id) },
      el("div", { class: "name" }, s.title || "新会话",
        el("span", { class: "desc" }, s.mode === "modify" ? `修改 ${s.target}` : s.mode === "create" ? "新增 skill" : fmtTime(s.updated_at))),
      el("button", { class: "x", title: "删除会话", onclick: (e) => { e.stopPropagation(); deleteSession(s.id); } }, "✕"))));
}

async function newSession() {
  const s = await api("POST", "/api/sessions");
  setSession(s);
  loadSessions();
}

async function openSession(id) {
  if (state.streaming) return toast("正在生成中，请稍候或先停止");
  try {
    setSession(await api("GET", `/api/sessions/${id}`));
    localStorage.setItem("lastSession", id);
  } catch {
    localStorage.removeItem("lastSession");
  }
  loadSessions();
}

function deleteSession(id) {
  showDialog("删除会话", "确定删除该会话吗？（不会影响 skill 库）", [
    { label: "删除", primary: true, onClick: async () => {
      await api("DELETE", `/api/sessions/${id}`);
      if (id === sid()) { state.session = null; render(); }
      loadSessions();
    } },
  ]);
}

function setSession(s, keepView = false) {
  const changed = s.id !== sid();
  state.session = s;
  try { localStorage.setItem("lastSession", s.id); } catch {}
  if (changed || !keepView) {
    state.viewVersion = null;
    state.versionFiles = null;
    state.edits = {};
  }
  const files = Object.keys(s.draft.files);
  if (!files.includes(state.activeFile)) state.activeFile = files.includes("SKILL.md") ? "SKILL.md" : files[0] || "SKILL.md";
  render();
}

// ------------------------------------------------------------ 渲染

function render() {
  const s = state.session;
  $("#empty").hidden = !!s;
  $("#workspace").hidden = !s;
  if (!s) { $("#panel").hidden = true; $(".layout").classList.add("no-panel"); return; }
  renderUploads();
  renderTarget();
  renderChat();
  renderComposer();
  renderPanel();
}

function renderUploads() {
  const s = state.session;
  $("#uploadChips").replaceChildren(...s.uploads.map((u) =>
    el("span", { class: "chip" + (u.sent ? "" : " new"), title: u.sent ? "已发送给模型" : "尚未发送给模型（下一轮会带上）" },
      el("span", { class: "n", onclick: () => previewUpload(u) }, `📄 ${u.name}`),
      el("span", { class: "muted" }, `${u.chars} 字`),
      el("button", { title: "移除", onclick: () => removeUpload(u.id) }, "✕"))));
}

async function previewUpload(u) {
  const data = await api("GET", `/api/sessions/${sid()}/uploads/${u.id}`);
  showDialog(`${data.name}（解析后交给模型的内容）`, data.text);
}

async function removeUpload(uid) {
  setSession(await api("DELETE", `/api/sessions/${sid()}/uploads/${uid}`), true);
}

async function uploadFiles(fileList) {
  if (!sid() || !fileList.length) return;
  const fd = new FormData();
  for (const f of fileList) fd.append("files", f);
  try {
    const r = await api("POST", `/api/sessions/${sid()}/uploads`, fd);
    setSession(r.session, true);
    if (r.errors.length) toast(r.errors.join("\n"), true);
    loadSessions();
  } catch (e) { toast(e.message, true); }
}

function renderTarget() {
  const s = state.session;
  const body = $("#targetBody");
  if (!s || !body) return;
  const started = s.messages.length > 0;
  if (started && s.mode) {
    body.replaceChildren(el("div", { class: "target-badge" },
      el("span", { class: "pill" }, s.mode === "modify" ? "修改" : "新增"),
      el("b", {}, s.mode === "modify" ? s.target : (s.draft.name || s.name || "（名称由模型按语义生成）")),
      el("span", { class: "muted small" }, "已开始生成，如需切换目标请新建会话")));
    return;
  }

  const rec = s.recommendation;
  const nodes = [];
  nodes.push(el("div", { class: "row" },
    el("button", { class: "btn", disabled: !s.uploads.length, onclick: recommend, id: "recBtn" }, rec ? "重新分析" : "分析并推荐"),
    el("span", { class: "muted small" }, s.uploads.length ? "由模型对比 skill 库，建议合并到已有 skill 还是新增" : "先上传源文档")));

  if (rec) {
    const msg = rec.action === "modify"
      ? ["建议", el("b", {}, " 修改 "), el("code", {}, rec.target)]
      : ["建议", el("b", {}, " 新增 skill"), rec.suggested_name ? ["（", el("code", {}, rec.suggested_name), "）"] : ""];
    const btns = rec.action === "modify"
      ? [el("button", { class: "btn primary", onclick: () => chooseTarget("modify", rec.target) }, `按推荐修改 ${rec.target}`)]
      : [el("button", { class: "btn primary", onclick: () => chooseTarget("create", null, $("#nameInput").value) }, "按推荐新增 skill")];
    nodes.push(el("div", { class: "rec" },
      el("div", {}, ...msg),
      rec.reason ? el("div", { class: "small muted" }, rec.reason) : null,
      rec.candidates?.length ? el("div", { class: "small muted" }, `其他相关：${rec.candidates.join("、")}`) : null,
      el("div", { class: "row", style: "margin-top:6px" }, ...btns)));
  }

  const opts = state.skills.map((k) => el("option", { value: k.name, selected: (s.target || rec?.target) === k.name }, k.name));
  nodes.push(el("div", { class: "row", style: "margin-top:6px" },
    el("select", { id: "targetSelect", disabled: !state.skills.length }, ...(opts.length ? opts : [el("option", {}, "（skill 库为空）")])),
    el("button", { class: "btn", disabled: !state.skills.length, onclick: () => chooseTarget("modify", $("#targetSelect").value) }, "修改所选 skill"),
    el("span", { class: "muted" }, "或"),
    el("input", { id: "nameInput", class: "btn", style: "cursor:text;width:220px", placeholder: "新 skill 名（英文 slug，可留空）",
      value: s.name || (rec?.action === "create" ? rec.suggested_name || "" : "") }),
    el("button", { class: "btn", onclick: () => chooseTarget("create", null, $("#nameInput").value) }, "新增 skill")));

  if (s.mode) {
    nodes.push(el("div", { class: "target-badge", style: "margin-top:8px" },
      el("span", { class: "pill" }, "已选择"),
      s.mode === "modify" ? `修改 ${s.target}` : `新增 skill${s.name ? `：${s.name}` : ""}`,
      el("span", { class: "muted small" }, "— 在下方填写补充要求（可选）后点「开始生成」")));
  }
  body.replaceChildren(...nodes);
}

async function recommend() {
  const b = $("#recBtn");
  b.disabled = true;
  b.textContent = "分析中…";
  try {
    setSession(await api("POST", `/api/sessions/${sid()}/recommend`), true);
  } catch (e) {
    toast(e.message, true);
    renderTarget();
  }
}

async function chooseTarget(mode, target, name) {
  try {
    setSession(await api("POST", `/api/sessions/${sid()}/target`, { mode, target, name: (name || "").trim() || null }));
    loadSessions();
    $("#input").focus();
  } catch (e) { toast(e.message, true); }
}

// 把模型回复中的文件块折叠成标签
function formatAssistant(text, streaming) {
  const parts = [];
  const re = /^<<<FILE:\s*([^>\n]+?)\s*>>>[ \t]*\n([\s\S]*?)(^<<<END FILE>>>[ \t]*$|$(?![\s\S]))/gm;
  let last = 0, m;
  while ((m = re.exec(text))) {
    parts.push(inlineMd(text.slice(last, m.index)));
    const done = !!m[3];
    const lines = m[2].split("\n").length;
    parts.push(`<span class="file-chip${done ? "" : " writing"}" data-file="${esc(m[1])}">📄 ${esc(m[1])} ${done ? `· ${lines} 行` : `正在写入… ${lines} 行`}</span>`);
    last = re.lastIndex;
    if (!done) break;
  }
  let rest = text.slice(last).replace(/^<<<DELETE:\s*([^>\n]+?)\s*>>>[ \t]*$/gm, (_, p) => `\u0000DEL${p}\u0000`);
  rest = inlineMd(rest).replace(/\u0000DEL(.+?)\u0000/g, (_, p) => `<span class="file-chip">🗑 删除 ${p}</span>`);
  parts.push(rest);
  return parts.join("").replace(/\n{3,}/g, "\n\n").trim() || (streaming ? "…" : "");
}

function inlineMd(s) {
  return esc(s).replace(/`([^`\n]+)`/g, "<code>$1</code>").replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");
}

function renderChat() {
  const s = state.session;
  const chat = $("#chat");
  const nodes = s.messages.map((m) => {
    if (m.role === "user") return el("div", { class: "msg user" }, m.content);
    const div = el("div", { class: "msg assistant" });
    div.innerHTML = formatAssistant(m.content, false) + (m.version ? `\n<span class="muted small">→ 草稿 v${m.version}</span>` : "");
    return div;
  });
  if (state.streaming) {
    nodes.push(el("div", { class: "msg user" }, state.streaming.display));
    const div = el("div", { class: "msg assistant", id: "streamMsg" });
    div.innerHTML = formatAssistant(state.streaming.text, true);
    nodes.push(div);
  }
  chat.replaceChildren(...nodes);
  chat.querySelectorAll(".file-chip[data-file]").forEach((c) => c.addEventListener("click", () => {
    state.activeFile = c.dataset.file;
    state.view = "edit";
    renderPanel();
  }));
  chat.scrollTop = chat.scrollHeight;
}

function renderComposer() {
  const s = state.session;
  const first = !s.messages.some((m) => m.role === "assistant");
  const input = $("#input");
  const ready = !!s.mode && s.uploads.length > 0;
  input.disabled = !s.mode || !!state.streaming;
  input.placeholder = !s.mode ? "先完成第 2 步：选择修改已有 skill 或新增 skill"
    : first ? "补充要求（可选），例如：子场景 A 的步骤插在原步骤 3 之后；根因名称沿用表格写法……"
    : "继续追问或提出修改意见，例如：步骤 5 的跳转条件写反了，请修正";
  $("#sendBtn").textContent = first ? "开始生成" : "发送";
  $("#sendBtn").disabled = !ready || !!state.streaming;
  $("#stopBtn").hidden = !state.streaming;
  $("#stepUpload").classList.toggle("collapsed", !first && s.uploads.length > 0);
}

// ------------------------------------------------------------ 对话（流式）

async function send(text) {
  const s = state.session;
  if (!s || state.streaming) return;
  const first = !s.messages.some((m) => m.role === "assistant");
  if (!first && !text.trim()) return;
  const controller = new AbortController();
  state.streaming = { controller, text: "", display: text.trim() || "请按模板规范生成。" };
  $("#input").value = "";
  renderChat();
  renderComposer();

  try {
    const res = await fetch(`/api/sessions/${s.id}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
      signal: controller.signal,
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d.detail || `请求失败（${res.status}）`);
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    let final = null;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, i).trim();
        buf = buf.slice(i + 2);
        if (!line.startsWith("data:")) continue;
        const ev = JSON.parse(line.slice(5));
        if (ev.type === "delta") {
          state.streaming.text += ev.text;
          updateStreaming();
        } else if (ev.type === "error") {
          throw new Error(ev.message);
        } else if (ev.type === "done") {
          final = ev;
        }
      }
    }
    state.streaming = null;
    if (final) {
      setSession(final.session);
      if (final.truncated?.length) toast(`输出被截断，未完成的文件：${final.truncated.join("、")}（可追问“请完整输出这些文件”）`, true);
      else if (final.errors?.length) toast(final.errors.join("\n"), true);
      else if (final.version) toast(`已生成草稿 v${final.version}`);
    } else {
      render();
    }
  } catch (e) {
    const aborted = e.name === "AbortError";
    const partial = state.streaming?.text;
    state.streaming = null;
    render();
    toast(aborted ? "已停止（本轮未保存）" : e.message, !aborted);
    if (!aborted && partial === "") $("#input").value = text;
  }
  loadSessions();
}

// 流式过程中：更新聊天气泡，并把正在写入的文件实时显示在右侧
let rafPending = false;
function updateStreaming() {
  if (rafPending) return;
  rafPending = true;
  requestAnimationFrame(() => {
    rafPending = false;
    if (!state.streaming) return;
    const div = $("#streamMsg");
    if (div) {
      div.innerHTML = formatAssistant(state.streaming.text, true);
      const chat = $("#chat");
      chat.scrollTop = chat.scrollHeight;
    }
    const m = [...state.streaming.text.matchAll(/^<<<FILE:\s*([^>\n]+?)\s*>>>[ \t]*\n/gm)].pop();
    if (m) {
      const body = state.streaming.text.slice(m.index + m[0].length).split(/^<<<END FILE>>>/m)[0];
      $("#panel").hidden = false;
      $(".layout").classList.remove("no-panel");
      $("#draftTitle").textContent = "生成中…";
      $("#draftSub").textContent = m[1];
      $("#diffView").hidden = true;
      const ed = $("#editor");
      ed.hidden = false;
      ed.readOnly = true;
      ed.value = body;
      ed.scrollTop = ed.scrollHeight;
    }
  });
}

// ------------------------------------------------------------ 右侧草稿面板

function panelFiles() {
  return state.viewVersion != null && state.versionFiles ? state.versionFiles : state.session.draft.files;
}

function renderPanel() {
  const s = state.session;
  const panel = $("#panel");
  const hasDraft = Object.keys(s.draft.files).length > 0;
  panel.hidden = !s.mode;
  $(".layout").classList.toggle("no-panel", !s.mode);
  if (!s.mode) return;

  const d = s.draft;
  const viewingOld = state.viewVersion != null && state.viewVersion !== d.version;
  $("#draftTitle").textContent = hasDraft ? `${d.name || "（未命名）"}` : "草稿";
  const wb = s.writebacks.at(-1);
  $("#draftSub").textContent = !hasDraft ? "生成后在这里查看、编辑、对比"
    : viewingOld ? `正在查看历史版本 v${state.viewVersion}（只读）`
    : `当前 v${d.version}` + (wb ? ` · 上次写回 v${wb.version}（${fmtTime(wb.ts)}）` : " · 尚未写回");

  const sel = $("#versionSelect");
  sel.hidden = !s.versions.length;
  sel.replaceChildren(...s.versions.slice().reverse().map((v) => el("option", { value: v.version, selected: v.version === (state.viewVersion ?? d.version) },
    `v${v.version} · ${{ base: "原始", model: "模型", manual: "手动", revert: "回退", writeback: "写回" }[v.source] || v.source}${v.note ? " · " + v.note.slice(0, 24) : ""}`)));

  const files = panelFiles();
  const names = Object.keys(files).sort((a, b) => (a === "SKILL.md" ? -1 : b === "SKILL.md" ? 1 : a.localeCompare(b)));
  const deleted = Object.entries(d.changed).filter(([, st]) => st === "deleted").map(([p]) => p);
  $("#fileTabs").replaceChildren(...names.concat(viewingOld ? [] : deleted).map((p) => {
    const st = viewingOld ? null : d.changed[p];
    return el("button", { class: "tab" + (p === state.activeFile ? " active" : ""), onclick: () => { state.activeFile = p; renderPanel(); } },
      p + (state.edits[p] != null ? " *" : ""), st ? el("span", { class: `st ${st}` }, { added: "新增", modified: "已改", deleted: "删除" }[st]) : null);
  }));

  document.querySelectorAll(".tabs.sub .tab").forEach((t) => t.classList.toggle("active", t.dataset.view === state.view));

  const ed = $("#editor");
  const diffView = $("#diffView");
  if (state.view === "edit") {
    ed.hidden = false;
    diffView.hidden = true;
    const content = state.edits[state.activeFile] ?? files[state.activeFile];
    ed.value = content ?? (deleted.includes(state.activeFile) ? "（该文件将在写回时删除）" : "");
    ed.readOnly = viewingOld || !!state.streaming || !hasDraft || content == null;
    ed.placeholder = hasDraft ? "" : "尚未生成";
  } else {
    ed.hidden = true;
    diffView.hidden = false;
    loadDiff();
  }

  renderLint(viewingOld ? [] : d.lint, viewingOld ? [] : d.lint_existing, hasDraft && !viewingOld);

  const dirty = Object.keys(state.edits).length > 0;
  $("#saveEdit").hidden = !dirty;
  const dl = $("#downloadDraft");
  dl.href = `/api/sessions/${s.id}/draft/download`;
  dl.classList.toggle("disabled", !hasDraft);
  const wbBtn = $("#writeback");
  if (viewingOld) {
    wbBtn.textContent = `恢复为 v${state.viewVersion}`;
    wbBtn.onclick = () => revertTo(state.viewVersion);
  } else {
    wbBtn.textContent = "写回 skill 库";
    wbBtn.onclick = () => writeback(false);
  }
  wbBtn.disabled = !hasDraft || !!state.streaming || dirty;
  wbBtn.title = dirty ? "请先保存手动修改" : "";
}

function renderLint(issues, existing, show) {
  const box = $("#lint");
  if (!show) { box.replaceChildren(); return; }
  const s = state.session;
  const scoped = !!s.base_files.length;  // 有原版：只看修改内容
  const unchanged = scoped && !Object.keys(s.draft.changed).length;
  const nodes = [];
  if (!issues.length) {
    nodes.push(el("div", { class: "ok" }, unchanged ? "尚未修改，没有需要检查的改动"
      : scoped ? "✓ 本次修改未引入格式问题" : "✓ 格式检查通过"));
  } else {
    const errs = issues.filter((i) => i.level === "error").length;
    nodes.push(
      el("div", { class: "head" },
        el("span", {}, `${scoped ? "本次修改引入" : "格式检查"}：${errs} 个错误，${issues.length - errs} 个提醒`),
        el("button", { class: "link", disabled: !!state.streaming, onclick: fixByModel }, "让模型按检查结果修正")),
      el("ul", {}, ...issues.map((i) => el("li", { class: i.level }, `[${i.file}] ${i.message}`))));
  }
  if (existing.length) {
    nodes.push(el("div", { class: "muted small existing-toggle" },
      `另有 ${existing.length} 个原版中已存在的问题，不在本次修改范围 `,
      el("button", { class: "link", onclick: () => { state.showExisting = !state.showExisting; renderLint(issues, existing, show); } },
        state.showExisting ? "收起" : "查看")));
    if (state.showExisting) {
      nodes.push(el("ul", { class: "existing" }, ...existing.map((i) => el("li", {}, `[${i.file}] ${i.message}`))));
    }
  }
  box.replaceChildren(...nodes);
}

function fixByModel() {
  const issues = state.session.draft.lint;
  const scoped = !!state.session.base_files.length;
  const text = "请按模板规范修正以下格式检查问题（提醒类如确认无误可保持并说明理由）" +
    (scoped ? "。这些都是本次修改引入的问题；原版中已存在的问题不在本次范围，不要顺带改动未修改的内容" : "") + "：\n" +
    issues.map((i, n) => `${n + 1}. [${i.level === "error" ? "错误" : "提醒"}][${i.file}] ${i.message}`).join("\n");
  send(text);
}

async function loadDiff() {
  const s = state.session;
  const against = state.view === "diff-base" ? "base" : "prev";
  const q = new URLSearchParams({ against });
  if (state.viewVersion != null) q.set("version", state.viewVersion);
  const box = $("#diffView");
  box.textContent = "加载中…";
  const data = await api("GET", `/api/sessions/${s.id}/diff?${q}`);
  if (!data.files.length) {
    box.replaceChildren(el("div", { class: "none" }, against === "base"
      ? (s.mode === "create" && !s.writebacks.length ? "新增 skill，没有原版可对比；全部为新内容。" : "与原版相同，没有改动。")
      : "与上一版本相同。"));
    return;
  }
  const nodes = [];
  for (const f of data.files) {
    nodes.push(el("div", { class: "f" }, `${f.path}  (${{ added: "新增", modified: "修改", deleted: "删除" }[f.status]})`));
    for (const line of f.diff.slice(2)) {
      const cls = line.startsWith("@@") ? "hunk" : line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : "";
      nodes.push(el("div", { class: "l " + cls }, line || " "));
    }
  }
  box.replaceChildren(...nodes);
}

async function selectVersion(v) {
  const s = state.session;
  if (v === s.draft.version) {
    state.viewVersion = null;
    state.versionFiles = null;
  } else {
    const d = await api("GET", `/api/sessions/${s.id}/draft/version/${v}`);
    state.viewVersion = v;
    state.versionFiles = d.files;
  }
  renderPanel();
}

async function revertTo(v) {
  setSession(await api("POST", `/api/sessions/${sid()}/draft/revert`, { version: v }));
  toast(`已恢复为 v${v}（生成新版本）`);
}

async function saveEdits() {
  const files = { ...state.session.draft.files, ...state.edits };
  try {
    setSession(await api("PUT", `/api/sessions/${sid()}/draft`, { files, note: `手动编辑 ${Object.keys(state.edits).join(", ")}` }));
    toast("已保存为新版本");
  } catch (e) { toast(e.message, true); }
}

function writeback(overwrite) {
  const s = state.session;
  const d = s.draft;
  const changes = Object.entries(d.changed).map(([p, st]) => `  ${{ added: "新增", modified: "修改", deleted: "删除" }[st]}  ${p}`);
  const errs = d.lint.filter((i) => i.level === "error").length;
  const target = s.mode === "modify" ? s.target : d.name;
  const body = [
    `写回到：${state.config.skills_dir}/${target || "?"}`,
    s.mode === "modify" ? "原目录会先整体备份到 data/backups/。" : "新增 skill。",
    "",
    changes.length ? "变更文件：\n" + changes.join("\n") : "与库中版本相比没有变更。",
    d.version_bumps.length ? "\nversion 自动递增：\n" + d.version_bumps.map((b) => `  ${b.path}  ${b.old} → ${b.new}`).join("\n") : "",
    errs ? `\n⚠ 格式检查仍有 ${errs} 个错误，确定要写回吗？` : "",
  ].join("\n");
  const doIt = async (ow) => {
    try {
      const r = await api("POST", `/api/sessions/${sid()}/writeback`, { overwrite: ow });
      setSession(r.session);
      loadSkills();
      loadSessions();
      toast(`已写回 ${r.name}` + (r.bumps?.length ? `（${r.bumps.map((b) => `${b.path} → ${b.new}`).join("，")}）` : "") + (r.backup ? `\n备份：${r.backup}` : ""));
    } catch (e) {
      if (e.status === 409) showDialog("skill 已存在", e.message, [{ label: "覆盖（先备份）", primary: true, onClick: () => doIt(true) }]);
      else toast(e.message, true);
    }
  };
  if (overwrite) return doIt(true);
  showDialog("写回 skill 库", body, [{ label: "确认写回", primary: true, onClick: () => doIt(false) }]);
}

// ------------------------------------------------------------ 事件绑定

function bind() {
  $("#newSession").onclick = newSession;
  $("#newSession2").onclick = newSession;
  $("#refreshSkills").onclick = loadSkills;
  $("#fileInput").onchange = (e) => { uploadFiles(e.target.files); e.target.value = ""; };
  const drop = $("#drop");
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); uploadFiles(e.dataTransfer.files); });
  $("#stepUpload .card-head").addEventListener("click", () => $("#stepUpload").classList.remove("collapsed"));

  $("#composer").addEventListener("submit", (e) => { e.preventDefault(); send($("#input").value); });
  $("#input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send($("#input").value); }
  });
  $("#stopBtn").onclick = () => state.streaming?.controller.abort();

  document.querySelectorAll(".tabs.sub .tab").forEach((t) => (t.onclick = () => { state.view = t.dataset.view; renderPanel(); }));
  $("#versionSelect").onchange = (e) => selectVersion(Number(e.target.value));
  $("#editor").addEventListener("input", (e) => {
    const orig = state.session.draft.files[state.activeFile];
    if (e.target.value === orig) delete state.edits[state.activeFile];
    else state.edits[state.activeFile] = e.target.value;
    $("#saveEdit").hidden = !Object.keys(state.edits).length;
    $("#writeback").disabled = Object.keys(state.edits).length > 0;
  });
  $("#saveEdit").onclick = saveEdits;
  window.addEventListener("beforeunload", (e) => { if (Object.keys(state.edits).length || state.streaming) e.preventDefault(); });
}

(async function init() {
  bind();
  await loadConfig().catch((e) => toast(e.message, true));
  await loadSkills().catch((e) => toast(e.message, true));
  await loadSessions();
  let last = null;
  try { last = localStorage.getItem("lastSession"); } catch {}
  if (last) await openSession(last);
  else render();
})();
