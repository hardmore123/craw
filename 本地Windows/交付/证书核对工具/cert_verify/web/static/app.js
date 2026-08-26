"use strict";

let FILE_ID = null;
let SHEETS = [];
let TASK_ID = null;
let OFFSET = 0;
let ROWS = [];
let POLL = null;

const $ = (id) => document.getElementById(id);

// 字段：key, 中文标签, 默认列, 说明, 是否高级(收进折叠区)
const FIELDS = [
  ["cert_no", "证书号", "I", "去官网查询用的号，最关键", false],
  ["agency", "认证类别 / 机构", "J", "用来判断去 VDE / UL / TÜV 哪个官网查", false],
  ["manufacturer", "制造商", "G", "和官网制造商比对", false],
  ["model", "型号", "E", "和官网型号比对", false],
  ["standard", "标准", "H", "和官网标准比对", false],
  ["result", "结果写入列", "M", "把核对结论写回这一列", true],
  ["factory", "生产厂", "", "官网通常不公开，可留空", true],
  ["cert_status", "证书状态", "", "有效/失效等，可留空", true],
];

// 各字段的表头关键词，【按优先级从高到低排列】。
// 关键：这些报备表里 B 列“厂家”是内部供应商标记（第一厂家/扩点厂家），
// 真正的制造商是 G 列“商标/制造商”。所以“制造商/商标/manufacturer”必须
// 优先于“厂家”，否则会把制造商错配到 B 列，导致每行都判不一致。
const FIELD_KEYWORDS = {
  cert_no: ["出口认证证书号", "证书编号", "证书号码", "证书号", "认证编号", "licenseno", "licence", "license", "certificateno", "certno", "certificate", "编号"],
  agency: ["认证类别", "认证类型", "认证机构", "机构", "类别", "agency"],
  manufacturer: ["商标/制造商", "制造商", "商标", "manufacturer", "trademark", "生产商", "厂商", "厂家"],
  model: ["规格型号", "产品型号", "型号", "model"],
  standard: ["认证标准", "标准", "standard", "norm"],
  result: ["核对结果", "比对结果", "结果"],
  factory: ["生产厂", "生产工厂", "工厂", "制造地", "factory"],
  cert_status: ["证书状态", "有效期", "状态", "status"],
};

// 依据某张表的列清单，按表头文字识别各字段对应的列。
// 返回 { guess:{key:列字母}, detected:{key:是否靠表头识别到} }
// 按关键词优先级匹配：高优先关键词命中的列胜出，避免“厂家”抢在“制造商”前面。
// 识别不到时不乱填默认列（仅已知 CB/UL 表沿用老默认），其它表留空让用户自己选。
function autoDetect(columns, useDefault) {
  const guess = {}, detected = {};
  const norm = (s) => String(s || "").toLowerCase().replace(/[\s._/\\]/g, "");
  FIELDS.forEach(([key, , def]) => {
    let hit = "";
    const kws = FIELD_KEYWORDS[key] || [];
    for (const kw of kws) {                 // 关键词按优先级依次尝试
      const nk = norm(kw);
      const col = columns.find(c => c.name && norm(c.name).includes(nk));
      if (col) { hit = col.col; break; }
    }
    detected[key] = !!hit;
    guess[key] = hit || (useDefault ? def : "");
  });
  return { guess, detected };
}

// ---------------- 上传 ----------------
$("btn-upload").onclick = async () => {
  const f = $("file").files[0];
  if (!f) { setMsg("upload-msg", "请先选择 .xlsx 文件", true); return; }
  const fd = new FormData();
  fd.append("file", f);
  setMsg("upload-msg", "上传解析中…", false);
  try {
    const r = await fetch("/api/upload", { method: "POST", body: fd });
    const d = await r.json();
    if (d.error) { setMsg("upload-msg", d.error, true); return; }
    FILE_ID = d.file_id;
    SHEETS = d.sheets;
    setMsg("upload-msg", `已解析：${d.filename}，共 ${d.sheets.length} 个工作表`, false, true);
    renderSheets();
    $("step-map").classList.remove("hidden");
    refreshUlStatus();
  } catch (e) { setMsg("upload-msg", "上传失败：" + e, true); }
};

// ---------------- 列映射渲染 ----------------
function colOptions(columns, selected) {
  // 生成下拉选项：空 + 每一列（显示“列字母 — 表头文字”）
  let html = `<option value="">（无 / 不使用）</option>`;
  columns.forEach(c => {
    const label = c.name ? `${c.col} 列 — ${escapeHtml(c.name)}` : `${c.col} 列`;
    const sel = c.col === selected ? " selected" : "";
    html += `<option value="${c.col}"${sel}>${label}</option>`;
  });
  // 若默认列不在列清单里（超出 max_col），也补一个，保证能选中
  if (selected && !columns.some(c => c.col === selected)) {
    html += `<option value="${selected}" selected>${selected} 列</option>`;
  }
  return html;
}

