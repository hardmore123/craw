"use strict";
// 海外电视数据采集台 前端逻辑（零依赖，原生 JS）

const $ = (id) => document.getElementById(id);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  for (const kid of kids) n.append(kid instanceof Node ? kid : document.createTextNode(kid));
  return n;
};

// ---------------- API ----------------
async function api(path, opts) {
  const res = await fetch(path, opts);
  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch { data = { error: text }; }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}
const apiGet = (p) => api(p);
const apiPost = (p, body) => api(p, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body || {}),
});

function toast(msg, kind = "") {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast show " + kind;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.className = "toast " + kind; }, 3000);
}

// ---------------- 全局状态 ----------------
const STATE = { regions: [], region: "jp", brands: [], statusName: {}, viewEpoch: 0 };

// 日本 SPEC 六品牌固定顺序（前端展示名）
const BRAND_NAMES = {
  hisense_jp: "Hisense", sony_jp: "SONY", regza_jp: "REGZA",
  panasonic_jp: "Panasonic", tcl_jp: "TCL", sharp_jp: "SHARP",
};

// ---------------- 初始化 ----------------
async function init() {
  bindTabs();
  bindActions();
  await loadHealth();
  await loadRegions();
  loadSchedules();
  refreshJobs();
  loadShopAliases();
  // 轮询后端健康与队列
  setInterval(loadHealth, 15000);
}

// 店铺译名对照表（只读）：日文原名 → 中文译名，与价格导出表头同源。
async function loadShopAliases() {
  const wrap = $("shopAliasWrap");
  if (!wrap) return;
  try {
    const d = await apiGet("/api/shop-aliases");
    const items = d.items || [];
    if (!items.length) { wrap.innerHTML = '<span class="muted">暂无店铺译名。</span>'; return; }
    const table = el("table", { class: "alias-table" });
    table.append(el("thead", {}, el("tr", {},
      el("th", {}, "店铺原名（日文）"), el("th", {}, "中文译名"))));
    const tb = el("tbody");
    items.forEach((it) => tb.append(el("tr", {},
      el("td", {}, it.original), el("td", {}, it.zh))));
    table.append(tb);
    wrap.innerHTML = "";
    wrap.append(table);
  } catch (e) {
    wrap.innerHTML = `<span class="muted">加载失败：${e.message}</span>`;
  }
}

function bindTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
      tab.classList.add("active");
      $("panel-" + tab.dataset.tab).classList.add("active");
      if (tab.dataset.tab === "jobs") refreshJobs();
      if (tab.dataset.tab === "schedule") loadSchedules();
    });
  });
}

async function loadHealth() {
  try {
    const h = await apiGet("/api/health");
    $("health").className = "health ok";
    $("health").title = `已连接 · schema ${h.schema_version} · 规格 ${h.stats.spec_rows} 行 · 评价 ${h.stats.reviews} 条`;
  } catch {
    $("health").className = "health bad";
    $("health").title = "后端未连接";
  }
}

async function loadRegions() {
  const data = await apiGet("/api/regions");
  STATE.regions = data.items || [];
  const sel = $("regionSelect");
  sel.innerHTML = "";
  STATE.regions.forEach((r) => {
    const label = r.name + (r.enabled ? "" : "（待接入）");
    sel.append(el("option", { value: r.code }, label));
  });
  sel.value = STATE.regions.some((r) => r.code === STATE.region)
    ? STATE.region : (STATE.regions[0] ? STATE.regions[0].code : "jp");
  STATE.region = sel.value;
  sel.addEventListener("change", () => {
    STATE.region = sel.value;
    onRegionChange().catch((e) => toast("切换地区失败：" + e.message, "bad"));
  });
  await onRegionChange();
}

function currentRegion() {
  return STATE.regions.find((r) => r.code === STATE.region) || {};
}

async function onRegionChange() {
  const epoch = ++STATE.viewEpoch;
  const r = currentRegion();
  $("sub").textContent = `SPEC · 评价 · 价格　当前地区：${r.name || STATE.region}` +
    (r.enabled ? "" : "（该地区待接入，仅日本线可用）");
  // 地区切换时立即清空其它业务页的旧内容，避免短暂显示上一条线的数据。
  ["reviewTableWrap", "priceTableWrap"].forEach((id) => {
    const wrap = $(id);
    if (wrap) wrap.innerHTML = '<p class="muted">切换地区后请重新加载。</p>';
  });
  // 非日本线没有发售年份筛选；清空并禁用控件，避免把默认 2026 发送到后端。
  const jp = STATE.region === "jp";
  ["reviewYear", "priceYear", "schYear"].forEach((id) => {
    const sel = $(id);
    if (!sel) return;
    sel.disabled = !jp;
    if (!jp) sel.value = "";
  });
  // 切换地区时刷新品牌下拉、规格宽表和入口型号总览，避免沿用上一条线。
  await loadBrands();
  if (epoch !== STATE.viewEpoch) return;
  const brand = $("specViewBrand") ? $("specViewBrand").value : "";
  if (brand) await Promise.all([loadSpecTable(), loadModelOverview()]);
}

