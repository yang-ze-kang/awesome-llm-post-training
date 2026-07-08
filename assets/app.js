(function () {
  "use strict";

  const state = {
    lang: localStorage.getItem("lang") || "en",
    theme: localStorage.getItem("theme") || "light",
    query: "",
    categories: [],
    papers: [],
    activeCategory: null
  };

  const el = {
    langToggle: document.getElementById("lang-toggle"),
    themeToggle: document.getElementById("theme-toggle"),
    search: document.getElementById("search"),
    nav: document.getElementById("category-nav"),
    sections: document.getElementById("paper-sections"),
    empty: document.getElementById("empty-state"),
    paperCount: document.getElementById("paper-count"),
    lastUpdated: document.getElementById("last-updated")
  };

  // ---- data loading ----
  async function loadData() {
    try {
      const [catRes, paperRes] = await Promise.all([
        fetch("data/categories.json"),
        fetch("data/papers.json")
      ]);
      if (!catRes.ok || !paperRes.ok) throw new Error("fetch failed");
      const catJson = await catRes.json();
      const paperJson = await paperRes.json();
      state.categories = catJson.categories || [];
      state.papers = paperJson.papers || [];
      state.meta = paperJson.meta || {};
    } catch (e) {
      console.error("Failed to load data:", e);
      el.sections.innerHTML =
        '<p class="empty-state">Failed to load paper data. If viewing locally, serve over HTTP (e.g. <code>python3 -m http.server</code>).</p>';
    }
  }

  // ---- helpers ----
  function t(key) {
    return I18N[state.lang][key];
  }

  function papersByCategory(catId) {
    const q = state.query.trim().toLowerCase();
    return state.papers
      .filter((p) => p.category === catId)
      .filter((p) => {
        if (!q) return true;
        const hay = [
          p.title,
          p.summary?.en,
          p.summary?.zh,
          p.venue,
          p.category
        ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        return hay.includes(q);
      })
      .sort((a, b) => (b.date || "").localeCompare(a.date || ""));
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // ---- rendering ----
  function renderNav(counts) {
    el.nav.innerHTML = "";
    state.categories.forEach((cat) => {
      const count = counts[cat.id] || 0;
      if (count === 0 && state.query) return; // hide empty during search
      const a = document.createElement("a");
      a.href = "#cat-" + cat.id;
      a.innerHTML =
        '<span>' + escapeHtml(cat.name[state.lang]) + "</span>" +
        '<span class="badge">' + count + "</span>";
      a.addEventListener("click", () => {
        state.activeCategory = cat.id;
      });
      el.nav.appendChild(a);
    });
  }

  function renderSections() {
    el.sections.innerHTML = "";
    const counts = {};
    let totalVisible = 0;

    state.categories.forEach((cat) => {
      const papers = papersByCategory(cat.id);
      counts[cat.id] = papers.length;
      if (papers.length === 0) return;
      totalVisible += papers.length;

      const section = document.createElement("section");
      section.className = "category-section";
      section.id = "cat-" + cat.id;

      const h2 = document.createElement("h2");
      h2.textContent = cat.name[state.lang];
      section.appendChild(h2);

      const desc = document.createElement("p");
      desc.className = "category-desc";
      desc.textContent = cat.desc[state.lang];
      section.appendChild(desc);

      papers.forEach((p) => section.appendChild(renderCard(p)));
      el.sections.appendChild(section);
    });

    renderNav(counts);
    el.empty.hidden = totalVisible !== 0;
    el.paperCount.textContent = t("papersCount")(state.papers.length);
    if (state.meta && state.meta.lastUpdated) {
      el.lastUpdated.textContent = t("updated")(state.meta.lastUpdated);
    }
  }

  function renderCard(p) {
    const card = document.createElement("article");
    card.className = "paper-card";

    const title = document.createElement("h3");
    title.className = "paper-title";
    if (p.url) {
      const link = document.createElement("a");
      link.href = p.url;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = p.title;
      title.appendChild(link);
    } else {
      title.textContent = p.title;
    }
    card.appendChild(title);

    const summary = document.createElement("p");
    summary.className = "paper-summary";
    summary.textContent =
      (p.summary && p.summary[state.lang]) || p.summary?.en || "";
    card.appendChild(summary);

    const meta = document.createElement("div");
    meta.className = "paper-meta";
    let metaHtml = "";
    if (p.venue) metaHtml += '<span class="venue">' + escapeHtml(p.venue) + "</span>";
    if (p.date) metaHtml += '<span class="tag">' + escapeHtml(p.date) + "</span>";
    if (p.url) {
      metaHtml +=
        '<a href="' + escapeHtml(p.url) + '" target="_blank" rel="noopener">Paper →</a>';
    }
    meta.innerHTML = metaHtml;
    card.appendChild(meta);

    return card;
  }

  // ---- static UI strings ----
  function applyStaticI18n() {
    document.documentElement.lang = state.lang === "zh" ? "zh-CN" : "en";
    document.querySelectorAll("[data-i18n]").forEach((node) => {
      const key = node.getAttribute("data-i18n");
      if (I18N[state.lang][key]) node.textContent = I18N[state.lang][key];
    });
    document.querySelectorAll("[data-i18n-ph]").forEach((node) => {
      const key = node.getAttribute("data-i18n-ph");
      if (I18N[state.lang][key]) node.placeholder = I18N[state.lang][key];
    });
    el.langToggle.textContent = t("langButton");
  }

  // ---- theme ----
  function applyTheme() {
    document.documentElement.setAttribute("data-theme", state.theme);
    el.themeToggle.textContent = state.theme === "dark" ? "☀️" : "🌙";
  }

  // ---- events ----
  function bindEvents() {
    el.langToggle.addEventListener("click", () => {
      state.lang = state.lang === "en" ? "zh" : "en";
      localStorage.setItem("lang", state.lang);
      applyStaticI18n();
      renderSections();
    });

    el.themeToggle.addEventListener("click", () => {
      state.theme = state.theme === "dark" ? "light" : "dark";
      localStorage.setItem("theme", state.theme);
      applyTheme();
    });

    let debounce;
    el.search.addEventListener("input", (e) => {
      clearTimeout(debounce);
      debounce = setTimeout(() => {
        state.query = e.target.value;
        renderSections();
      }, 150);
    });
  }

  // ---- init ----
  async function init() {
    applyTheme();
    bindEvents();
    await loadData();
    applyStaticI18n();
    renderSections();
  }

  init();
})();
