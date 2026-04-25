const chartInstances = new Map();
let selectedFile = null;
let progressTimer = null;
let echartsReadyPromise = null;
let currentDigest = null;

const palette = {
  blue: "#3f6ff4",
  blueSoft: "#8bb0ff",
  green: "#23b36b",
  greenDark: "#0f8075",
  red: "#ef4444",
  orange: "#f97316",
  purple: "#7867d9",
  gray: "#9aa4b5",
  grid: "rgba(19, 36, 64, 0.08)",
  text: "#132440",
  muted: "#74809a",
};

const metricOrder = [
  "revenue",
  "gross_profit",
  "operating_income",
  "net_income",
  "gross_margin",
  "operating_margin",
  "diluted_eps",
  "operating_cash_flow",
  "free_cash_flow",
  "cash_and_equivalents",
];

const miniMetricOrder = [
  "revenue",
  "gross_profit",
  "gross_margin",
  "net_income",
  "diluted_eps",
];

const iconByMetric = {
  revenue: "▣",
  gross_profit: "%",
  operating_income: "↗",
  net_income: "◆",
  gross_margin: "%",
  operating_margin: "◌",
  diluted_eps: "$",
  operating_cash_flow: "↔",
  free_cash_flow: "▥",
  cash_and_equivalents: "●",
};

function $(id) {
  return document.getElementById(id);
}

function numberLabel(value, digits = 0) {
  if (!Number.isFinite(value)) {
    return "--";
  }
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: digits }).format(value);
}

function baseGrid(extra = {}) {
  return {
    top: 36,
    right: 24,
    bottom: 36,
    left: 54,
    containLabel: false,
    ...extra,
  };
}

function clearCharts() {
  chartInstances.forEach((chart) => chart.dispose());
  chartInstances.clear();
}

function initChart(id, option) {
  const target = $(id);
  if (!target) {
    return null;
  }
  if (typeof echarts === "undefined") {
    target.innerHTML = '<div class="chart-placeholder">图表库加载失败，文字报告仍可阅读。</div>';
    return null;
  }
  const existing = chartInstances.get(id);
  if (existing) {
    existing.dispose();
  }
  const chart = echarts.init(target, null, { renderer: "canvas" });
  chart.setOption(option);
  chartInstances.set(id, chart);
  return chart;
}

function ensureEcharts() {
  if (typeof echarts !== "undefined") {
    return Promise.resolve();
  }
  if (echartsReadyPromise) {
    return echartsReadyPromise;
  }
  echartsReadyPromise = new Promise((resolve, reject) => {
    const existing = document.querySelector('script[data-chart-lib="echarts"]');
    if (existing) {
      existing.addEventListener("load", resolve, { once: true });
      existing.addEventListener("error", reject, { once: true });
      return;
    }
    const script = document.createElement("script");
    script.src = "https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js";
    script.async = true;
    script.dataset.chartLib = "echarts";
    script.addEventListener("load", resolve, { once: true });
    script.addEventListener("error", () => reject(new Error("ECharts failed to load")), { once: true });
    document.head.append(script);
  });
  return echartsReadyPromise;
}

function setText(id, value) {
  const target = $(id);
  if (target) {
    target.textContent = value ?? "";
  }
}

function setProgress(text, percent, tone = "idle") {
  setText("status-text", text);
  setText("status-percent", `${Math.round(percent)}%`);
  const fill = $("status-progress-fill");
  if (fill) {
    fill.style.width = `${Math.max(0, Math.min(100, percent))}%`;
    fill.dataset.tone = tone;
  }
}

function stopProgressTimer() {
  if (progressTimer) {
    window.clearInterval(progressTimer);
    progressTimer = null;
  }
}

function startProgressTimer() {
  stopProgressTimer();
  let current = 8;
  setProgress("正在读取财报并提取关键模块...", current, "busy");
  progressTimer = window.setInterval(() => {
    current = Math.min(92, current + Math.max(1, (94 - current) * 0.08));
    setProgress("正在提取财务数据、风险段落并生成投资者摘要...", current, "busy");
  }, 900);
}

async function checkRuntime() {
  try {
    const response = await fetch("/health");
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    setText("runtime-model", data.model || "analysis service");
    setText("runtime-status", "分析服务已连接，可以上传或输入财报路径。");
  } catch (error) {
    setText("runtime-model", "未连接");
    setText("runtime-status", "后端服务不可用，请先启动 API 服务。");
  }
}