// 品牌下拉：优先按当前地区取品牌；日本线回退到 /api/brands。
function regionBrands() {
  const r = currentRegion();
  // 集中元数据优先提供品牌；旧日本元数据仍兼容 spec.sites 回退。
  const brands = r.brands || [];
  if (brands.length) {
    return brands.map((b) => ({ code: b.code, name: b.name || b.code }));
  }
  const sites = (r.spec && r.spec.sites) || [];
  if (sites.length) {
    return sites.map((s) => ({ code: s.brand || s.site, name: s.name || s.site }));
  }
  return null;
}

async function loadBrands() {
  let brands = regionBrands();
  if (!brands) {
    try {
      const data = await apiGet(`/api/brands?region=${encodeURIComponent(STATE.region)}`);
      brands = (data.items || []).map((b) => ({ code: b.code, name: b.name }));
    } catch {
      brands = STATE.region === "jp"
        ? Object.entries(BRAND_NAMES).map(([code, name]) => ({ code, name }))
        : [];
    }
  }
  STATE.brands = brands;
  ["specBrand", "reviewBrand", "priceBrand", "schBrand",
   "specViewBrand", "reviewViewBrand", "priceViewBrand"].forEach((id) => {
    const sel = $(id);
    if (!sel) return;
    sel.innerHTML = "";
    brands.forEach((b) => sel.append(el("option", { value: b.code }, b.name)));
  });
}

// ---------------- 动作绑定 ----------------
function bindActions() {
  // SPEC
  $("specRun").addEventListener("click", () => runJob("spec", {
    brand: $("specBrand").value,
    series: splitList($("specSeries").value),
  }, "specLog"));
  $("specRefresh").addEventListener("click", () => runJob("spec_refresh", {}, "specLog"));
  $("specLoad").addEventListener("click", loadSpecTable);
  $("modelLoad").addEventListener("click", loadModelOverview);

  // REVIEW
  $("reviewRun").addEventListener("click", () => runJob("review",
    buildReviewParams(), "reviewLog"));
  $("reviewLoad").addEventListener("click", loadReviewTable);

  // PRICE
  $("priceRun").addEventListener("click", () => runJob("price", {
    brands: [$("priceBrand").value],
    year: STATE.region === "jp" ? numOrNull($("priceYear").value) : null,
    limit_models: parseInt($("priceLimit").value) || 0,
  }, "priceLog"));
  $("priceLoad").addEventListener("click", loadPriceTable);

  // PROBE
  $("probeRun").addEventListener("click", runProbe);

  // SCHEDULE
  $("schKind").addEventListener("change", () => {
    const weekly = $("schKind").value === "weekly";
    $("schWeekly").style.display = weekly ? "" : "none";
    $("schInterval").style.display = weekly ? "none" : "";
  });
  $("schAdd").addEventListener("click", addSchedule);

  // JOBS
  $("jobsRefresh").addEventListener("click", refreshJobs);

  // 导出按钮（SPEC/评价/价格 三页共用）
  document.querySelectorAll("[data-export]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const kind = btn.dataset.export;          // spec / reviews / prices
      const fmt = btn.dataset.fmt;              // xlsx / csv
      const brandSel = { spec: "specViewBrand", reviews: "reviewViewBrand",
                         prices: "priceViewBrand" }[kind];
      const brand = $(brandSel) ? $(brandSel).value : "";
      exportData(kind, brand, fmt);
    });
  });
}

