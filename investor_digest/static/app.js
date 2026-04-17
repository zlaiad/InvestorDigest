const samplePath =
  "sec_filings/sec-edgar-filings/AAPL/10-K/0000320193-23-000106";

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
const fileFormEl = document.getElementById("file-form");
const fileInputEl = document.getElementById("file-input");
const uploadZoneEl = document.getElementById("upload-zone");
const filePickerButtonEl = document.getElementById("file-picker-button");
const fileNameEl = document.getElementById("file-name");
const uploadSubmitEl = document.getElementById("upload-submit");
const reportTitleEl = document.getElementById("report-title");
const reportContentEl = document.getElementById("report-content");
const emptyStateEl = document.getElementById("empty-state");
const downloadJsonEl = document.getElementById("download-json");
const printReportEl = document.getElementById("print-report");

const ANALYSIS_PROGRESS_STEPS = [
  { percent: 8, message: "正在读取文件和基础元数据…" },
  { percent: 24, message: "正在抽取关键章节与财务事实…" },
  { percent: 46, message: "正在整理重点风险与亮点…" },
  { percent: 68, message: "正在生成投资者可读摘要…" },
  { percent: 86, message: "正在整理图表、术语和提示…" },
  { percent: 94, message: "正在完成最终报告排版…" },
];

document.getElementById("sample-button").addEventListener("click", () => {
  pathInputEl.value = samplePath;
});

document.getElementById("path-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const path = pathInputEl.value.trim();
  if (!path) {
    setStatus("请输入财报路径。");
    return;
  }

  await runAnalysis({
    endpoint: "/api/analyze/path",
    options: {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path,
        audience: audienceEl.value.trim() || "普通投资者",
        language: languageEl.value,
      }),
    },
    label: `正在分析路径: ${path}`,
  });
});

fileFormEl.addEventListener("submit", async (event) => {
  event.preventDefault();
  const [file] = fileInputEl.files;
  if (!file) {
    setStatus("请先选择要上传的文件。");
    return;
  }

  const formData = new FormData();
  formData.append("file", file);
  formData.append("audience", audienceEl.value.trim() || "普通投资者");
  formData.append("language", languageEl.value);

  await runAnalysis({
    endpoint: "/api/analyze/file",
    options: {
      method: "POST",
      body: formData,
    },
    label: `正在分析上传文件: ${file.name}`,
  });
});

uploadZoneEl.addEventListener("click", (event) => {
  if (event.target instanceof HTMLElement && event.target.closest("button")) {
    return;
  }
  fileInputEl.click();
});

uploadZoneEl.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") {
    return;
  }
  event.preventDefault();
  fileInputEl.click();
});

filePickerButtonEl.addEventListener("click", () => fileInputEl.click());

fileInputEl.addEventListener("change", () => {
  const [file] = fileInputEl.files;
  if (!file) {
    fileNameEl.textContent = "尚未选择文件";
    uploadSubmitEl.disabled = true;
    setProgress(0, "idle");
    return;
  }

  fileNameEl.textContent = file.name;
  uploadSubmitEl.disabled = false;
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
    runtimeStatusEl.textContent = "本地分析服务已连接，可以直接生成报告。";
  } catch (error) {
    runtimeModelEl.textContent = "离线";
    runtimeStatusEl.textContent = "当前前端已就绪，但本地分析服务还没有响应。";
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
  uploadSubmitEl.disabled = isBusy || !fileInputEl.files?.length;
  filePickerButtonEl.disabled = isBusy;
}

function renderDigest(digest) {
  reportTitleEl.textContent = `${digest.company_name} · ${digest.reporting_period}`;
  document.getElementById("meta-company").textContent = digest.company_name;
  document.getElementById("meta-period").textContent = digest.reporting_period;
  document.getElementById("meta-audience").textContent = digest.audience;
  document.getElementById("takeaway").textContent = digest.one_sentence_takeaway;
  document.getElementById("overview").innerHTML = marked.parse(
    digest.overview_markdown || ""
  );

  fillList("key-points", digest.key_points);
  fillList("positives", digest.positives);
  fillList("risks", digest.risks);
  fillList("watchlist", digest.watchlist);
  fillWarnings(digest.warnings);
  fillGlossary(digest.glossary);

  document.getElementById("disclaimer").textContent = digest.risk_disclaimer || "";
  document.getElementById("raw-json").textContent = JSON.stringify(digest, null, 2);

  emptyStateEl.classList.add("hidden");
  reportContentEl.classList.remove("hidden");
  renderCharts(digest.chart_specs || []);
  downloadJsonEl.disabled = false;
  printReportEl.disabled = false;
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
  grid.innerHTML = "";

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

  chartSpecs.forEach((spec, index) => {
    const card = document.createElement("article");
    card.className = `chart-card ${getChartCardClass(spec, index, chartSpecs)}`;
    const canvasId = `chart-${Math.random().toString(36).slice(2)}`;
    card.innerHTML = `
      <div class="chart-card-header">
        <div>
          <h3>${escapeHtml(spec.title)}</h3>
          <p class="chart-copy">${escapeHtml(spec.why_it_matters || "")}</p>
        </div>
        <span class="confidence-pill">${escapeHtml(spec.confidence || "medium")}</span>
      </div>
      <div id="${canvasId}" class="chart-canvas"></div>
      <div class="source-footnote">
        <strong>数据线索：</strong> ${escapeHtml(spec.source_snippet || "")}
      </div>
    `;
    grid.appendChild(card);

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
  });
}

function buildChartOption(spec) {
  const categories = spec.categories || [];
  const palette = spec.palette && spec.palette.length
    ? spec.palette
    : ["#1d4ed8", "#0f766e", "#ea580c", "#56703d", "#b45309"];
  const valueFormatter = new Intl.NumberFormat(undefined, {
    maximumFractionDigits: 1,
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

  if (spec.chart_type === "sankey") {
    const nodes = (spec.flow_nodes || []).map((node, index) => ({
      name: node.name,
      value: node.value ?? undefined,
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
        valueFormatter: (value) => valueFormatter.format(Number(value || 0)),
      },
      series: [
        {
          type: "sankey",
          left: 10,
          top: 28,
          right: 16,
          bottom: 12,
          emphasis: { focus: "adjacency" },
          draggable: false,
          nodeWidth: 18,
          nodeGap: 18,
          data: nodes,
          links,
          lineStyle: {
            color: "gradient",
            curveness: 0.46,
            opacity: 0.42,
          },
          label: {
            color: "#17313a",
            fontSize: 13,
            fontWeight: 600,
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

detectRuntime();
setProgress(0, "idle");
window.addEventListener("resize", () => {
  state.charts.forEach((chart) => chart.resize());
});