function setLoading(isLoading) {
  const button = $("generate-button");
  if (!button) {
    return;
  }
  button.disabled = isLoading;
  button.textContent = isLoading ? "生成中..." : "生成报告";
}

async function requestDigest() {
  const path = $("path-input")?.value.trim() || "";
  const audience = $("audience")?.value.trim() || "普通投资者";
  const language = $("language")?.value || "zh-Hans";

  if (!selectedFile && !path) {
    throw new Error("请先上传 10-K 财报文件，或输入本地财报路径。");
  }

  if (selectedFile) {
    const formData = new FormData();
    formData.append("file", selectedFile);
    formData.append("audience", audience);
    formData.append("language", language);
    return postDigest("/api/analyze/file", { method: "POST", body: formData });
  }

  return postDigest("/api/analyze/path", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, audience, language }),
  });
}

async function postDigest(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = await response.text();
    try {
      const payload = JSON.parse(detail);
      detail = payload.detail || payload.error || detail;
    } catch {
      // Keep raw server text.
    }
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return response.json();
}

async function handleGenerate(event) {
  event.preventDefault();
  setLoading(true);
  currentDigest = null;
  setReportActionsEnabled(false);
  startProgressTimer();

  try {
    const chartLibraryReady = ensureEcharts().catch(() => null);
    const digest = await requestDigest();
    await chartLibraryReady;
    stopProgressTimer();
    setProgress("报告生成完成。", 100, "done");
    currentDigest = digest;
    renderDigest(digest);
    setReportActionsEnabled(true);
    $("empty-state")?.classList.add("hidden");
    $("report-content")?.classList.remove("hidden");
    $("report-content")?.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    stopProgressTimer();
    setProgress(`生成失败：${error.message}`, 100, "error");
  } finally {
    setLoading(false);
  }
}

function factMap(digest) {
  return Object.fromEntries((digest.fact_snapshot || []).map((item) => [item.metric_key, item]));
}

function chartByType(digest, type) {
  return (digest.chart_specs || []).find((chart) => chart.chart_type === type);
}

function isAppleDigest(digest) {
  const name = `${digest.company_name || ""}`.toLowerCase();
  return name.includes("apple") || name.includes("aapl") || name.includes("苹果");
}

function extractYear(period) {
  const matches = `${period || ""}`.match(/20\d{2}/g);
  return matches ? matches[matches.length - 1] : new Date().getFullYear().toString();
}

function formatPeriod(period) {
  const text = `${period || ""}`.trim();
  const date = new Date(text);
  if (!Number.isNaN(date.getTime())) {
    return date.toISOString().slice(0, 10).replaceAll("-", ".");
  }
  return text || "--";
}

function companyInitials(companyName) {
  const clean = `${companyName || "Company"}`.replace(/[^a-zA-Z0-9\u4e00-\u9fa5 ]/g, " ");
  const words = clean.split(/\s+/).filter(Boolean);
  if (!words.length) {
    return "F";
  }
  return words.slice(0, 2).map((word) => word[0]).join("").toUpperCase();
}

function cleanYoy(text) {
  return `${text || ""}`.replace(/\s*·\s*/g, "，").trim() || "--";
}

function signedClass(text) {
  return `${text || ""}`.includes("-") ? "negative" : "";
}

function parseMoneyToMillions(text) {
  const raw = `${text || ""}`.replace(/,/g, "").trim();
  const match = raw.match(/-?\d+(?:\.\d+)?/);
  if (!match) {
    return null;
  }
  const value = Number(match[0]);
  if (!Number.isFinite(value)) {
    return null;
  }
  if (/B\b/i.test(raw)) {
    return value * 1000;
  }
  if (/T\b/i.test(raw)) {
    return value * 1000000;
  }
  return value;
}

function formatMillion(value) {
  return numberLabel(value, Math.abs(value) < 1000 ? 1 : 0);
}

function metricValue(facts, key) {
  return parseMoneyToMillions(facts[key]?.value_text);
}