// 导出：直接用浏览器下载（后端以附件形式返回文件流）
function exportData(kind, brand, fmt) {
  const q = new URLSearchParams({ format: fmt, region: STATE.region });
  if (brand) q.set("brand", brand);
  const url = `/api/export/${kind}?${q.toString()}`;
  toast(`正在导出 ${fmt.toUpperCase()}…`, "ok");
  // 用隐藏 a 触发下载，保留后端的文件名
  const a = document.createElement("a");
  a.href = url;
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

const splitList = (s) => (s || "").split(/[,，\s]+/).map((x) => x.trim()).filter(Boolean);
const numOrNull = (s) => (s === "" || s == null ? null : parseInt(s));

// 7.4：把「抓取速度」预设映射为每页等待时间参数（越快等待越短）。
// standard 不下传任何等待参数 → 完全沿用后端默认（最稳）。
const SPEED_PRESETS = {
  standard: {},
  fast: { settle_ms: 1500, scroll_passes: 0, entry_nav_timeout_ms: 30000 },
  turbo: { settle_ms: 800, scroll_passes: 0, scroll_wait_ms: 0,
           entry_nav_timeout_ms: 20000 },
};

// 组装评价任务参数：品牌/年份/型号 + 速度预设 + AI/降耗开关。
function buildReviewParams() {
  const params = {
    brand: $("reviewBrand").value,
    year: STATE.region === "jp" ? numOrNull($("reviewYear").value) : null,
    models: splitList($("reviewModels").value),
  };
  const speed = $("reviewSpeed") ? $("reviewSpeed").value : "standard";
  Object.assign(params, SPEED_PRESETS[speed] || {});
  // 发售年份预筛固定开启：用上面选的「上市年份」跳过明确非该年份的型号（无 UI 开关）。
  params.release_cache = "prefer";

  const translate = $("reviewTranslate") && $("reviewTranslate").checked;
  const extractPros = $("reviewExtractPros") && $("reviewExtractPros").checked;
  const extractCons = $("reviewExtractCons") && $("reviewExtractCons").checked;
  const engine = $("reviewTranslateEngine")
    ? $("reviewTranslateEngine").value
    : "nllb";
  if (translate) params.translate = true;
  if (extractPros) params.extract_pros = true;
  if (extractCons) params.extract_cons = true;
  if (translate || extractPros || extractCons) params.translate_engine = engine;

  const ti = $("reviewThreadInc") ? $("reviewThreadInc").value : "off";
  if (ti === "on") params.thread_incremental = "on";
  if ($("reviewSkipNoItem") && $("reviewSkipNoItem").checked) {
    params.skip_known_no_item = true;
  }
  return params;
}

// ---------------- 触发任务 + 轮询日志 ----------------
async function runJob(task, params, logId) {
  const logEl = $(logId);
  try {
    // 每个任务自动携带当前地区，后端据此路由到对应线的脚本（jp=既有；ca=加拿大零售/官网）。
    const withRegion = { ...params, region: STATE.region };
    const job = await apiPost("/api/jobs", { task, params: withRegion });
    toast(`任务已提交（${task}）`, "ok");
    logEl.textContent = `[提交] ${task} ${JSON.stringify(params)}\n任务 ${job.id} 已入队…\n`;
    pollJobLog(job.id, logEl);
  } catch (e) {
    toast("提交失败：" + e.message, "bad");
    logEl.textContent += "\n[错误] " + e.message;
  }
}

// 从任务日志文本里解析抓取进度。
// 抓取脚本会打印：型号总数（型号数=N / models=N）、逐型号进度（[i/N] 型号 ...）、
// 完成汇总（[汇总] ...）。这里增量喂入日志片段，维护一个进度状态。
function makeProgressTracker() {
  const st = { total: 0, done: 0, current: "", finished: false, brands: {} };
  // 多品牌并发时按品牌各自计数；单品牌则只有一个隐式桶。
  return {
    feed(chunk) {
      const lines = (chunk || "").split("\n");
      for (const line of lines) {
        // 总数：型号数=42 / 型号数=42 ... / models=42
        let m = line.match(/型号数[=＝]\s*(\d+)/) || line.match(/models[=＝]\s*(\d+)/i);
        if (m) st.total = parseInt(m[1], 10) || st.total;
        // 逐型号进度：[3/42] 65U8S ...
        m = line.match(/\[(\d+)\s*\/\s*(\d+)\]\s*(\S+)?/);
        if (m) {
          st.done = parseInt(m[1], 10);
          if (!st.total) st.total = parseInt(m[2], 10);
          st.current = m[3] || "";
        }
        // 完成汇总
        if (/\[汇总\]|多品牌完成|=== 任务结束/.test(line)) st.finished = true;
      }
      return st;
    },
    state: st,
  };
}

function renderProgress(progEl, st, jobStatus) {
  if (!progEl) return;
  const done = st.finished ? (st.total || st.done) : st.done;
  const total = st.total || 0;
  const ended = ["succeeded", "failed", "cancelled"].includes(jobStatus);
  // 拿不到任何计数（如 SPEC 任务无 [i/N] 输出）时不显示进度条，避免误导。
  if (!total && !done && !ended) { progEl.hidden = true; return; }
  progEl.hidden = false;
  const pct = total ? Math.min(100, Math.round((done / total) * 100)) : (ended ? 100 : 0);
  const fillPct = st.finished || ended ? 100 : pct;
  let label;
  if (ended && jobStatus !== "succeeded") label = `已结束（${jobStatus}）`;
  else if (st.finished || ended) label = `完成 ${total || done}/${total || done}（100%）`;
  else if (total) label = `${st.current ? st.current + " · " : ""}${done}/${total}（${pct}%）`;
  else label = "准备中…";
  const cls = ended && jobStatus !== "succeeded" ? "bar bad"
            : (st.finished || jobStatus === "succeeded") ? "bar done" : "bar";
  progEl.innerHTML =
    `<div class="bar-track"><div class="${cls}" style="width:${fillPct}%"></div></div>` +
    `<div class="bar-label">${label}</div>`;
}

// logEl.id 形如 reviewLog → 进度条元素 reviewProg
function progressElFor(logEl) {
  if (!logEl || !logEl.id || !logEl.id.endsWith("Log")) return null;
  return $(logEl.id.slice(0, -3) + "Prog");
}

// 全局唯一日志轮询器：同一时刻只跟踪一个任务，避免多个 setInterval 并存
// 各自 renderProgress 同一进度条导致的闪动。开新轮询前先停掉旧的。
let ACTIVE_POLL = null;
function stopJobPoll() {
  if (ACTIVE_POLL) {
    clearInterval(ACTIVE_POLL);
    ACTIVE_POLL = null;
  }
}

async function pollJobLog(jobId, logEl) {
  stopJobPoll();
  let offset = 0;
  const progEl = progressElFor(logEl);
  const tracker = makeProgressTracker();
  if (progEl) { progEl.hidden = true; progEl.innerHTML = ""; }
  const timer = setInterval(async () => {
    // 轮询期间若被新的请求取代，立即退出，避免旧任务继续覆盖进度条。
    if (ACTIVE_POLL !== timer) { clearInterval(timer); return; }
    try {
      const job = await apiGet(`/api/jobs/${jobId}`);
      const log = await apiGet(`/api/jobs/${jobId}/log?offset=${offset}`);
      if (ACTIVE_POLL !== timer) { clearInterval(timer); return; }
      if (log.content) {
        logEl.textContent += log.content;
        offset = log.offset;
        logEl.scrollTop = logEl.scrollHeight;
        tracker.feed(log.content);
      }
      renderProgress(progEl, tracker.state, job.status);
      if (["succeeded", "failed", "cancelled"].includes(job.status)) {
        stopJobPoll();
        logEl.textContent += `\n=== 任务结束：${job.status}（退出码 ${job.exit_code}）===\n`;
        logEl.scrollTop = logEl.scrollHeight;
        renderProgress(progEl, tracker.state, job.status);
        if (job.status === "succeeded" && logEl.id === "specLog") {
          await Promise.all([loadSpecTable(), loadModelOverview()]);
        }
        toast(`任务${job.status === "succeeded" ? "完成" : "结束：" + job.status}`,
              job.status === "succeeded" ? "ok" : "bad");
      }
    } catch (e) {
      stopJobPoll();
      logEl.textContent += "\n[轮询中断] " + e.message;
    }
  }, 2500);
  ACTIVE_POLL = timer;
}

// ---------------- SPEC 表 ----------------
async function loadSpecTable() {
  const epoch = STATE.viewEpoch;
  const brand = $("specViewBrand").value;
  const wrap = $("specTableWrap");
  wrap.innerHTML = '<p class="muted">加载中…</p>';
  try {
    const d = await apiGet(`/api/spec?region=${encodeURIComponent(STATE.region)}&brand=${encodeURIComponent(brand)}`);
    if (epoch !== STATE.viewEpoch) return;
    if (!d.models.length) { wrap.innerHTML = '<p class="muted">该品牌暂无已入库规格。</p>'; return; }
    const table = el("table", { class: "spec-table" });
    const head = el("tr", {}, el("th", {}, "区分"), el("th", {}, "项目"), el("th", {}, "中文"));
    d.models.forEach((m) => head.append(el("th", {}, m)));
    table.append(el("thead", {}, head));
    const tb = el("tbody");
    d.rows.forEach((row) => {
      const tr = el("tr", {}, el("td", {}, row.category), el("td", {}, row.item_ja),
                    el("td", {}, row.item_zh));
      d.models.forEach((m) => tr.append(el("td", {}, row.values[m] || "")));
      tb.append(tr);
    });
    table.append(tb);
    wrap.innerHTML = "";
    wrap.append(el("div", { class: "muted", style: "padding:6px 10px" },
                   `${d.brand_name} · ${d.week} · ${d.models.length} 机型 · ${d.rows.length} 行`));
    wrap.append(table);
  } catch (e) {
    if (epoch !== STATE.viewEpoch) return;
    wrap.innerHTML = `<p class="muted">加载失败：${e.message}</p>`;
  }
}

// ---------------- 系列 / 型号总览 ----------------
const countText = (value) => (value == null || value === "" ? "未知" : String(value));
const specStatusClass = (status) => ({
  success: "ok", available: "ok", entry_only: "gray", failed: "fail", empty: "queued",
}[status] || "gray");
const specStatusText = (status) => ({
  success: "SPEC成功", available: "已有SPEC", entry_only: "仅入口发现",
  failed: "SPEC失败", empty: "SPEC为空", unknown: "未知",
}[status] || status || "未知");

async function loadModelOverview() {
  const epoch = STATE.viewEpoch;
  const brand = $("specViewBrand").value;
  const auditWrap = $("modelAudit");
  const wrap = $("modelTableWrap");
  if (!brand) {
    auditWrap.innerHTML = '<p class="muted">当前地区暂无可用品牌。</p>';
    wrap.innerHTML = '<p class="muted">暂无型号。</p>';
    return;
  }
  auditWrap.innerHTML = '<p class="muted">加载入口审计…</p>';
  wrap.innerHTML = '<p class="muted">加载型号…</p>';
  try {
    const d = await apiGet(`/api/models?region=${encodeURIComponent(STATE.region)}&brand=${encodeURIComponent(brand)}&limit=5000`);
    if (epoch !== STATE.viewEpoch) return;
    const audit = (d.entry_audit || {})[brand] || null;
    const stats = el("div", { class: "model-stats" },
      el("div", { class: "model-stat" }, el("span", {}, "系列总数"),
         el("strong", {}, countText(d.series_total))),
      el("div", { class: "model-stat" }, el("span", {}, "去重型号"),
         el("strong", {}, countText(d.total))),
      el("div", { class: "model-stat" }, el("span", {}, "已入库SPEC系列"),
         el("strong", {}, countText(d.spec_series_total))),
      el("div", { class: "model-stat" }, el("span", {}, "入口发现系列"),
         el("strong", {}, countText(audit && audit.discovered_series_count))),
      el("div", { class: "model-stat" }, el("span", {}, "入口发现型号"),
         el("strong", {}, countText(audit && audit.discovered_model_count))));
    auditWrap.innerHTML = "";
    auditWrap.append(stats);
    const auditLine = audit
      ? `期望系列 ${countText(audit.expected_series_count)} · 期望型号 ${countText(audit.expected_model_count)} · 终止：${audit.termination_reason || "未知"} · ${audit.captured_at || ""}`
      : "暂无入口审计记录；当前显示的是 spec_series 已入库型号。";
    auditWrap.append(el("div", { class: "model-audit-line" }, auditLine));

    const items = d.items || [];
    if (!items.length) {
      wrap.innerHTML = '<p class="muted">该品牌暂无入口发现或已入库型号。</p>';
      return;
    }
    const groups = new Map();
    items.forEach((item) => {
      const names = Array.isArray(item.series_list) && item.series_list.length
        ? item.series_list : String(item.series || "未分组").split(" | ").filter(Boolean);
      (names.length ? names : ["未分组"]).forEach((series) => {
        if (!groups.has(series)) groups.set(series, []);
        groups.get(series).push(item);
      });
    });
    wrap.innerHTML = "";
    [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0])).forEach(([series, rows]) => {
      const details = el("details", { class: "series-group" });
      details.open = true;
      details.append(el("summary", {}, `${series} · ${rows.length} 个去重型号`));
      const table = el("table", { class: "model-table" });
      table.append(el("thead", {}, el("tr", {},
        el("th", {}, "型号"), el("th", {}, "SPEC状态"), el("th", {}, "来源"),
        el("th", {}, "商品/站点"), el("th", { class: "wrap" }, "入口/规格URL"))));
      const tb = el("tbody");
      rows.forEach((item) => {
        const source = item.entry_discovered ? `${item.source} · 入口` : item.source;
        const product = item.product_id
          ? `${item.product_id}${item.site_name ? " · " + item.site_name : ""}` : "-";
        tb.append(el("tr", {},
          el("td", {}, item.model),
          el("td", {}, el("span", { class: "badge " + specStatusClass(item.spec_status) },
            specStatusText(item.spec_status))),
          el("td", { class: "wrap" }, source || "-"),
          el("td", {}, product),
          el("td", { class: "wrap" }, item.source_url || "-")));
      });
      table.append(tb);
      details.append(table);
      wrap.append(details);
    });
  } catch (e) {
    if (epoch !== STATE.viewEpoch) return;
    auditWrap.innerHTML = `<p class="muted">加载失败：${e.message}</p>`;
    wrap.innerHTML = `<p class="muted">加载失败：${e.message}</p>`;
  }
}

