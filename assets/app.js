(function () {
  "use strict";

  const preferredTheme = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  const state = {
    lang: localStorage.getItem("lang") || "en",
    theme: localStorage.getItem("theme") || preferredTheme,
    query: "",
    groups: [],
    papers: [],
    meta: {},
    crawlStats: { runs: [] },
    categoryMap: new Map()
  };

  const el = {
    langToggle: document.getElementById("lang-toggle"),
    themeToggle: document.getElementById("theme-toggle"),
    statsToggle: document.getElementById("stats-toggle"),
    statsDialog: document.getElementById("stats-dialog"),
    statsClose: document.getElementById("stats-close"),
    statsContent: document.getElementById("stats-content"),
    search: document.getElementById("search"),
    searchClear: document.getElementById("search-clear"),
    navToggle: document.getElementById("nav-toggle"),
    nav: document.getElementById("category-nav"),
    sections: document.getElementById("paper-sections"),
    empty: document.getElementById("empty-state"),
    paperCount: document.getElementById("paper-count"),
    lastUpdated: document.getElementById("last-updated"),
    resultsCount: document.getElementById("results-count"),
    heroPaperCount: document.getElementById("hero-paper-count"),
    heroUpdated: document.getElementById("hero-updated"),
    heroModel: document.getElementById("hero-model"),
    backToTop: document.getElementById("back-to-top"),
    themeMeta: document.querySelector('meta[name="theme-color"]')
  };

  let navObserver;
  let chartResizeObserver;

  async function loadData() {
    try {
      const [catRes, paperRes, statsRes] = await Promise.all([
        fetch("data/categories.json"),
        fetch("data/papers.json"),
        fetch("data/crawl-stats.json").catch(() => null)
      ]);
      if (!catRes.ok || !paperRes.ok) throw new Error("paper data fetch failed");
      const [catJson, paperJson] = await Promise.all([catRes.json(), paperRes.json()]);
      state.groups = catJson.groups || [];
      state.papers = paperJson.papers || [];
      state.meta = paperJson.meta || {};
      state.crawlStats = statsRes && statsRes.ok ? await statsRes.json() : { runs: [] };
      state.categoryMap.clear();
      state.groups.forEach((group) => {
        (group.categories || []).forEach((category) => state.categoryMap.set(category.id, category));
      });
    } catch (error) {
      console.error("Failed to load data:", error);
      el.sections.innerHTML =
        '<p class="empty-state">Failed to load paper data. If viewing locally, serve over HTTP (for example: <code>python3 -m http.server</code>).</p>';
    }
  }

  function t(key) {
    return I18N[state.lang][key];
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function asNumber(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function locale() {
    return state.lang === "zh" ? "zh-CN" : "en-US";
  }

  function formatTokens(value) {
    return new Intl.NumberFormat(locale()).format(asNumber(value));
  }

  function formatUsd(value) {
    if (value == null || !Number.isFinite(Number(value))) return "—";
    const amount = Number(value);
    const digits = amount === 0 ? 4 : amount < 0.01 ? 6 : 4;
    return `$${amount.toFixed(digits)}`;
  }

  function formatRunDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat(locale(), {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit"
    }).format(date);
  }

  function matchesQuery(paper) {
    const query = state.query.trim().toLowerCase();
    if (!query) return true;
    const category = state.categoryMap.get(paper.category);
    const haystack = [
      paper.title,
      paper.summary?.en,
      paper.summary?.zh,
      paper.venue,
      paper.category,
      category?.name?.en,
      category?.name?.zh
    ].filter(Boolean).join(" ").toLowerCase();
    return haystack.includes(query);
  }

  function papersByCategory(categoryId) {
    return state.papers
      .filter((paper) => paper.category === categoryId && matchesQuery(paper))
      .sort((a, b) => (b.date || "").localeCompare(a.date || ""));
  }

  function categoryLabel(categoryId) {
    const category = state.categoryMap.get(categoryId);
    return category?.name?.[state.lang] || category?.name?.en || categoryId;
  }

  function isRecent(dateString) {
    if (!dateString) return false;
    const timestamp = new Date(`${dateString}T00:00:00Z`).getTime();
    return Number.isFinite(timestamp) && Date.now() - timestamp < 30 * 24 * 60 * 60 * 1000;
  }

  function renderNav(categoryCounts, groupCounts) {
    el.nav.innerHTML = "";
    state.groups.forEach((group) => {
      if ((groupCounts[group.id] || 0) === 0 && state.query) return;
      const wrapper = document.createElement("div");
      wrapper.className = "nav-group";

      const header = document.createElement("a");
      header.className = "nav-group-title";
      header.href = `#group-${group.id}`;
      header.innerHTML = `<span>${escapeHtml(group.name[state.lang])}</span><span class="badge">${groupCounts[group.id] || 0}</span>`;
      wrapper.appendChild(header);

      (group.categories || []).forEach((category) => {
        const count = categoryCounts[category.id] || 0;
        if (count === 0 && state.query) return;
        const link = document.createElement("a");
        link.className = "nav-cat";
        link.href = `#cat-${category.id}`;
        link.innerHTML = `<span>${escapeHtml(category.name[state.lang])}</span><span class="badge">${count}</span>`;
        wrapper.appendChild(link);
      });
      el.nav.appendChild(wrapper);
    });
  }

  function renderSections() {
    const root = document.createDocumentFragment();
    const categoryCounts = {};
    const groupCounts = {};
    let totalVisible = 0;

    state.groups.forEach((group) => {
      let groupTotal = 0;
      const categoryFragments = [];
      (group.categories || []).forEach((category) => {
        const papers = papersByCategory(category.id);
        categoryCounts[category.id] = papers.length;
        if (!papers.length) return;
        groupTotal += papers.length;

        const section = document.createElement("section");
        section.className = "category-section";
        section.id = `cat-${category.id}`;

        const heading = document.createElement("div");
        heading.className = "category-heading";
        heading.innerHTML = `<div><h3>${escapeHtml(category.name[state.lang])}<span class="count">${papers.length}</span></h3><p>${escapeHtml(category.desc[state.lang])}</p></div>`;
        section.appendChild(heading);

        const cards = document.createElement("div");
        cards.className = "paper-list";
        papers.forEach((paper) => cards.appendChild(renderCard(paper)));
        section.appendChild(cards);
        categoryFragments.push(section);
      });

      groupCounts[group.id] = groupTotal;
      if (!groupTotal) return;
      totalVisible += groupTotal;

      const groupSection = document.createElement("section");
      groupSection.className = "group-section";
      groupSection.id = `group-${group.id}`;
      const groupHeading = document.createElement("div");
      groupHeading.className = "group-heading";
      groupHeading.innerHTML = `<span class="group-index">${String(root.childElementCount + 1).padStart(2, "0")}</span><div><h2>${escapeHtml(group.name[state.lang])}</h2><p>${escapeHtml(group.desc[state.lang])}</p></div>`;
      groupSection.appendChild(groupHeading);
      categoryFragments.forEach((fragment) => groupSection.appendChild(fragment));
      root.appendChild(groupSection);
    });

    el.sections.replaceChildren(root);
    renderNav(categoryCounts, groupCounts);
    el.empty.hidden = totalVisible !== 0;
    el.paperCount.textContent = t("papersCount")(state.papers.length);
    el.resultsCount.textContent = t("resultsCount")(totalVisible, state.papers.length);
    el.searchClear.hidden = !state.query;
    if (state.meta.lastUpdated) el.lastUpdated.textContent = t("updated")(state.meta.lastUpdated);
    renderOverview();
    window.requestAnimationFrame(setupNavObserver);
  }

  function renderCard(paper) {
    const card = document.createElement("article");
    card.className = "paper-card";

    const title = document.createElement("h4");
    title.className = "paper-title";
    if (paper.url) {
      const link = document.createElement("a");
      link.href = paper.url;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = paper.title;
      title.appendChild(link);
    } else {
      title.textContent = paper.title;
    }
    card.appendChild(title);

    const summary = document.createElement("p");
    summary.className = "paper-summary";
    summary.textContent = paper.summary?.[state.lang] || paper.summary?.en || "";
    card.appendChild(summary);

    const meta = document.createElement("div");
    meta.className = "paper-meta";
    const category = document.createElement("span");
    category.className = "tag tag-category";
    category.textContent = categoryLabel(paper.category);
    meta.appendChild(category);
    if (paper.venue) {
      const venue = document.createElement("span");
      venue.className = "venue";
      venue.textContent = paper.venue;
      meta.appendChild(venue);
    }
    if (paper.date) {
      const date = document.createElement("span");
      date.className = "paper-date";
      date.textContent = paper.date;
      meta.appendChild(date);
      if (isRecent(paper.date)) {
        const recent = document.createElement("span");
        recent.className = "tag tag-new";
        recent.textContent = t("newBadge");
        meta.appendChild(recent);
      }
    }
    if (paper.url) {
      const read = document.createElement("a");
      read.className = "paper-link";
      read.href = paper.url;
      read.target = "_blank";
      read.rel = "noopener";
      read.textContent = t("paperLink");
      meta.appendChild(read);
    }
    card.appendChild(meta);
    return card;
  }

  function latestRun() {
    return [...(state.crawlStats.runs || [])].sort((a, b) =>
      String(b.finishedAt || b.date || "").localeCompare(String(a.finishedAt || a.date || ""))
    )[0];
  }

  function renderOverview() {
    el.heroPaperCount.textContent = formatTokens(state.papers.length);
    el.heroUpdated.textContent = state.meta.lastUpdated || "—";
    el.heroModel.textContent = latestRun()?.model || "gpt-5.6-terra";
  }

  function statusInfo(status) {
    const statuses = {
      added: ["statusAdded", "success"],
      no_new: ["statusNoNew", "neutral"],
      dry_run: ["statusDryRun", "warning"],
      llm_failure: ["statusFailure", "error"]
    };
    const [key, tone] = statuses[status] || ["statusUnknown", "neutral"];
    return { label: t(key), tone };
  }

  function aggregateDailyRuns(runs) {
    const days = new Map();
    runs.forEach((run) => {
      const date = run.date || String(run.finishedAt || "").slice(0, 10);
      if (!date) return;
      if (!days.has(date)) {
        days.set(date, {
          date,
          timestamp: new Date(`${date}T00:00:00Z`).getTime(),
          tokens: 0,
          cost: 0,
          hasCost: false,
          added: 0,
          categories: {}
        });
      }
      const day = days.get(date);
      day.tokens += asNumber(run.usage?.totalTokens);
      day.added += asNumber(run.papers?.added);
      if (run.usage?.estimatedCostUsd != null) {
        day.cost += asNumber(run.usage.estimatedCostUsd);
        day.hasCost = true;
      }
      Object.entries(run.papers?.byCategory || {}).forEach(([category, count]) => {
        day.categories[category] = (day.categories[category] || 0) + asNumber(count);
      });
    });
    return [...days.values()].sort((a, b) => a.timestamp - b.timestamp);
  }

  function compactNumber(value) {
    return new Intl.NumberFormat(locale(), { notation: "compact", maximumFractionDigits: 1 }).format(value);
  }

  function shortDate(date) {
    const value = new Date(`${date}T00:00:00Z`);
    return new Intl.DateTimeFormat(locale(), { month: "short", day: "numeric", timeZone: "UTC" }).format(value);
  }

  function svgNode(tag, attributes = {}) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, value));
    return node;
  }

  function drawLineChart(container, data, config) {
    container.replaceChildren();
    if (!data.length || !config.series.length) {
      const empty = document.createElement("p");
      empty.className = "chart-empty muted";
      empty.textContent = t("noTrendData");
      container.appendChild(empty);
      return;
    }

    const width = Math.max(280, Math.floor(container.clientWidth || 520));
    const height = width < 400 ? 218 : 232;
    const margin = { top: 16, right: 18, bottom: 45, left: width < 400 ? 68 : 62 };
    const plotWidth = width - margin.left - margin.right;
    const plotHeight = height - margin.top - margin.bottom;
    const allValues = config.series.flatMap((series) => data.map((point) => series.value(point)));
    const observedMax = Math.max(0, ...allValues);
    const yMax = observedMax > 0 ? observedMax : 1;
    const firstTime = data[0].timestamp;
    const lastTime = data[data.length - 1].timestamp;
    const x = (point) => data.length === 1
      ? margin.left + plotWidth / 2
      : margin.left + (point.timestamp - firstTime) / (lastTime - firstTime) * plotWidth;
    const y = (value) => margin.top + plotHeight - value / yMax * plotHeight;

    const svg = svgNode("svg", {
      viewBox: `0 0 ${width} ${height}`,
      role: "img",
      "aria-label": `${config.title}. ${data.length} daily observations.`,
      preserveAspectRatio: "xMidYMid meet"
    });
    const title = svgNode("title");
    title.textContent = config.title;
    svg.appendChild(title);

    for (let step = 0; step <= 4; step += 1) {
      const value = yMax * step / 4;
      const lineY = y(value);
      svg.appendChild(svgNode("line", {
        x1: margin.left,
        x2: width - margin.right,
        y1: lineY,
        y2: lineY,
        class: "chart-grid-line"
      }));
      const label = svgNode("text", {
        x: margin.left - 9,
        y: lineY + 4,
        "text-anchor": "end",
        class: "chart-axis-text"
      });
      label.textContent = config.axisFormatter(value);
      svg.appendChild(label);
    }

    const tickTarget = width < 400 ? 3 : 5;
    const tickCount = Math.min(tickTarget, data.length);
    const tickIndexes = new Set();
    for (let index = 0; index < tickCount; index += 1) {
      tickIndexes.add(tickCount === 1 ? 0 : Math.round(index * (data.length - 1) / (tickCount - 1)));
    }
    [...tickIndexes].forEach((index) => {
      const point = data[index];
      const label = svgNode("text", {
        x: x(point),
        y: height - 22,
        "text-anchor": data.length === 1 ? "middle" : index === 0 ? "start" : index === data.length - 1 ? "end" : "middle",
        class: "chart-axis-text"
      });
      label.textContent = shortDate(point.date);
      svg.appendChild(label);
    });

    config.series.forEach((series) => {
      const values = data.map((point) => ({ point, value: series.value(point) }));
      const pathData = values.map((item, index) => `${index ? "L" : "M"}${x(item.point).toFixed(2)},${y(item.value).toFixed(2)}`).join(" ");
      svg.appendChild(svgNode("path", {
        d: pathData,
        fill: "none",
        stroke: `var(--chart-${series.color})`,
        "stroke-width": "2.25",
        "stroke-linecap": "round",
        "stroke-linejoin": "round",
        class: "chart-series-line"
      }));
      values.forEach((item) => {
        const point = svgNode("circle", {
          cx: x(item.point),
          cy: y(item.value),
          r: "3.5",
          fill: `var(--chart-${series.color})`,
          stroke: "var(--surface)",
          "stroke-width": "1.5",
          class: "chart-point"
        });
        const pointTitle = svgNode("title");
        pointTitle.textContent = `${item.point.date} · ${series.label}: ${config.valueFormatter(item.value)}`;
        point.appendChild(pointTitle);
        svg.appendChild(point);
      });
    });

    const yTitle = svgNode("text", {
      x: 13,
      y: margin.top + plotHeight / 2,
      transform: `rotate(-90 13 ${margin.top + plotHeight / 2})`,
      "text-anchor": "middle",
      class: "chart-axis-title"
    });
    yTitle.textContent = config.yLabel;
    svg.appendChild(yTitle);
    const xTitle = svgNode("text", {
      x: margin.left + plotWidth / 2,
      y: height - 3,
      "text-anchor": "middle",
      class: "chart-axis-title"
    });
    xTitle.textContent = t("axisDate");
    svg.appendChild(xTitle);

    const guide = svgNode("line", {
      y1: margin.top,
      y2: margin.top + plotHeight,
      class: "chart-hover-guide",
      hidden: ""
    });
    svg.appendChild(guide);
    const overlay = svgNode("rect", {
      x: margin.left,
      y: margin.top,
      width: plotWidth,
      height: plotHeight,
      fill: "transparent",
      class: "chart-hover-overlay"
    });
    svg.appendChild(overlay);
    container.appendChild(svg);

    const tooltip = document.createElement("div");
    tooltip.className = "chart-tooltip";
    tooltip.setAttribute("role", "tooltip");
    tooltip.hidden = true;
    container.appendChild(tooltip);

    overlay.addEventListener("pointermove", (event) => {
      const bounds = svg.getBoundingClientRect();
      const pointerX = (event.clientX - bounds.left) * width / bounds.width;
      const nearest = data.reduce((best, point) => Math.abs(x(point) - pointerX) < Math.abs(x(best) - pointerX) ? point : best, data[0]);
      const guideX = x(nearest);
      guide.setAttribute("x1", guideX);
      guide.setAttribute("x2", guideX);
      guide.removeAttribute("hidden");
      tooltip.innerHTML = `<strong>${escapeHtml(nearest.date)}</strong>${config.series.map((series) => `<span><i style="background:var(--chart-${series.color})"></i>${escapeHtml(series.label)}<b>${escapeHtml(config.valueFormatter(series.value(nearest)))}</b></span>`).join("")}`;
      tooltip.hidden = false;
      const cssX = guideX / width * bounds.width;
      const tooltipLeft = Math.max(7, Math.min(bounds.width - tooltip.offsetWidth - 7, cssX + 10));
      tooltip.style.left = `${tooltipLeft}px`;
      tooltip.style.top = `${Math.max(5, margin.top - 2)}px`;
    });
    overlay.addEventListener("pointerleave", () => {
      guide.setAttribute("hidden", "");
      tooltip.hidden = true;
    });
  }

  function renderTrendCharts(runs) {
    if (chartResizeObserver) chartResizeObserver.disconnect();
    const daily = aggregateDailyRuns(runs);
    const categoryTotals = {};
    daily.forEach((day) => Object.entries(day.categories).forEach(([category, count]) => {
      categoryTotals[category] = (categoryTotals[category] || 0) + count;
    }));
    const categoryIds = Object.keys(categoryTotals).sort((a, b) => categoryTotals[b] - categoryTotals[a]);
    const topCategories = categoryIds.slice(0, 5);
    const remainingCategories = categoryIds.slice(5);
    const categorySeries = topCategories.map((category, index) => ({
      label: categoryLabel(category),
      color: index + 1,
      value: (point) => asNumber(point.categories[category])
    }));
    if (remainingCategories.length) {
      categorySeries.push({
        label: t("otherCategories"),
        color: 6,
        value: (point) => remainingCategories.reduce((total, category) => total + asNumber(point.categories[category]), 0)
      });
    }

    const specs = [
      {
        id: "chart-token-trend",
        title: t("trendTokens"),
        yLabel: t("axisTokens"),
        axisFormatter: compactNumber,
        valueFormatter: formatTokens,
        series: [{ label: t("trendTokens"), color: 1, value: (point) => point.tokens }]
      },
      {
        id: "chart-cost-trend",
        title: t("trendCost"),
        yLabel: t("axisUsd"),
        axisFormatter: (value) => `$${value < 0.1 ? value.toFixed(3) : value.toFixed(2)}`,
        valueFormatter: formatUsd,
        series: [{ label: t("trendCost"), color: 2, value: (point) => point.hasCost ? point.cost : 0 }]
      },
      {
        id: "chart-paper-trend",
        title: t("trendPapers"),
        yLabel: t("axisPapers"),
        axisFormatter: (value) => Number.isInteger(value) ? String(value) : value.toFixed(1),
        valueFormatter: (value) => formatTokens(value),
        series: [{ label: t("trendPapers"), color: 3, value: (point) => point.added }]
      },
      {
        id: "chart-category-trend",
        title: t("trendTypes"),
        yLabel: t("axisPapers"),
        axisFormatter: (value) => Number.isInteger(value) ? String(value) : value.toFixed(1),
        valueFormatter: (value) => formatTokens(value),
        series: categorySeries
      }
    ];

    const legend = document.getElementById("chart-category-legend");
    if (legend) {
      legend.innerHTML = categorySeries.map((series) => `<span><i style="background:var(--chart-${series.color})"></i>${escapeHtml(series.label)}</span>`).join("");
    }

    const drawers = [];
    specs.forEach((spec) => {
      const container = document.getElementById(spec.id);
      if (!container) return;
      const draw = () => drawLineChart(container, daily, spec);
      draw();
      drawers.push({ container, draw });
    });
    chartResizeObserver = new ResizeObserver((entries) => {
      entries.forEach((entry) => drawers.find((item) => item.container === entry.target)?.draw());
    });
    drawers.forEach((item) => chartResizeObserver.observe(item.container));
  }

  function renderStats() {
    const runs = [...(state.crawlStats.runs || [])].sort((a, b) =>
      String(b.finishedAt || b.date || "").localeCompare(String(a.finishedAt || a.date || ""))
    );
    const totals = runs.reduce((acc, run) => {
      acc.evaluated += asNumber(run.papers?.evaluated);
      acc.added += asNumber(run.papers?.added);
      acc.tokens += asNumber(run.usage?.totalTokens);
      if (run.usage?.estimatedCostUsd != null) {
        acc.cost += asNumber(run.usage.estimatedCostUsd);
        acc.hasCost = true;
      }
      Object.entries(run.papers?.byCategory || {}).forEach(([category, count]) => {
        acc.categories[category] = (acc.categories[category] || 0) + asNumber(count);
      });
      return acc;
    }, { evaluated: 0, added: 0, tokens: 0, cost: 0, hasCost: false, categories: {} });

    let html = `<div class="stats-summary-grid">
      ${statCard(t("statsRuns"), formatTokens(runs.length))}
      ${statCard(t("statsEvaluated"), formatTokens(totals.evaluated))}
      ${statCard(t("statsAdded"), formatTokens(totals.added))}
      ${statCard(t("statsSpend"), totals.hasCost ? formatUsd(totals.cost) : "—", "accent")}
    </div>`;

    if (!runs.length) {
      html += `<div class="stats-empty"><span aria-hidden="true">◌</span><h3>${escapeHtml(t("statsEmptyTitle"))}</h3><p>${escapeHtml(t("statsEmptyBody"))}</p></div>`;
      el.statsContent.innerHTML = html;
      return;
    }

    const latest = runs[0];
    const status = statusInfo(latest.status);
    const usage = latest.usage || {};
    const sources = latest.sources || {};
    html += `<section class="stats-section latest-run">
      <div class="stats-section-heading"><h3>${escapeHtml(t("latestRun"))}</h3><span class="status-pill status-${status.tone}">${escapeHtml(status.label)}</span></div>
      <div class="latest-run-grid">
        <div><span class="detail-label">${escapeHtml(formatRunDate(latest.finishedAt || latest.date))}</span><strong>${escapeHtml(latest.model || "—")}</strong><small>${escapeHtml(t("latestRunSummary")(asNumber(latest.papers?.evaluated), asNumber(latest.papers?.added)))}</small></div>
        <div><span class="detail-label">Sources</span><strong>${escapeHtml(t("sourceSummary")(asNumber(sources.arxiv), asNumber(sources.huggingFace), asNumber(sources.merged)))}</strong><small>${escapeHtml(t("errors")(asNumber(latest.papers?.errors)))}</small></div>
        <div><span class="detail-label">${escapeHtml(t("statsSpend"))}</span><strong>${escapeHtml(formatUsd(usage.estimatedCostUsd))}</strong><small>${asNumber(latest.durationSeconds).toFixed(1)}s · ${asNumber(usage.apiCalls)} API calls</small></div>
      </div>
    </section>`;

    html += `<section class="stats-section trends-section">
      <div class="stats-section-heading chart-section-heading"><div><h3>${escapeHtml(t("trendsTitle"))}</h3><p>${escapeHtml(t("trendsDescription"))}</p></div></div>
      <div class="trend-grid">
        <article class="trend-panel"><h4>${escapeHtml(t("trendTokens"))}</h4><div id="chart-token-trend" class="trend-chart"></div></article>
        <article class="trend-panel"><h4>${escapeHtml(t("trendCost"))}</h4><div id="chart-cost-trend" class="trend-chart"></div></article>
        <article class="trend-panel"><h4>${escapeHtml(t("trendPapers"))}</h4><div id="chart-paper-trend" class="trend-chart"></div></article>
      </div>
      <article class="trend-panel category-trend-panel"><div class="category-trend-heading"><h4>${escapeHtml(t("trendTypes"))}</h4><div id="chart-category-legend" class="chart-legend"></div></div><div id="chart-category-trend" class="trend-chart trend-chart-wide"></div></article>
    </section>`;

    const tokenItems = [
      ["inputTokens", usage.inputTokens],
      ["cachedTokens", usage.cachedInputTokens],
      ["cacheWriteTokens", usage.cacheWriteTokens],
      ["outputTokens", usage.outputTokens],
      ["reasoningTokens", usage.reasoningTokens],
      ["totalTokens", usage.totalTokens]
    ];
    html += `<section class="stats-section"><div class="stats-section-heading"><h3>${escapeHtml(t("tokenUsage"))}</h3></div><div class="token-grid">${tokenItems.map(([key, value]) => `<div><span>${escapeHtml(t(key))}</span><strong>${formatTokens(value)}</strong></div>`).join("")}</div></section>`;

    const categoryEntries = Object.entries(totals.categories).sort((a, b) => b[1] - a[1]);
    const maxCategory = categoryEntries.length ? categoryEntries[0][1] : 1;
    html += `<section class="stats-section"><div class="stats-section-heading"><h3>${escapeHtml(t("papersByType"))}</h3></div>`;
    if (!categoryEntries.length) {
      html += `<p class="muted">${escapeHtml(t("noCategoryData"))}</p>`;
    } else {
      html += `<div class="category-bars">${categoryEntries.map(([category, count]) => `<div class="category-bar-row"><div><span>${escapeHtml(categoryLabel(category))}</span><strong>${formatTokens(count)}</strong></div><span class="bar-track"><span style="width:${Math.max(4, count / maxCategory * 100)}%"></span></span></div>`).join("")}</div>`;
    }
    html += `</section>`;

    html += `<section class="stats-section"><div class="stats-section-heading"><h3>${escapeHtml(t("runHistory"))}</h3></div><div class="table-wrap"><table><thead><tr><th>${escapeHtml(t("historyDate"))}</th><th>${escapeHtml(t("historyStatus"))}</th><th>${escapeHtml(t("historyModel"))}</th><th>${escapeHtml(t("historyPapers"))}</th><th>${escapeHtml(t("historyTokens"))}</th><th>${escapeHtml(t("historyCost"))}</th></tr></thead><tbody>${runs.slice(0, 90).map(renderHistoryRow).join("")}</tbody></table></div><p class="cost-note">${escapeHtml(t("costNote"))}</p></section>`;
    el.statsContent.innerHTML = html;
    window.requestAnimationFrame(() => renderTrendCharts(runs));
  }

  function statCard(label, value, tone) {
    return `<article class="stats-summary-card${tone ? ` stats-${tone}` : ""}"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></article>`;
  }

  function renderHistoryRow(run) {
    const status = statusInfo(run.status);
    const papers = run.papers || {};
    return `<tr>
      <td>${escapeHtml(formatRunDate(run.finishedAt || run.date))}</td>
      <td><span class="status-pill status-${status.tone}">${escapeHtml(status.label)}</span></td>
      <td><code>${escapeHtml(run.model || "—")}</code></td>
      <td>${formatTokens(papers.evaluated)} / <strong>+${formatTokens(papers.added)}</strong></td>
      <td>${formatTokens(run.usage?.totalTokens)}</td>
      <td>${escapeHtml(formatUsd(run.usage?.estimatedCostUsd))}</td>
    </tr>`;
  }

  function applyStaticI18n() {
    document.documentElement.lang = state.lang === "zh" ? "zh-CN" : "en";
    document.title = state.lang === "zh" ? "精选 LLM 后训练论文 — 持续更新的研究索引" : "Awesome LLM Post-Training — Living Paper Index";
    document.querySelectorAll("[data-i18n]").forEach((node) => {
      const value = I18N[state.lang][node.getAttribute("data-i18n")];
      if (typeof value === "string") node.textContent = value;
    });
    document.querySelectorAll("[data-i18n-ph]").forEach((node) => {
      const value = I18N[state.lang][node.getAttribute("data-i18n-ph")];
      if (typeof value === "string") node.placeholder = value;
    });
    el.langToggle.textContent = t("langButton");
    el.statsToggle.setAttribute("aria-label", t("statsButton"));
    el.statsClose.setAttribute("aria-label", t("close"));
    el.themeToggle.setAttribute("aria-label", t("toggleTheme"));
    el.searchClear.setAttribute("aria-label", t("clearSearch"));
  }

  function applyTheme() {
    document.documentElement.setAttribute("data-theme", state.theme);
    el.themeToggle.textContent = state.theme === "dark" ? "☀️" : "🌙";
    if (el.themeMeta) el.themeMeta.content = state.theme === "dark" ? "#0d1020" : "#f7f8fc";
  }

  function setupNavObserver() {
    if (navObserver) navObserver.disconnect();
    const sections = document.querySelectorAll(".group-section[id], .category-section[id]");
    navObserver = new IntersectionObserver((entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (!visible) return;
      el.nav.querySelectorAll("a.active").forEach((link) => link.classList.remove("active"));
      const active = el.nav.querySelector(`a[href="#${visible.target.id}"]`);
      if (active) active.classList.add("active");
    }, { rootMargin: "-18% 0px -70% 0px", threshold: [0, 0.2, 0.6] });
    sections.forEach((section) => navObserver.observe(section));
  }

  function setQuery(value) {
    state.query = value;
    el.search.value = value;
    renderSections();
  }

  function openStats() {
    renderStats();
    if (typeof el.statsDialog.showModal === "function") el.statsDialog.showModal();
    else el.statsDialog.setAttribute("open", "");
    document.body.classList.add("modal-open");
  }

  function closeStats() {
    if (typeof el.statsDialog.close === "function") el.statsDialog.close();
    else el.statsDialog.removeAttribute("open");
    document.body.classList.remove("modal-open");
  }

  function bindEvents() {
    el.langToggle.addEventListener("click", () => {
      state.lang = state.lang === "en" ? "zh" : "en";
      localStorage.setItem("lang", state.lang);
      applyStaticI18n();
      renderSections();
      if (el.statsDialog.open) renderStats();
    });

    el.themeToggle.addEventListener("click", () => {
      state.theme = state.theme === "dark" ? "light" : "dark";
      localStorage.setItem("theme", state.theme);
      applyTheme();
    });

    let debounce;
    el.search.addEventListener("input", (event) => {
      window.clearTimeout(debounce);
      debounce = window.setTimeout(() => setQuery(event.target.value), 100);
    });
    el.searchClear.addEventListener("click", () => {
      setQuery("");
      el.search.focus();
    });

    el.navToggle.addEventListener("click", () => {
      const expanded = el.navToggle.getAttribute("aria-expanded") === "true";
      el.navToggle.setAttribute("aria-expanded", String(!expanded));
      el.nav.classList.toggle("is-open", !expanded);
    });
    el.nav.addEventListener("click", () => {
      if (window.innerWidth <= 800) {
        el.nav.classList.remove("is-open");
        el.navToggle.setAttribute("aria-expanded", "false");
      }
    });

    el.statsToggle.addEventListener("click", openStats);
    el.statsClose.addEventListener("click", closeStats);
    el.statsDialog.addEventListener("close", () => document.body.classList.remove("modal-open"));
    el.statsDialog.addEventListener("click", (event) => {
      if (event.target === el.statsDialog) closeStats();
    });

    document.addEventListener("keydown", (event) => {
      const typing = /input|textarea|select/i.test(document.activeElement?.tagName || "");
      if (event.key === "/" && !typing && !el.statsDialog.open) {
        event.preventDefault();
        el.search.focus();
      }
      if (event.key === "Escape" && document.activeElement === el.search && state.query) setQuery("");
    });

    window.addEventListener("scroll", () => {
      el.backToTop.hidden = window.scrollY < 700;
    }, { passive: true });
    el.backToTop.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
  }

  async function init() {
    applyTheme();
    bindEvents();
    await loadData();
    applyStaticI18n();
    renderSections();
  }

  init();
})();