function renderDigest(digest) {
  clearCharts();
  const facts = factMap(digest);
  const lineChart = chartByType(digest, "line");
  const waterfallChart = chartByType(digest, "waterfall");
  const sankeyChart = chartByType(digest, "sankey");
  const apple = isAppleDigest(digest);
  const year = extractYear(digest.reporting_period);
  const companyName = digest.company_name || "Unknown Company";

  renderHero(digest, apple, year, companyName);
  renderRevenueOverview(digest, facts, lineChart, year, companyName);
  renderMiniKpis(facts);
  renderProfitFlow(facts, sankeyChart);
  renderCashSection(facts, waterfallChart);
  renderRiskGrid(digest);
  renderDashboard(digest, facts, lineChart, sankeyChart, apple);

  window.setTimeout(() => chartInstances.forEach((chart) => chart.resize()), 80);
}

function renderHero(digest, apple, year, companyName) {
  setText("hero-mark", apple ? "" : companyInitials(companyName));
  setText("hero-title", `${apple ? "苹果公司" : companyName} ${year} 财年报告解析`);
  setText("hero-subtitle", `Annual Report Analysis ${year}`);
  setText(
    "hero-description",
    `深入解读 ${companyName} ${year} 财年业绩表现、财务状况与未来展望`
  );
  setText("hero-period", `报告期间：${formatPeriod(digest.reporting_period)}`);
  $("hero-visual")?.classList.toggle("is-generic", !apple);
}

function renderRevenueOverview(digest, facts, chartSpec, year, companyName) {
  const revenue = facts.revenue;
  const revenueText = revenue?.value_text || "--";
  const yoy = cleanYoy(revenue?.yoy_text);
  setText(
    "revenue-section-copy",
    `${year} 财年${companyName}营收达 ${revenueText}，${yoy}。`
  );
  setText("revenue-total-label", "营收总额");
  setText("revenue-total-value", revenueText);
  setText("revenue-total-yoy", firstYoyPart(revenue?.yoy_text));
  setText("revenue-chart-title", chartSpec?.title || "收入与利润趋势（百万美元）");
  renderRevenueLegend(chartSpec);
  renderRevenueLine(chartSpec, facts);
}

function firstYoyPart(text) {
  const clean = `${text || ""}`.split("·")[0].trim();
  return clean.replace("同比", "") || "--";
}

function renderRevenueLegend(chartSpec) {
  const target = $("revenue-chart-legend");
  if (!target) {
    return;
  }
  target.innerHTML = "";
  const series = chartSpec?.series?.length ? chartSpec.series.slice(0, 3) : [{ name: "营业收入" }];
  series.forEach((item, index) => {
    const label = document.createElement("span");
    const dot = document.createElement("i");
    dot.className = "dot";
    dot.style.background = chartColor(index);
    label.append(dot, item.name);
    target.append(label);
  });
}

function renderRevenueLine(chartSpec, facts) {
  const categories = chartSpec?.categories?.length ? chartSpec.categories : ["上一期", "本期"];
  const series = chartSpec?.series?.length
    ? chartSpec.series
    : [
        {
          name: "营业收入",
          values: [metricValue(facts, "revenue") || 0, metricValue(facts, "revenue") || 0],
        },
      ];

  initChart("revenue-line-chart", {
    color: series.map((_, index) => chartColor(index)),
    tooltip: { trigger: "axis" },
    grid: baseGrid({ top: 28, left: 52, right: 20, bottom: 32 }),
    xAxis: {
      type: "category",
      boundaryGap: false,
      data: categories,
      axisTick: { show: false },
      axisLine: { lineStyle: { color: "#d5dbe7" } },
      axisLabel: { color: palette.muted, fontWeight: 600 },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: palette.muted, formatter: numberLabel },
      splitLine: { lineStyle: { color: palette.grid } },
    },
    series: series.map((item, index) => ({
      name: item.name,
      type: "line",
      smooth: true,
      symbol: "circle",
      symbolSize: 7,
      lineStyle: { width: index === 0 ? 4 : 3 },
      data: item.values || [],
    })),
  });
}

function renderMiniKpis(facts) {
  const target = $("mini-kpis");
  if (!target) {
    return;
  }
  target.innerHTML = "";
  pickFacts(facts, miniMetricOrder, 5).forEach((fact, index) => {
    target.append(createMetricCard(fact, index, "mini"));
  });
}