// ---------------- 评价表 ----------------
async function loadReviewTable() {
  const epoch = STATE.viewEpoch;
  const brand = $("reviewViewBrand").value;
  const wrap = $("reviewTableWrap");
  wrap.innerHTML = '<p class="muted">加载中…</p>';
  try {
    const d = await apiGet(`/api/reviews?region=${encodeURIComponent(STATE.region)}&brand=${encodeURIComponent(brand)}&limit=500`);
    if (epoch !== STATE.viewEpoch) return;
    if (!d.total) { wrap.innerHTML = '<p class="muted">该品牌暂无已入库评价。</p>'; return; }
    const cols = ["型号", "尺寸", "来源", "评分", "标题", "内容", "作者", "日期"];
    const table = el("table");
    table.append(el("thead", {}, el("tr", {}, ...cols.map((c) =>
      el("th", { class: c === "内容" ? "wrap" : "" }, c)))));
    const tb = el("tbody");
    d.items.forEach((r) => {
      tb.append(el("tr", {},
        el("td", {}, r.model), el("td", {}, r.size),
        el("td", {}, el("span", { class: "badge " + (r.source === "レビュー" ? "run" : "gray") }, r.source || "-")),
        el("td", {}, r.rating == null ? "" : String(r.rating)),
        el("td", { class: "wrap" }, r.title || ""),
        el("td", { class: "wrap" }, r.body || ""),
        el("td", {}, r.author || ""), el("td", {}, r.review_date || "")));
    });
    table.append(tb);
    wrap.innerHTML = "";
    wrap.append(el("div", { class: "muted", style: "padding:6px 10px" }, `共 ${d.total} 条评价`));
    wrap.append(table);
  } catch (e) {
    if (epoch !== STATE.viewEpoch) return;
    wrap.innerHTML = `<p class="muted">加载失败：${e.message}</p>`;
  }
}