function previewTable(s) {
  // 用一张小表展示前几行：表头是列字母，第一格标出该列表头文字
  const cols = (s.columns || []).filter(c => c.name)   // 只显示有表头的列，避免太宽
    .slice(0, 12);
  if (!cols.length) return `<div class="preview-empty">（此表无可预览的表头）</div>`;
  const head = cols.map(c => `<th>${c.col}<br><span class="ph">${escapeHtml(c.name)}</span></th>`).join("");
  const rows = (s.preview || []).filter(r => r.row >= 2).slice(0, 2).map(r => {
    const map = {};
    r.cells.forEach(c => { map[c.col] = c.value; });
    const tds = cols.map(c => `<td>${escapeHtml(map[c.col] || "")}</td>`).join("");
    return `<tr>${tds}</tr>`;
  }).join("");
  return `<table class="mini-preview"><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>`;
}

function renderSheets() {
  const box = $("sheets");
  box.innerHTML = "";
  const targetHint = ["认证类别CB", "认证类别UL", "CB", "UL"];
  SHEETS.forEach((s, i) => {
    const isKnown = targetHint.some(t => s.name.includes(t));
    const enabled = isKnown || SHEETS.length <= 2;
    const { guess, detected } = autoDetect(s.columns || [], isKnown);
    const div = document.createElement("div");
    div.className = "sheet-map";

    const mainFields = FIELDS.filter(f => !f[4]);
    const advFields = FIELDS.filter(f => f[4]);
    const mkField = ([k, label, , desc]) => {
      // 状态徽标：自动识别到 / 未识别（已填默认，请核对）/ 未识别（需选择）
      let badge;
      if (detected[k]) badge = `<span class="bdg ok">✓ 自动识别</span>`;
      else if (guess[k]) badge = `<span class="bdg warn">默认猜测·请核对</span>`;
      else badge = `<span class="bdg need">未识别·请选择</span>`;
      const need = (!detected[k] && k === "cert_no") ? " field-need" : "";
      return `
      <label class="field${need}">
        <span class="fl">${label}${k === "cert_no" ? ' <b class="req">*必填</b>' : ""} ${badge}</span>
        <select data-sheet="${i}" data-key="${k}">${colOptions(s.columns || [], guess[k])}</select>
        <span class="fd">${desc}</span>
      </label>`;
    };
    const mainHtml = mainFields.map(mkField).join("");
    const advHtml = advFields.map(mkField).join("");

    // 顶部提示：哪些关键字段没自动识别到
    const keyFields = [["cert_no", "证书号"], ["agency", "认证类别"], ["manufacturer", "制造商"], ["model", "型号"], ["standard", "标准"]];
    const missing = keyFields.filter(([k]) => !detected[k]).map(([, l]) => l);
    let notice = "";
    if (!detected.cert_no) {
      notice = `<div class="sheet-notice err">⚠ 没能自动认出“证书号”列，请务必手动选择（点下方“预览”看每列内容）。</div>`;
    } else if (missing.length) {
      notice = `<div class="sheet-notice">部分列没自动认出：<b>${missing.join("、")}</b>。已识别的可直接用，其余请核对或留空。</div>`;
    } else {
      notice = `<div class="sheet-notice ok">✓ 所有关键列均已自动识别，检查无误即可。</div>`;
    }

    div.innerHTML = `
      <div class="title">
        <label class="sheet-title"><input type="checkbox" data-sheet="${i}" class="sheet-en" ${enabled ? "checked" : ""} />
          <b>${escapeHtml(s.name)}</b> <span class="dim">（${s.max_row} 行 × ${s.max_col} 列）</span></label>
        <label class="startrow">数据从第 <input data-sheet="${i}" data-key="first_data_row" value="2" /> 行开始</label>
      </div>
      ${notice}
      <div class="cols">${mainHtml}</div>
      <details class="field-adv"><summary>更多列（结果列 / 生产厂 / 证书状态）</summary>
        <div class="cols">${advHtml}</div>
      </details>
      <details class="preview-box"><summary>预览这张表的前几行</summary>${previewTable(s)}</details>`;
    box.appendChild(div);
  });
}