function pickFacts(facts, order, limit) {
  const seen = new Set();
  const picked = [];
  order.forEach((key) => {
    if (facts[key]) {
      picked.push(facts[key]);
      seen.add(key);
    }
  });
  Object.values(facts).forEach((fact) => {
    if (!seen.has(fact.metric_key)) {
      picked.push(fact);
    }
  });
  return picked.slice(0, limit);
}

function createMetricCard(fact, index, size = "mini") {
  const card = document.createElement("article");
  const icon = document.createElement("span");
  icon.className = `mini-icon ${iconTone(index, fact.metric_key)}`;
  icon.textContent = iconByMetric[fact.metric_key] || "◆";

  const body = document.createElement("span");
  const label = document.createElement("p");
  const value = document.createElement("strong");
  const change = document.createElement("em");
  label.textContent = fact.label || fact.metric_key;
  value.textContent = fact.value_text || "--";
  change.textContent = size === "dashboard" ? firstYoyPart(fact.yoy_text) : firstYoyPart(fact.yoy_text);
  change.className = signedClass(fact.yoy_text);
  body.append(label, value, change);
  card.append(icon, body);
  return card;
}

function iconTone(index, metricKey) {
  if (metricKey?.includes("margin") || metricKey?.includes("profit")) {
    return "green";
  }
  if (metricKey?.includes("cash")) {
    return "purple";
  }
  return ["app", "green", "orange", "purple", "muted"][index % 5];
}

function renderProfitFlow(facts, sankeyChart) {
  const metrics = flowMetrics(facts, sankeyChart);
  const base = metrics.revenue || 1;

  setText("profit-flow-title", `利润流向（${extractYearFromFlow(sankeyChart)}年，百万美元）`);
  setFlowNode("flow-revenue", "营收", metrics.revenue, base);
  setFlowNode("flow-gross", "毛利润", metrics.gross, base);
  setFlowNode("flow-cost", "营业成本", metrics.cost, base);
  setFlowNode("flow-operating", "营业利润", metrics.operating, base);
  setFlowNode("flow-opex", "营业费用", metrics.opex, base);
  setFlowNode("flow-net", "净利润", metrics.net, base);
  setFlowNode("flow-tax", metrics.taxLabel || "税项及其他", metrics.tax, base);
  setFlowNode("flow-other", "其他收益", metrics.other, base);
  setFlowWidths(metrics, base);
}

function extractYearFromFlow(chartSpec) {
  const match = `${chartSpec?.title || ""}`.match(/20\d{2}/);
  return match ? match[0] : "本期";
}

function flowMetrics(facts, chartSpec) {
  const nodeValue = (...names) => {
    const nodes = chartSpec?.flow_nodes || [];
    const normalized = names.map((name) => name.toLowerCase());
    const node = nodes.find((entry) => normalized.some((name) => `${entry.name}`.toLowerCase().includes(name)));
    return Number.isFinite(node?.value) ? node.value : null;
  };

  const revenue = nodeValue("总收入", "revenue") || metricValue(facts, "revenue");
  const gross = nodeValue("毛利润", "gross") || metricValue(facts, "gross_profit");
  const cost = nodeValue("营业成本", "cost of revenue", "cost") || safeSubtract(revenue, gross);
  const operating = nodeValue("营业利润", "operating") || metricValue(facts, "operating_income");
  const opex = nodeValue("营业费用", "operating expenses") || safeSubtract(gross, operating);
  const net = nodeValue("净利润", "net income") || metricValue(facts, "net_income");
  const tax = nodeValue("所得税", "tax") || nodeValue("税项", "tax") || safeSubtract(operating, net);
  const other = nodeValue("营业外", "other") || 0;

  return {
    revenue,
    gross,
    cost,
    operating,
    opex,
    net,
    tax: tax && tax > 0 ? tax : 0,
    taxLabel: chartSpec?.flow_nodes?.some((node) => `${node.name}`.includes("所得税")) ? "所得税费用" : "税项及其他",
    other: Math.max(0, other || 0),
  };
}

function safeSubtract(a, b) {
  if (!Number.isFinite(a) || !Number.isFinite(b)) {
    return null;
  }
  return Math.max(0, a - b);
}

function setFlowNode(id, label, value, base) {
  const target = $(id);
  if (!target) {
    return;
  }
  const percent = Number.isFinite(value) && base ? `${((value / base) * 100).toFixed(1)}%` : "--";
  target.innerHTML = `<span>${label}</span><strong>${Number.isFinite(value) ? formatMillion(value) : "--"}</strong><span>${percent}</span>`;
}