// ---------------- 价格表 ----------------
async function loadPriceTable() {
  const epoch = STATE.viewEpoch;
  const brand = $("priceViewBrand").value;
  const wrap = $("priceTableWrap");
  wrap.innerHTML = '<p class="muted">加载中…</p>';
  try {
    const d = await apiGet(`/api/prices?region=${encodeURIComponent(STATE.region)}&brand=${encodeURIComponent(brand)}&limit=500`);
    if (epoch !== STATE.viewEpoch) return;
    if (!d.total) { wrap.innerHTML = '<p class="muted">该品牌暂无已入库价格。</p>'; return; }
    const table = el("table");
    table.append(el("thead", {}, el("tr", {},
      el("th", {}, "型号"), el("th", {}, "尺寸"), el("th", {}, "上市"),
      el("th", {}, "最低价"), el("th", {}, "在售店铺"), el("th", {}, "商品ID"))));
    const tb = el("tbody");
    d.items.forEach((p) => {
      const low = p.lowest ? `${p.lowest.currency || ""} ${fmt(p.lowest.price)}` : "";
      const shops = (p.shops || []).length;
      const tr = el("tr", { class: p.product_id ? "clickable" : "" },
        el("td", {}, p.model), el("td", {}, p.size),
        el("td", {}, p.release || ""), el("td", {}, low),
        el("td", {}, `${shops} 家`), el("td", {}, p.item_id || ""));
      if (p.product_id) tr.addEventListener("click", () => showPriceDetail(p));
      tb.append(tr);
    });
    table.append(tb);
    wrap.innerHTML = "";
    wrap.append(el("div", { class: "muted", style: "padding:6px 10px" },
                   `共 ${d.total} 个机型（点行看各店铺价）`));
    wrap.append(table);
  } catch (e) {
    if (epoch !== STATE.viewEpoch) return;
    wrap.innerHTML = `<p class="muted">加载失败：${e.message}</p>`;
  }
}

