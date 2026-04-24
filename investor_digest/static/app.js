const state = {
  digest: null,
  charts: [],
  chartObservers: [],
  progressTimer: null,
  progressValue: 0,
};

const runtimeModelEl = document.getElementById("runtime-model");
const runtimeStatusEl = document.getElementById("runtime-status");
const statusStripEl = document.getElementById("status-strip");
const statusTextEl = document.getElementById("status-text");
const statusPercentEl = document.getElementById("status-percent");
const statusProgressFillEl = document.getElementById("status-progress-fill");
const pathInputEl = document.getElementById("path-input");
const audienceEl = document.getElementById("audience");
const languageEl = document.getElementById("language");
const analysisFormEl = document.getElementById("analysis-form");
const fileInputEl = document.getElementById("file-input");
const uploadZoneEl = document.getElementById("upload-zone");
const fileNameEl = document.getElementById("file-name");
const generateButtonEl = document.getElementById("generate-button");
const reportTitleEl = document.getElementById("report-title");
const reportContentEl = document.getElementById("report-content");
const emptyStateEl = document.getElementById("empty-state");
const downloadJsonEl = document.getElementById("download-json");
const printReportEl = document.getElementById("print-report");
const summaryCompanyTitleEl = document.getElementById("summary-company-title");
const companyAvatarEl = document.getElementById("company-avatar");

const ANALYSIS_PROGRESS_STEPS = [
  { percent: 8, message: "正在读取文件和基础元数据…" },
  { percent: 24, message: "正在抽取关键章节与财务事实…" },
  { percent: 46, message: "正在整理重点风险与亮点…" },
  { percent: 68, message: "正在生成投资者可读摘要…" },
  { percent: 86, message: "正在整理图表、术语和提示…" },
  { percent: 94, message: "正在完成最终报告排版…" },
];

analysisFormEl.addEventListener("submit", async (event) => {
  event.preventDefault();
  const [file] = fileInputEl.files;
  const path = pathInputEl.value.trim();
  const audience = audienceEl.value.trim() || "普通投资者";
  const language = languageEl.value;

  if (file) {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("audience", audience);
    formData.append("language", language);

    await runAnalysis({
      endpoint: "/api/analyze/file",
      options: {
        method: "POST",
        body: formData,
      },
      label: `正在分析上传文件: ${file.name}`,
    });
    return;
  }

  if (!path) {
    setStatus("请输入财报路径或上传文件。");
    return;
  }

  await runAnalysis({
    endpoint: "/api/analyze/path",
    options: {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path,
        audience,
        language,
      }),
    },
    label: `正在分析路径: ${path}`,
  });
});

uploadZoneEl.addEventListener("click", (event) => {
  event.preventDefault();
  fileInputEl.click();
});

uploadZoneEl.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") {
    return;
  }
  event.preventDefault();
  fileInputEl.click();
});

fileInputEl.addEventListener("change", () => {
  const [file] = fileInputEl.files;
  if (!file) {
    fileNameEl.textContent = "支持 PDF / HTML / TXT";
    setProgress(0, "idle");
    return;
  }

  fileNameEl.textContent = file.name;
  setProgress(0, "idle");
  setStatus(`已选择文件: ${file.name}`);
});