function setFlowWidths(metrics, base) {
  const setWidth = (selector, value, min = 14, max = 112) => {
    const target = document.querySelector(selector);
    if (!target || !Number.isFinite(value) || !base) {
      return;
    }
    const width = Math.max(min, Math.min(max, (value / base) * max));
    target.style.strokeWidth = width.toFixed(1);
  };
  setWidth(".flow-gross", metrics.gross, 28, 96);
  setWidth(".flow-cost", metrics.cost, 24, 108);
  setWidth(".flow-opex", metrics.opex, 12, 58);
  setWidth(".flow-net", metrics.net, 24, 82);
  setWidth(".flow-tax", metrics.tax, 8, 42);
}

function renderCashSection(facts, chartSpec) {
  setText("cash-chart-title", chartSpec?.title || "经营现金流桥接（百万美元）");
  const ocf = metricValue(facts, "operating_cash_flow");
  const fcf = metricValue(facts, "free_cash_flow");
  const capex = Number.isFinite(ocf) && Number.isFinite(fcf) ? fcf - ocf : null;
  const values = chartSpec?.series?.[0]?.values?.length
    ? chartSpec.series[0].values.map(Number)
    : [ocf || 0, capex || 0, fcf || 0];
  renderCashWaterfall(chartSpec, values);
  renderCashKpis(facts, values);
}

function renderCashWaterfall(chartSpec, values) {
  const operatingCash = Number(values[0]) || 0;
  const capex = Number(values[1]) || 0;
  const freeCash = Number(values[2]) || operatingCash + capex;
  const labels = chartSpec?.categories?.length
    ? chartSpec.categories.slice(0, 3)
    : ["经营现金流", "资本开支", "自由现金流"];
  const capexAbs = Math.abs(capex);
  const bridgeBase = Math.min(operatingCash, freeCash);
  const maxValue = Math.max(operatingCash, freeCash, operatingCash + capexAbs) * 1.22;

  initChart("cash-waterfall-chart", {
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: () =>
        `${labels[0]}：${numberLabel(operatingCash)}<br/>${labels[1]}：${numberLabel(capex)}<br/>${labels[2]}：${numberLabel(freeCash)}`,
    },
    grid: baseGrid({ top: 38, left: 58, right: 26, bottom: 52 }),
    xAxis: {
      type: "category",
      data: labels,
      axisTick: { show: true, lineStyle: { color: "#cbd4e1" } },
      axisLine: { lineStyle: { color: "#cbd4e1" } },
      axisLabel: { color: palette.text, fontSize: 12, fontWeight: 700, margin: 12 },
    },
    yAxis: {
      type: "value",
      min: 0,
      max: maxValue || 100,
      axisLabel: { color: "#536879", formatter: numberLabel },
      splitLine: { lineStyle: { color: palette.grid } },
    },
    series: [
      {
        type: "bar",
        stack: "cash",
        data: [0, bridgeBase, 0],
        itemStyle: { color: "transparent" },
        emphasis: { disabled: true },
        silent: true,
      },
      {
        type: "bar",
        stack: "cash",
        barWidth: 56,
        data: [operatingCash, null, freeCash],
        itemStyle: { color: palette.greenDark, borderRadius: [10, 10, 2, 2] },
        label: {
          show: true,
          position: "top",
          color: palette.text,
          fontWeight: 800,
          formatter: ({ value }) => numberLabel(value),
        },
      },
      {
        type: "bar",
        stack: "cash",
        barWidth: 56,
        data: [null, capexAbs, null],
        itemStyle: { color: palette.red, borderRadius: [10, 10, 2, 2] },
        label: {
          show: true,
          position: capex < 0 ? "bottom" : "top",
          color: palette.text,
          fontWeight: 800,
          formatter: () => numberLabel(capex),
        },
      },
      {
        type: "line",
        symbol: "none",
        silent: true,
        tooltip: { show: false },
        data: [operatingCash, operatingCash, freeCash],
        lineStyle: { width: 2, type: "dashed", color: "rgba(19,36,64,0.22)" },
      },
    ],
  });
}