function showPriceDetail(p) {
  const lines = (p.shops || []).map((s) =>
    `  ${s.shop}: ${s.currency || ""} ${fmt(s.price)}${s.is_lowest ? "  ← 最安" : ""}`).join("\n");
  toast(`${p.model} 各店铺价格已在下方日志显示`, "ok");
  $("priceLog").textContent = `${p.brand_name || ""} ${p.model} （${p.item_id}）\n最低价：${p.lowest ? p.lowest.price : "-"}\n\n各店铺：\n${lines || "  （无）"}`;
}

const fmt = (n) => (n == null ? "" : Number(n).toLocaleString());

// ---------------- 通道验证 ----------------
async function runProbe() {
  const wrap = $("probeResult");
  const url = $("probeUrl").value.trim();
  const browser = $("probeBrowser").checked ? "1" : "0";
  wrap.innerHTML = '<p class="muted">探测中…（浏览器模式较慢，请稍候）</p>';
  try {
    let items;
    if (url) {
      const r = await apiGet(`/api/probe?url=${encodeURIComponent(url)}&browser=${browser}`);
      items = [{ region: "-", cap: "-", name: url, ...r }];
    } else {
      const d = await apiGet(`/api/probe?region=${STATE.region}&browser=${browser}`);
      items = d.items || [];
    }
    if (!items.length) { wrap.innerHTML = '<p class="muted">当前地区无可探测目标（待接入）。</p>'; return; }
    const table = el("table");
    table.append(el("thead", {}, el("tr", {},
      el("th", {}, "站点"), el("th", {}, "能力"), el("th", {}, "结果"),
      el("th", {}, "状态码"), el("th", {}, "耗时"), el("th", {}, "方式"),
      el("th", { class: "wrap" }, "说明"), el("th", { class: "wrap" }, "URL"))));
    const tb = el("tbody");
    items.forEach((r) => {
      let dot = "warn", label = "被拦截";
      if (r.ok) { dot = "ok"; label = "可访问"; }
      else if (r.error) { dot = "bad"; label = "不可达"; }
      const note = r.error || r.block_reason || (r.ok ? "" : "");
      tb.append(el("tr", {},
        el("td", {}, r.name || r.site || "-"),
        el("td", {}, capName(r.cap)),
        el("td", {}, el("span", {},
          el("span", { class: "dot " + dot }), label)),
        el("td", {}, String(r.status || "")),
        el("td", {}, (r.elapsed_ms != null ? r.elapsed_ms + " ms" : "")),
        el("td", {}, r.mode || ""),
        el("td", { class: "wrap" }, note),
        el("td", { class: "wrap" }, r.url || "")));
    });
    table.append(tb);
    wrap.innerHTML = "";
    wrap.append(table);
  } catch (e) { wrap.innerHTML = `<p class="muted">探测失败：${e.message}</p>`; }
}
const capName = (c) => ({ spec: "SPEC", price: "价格", review: "评价" }[c] || c || "-");