downloadJsonEl.addEventListener("click", () => {
  if (!state.digest) {
    return;
  }

  const blob = new Blob([JSON.stringify(state.digest, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  const company = (state.digest.company_name || "report").replace(/[^\w-]+/g, "_");
  anchor.href = url;
  anchor.download = `${company}_${state.digest.reporting_period || "digest"}.json`;
  anchor.click();
  URL.revokeObjectURL(url);
});

printReportEl.addEventListener("click", () => window.print());

async function detectRuntime() {
  try {
    const response = await fetch("/health");
    if (!response.ok) {
      throw new Error(`health ${response.status}`);
    }
    const payload = await response.json();
    runtimeModelEl.textContent = payload.model || "unknown";
    runtimeStatusEl.textContent = "分析服务已连接";
  } catch (error) {
    runtimeModelEl.textContent = "离线";
    runtimeStatusEl.textContent = "当前服务未响应";
  }
}

async function runAnalysis({ endpoint, options, label }) {
  startProgress(label);
  toggleBusy(true);
  try {
    const response = await fetch(endpoint, options);
    const payload = await response.json();
    if (!response.ok) {
      const detail = payload.detail || "分析失败";
      throw new Error(detail);
    }

    state.digest = payload;
    renderDigest(payload);
    finishProgress("报告已生成。你可以继续上传其他文件，或直接打印当前报告。", "success");
  } catch (error) {
    console.error(error);
    finishProgress(`生成失败: ${error.message}`, "error");
  } finally {
    toggleBusy(false);
  }
}

function setStatus(message) {
  statusTextEl.textContent = message;
}

function setProgress(value, stateName = "idle") {
  state.progressValue = Math.max(0, Math.min(100, Math.round(value)));
  statusPercentEl.textContent = `${state.progressValue}%`;
  statusProgressFillEl.style.width = `${state.progressValue}%`;
  statusStripEl.dataset.state = stateName;
}

function clearProgressTimer() {
  if (!state.progressTimer) {
    return;
  }

  window.clearInterval(state.progressTimer);
  state.progressTimer = null;
}

function startProgress(initialMessage) {
  clearProgressTimer();
  setStatus(initialMessage);
  setProgress(3, "running");

  let stepIndex = 0;
  state.progressTimer = window.setInterval(() => {
    const step = ANALYSIS_PROGRESS_STEPS[Math.min(stepIndex, ANALYSIS_PROGRESS_STEPS.length - 1)];
    const nextValue = Math.max(state.progressValue + 1, step.percent);
    setStatus(step.message);
    setProgress(Math.min(nextValue, 95), "running");
    if (stepIndex < ANALYSIS_PROGRESS_STEPS.length - 1) {
      stepIndex += 1;
    }
  }, 2400);
}

function finishProgress(message, stateName) {
  clearProgressTimer();
  setStatus(message);
  setProgress(100, stateName);
}

function toggleBusy(isBusy) {
  document.body.dataset.busy = String(isBusy);
  generateButtonEl.disabled = isBusy;
  fileInputEl.disabled = isBusy;
  pathInputEl.disabled = isBusy;
  audienceEl.disabled = isBusy;
  languageEl.disabled = isBusy;
  uploadZoneEl.disabled = isBusy;
}

function renderDigest(digest) {
  const generatedAtText = `报告生成时间：${new Date().toLocaleString("zh-Hans-CN", {
    hour12: false,
  })}`;
  reportTitleEl.textContent = digest.company_name;
  summaryCompanyTitleEl.textContent = digest.company_name;
  document.getElementById("meta-company").textContent = digest.ticker || "10-K";
  document.getElementById("meta-period").textContent = digest.reporting_period;
  document.getElementById("meta-audience").textContent = digest.audience;
  companyAvatarEl.textContent = buildCompanyAvatar(digest.company_name);
  document.getElementById("generated-at").textContent = generatedAtText;
  document.getElementById("footer-generated-at").textContent = generatedAtText.replace(/^报告生成时间：/u, "");
  document.getElementById("takeaway").textContent = digest.one_sentence_takeaway;
  document.getElementById("overview").innerHTML = marked.parse(
    digest.overview_markdown || ""
  );
  document.getElementById("investor-view").innerHTML = marked.parse(
    digest.investor_view_markdown || ""
  );

  fillFactSnapshot(digest.fact_snapshot || []);
  fillCoverPills(digest.fact_snapshot || []);
  fillPlainLanguageSummary(digest);
  fillList("key-points", digest.key_points);
  fillList("positives", digest.positives);
  fillList("risks", digest.risks);
  fillList("watchlist", digest.watchlist);
  fillWarnings(digest.warnings);
  fillGlossary(digest.glossary);
  fillEvidenceCards(digest.evidence_cards || []);

  document.getElementById("disclaimer").textContent = digest.risk_disclaimer || "";
  document.getElementById("raw-json").textContent = JSON.stringify(digest, null, 2);

  emptyStateEl.classList.add("hidden");
  reportContentEl.classList.remove("hidden");
  renderCharts(digest.chart_specs || []);
  downloadJsonEl.disabled = false;
  printReportEl.disabled = false;
}

function fillFactSnapshot(items = []) {
  const target = document.getElementById("fact-grid");
  const morePanel = document.getElementById("more-facts-panel");
  const moreTarget = document.getElementById("more-fact-grid");
  const moreCount = document.getElementById("more-facts-count");
  target.innerHTML = "";
  moreTarget.innerHTML = "";
  morePanel.classList.add("hidden");
  if (!items.length) {
    target.innerHTML = `
      <article class="fact-card empty-card">
        <h4>当前没有事实卡片</h4>
        <p>本轮输出没有足够稳定的结构化指标。</p>
      </article>
    `;
    return;
  }

  const { primary, secondary } = splitFactItems(items);

  primary.forEach((item) => {
    target.appendChild(buildFactCard(item, "primary"));
  });

  if (secondary.length) {
    morePanel.classList.remove("hidden");
    moreCount.textContent = `${secondary.length} 项`;
    secondary.forEach((item) => {
      moreTarget.appendChild(buildFactCard(item, "secondary"));
    });
  }
}

function fillCoverPills(items = []) {
  const target = document.getElementById("cover-pill-grid");
  target.innerHTML = "";
  if (!items.length) {
    return;
  }

  const priority = [
    "revenue",
    "net_income",
    "operating_cash_flow",
    "operating_income",
    "diluted_eps",
    "free_cash_flow",
    "gross_margin",
    "operating_margin",
  ];
  const rank = new Map(priority.map((key, index) => [key, index]));
  const selected = [...items]
    .sort((a, b) => {
      const aRank = rank.has(a.metric_key) ? rank.get(a.metric_key) : 99;
      const bRank = rank.has(b.metric_key) ? rank.get(b.metric_key) : 99;
      return aRank - bRank;
    })
    .slice(0, 3);

  selected.forEach((item) => {
    const trend = parseFactTrend(item.yoy_text || "");
    const performance = formatPerformanceBadgeForMetric(item);
    const pill = document.createElement("article");
    pill.className = `cover-pill ${trend.yoy.tone}`;
    pill.innerHTML = `
      <div class="cover-pill-head">
        <span class="cover-pill-label">${escapeHtml(item.label)}</span>
        <span class="cover-pill-confidence ${escapeHtml(performance.className)}">${escapeHtml(performance.label)}</span>
      </div>
      <strong>${escapeHtml(item.value_text)}</strong>
      <div class="cover-pill-meta">
        <span>${escapeHtml(trend.yoy.label)} ${escapeHtml(trend.yoy.value)}</span>
        <span>${escapeHtml(trend.change.label)} ${escapeHtml(trend.change.value)}</span>
      </div>
    `;
    target.appendChild(pill);
  });
}

function fillPlainLanguageSummary(digest) {
  const target = document.getElementById("plain-language-summary");
  const content =
    digest.investor_view_markdown ||
    digest.overview_markdown ||
    digest.one_sentence_takeaway ||
    "";

  if (!content.trim()) {
    target.innerHTML = "<p>当前没有可展示的通俗总结。</p>";
    return;
  }

  target.innerHTML = marked.parse(content);
}

function splitFactItems(items) {
  const priority = [
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "diluted_eps",
    "operating_cash_flow",
  ];
  const rank = new Map(priority.map((key, index) => [key, index]));
  const sorted = [...items].sort((a, b) => {
    const aRank = rank.has(a.metric_key) ? rank.get(a.metric_key) : 99;
    const bRank = rank.has(b.metric_key) ? rank.get(b.metric_key) : 99;
    if (aRank !== bRank) {
      return aRank - bRank;
    }
    return 0;
  });
  return {
    primary: sorted.slice(0, 6),
    secondary: sorted.slice(6),
  };
}

function buildFactCard(item, variant = "primary") {
  const trend = parseFactTrend(item.yoy_text || "");
  const performance = formatPerformanceBadgeForMetric(item);
  const card = document.createElement("article");
  card.className = `fact-card fact-card-${variant}`;
  card.innerHTML = `
    <div class="fact-card-header">
      <p class="card-kicker">${escapeHtml(item.label)}</p>
      <span class="confidence-pill ${escapeHtml(performance.className)}">${escapeHtml(performance.label)}</span>
    </div>
    <h4>${escapeHtml(item.value_text)}</h4>
    <div class="fact-trend">
      <div class="fact-trend-row ${trend.yoy.tone}">
        <span class="fact-trend-label">${escapeHtml(trend.yoy.label)}</span>
        <span class="fact-trend-value">${escapeHtml(trend.yoy.value)}</span>
      </div>
      <div class="fact-trend-row ${trend.change.tone}">
        <span class="fact-trend-label">${escapeHtml(trend.change.label)}</span>
        <span class="fact-trend-value">${escapeHtml(trend.change.value)}</span>
      </div>
    </div>
  `;
  return card;
}

function parseFactTrend(text) {
  const parts = String(text || "")
    .split("·")
    .map((part) => part.trim())
    .filter(Boolean);

  const yoyPart = parts.find((part) => part.startsWith("同比")) || "";
  const changePart = parts.find((part) => part.startsWith("变化")) || "";

  return {
    yoy: buildTrendPart("同比", yoyPart.replace(/^同比\s*/u, "") || "--"),
    change: buildTrendPart("变化", changePart.replace(/^变化\s*/u, "") || "--"),
  };
}

function buildTrendPart(label, value) {
  const trimmed = String(value || "--").trim();
  return {
    label,
    value: trimmed,
    tone: getTrendTone(trimmed),
  };
}

function getTrendTone(value) {
  if (!value || value === "--") {
    return "is-neutral";
  }
  if (value.includes("+")) {
    return "is-positive";
  }
  if (value.includes("-")) {
    return "is-negative";
  }
  return "is-neutral";
}

function fillEvidenceCards(items = []) {
  const target = document.getElementById("evidence-grid");
  target.innerHTML = "";
  if (!items.length) {
    target.innerHTML = `
      <article class="evidence-card empty-card">
        <h4>当前没有证据卡片</h4>
        <p>本轮输出没有附加来源片段。</p>
      </article>
    `;
    return;
  }

  items.forEach((item) => {
    const metrics = Array.isArray(item.related_metrics) ? item.related_metrics : [];
    const chips = metrics.length
      ? `<div class="evidence-chip-row">${metrics
          .map((metric) => `<span class="evidence-chip">${escapeHtml(metric)}</span>`)
          .join("")}</div>`
      : "";
    const confidence = formatImportanceBadge(item.importance || "medium");

    const card = document.createElement("article");
    card.className = `evidence-card evidence-${escapeHtml(item.category || "explanation")}`;
    card.innerHTML = `
      <div class="evidence-card-header">
        <div>
          <p class="card-kicker">${escapeHtml(item.category || "evidence")}</p>
          <h4>${escapeHtml(item.title)}</h4>
        </div>
        <span class="confidence-pill ${escapeHtml(confidence.className)}">${escapeHtml(confidence.label)}</span>
      </div>
      <p class="evidence-summary">${escapeHtml(item.summary || "")}</p>
      ${chips}
      <div class="evidence-meta">
        <strong>来源：</strong> ${escapeHtml(item.source_label || "未标注")}
      </div>
      <p class="evidence-snippet">${escapeHtml(item.source_snippet || "")}</p>
      ${
        item.why_it_matters
          ? `<p class="evidence-why"><strong>为何重要：</strong> ${escapeHtml(item.why_it_matters)}</p>`
          : ""
      }
    `;
    target.appendChild(card);
  });
}

function fillList(elementId, items = []) {
  const target = document.getElementById(elementId);
  target.innerHTML = "";
  if (!items.length) {
    const li = document.createElement("li");
    li.textContent = "当前没有可显示内容。";
    target.appendChild(li);
    return;
  }

  items.forEach((item) => {
    const li = document.createElement("li");
    li.textContent = item;
    target.appendChild(li);
  });
}

function fillWarnings(warnings = []) {
  const target = document.getElementById("warnings");
  target.innerHTML = "";
  if (!warnings.length) {
    const li = document.createElement("li");
    li.textContent = "当前没有额外警告。";
    target.appendChild(li);
    return;
  }
  warnings.forEach((warning) => {
    const li = document.createElement("li");
    li.textContent = warning;
    target.appendChild(li);
  });
}

function fillGlossary(items = []) {
  const target = document.getElementById("glossary");
  target.innerHTML = "";
  if (!items.length) {
    target.innerHTML = "<p>当前没有术语卡片。</p>";
    return;
  }

  items.forEach((item) => {
    const card = document.createElement("article");
    card.className = "glossary-card";
    card.innerHTML = `
      <h4>${escapeHtml(item.term)}</h4>
      <p>${escapeHtml(item.plain_explanation)}</p>
    `;
    target.appendChild(card);
  });
}

function renderCharts(chartSpecs = []) {
  state.chartObservers.forEach((observer) => observer.disconnect());
  state.chartObservers = [];
  state.charts.forEach((chart) => chart.dispose());
  state.charts = [];

  const grid = document.getElementById("charts-grid");
  const featuredGrid = document.getElementById("featured-chart-grid");
  grid.innerHTML = "";
  featuredGrid.innerHTML = "";

  if (!chartSpecs.length) {
    grid.innerHTML = `
      <article class="chart-card">
        <div class="chart-card-header">
          <div>
            <h3>当前没有图表</h3>
            <p class="chart-copy">模型没有在本轮上下文里拿到足够明确的结构化数字。</p>
          </div>
        </div>
      </article>
    `;
    return;
  }

  const featuredChart =
    chartSpecs.find((spec) => spec.chart_type === "sankey") || null;
  const regularCharts = featuredChart
    ? chartSpecs.filter((spec) => spec !== featuredChart)
    : chartSpecs;

  if (featuredChart) {
    renderChartCard(featuredGrid, featuredChart, true, chartSpecs);
  }

  regularCharts.forEach((spec, index) => {
    renderChartCard(grid, spec, false, regularCharts, index, regularCharts.length === 1);
  });
}

function renderChartCard(target, spec, forceFeatured = false, chartSpecs = [], index = 0, isSolo = false) {
  const card = document.createElement("article");
  card.className = `chart-card ${forceFeatured ? "featured" : getChartCardClass(spec, index, chartSpecs)}`;
  if (isSolo) {
    card.classList.add("solo");
  }
  if (spec.chart_type === "sankey") {
    card.classList.add("is-sankey");
  }
  const confidence = formatPerformanceBadgeForChart(spec, state.digest?.fact_snapshot || []);
  const canvasId = `chart-${Math.random().toString(36).slice(2)}`;
  card.innerHTML = `
    <div class="chart-card-header">
      <div>
        <h3>${escapeHtml(spec.title)}</h3>
        <p class="chart-copy">${escapeHtml(spec.why_it_matters || "")}</p>
      </div>
      <span class="confidence-pill ${escapeHtml(confidence.className)}">${escapeHtml(confidence.label)}</span>
    </div>
    <div id="${canvasId}" class="chart-canvas"></div>
    <div class="source-footnote">
      <strong>数据线索：</strong> ${escapeHtml(spec.source_snippet || "")}
    </div>
  `;
  target.appendChild(card);

  const chart = echarts.init(document.getElementById(canvasId), null, {
    renderer: "canvas",
  });
  chart.setOption(buildChartOption(spec));
  state.charts.push(chart);

  queueChartResize(chart);
  if ("ResizeObserver" in window) {
    const observer = new ResizeObserver(() => queueChartResize(chart));
    observer.observe(card);
    state.chartObservers.push(observer);
  }
}

function buildChartOption(spec) {
  const categories = spec.categories || [];
  const palette = spec.palette && spec.palette.length
    ? spec.palette
    : ["#1d4ed8", "#0f766e", "#ea580c", "#56703d", "#b45309"];
  const valueFormatter = new Intl.NumberFormat(undefined, {
    maximumFractionDigits: 1,
  });
  const integerFormatter = new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 0,
  });
  const legendType = (spec.series || []).length > 3 ? "scroll" : "plain";

  if (spec.chart_type === "donut") {
    const firstSeries = spec.series?.[0] || { values: [] };
    return {
      color: palette,
      tooltip: { trigger: "item" },
      legend: {
        bottom: 0,
        left: "center",
        type: legendType,
        icon: "circle",
        textStyle: { color: "#536a72" },
      },
      series: [
        {
          name: firstSeries.name || spec.title,
          type: "pie",
          radius: ["44%", "72%"],
          avoidLabelOverlap: true,
          label: { formatter: "{b}\n{d}%" },
          data: categories.map((category, index) => ({
            name: category,
            value: firstSeries.values?.[index] ?? 0,
          })),
        },
      ],
    };
  }

  if (spec.chart_type === "waterfall") {
    const firstSeries = spec.series?.[0] || { values: [] };
    const rawValues = (firstSeries.values || []).map((value) => Number(value || 0));
    const helper = [];
    const positive = [];
    const negative = [];
    let runningTotal = 0;
    const levelAfterBar = [];

    const buildConnectorSeries = (startIndex, endIndex, value) => {
      const lineData = rawValues.map((_, idx) => {
        if (idx === startIndex || idx === endIndex) {
          return value;
        }
        return null;
      });
      return {
        type: "line",
        data: lineData,
        symbol: "none",
        silent: true,
        tooltip: { show: false },
        connectNulls: false,
        lineStyle: {
          color: "rgba(23,49,58,0.24)",
          width: 1.6,
          type: "dashed",
        },
        z: 1,
      };
    };

    rawValues.forEach((value, index) => {
      const isTerminalBar = index === rawValues.length - 1;
      if (index === 0 || isTerminalBar) {
        helper.push({
          value: 0,
          itemStyle: { color: "transparent", borderColor: "transparent" },
        });
        positive.push(Math.max(value, 0));
        negative.push(value < 0 ? Math.abs(value) : null);
        if (index === 0) {
          runningTotal = value;
        }
        levelAfterBar[index] = index === 0 ? value : runningTotal;
        return;
      }

      if (value >= 0) {
        helper.push({
          value: runningTotal,
          itemStyle: { color: "rgba(15,118,110,0.12)", borderColor: "transparent" },
        });
        positive.push(value);
        negative.push(null);
        runningTotal += value;
        levelAfterBar[index] = runningTotal;
      } else {
        helper.push({
          value: runningTotal + value,
          itemStyle: { color: "rgba(239,68,68,0.12)", borderColor: "transparent" },
        });
        positive.push(null);
        negative.push(Math.abs(value));
        runningTotal += value;
        levelAfterBar[index] = runningTotal;
      }
    });

    const connectorLineSeries = [];
    for (let index = 0; index < rawValues.length - 1; index += 1) {
      const level = levelAfterBar[index];
      if (typeof level !== "number" || !Number.isFinite(level)) {
        continue;
      }
      connectorLineSeries.push(buildConnectorSeries(index, index + 1, level));
    }

    return {
      color: palette,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (params) => {
          const idx = params?.[0]?.dataIndex ?? 0;
          const raw = rawValues[idx] ?? 0;
          return `${escapeHtml(categories[idx] || "")}<br/>${valueFormatter.format(raw)}（百万美元）`;
        },
      },
      legend: { show: false },
      grid: { top: 44, left: 22, right: 20, bottom: 28, containLabel: true },
      xAxis: {
        type: "category",
        data: categories,
        axisLabel: { color: "#536a72", interval: 0 },
        axisLine: { lineStyle: { color: "rgba(23,49,58,0.15)" } },
      },
      yAxis: {
        type: "value",
        axisLabel: {
          color: "#536a72",
          formatter: (value) => valueFormatter.format(Number(value || 0)),
        },
        splitLine: { lineStyle: { color: "rgba(23,49,58,0.08)" } },
      },
      series: [
        {
          type: "bar",
          stack: "total",
          itemStyle: { color: "transparent", borderColor: "transparent" },
          emphasis: { disabled: true },
          data: helper,
          silent: true,
        },
        {
          name: "positive",
          type: "bar",
          stack: "total",
          itemStyle: { color: palette[0] || "#0f766e", borderRadius: [10, 10, 0, 0] },
          data: positive,
          label: {
            show: true,
            position: "top",
            color: "#17313a",
            formatter: ({ dataIndex, value }) =>
              value == null ? "" : integerFormatter.format(rawValues[dataIndex] || 0),
          },
        },
        {
          name: "negative",
          type: "bar",
          stack: "total",
          itemStyle: { color: palette[1] || "#ef4444", borderRadius: [10, 10, 0, 0] },
          data: negative,
          label: {
            show: true,
            position: "bottom",
            color: "#17313a",
            formatter: ({ dataIndex, value }) =>
              value == null ? "" : integerFormatter.format(rawValues[dataIndex] || 0),
          },
        },
        ...connectorLineSeries,
      ],
    };
  }

  if (spec.chart_type === "sankey") {
      const wrapSankeyLabel = (value) => {
        const text = String(value || "").trim();
        if (!text || text.length <= 16 || !text.includes(" ")) {
          return text;
        }
        const words = text.split(/\s+/);
        const lines = [];
        let current = "";
        for (const word of words) {
          const next = current ? `${current} ${word}` : word;
          if (next.length > 16 && current) {
            lines.push(current);
            current = word;
          } else {
            current = next;
          }
        }
        if (current) {
          lines.push(current);
        }
        return lines.slice(0, 2).join("\n");
      };
      const formatSankeyValue = (value) => integerFormatter.format(Number(value || 0));
      const nodes = [...(spec.flow_nodes || [])]
        .sort((a, b) => {
          const depthA = Number(a?.depth ?? 99);
          const depthB = Number(b?.depth ?? 99);
          if (depthA !== depthB) {
            return depthA - depthB;
          }
          return Number(a?.layout_order ?? 0) - Number(b?.layout_order ?? 0);
        })
        .map((node, index) => ({
        name: node.name,
        value: node.value ?? undefined,
        depth: node.depth ?? undefined,
        itemStyle: {
          color: node.item_style_color || palette[index % palette.length],
          borderColor: "rgba(23,49,58,0.08)",
          borderWidth: 1,
        },
      }));
      const links = (spec.flow_links || []).map((link) => ({
        source: link.source,
        target: link.target,
        value: link.value,
      }));

      return {
        color: palette,
        tooltip: {
          trigger: "item",
          formatter: (params) => {
            if (params.dataType === "edge") {
              return `${escapeHtml(params.data.source)} → ${escapeHtml(params.data.target)}<br/>${formatSankeyValue(params.data.value)}（百万美元）`;
            }
            return `${escapeHtml(params.name)}<br/>${formatSankeyValue(params.value)}（百万美元）`;
          },
        },
        series: [
          {
            type: "sankey",
            left: 40,
            top: 28,
            right: 220,
            bottom: 20,
            emphasis: { focus: "adjacency" },
            draggable: false,
            nodeAlign: "left",
            layoutIterations: 32,
            nodeWidth: 26,
            nodeGap: 34,
            data: nodes,
            links,
            lineStyle: {
              color: "gradient",
              curveness: 0.46,
              opacity: 0.56,
            },
            label: {
              color: "#17313a",
              fontSize: 13,
              fontWeight: 600,
              position: "right",
              distance: 10,
              width: 194,
              overflow: "break",
              formatter: (params) => `${wrapSankeyLabel(params?.name)}\n${formatSankeyValue(params?.value)}`,
            },
          },
        ],
      };
    }

  const isLineLike = spec.chart_type === "line" || spec.chart_type === "area";
  const isSingleSeries = (spec.series || []).length === 1;
  const useHorizontalBars =
    spec.chart_type === "bar" && isSingleSeries && categories.length >= 4;
  const shouldRotateLabels = !useHorizontalBars && categories.some((category) =>
    String(category).length > 10
  );

  const series = (spec.series || []).map((item, index) => {
    const base = {
      name: item.name,
      type: spec.chart_type === "stacked_bar" ? "bar" : spec.chart_type,
      data: item.values || [],
      smooth: spec.chart_type === "line" || spec.chart_type === "area",
      emphasis: { focus: "series" },
    };

    if (spec.chart_type === "area") {
      base.type = "line";
      base.areaStyle = { opacity: 0.2 };
    }

    if (spec.chart_type === "stacked_bar") {
      base.stack = "total";
      base.borderRadius = [6, 6, 0, 0];
      base.barMaxWidth = 28;
    }

    if (base.type === "bar") {
      base.itemStyle = {
        borderRadius: useHorizontalBars ? [0, 8, 8, 0] : [8, 8, 2, 2],
      };
      base.barMaxWidth = useHorizontalBars ? 22 : 34;
    }

    if (isLineLike) {
      base.symbol = "circle";
      base.symbolSize = categories.length <= 4 ? 10 : 7;
      base.showSymbol = true;
      base.lineStyle = { width: 3 };
    }

    base.color = palette[index % palette.length];
    return base;
  });

  return {
    color: palette,
    animationDuration: 520,
    tooltip: {
      trigger: spec.chart_type === "donut" ? "item" : "axis",
      valueFormatter: (value) => valueFormatter.format(Number(value || 0)),
    },
    legend: {
      top: 0,
      left: 0,
      type: legendType,
      textStyle: { color: "#536a72" },
    },
    grid: useHorizontalBars
      ? { top: 52, left: 18, right: 28, bottom: 12, containLabel: true }
      : {
          top: (spec.series || []).length > 1 ? 62 : 48,
          left: 22,
          right: 20,
          bottom: shouldRotateLabels ? 58 : 32,
          containLabel: true,
        },
    xAxis: useHorizontalBars
      ? {
          type: "value",
          axisLabel: {
            color: "#536a72",
            formatter: (value) => valueFormatter.format(Number(value || 0)),
          },
          splitLine: { lineStyle: { color: "rgba(23,49,58,0.08)" } },
        }
      : {
          type: "category",
          name: spec.x_axis_label || "",
          axisLabel: {
            color: "#536a72",
            interval: 0,
            rotate: shouldRotateLabels ? 18 : 0,
          },
          axisLine: { lineStyle: { color: "rgba(23,49,58,0.15)" } },
          data: categories,
        },
    yAxis: useHorizontalBars
      ? {
          type: "category",
          axisLabel: { color: "#536a72" },
          axisLine: { lineStyle: { color: "rgba(23,49,58,0.15)" } },
          data: categories,
        }
      : {
          type: "value",
          axisLabel: {
            color: "#536a72",
            formatter: (value) => valueFormatter.format(Number(value || 0)),
          },
          splitLine: { lineStyle: { color: "rgba(23,49,58,0.08)" } },
        },
    series,
  };
}