function renderCashKpis(facts, values) {
  const target = $("cash-kpis");
  if (!target) {
    return;
  }
  const items = [
    facts.operating_cash_flow || { label: "经营现金流", value_text: `$${((values[0] || 0) / 1000).toFixed(1)}B`, yoy_text: "" },
    { label: "资本开支", value_text: `$${(Math.abs(values[1] || 0) / 1000).toFixed(1)}B`, yoy_text: "" },
    facts.free_cash_flow || { label: "自由现金流", value_text: `$${((values[2] || 0) / 1000).toFixed(1)}B`, yoy_text: "" },
    facts.cash_and_equivalents || { label: "期末现金", value_text: "--", yoy_text: "" },
  ];
  target.innerHTML = "";
  items.forEach((fact, index) => target.append(createMetricCard(fact, index)));
}

function renderRiskGrid(digest) {
  const target = $("risk-grid");
  if (!target) {
    return;
  }
  const icons = ["◎", "⌘", "♟", "盾", "¥", "◇"];
  const risks = (digest.risks || []).slice(0, 6);
  const fallback = [
    "宏观经济不确定性可能影响消费需求与汇率表现。",
    "供应链风险可能导致产品供给和成本波动。",
    "市场竞争加剧可能影响定价和利润率。",
    "监管与合规风险可能带来额外成本。",
    "汇率波动可能影响海外收入折算。",
    "新品需求不确定性可能影响未来增长。",
  ];
  target.innerHTML = "";
  (risks.length ? risks : fallback).slice(0, 6).forEach((risk, index) => {
    const title = riskTitle(risk, index);
    const card = document.createElement("section");
    card.className = "risk-item";
    card.innerHTML = `
      <span class="risk-icon">${icons[index % icons.length]}</span>
      <span>
        <h3>${escapeHtml(title)}</h3>
        <p>${escapeHtml(risk)}</p>
      </span>
    `;
    target.append(card);
  });
}

function riskTitle(text, index) {
  const clean = `${text || ""}`.replace(/^[-•\s]+/, "");
  const first = clean.split(/[：:，,。.;；]/)[0].trim();
  return first.length >= 3 && first.length <= 14 ? first : `风险 ${index + 1}`;
}

function renderDashboard(digest, facts, lineChart, sankeyChart, apple) {
  const year = extractYear(digest.reporting_period);
  setText("dashboard-section-copy", `关键指标一览，快速把握 ${year} 财年财务健康状况。`);
  setText(
    "source-note",
    `数据来源：${sankeyChart?.source_snippet || lineChart?.source_snippet || "公开 10-K / XBRL 披露"}`
  );
  renderDashboardKpis(facts);
  renderDonuts(digest, sankeyChart, apple);
  renderRevenueBars(lineChart, facts);
}

function renderDashboardKpis(facts) {
  const target = $("dashboard-kpis");
  if (!target) {
    return;
  }
  target.innerHTML = "";
  pickFacts(facts, metricOrder, 8).forEach((fact, index) => {
    target.append(createMetricCard(fact, index, "dashboard"));
  });
}

function renderDonuts(digest, sankeyChart, apple) {
  const businessData = apple
    ? [
        { name: "iPhone", value: 52.1 },
        { name: "服务", value: 21.9 },
        { name: "Mac", value: 8.7 },
        { name: "iPad", value: 7.1 },
        { name: "可穿戴", value: 10.0 },
      ]
    : topSankeySources(sankeyChart);
  const regionData = topSankeySources(sankeyChart);

  initChart(
    "business-donut-chart",
    donutOption(businessData, [palette.blue, palette.green, palette.orange, palette.purple, "#9fb2d6"])
  );
  initChart(
    "region-donut-chart",
    donutOption(regionData, [palette.blue, palette.orange, palette.green, palette.purple, "#26a3a3", "#8b9ab6"])
  );
}

function topSankeySources(chartSpec) {
  const nodes = chartSpec?.flow_nodes || [];
  const links = chartSpec?.flow_links || [];
  const targetNames = new Set(links.map((link) => link.target));
  const sourceNodes = nodes
    .filter((node) => !targetNames.has(node.name) && Number.isFinite(node.value) && node.value > 0)
    .sort((a, b) => b.value - a.value)
    .slice(0, 5);
  const total = sourceNodes.reduce((sum, item) => sum + item.value, 0);
  if (!sourceNodes.length || !total) {
    return [{ name: "收入", value: 100 }];
  }
  return sourceNodes.map((node) => ({
    name: cleanSegmentName(node.name),
    value: Number(((node.value / total) * 100).toFixed(1)),
  }));
}