// ---------------- 核对数量：分段按钮 ----------------
document.querySelectorAll('#limit-seg button[data-limit]').forEach(btn => {
  btn.onclick = () => {
    document.querySelectorAll('#limit-seg button[data-limit]').forEach(b => b.classList.remove("on"));
    btn.classList.add("on");
    $("limit").value = btn.dataset.limit;
  };
});
$("limit").oninput = () => {
  document.querySelectorAll('#limit-seg button[data-limit]').forEach(b => b.classList.remove("on"));
};

// ---------------- 查询间隔：分段按钮 ----------------
document.querySelectorAll('#interval-seg button[data-interval]').forEach(btn => {
  btn.onclick = () => {
    document.querySelectorAll('#interval-seg button[data-interval]').forEach(b => b.classList.remove("on"));
    btn.classList.add("on");
    $("query_interval").value = btn.dataset.interval;
  };
});
$("query_interval").oninput = () => {
  document.querySelectorAll('#interval-seg button[data-interval]').forEach(b => b.classList.remove("on"));
};

// ---------------- 开始核对 ----------------
$("btn-run").onclick = async () => {
  const sheets = SHEETS.map((s, i) => {
    const en = document.querySelector(`.sheet-en[data-sheet="${i}"]`).checked;
    const colmap = {};
    document.querySelectorAll(`[data-sheet="${i}"][data-key]`).forEach(inp => {
      colmap[inp.dataset.key] = inp.value.trim();
    });
    return { name: s.name, enabled: en, colmap };
  });
  const bad = sheets.find(s => s.enabled && !s.colmap.cert_no);
  if (bad) { alert(`工作表「${bad.name}」还没选“证书号”所在列，请先选好再开始。`); return; }
  if (!sheets.some(s => s.enabled)) { alert("请至少勾选一张要核对的工作表。"); return; }
  const body = {
    file_id: FILE_ID,
    sheets,
    limit: parseInt($("limit").value || "0"),
    query_interval: parseFloat($("query_interval").value || "1"),
    query_jitter: parseFloat($("query_jitter").value || "0"),
    proxy: $("proxy").value.trim(),
    default_agency: $("default_agency").value,
    detail_col: $("detail_col").value.trim(),
    stop_on_blocked: $("stop_on_blocked").checked,
  };
  const r = await fetch("/api/run", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const d = await r.json();
  if (d.error) { alert(d.error); return; }
  TASK_ID = d.task_id; OFFSET = 0; ROWS = [];
  $("step-progress").classList.remove("hidden");
  $("step-result").classList.remove("hidden");
  $("result-table").querySelector("tbody").innerHTML = "";
  startPoll();
};

// ---------------- 轮询进度 ----------------
function startPoll() {
  if (POLL) clearInterval(POLL);
  POLL = setInterval(pollOnce, 1000);
  pollOnce();
}
async function pollOnce() {
  if (!TASK_ID) return;
  const r = await fetch(`/api/progress?id=${TASK_ID}&offset=${OFFSET}`);
  const d = await r.json();
  if (d.error) return;
  OFFSET = d.next_offset;
  (d.new_rows || []).forEach(row => { ROWS.push(row); appendRow(row); });
  const pct = d.total ? Math.round(d.done / d.total * 100) : 0;
  $("bar").style.width = pct + "%";
  $("progress-text").textContent =
    `${statusText(d.status)} — 进度 ${d.done}/${d.total}` +
    (d.cur_cert ? `，当前：${d.cur_sheet} / ${d.cur_cert} / ${d.cur_agency}` : "");
  renderStats(d.stats);
  if (["completed", "stopped", "failed"].includes(d.status)) {
    clearInterval(POLL); POLL = null;
    $("btn-cancel").disabled = true;
    if (d.stop_reason === "blocked") $("progress-text").textContent += "（因网络拦截提前停止，已保存已完成部分）";
    if (d.stop_reason === "user_cancel") $("progress-text").textContent += "（已取消，已保存已完成部分）";
    if (d.status === "failed") $("progress-text").textContent += "，错误：" + d.error;
    if (d.degraded) setMsg("degraded-msg", d.degraded, true);
    applyFilter();
  }
}

function statusText(s) {
  return { pending: "准备中", running: "运行中", completed: "已完成",
           stopped: "已停止", failed: "失败" }[s] || s;
}

function renderStats(st) {
  if (!st) return;
  $("stats").innerHTML = [
    ["查到", st.found], ["官网未查到", st.not_found], ["被拦截", st.blocked],
    ["字段全一致", st.consistent], ["需人工复核", st.review],
  ].map(([k, v]) => `<span class="chip">${k}：${v}</span>`).join("");
}

// ---------------- 结果表 ----------------
function rowClass(row) {
  if (["not_found", "error", "unsupported"].includes(row.status)) return "row-problem";
  if (row.status === "blocked") return "row-blocked";
  if (!row.consistent) return "row-mismatch";
  return "row-ok";
}
function appendRow(row) {
  const tb = $("result-table").querySelector("tbody");
  const tr = document.createElement("tr");
  tr.className = rowClass(row);
  tr.dataset.idx = ROWS.length - 1;
  tr.innerHTML = `<td>${row.sheet}</td><td>${row.row}</td><td>${row.cert_no}</td>
    <td>${row.agency}</td><td class="verdict">${escapeHtml(row.verdict_text)}</td>
    <td>${statusZh(row.status)}</td>`;
  tr.onclick = () => showDetail(parseInt(tr.dataset.idx));
  tb.appendChild(tr);
}
function statusZh(s) {
  return { found: "查到", not_found: "官网无", blocked: "被拦截",
           error: "失败", unsupported: "未支持" }[s] || s;
}

// ---------------- 筛选 ----------------
$("filter").onchange = applyFilter;
function applyFilter() {
  const mode = $("filter").value;
  document.querySelectorAll("#result-table tbody tr").forEach(tr => {
    const row = ROWS[parseInt(tr.dataset.idx)];
    let show = true;
    if (mode === "mismatch") show = row.status === "found" && !row.consistent;
    else if (mode === "problem") show = ["not_found", "blocked", "error", "unsupported"].includes(row.status);
    tr.style.display = show ? "" : "none";
  });
}

// ---------------- 行明细 ----------------
function showDetail(idx) {
  const row = ROWS[idx];
  $("modal-title").textContent = `${row.sheet} 第 ${row.row} 行 · ${row.cert_no}（${row.agency}）`;
  const tb = $("modal-table").querySelector("tbody");
  if (!row.fields || !row.fields.length) {
    tb.innerHTML = `<tr><td colspan="4">${escapeHtml(row.verdict_text)}</td></tr>`;
  } else {
    tb.innerHTML = row.fields.map(f => {
      const cls = f.verdict === "不一致" ? "fv-mismatch"
        : f.verdict === "一致" ? "fv-match" : "fv-missing";
      const site = f.verdict === "网站无此项" ? "（官网未提供）" : escapeHtml(f.site_value);
      const v = f.verdict + (f.detail ? `（${escapeHtml(f.detail)}）` : "");
      return `<tr><td>${f.label}</td><td>${escapeHtml(f.sheet_value)}</td>
        <td>${site}</td><td class="${cls}">${v}</td></tr>`;
    }).join("");
  }
  $("modal-url").innerHTML = row.source_url
    ? `来源：<a href="${row.source_url}" target="_blank">${row.source_url}</a>` : "";
  $("detail-modal").classList.remove("hidden");
}
$("modal-close").onclick = () => $("detail-modal").classList.add("hidden");
$("detail-modal").onclick = (e) => { if (e.target.id === "detail-modal") $("detail-modal").classList.add("hidden"); };

// ---------------- 取消 / 下载 ----------------
$("btn-cancel").onclick = async () => {
  if (!TASK_ID) return;
  await fetch("/api/cancel", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task_id: TASK_ID }),
  });
};
$("btn-download").onclick = () => {
  if (TASK_ID) window.location = `/api/download?id=${TASK_ID}&kind=diff`;
};
$("btn-download-crawl").onclick = () => {
  if (TASK_ID) window.location = `/api/download?id=${TASK_ID}&kind=crawl`;
};

// ---------------- UL 登录 ----------------
async function refreshUlStatus() {
  try {
    const d = await (await fetch("/api/ul-status")).json();
    const el = $("ul-state");
    if (d.login_running) { el.textContent = "登录中…（在弹出的 Edge 里完成）"; el.className = ""; }
    else if (d.logged_in) { el.textContent = "已登录"; el.className = "on"; }
    else { el.textContent = "未登录"; el.className = "off"; }
  } catch (e) {}
}
$("btn-ul-login").onclick = async () => {
  await fetch("/api/ul-login", { method: "POST" });
  setTimeout(refreshUlStatus, 800);
  const iv = setInterval(refreshUlStatus, 3000);
  setTimeout(() => clearInterval(iv), 8 * 60 * 1000);
};

// ---------------- 工具 ----------------
function setMsg(id, text, isErr, isOk) {
  const el = $(id);
  el.textContent = text;
  el.className = "msg" + (isErr ? " err" : isOk ? " ok" : "");
}
function escapeHtml(s) {
  return (s == null ? "" : String(s)).replace(/[&<>"]/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