function getChartCardClass(spec, index, chartSpecs) {
  const categories = spec.categories || [];
  const seriesCount = (spec.series || []).length;

  if (chartSpecs.length === 1) {
    return "featured";
  }

  if (spec.chart_type === "line" || spec.chart_type === "area" || spec.chart_type === "sankey") {
    return "featured";
  }

  if (spec.chart_type === "stacked_bar") {
    return "focused";
  }

  if (spec.chart_type === "bar" && categories.length >= 4) {
    return "focused";
  }

  if (seriesCount >= 3 || index === 0) {
    return "focused";
  }

  return "supporting";
}

function queueChartResize(chart) {
  requestAnimationFrame(() => {
    chart.resize();
    requestAnimationFrame(() => chart.resize());
  });
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatImportanceBadge(value) {
  const normalized = String(value || "medium").toLowerCase();
  const labels = {
    high: "高关注",
    medium: "关注",
    low: "补充",
  };
  return {
    label: labels[normalized] || labels.medium,
    className: `is-${["high", "medium", "low"].includes(normalized) ? normalized : "medium"}`,
  };
}

function formatPerformanceBadgeForMetric(item) {
  const metricKey = String(item?.metric_key || "");
  const yoy = extractSignedPercent(item?.yoy_text || "");
  const direction = isInverseMetric(metricKey) ? -1 : 1;
  return formatPerformanceBadgeFromScore((yoy ?? 0) * direction, yoy === null);
}

function formatPerformanceBadgeForChart(spec, factSnapshot = []) {
  const title = String(spec?.title || "");
  const chartType = String(spec?.chart_type || "");
  const metricLookup = new Map(
    (factSnapshot || []).map((item) => [String(item.metric_key || ""), item])
  );

  const pickAverage = (keys) => {
    const scores = keys
      .map((key) => {
        const item = metricLookup.get(key);
        if (!item) {
          return null;
        }
        const yoy = extractSignedPercent(item.yoy_text || "");
        if (yoy === null) {
          return null;
        }
        return yoy * (isInverseMetric(key) ? -1 : 1);
      })
      .filter((value) => typeof value === "number");
    if (!scores.length) {
      return null;
    }
    return scores.reduce((sum, value) => sum + value, 0) / scores.length;
  };

  if (chartType === "waterfall" || title.includes("现金流")) {
    const score = pickAverage(["operating_cash_flow", "free_cash_flow", "capital_expenditures"]);
    return formatPerformanceBadgeFromScore(score ?? 0, score === null);
  }

  if (chartType === "sankey" || title.includes("营收流向") || title.includes("Revenue-to-profit")) {
    const score = pickAverage(["revenue", "operating_income", "net_income"]);
    return formatPerformanceBadgeFromScore(score ?? 0, score === null);
  }

  if (title.includes("流动性") || title.includes("债务") || title.includes("Liquidity")) {
    const score = pickAverage(["cash_and_equivalents", "short_term_investments", "total_debt"]);
    return formatPerformanceBadgeFromScore(score ?? 0, score === null);
  }

  const score = deriveScoreFromSeries(spec);
  return formatPerformanceBadgeFromScore(score ?? 0, score === null);
}

function deriveScoreFromSeries(spec) {
  const seriesList = Array.isArray(spec?.series) ? spec.series : [];
  const scores = [];
  seriesList.forEach((series) => {
    const values = Array.isArray(series?.values) ? series.values.filter((value) => typeof value === "number") : [];
    if (values.length < 2) {
      return;
    }
    const previous = Number(values[values.length - 2] || 0);
    const current = Number(values[values.length - 1] || 0);
    if (!Number.isFinite(previous) || Math.abs(previous) < 1e-6) {
      return;
    }
    scores.push(((current - previous) / Math.abs(previous)) * 100);
  });
  if (!scores.length) {
    return null;
  }
  return scores.reduce((sum, value) => sum + value, 0) / scores.length;
}

function extractSignedPercent(text) {
  const match = String(text || "").match(/同比\s*([+-]?\d+(?:\.\d+)?)%/u);
  return match ? Number(match[1]) : null;
}

function isInverseMetric(metricKey) {
  return ["total_debt", "capital_expenditures"].includes(String(metricKey || ""));
}

function formatPerformanceBadgeFromScore(score, isUnknown = false) {
  if (isUnknown) {
    return { label: "信息有限", className: "is-neutral" };
  }
  if (score >= 20) {
    return { label: "显著改善", className: "is-positive-strong" };
  }
  if (score >= 5) {
    return { label: "表现改善", className: "is-positive" };
  }
  if (score > -5) {
    return { label: "基本持平", className: "is-neutral" };
  }
  if (score > -15) {
    return { label: "小幅承压", className: "is-negative" };
  }
  return { label: "明显承压", className: "is-negative-strong" };
}

function buildCompanyAvatar(name = "") {
  const cleaned = String(name || "")
    .replace(/\b(inc|inc\.|corporation|corp|corp\.|company|co\.|limited|ltd\.|class\s+[abcz])\b/gi, "")
    .replace(/[^A-Za-z0-9\u4e00-\u9fff ]+/g, " ")
    .trim();
  if (!cleaned) {
    return "ID";
  }
  const parts = cleaned.split(/\s+/).filter(Boolean);
  if (parts.length === 1) {
    return parts[0].slice(0, 2).toUpperCase();
  }
  return `${parts[0][0] || ""}${parts[1][0] || ""}`.toUpperCase();
}

detectRuntime();
setProgress(0, "idle");
window.addEventListener("resize", () => {
  state.charts.forEach((chart) => chart.resize());
});