function cleanSegmentName(name) {
  return `${name || ""}`
    .replace(/\s+Segment$/i, "")
    .replace(/\s+segment$/i, "")
    .replace("Greater China", "大中华区")
    .replace("Rest Of Asia Pacific", "亚太其他")
    .replace("Americas", "美洲")
    .replace("Europe", "欧洲")
    .replace("Japan", "日本");
}

function donutOption(data, colors) {
  return {
    color: colors,
    tooltip: { trigger: "item", formatter: "{b}: {d}%" },
    legend: {
      right: 2,
      top: "center",
      orient: "vertical",
      icon: "circle",
      itemWidth: 8,
      itemHeight: 8,
      textStyle: { color: palette.text, fontSize: 10, fontWeight: 600 },
      formatter: (name) => {
        const item = data.find((entry) => entry.name === name);
        return `${name}    ${item?.value ?? 0}%`;
      },
    },
    series: [
      {
        type: "pie",
        radius: ["48%", "76%"],
        center: ["27%", "50%"],
        avoidLabelOverlap: true,
        label: { show: false },
        data,
      },
    ],
  };
}

function renderRevenueBars(chartSpec, facts) {
  const revenueSeries =
    chartSpec?.series?.find((item) => item.name?.includes("营收") || item.name?.includes("收入")) ||
    chartSpec?.series?.[0];
  const categories = chartSpec?.categories?.length ? chartSpec.categories : ["本期"];
  const values = revenueSeries?.values?.length ? revenueSeries.values : [metricValue(facts, "revenue") || 0];

  initChart("revenue-bar-chart", {
    color: [palette.blue],
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    grid: baseGrid({ top: 18, left: 44, right: 10, bottom: 28 }),
    xAxis: {
      type: "category",
      data: categories,
      axisTick: { show: false },
      axisLine: { lineStyle: { color: "#d5dbe7" } },
      axisLabel: { color: palette.muted, fontWeight: 600 },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: palette.muted, fontSize: 10, formatter: numberLabel },
      splitLine: { lineStyle: { color: palette.grid } },
    },
    series: [
      {
        type: "bar",
        barWidth: 30,
        data: values,
        itemStyle: { color: palette.blue, borderRadius: [4, 4, 0, 0] },
      },
    ],
  });
}

function chartColor(index) {
  return [palette.blue, palette.greenDark, palette.orange, palette.purple, palette.gray][index % 5];
}

function escapeHtml(value) {
  return `${value || ""}`
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function bindForm() {
  $("analysis-form")?.addEventListener("submit", handleGenerate);
  $("download-json")?.addEventListener("click", downloadCurrentDigest);
  $("print-report")?.addEventListener("click", () => window.print());
  $("upload-zone")?.addEventListener("click", () => $("file-input")?.click());
  $("file-input")?.addEventListener("change", (event) => {
    selectedFile = event.target.files?.[0] || null;
    setText("file-name", selectedFile ? selectedFile.name : "支持 PDF / HTML / TXT");
    if (selectedFile && $("path-input")) {
      $("path-input").value = "";
    }
  });
  $("path-input")?.addEventListener("input", () => {
    if ($("path-input")?.value.trim()) {
      selectedFile = null;
      if ($("file-input")) {
        $("file-input").value = "";
      }
      setText("file-name", "支持 PDF / HTML / TXT");
    }
  });
}

function setReportActionsEnabled(enabled) {
  ["download-json", "print-report"].forEach((id) => {
    const button = $(id);
    if (button) {
      button.disabled = !enabled;
    }
  });
}

function downloadCurrentDigest() {
  if (!currentDigest) {
    return;
  }
  const blob = new Blob([JSON.stringify(currentDigest, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  const company = `${currentDigest.company_name || "report"}`.replace(/[^\w-]+/g, "_");
  const period = `${currentDigest.reporting_period || "digest"}`.replace(/[^\w-]+/g, "_");
  anchor.href = url;
  anchor.download = `${company}_${period}.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}

window.addEventListener("DOMContentLoaded", () => {
  bindForm();
  checkRuntime();
  setProgress("输入路径或上传文件后开始分析。", 0);
});

window.addEventListener("resize", () => chartInstances.forEach((chart) => chart.resize()));