// ---------------- 定时任务 ----------------
async function addSchedule() {
  const task = $("schTask").value;
  const kind = $("schKind").value;
  const trigger = kind === "weekly"
    ? { kind: "weekly", weekday: parseInt($("schWeekday").value),
        hour: parseInt($("schHour").value), minute: parseInt($("schMinute").value) }
    : { kind: "interval", every_hours: parseInt($("schHours").value) };
  const params = { region: STATE.region };
  const brand = $("schBrand").value;
  const year = STATE.region === "jp" ? numOrNull($("schYear").value) : null;
  if (task === "price") {
    params.brands = [brand]; if (year != null) params.year = year;
  } else if (task === "review") {
    params.brand = brand; if (year != null) params.year = year;
  } else if (task === "spec") {
    params.brand = brand;
  } else if (task === "spec_refresh") {
    // 区域已在 params.region 中，后端按该区域刷新全部品牌。
  } else if (task === "release") {
    params.brand = brand;
  }
  const display = (STATE.brands.find((b) => b.code === brand) || {}).name || brand;
  const name = `${taskName(task)} · ${display}`.trim();
  try {
    await apiPost("/api/schedules", { task, params, trigger, name });
    toast("定时任务已创建", "ok");
    loadSchedules();
  } catch (e) { toast("创建失败：" + e.message, "bad"); }
}
const taskName = (t) => ({ price: "抓价格", review: "抓评价", spec: "抓SPEC",
  spec_refresh: "全量刷新SPEC", release: "抓发售日" }[t] || t);

