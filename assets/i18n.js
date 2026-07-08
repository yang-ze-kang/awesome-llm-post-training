// UI string translations. Paper/category content is bilingual in the JSON data.
const I18N = {
  en: {
    title: "Awesome LLM Post-Training",
    tagline: "A curated, auto-updating collection of LLM post-training papers.",
    search: "Search papers...",
    source: "Source",
    empty: "No papers match your search.",
    footer: "Built with a daily arXiv + Claude crawler. Contributions welcome.",
    langButton: "中文",
    paperLink: "Paper →",
    papersCount: (n) => `${n} papers`,
    updated: (d) => `Updated ${d}`
  },
  zh: {
    title: "精选 LLM 后训练论文",
    tagline: "一个精心整理、自动更新的 LLM 后训练论文集。",
    search: "搜索论文...",
    source: "源仓库",
    empty: "没有匹配的论文。",
    footer: "由每日 arXiv + Claude 爬虫自动构建,欢迎贡献。",
    langButton: "EN",
    paperLink: "原文 →",
    papersCount: (n) => `${n} 篇论文`,
    updated: (d) => `更新于 ${d}`
  }
};