async function loadSchedules() {
  const wrap = $("schList");
  try {
    const d = await apiGet("/api/schedules");
    if (!d.items.length) { wrap.innerHTML = '<p class="muted">暂无定时任务。</p>'; return; }
    const table = el("table");
    table.append(el("thead", {}, el("tr", {},
      el("th", {}, "名称"), el("th", {}, "任务"), el("th", {}, "触发规则"),
      el("th", {}, "下次运行"), el("th", {}, "上次运行"), el("th", {}, "状态"), el("th", {}, "操作"))));
    const tb = el("tbody");
    d.items.forEach((s) => {
      tb.append(el("tr", {},
        el("td", {}, s.name || "-"),
        el("td", {}, taskName(s.task)),
        el("td", {}, triggerText(s.trigger)),
        el("td", {}, (s.next_run_at || "").replace("T", " ")),
        el("td", {}, (s.last_run_at || "-").replace("T", " ")),
        el("td", {}, el("span", { class: "badge " + (s.enabled ? "ok" : "gray") },
                        s.enabled ? "启用" : "停用")),
        el("td", {},
          el("button", { class: "btn small", onclick: () => toggleSchedule(s) },
             s.enabled ? "停用" : "启用"),
          el("button", { class: "btn small", style: "margin-left:6px",
                         onclick: () => delSchedule(s) }, "删除"))));
    });
    table.append(tb);
    wrap.innerHTML = "";
    wrap.append(table);
  } catch (e) { wrap.innerHTML = `<p class="muted">加载失败：${e.message}</p>`; }
}
function triggerText(t) {
  if (!t) return "-";
  if (t.kind === "weekly") {
    const wd = ["周一","周二","周三","周四","周五","周六","周日"][t.weekday] || "?";
    return `每${wd} ${String(t.hour).padStart(2,"0")}:${String(t.minute).padStart(2,"0")}`;
  }
  if (t.kind === "interval") return `每 ${t.every_hours} 小时`;
  return JSON.stringify(t);
}
async function toggleSchedule(s) {
  try { await apiPost(`/api/schedules/${s.id}`, { enabled: !s.enabled }); loadSchedules(); }
  catch (e) { toast("操作失败：" + e.message, "bad"); }
}
async function delSchedule(s) {
  if (!confirm(`删除定时任务「${s.name || s.id}」？`)) return;
  try { await apiPost(`/api/schedules/${s.id}/delete`, {}); toast("已删除", "ok"); loadSchedules(); }
  catch (e) { toast("删除失败：" + e.message, "bad"); }
}

// ---------------- 运行记录 ----------------
async function refreshJobs() {
  const wrap = $("jobsList");
  try {
    const d = await apiGet("/api/jobs");
    const q = d.queue || {};
    $("queueInfo").textContent = `运行中：${q.running || "无"}　排队：${(q.queued || []).length}　历史：${q.total_jobs || 0}`;
    if (!d.items.length) { wrap.innerHTML = '<p class="muted">暂无运行记录。</p>'; return; }
    const table = el("table");
    table.append(el("thead", {}, el("tr", {},
      el("th", {}, "任务"), el("th", {}, "参数"), el("th", {}, "状态"),
      el("th", {}, "创建"), el("th", {}, "结束"))));
    const tb = el("tbody");
    d.items.forEach((j) => {
      const badge = { succeeded: "ok", running: "run", queued: "queued",
                      failed: "fail", cancelled: "gray" }[j.status] || "gray";
      tb.append(el("tr", { class: "clickable", onclick: () => viewJobLog(j.id) },
        el("td", {}, taskName(j.task)),
        el("td", { class: "wrap" }, JSON.stringify(j.params)),
        el("td", {}, el("span", { class: "badge " + badge }, j.status)),
        el("td", {}, (j.created_at || "").replace("T", " ")),
        el("td", {}, (j.finished_at || "").replace("T", " "))));
    });
    table.append(tb);
    wrap.innerHTML = "";
    wrap.append(table);
  } catch (e) { wrap.innerHTML = `<p class="muted">加载失败：${e.message}</p>`; }
}
async function viewJobLog(jobId) {
  stopJobPoll();   // 切换查看任务前先停掉上一个轮询，避免进度条被旧任务覆盖闪动
  const logEl = $("jobsLog");
  const progEl = progressElFor(logEl);
  if (progEl) { progEl.hidden = true; progEl.innerHTML = ""; }
  logEl.textContent = `加载任务 ${jobId} 日志…\n`;
  try {
    const log = await apiGet(`/api/jobs/${jobId}/log?offset=0`);
    logEl.textContent = log.content || "（无日志）";
    const job = await apiGet(`/api/jobs/${jobId}`);
    // 用已加载的全量日志还原进度快照；仍在运行则交给 pollJobLog 继续增量跟踪。
    const tracker = makeProgressTracker();
    tracker.feed(log.content || "");
    renderProgress(progEl, tracker.state, job.status);
    if (!log.eof && job.status === "running") pollJobLog(jobId, logEl);
  } catch (e) { logEl.textContent = "加载失败：" + e.message; }
}

document.addEventListener("DOMContentLoaded", init);
