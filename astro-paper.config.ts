import { defineAstroPaperConfig } from "./src/types/config";

export default defineAstroPaperConfig({
  site: {
    url: "https://techcapitallab.com/",
    title: "TechCapitalLab",
    description: "데이터와 리포트를 바탕으로 금융 및 투자 가치를 재해석하는 전문 블로그입니다.\n기초 경제 지식부터 매일의 주요 경제 뉴스, 증권사 산업 리포트 분석까지—단기적인 소음에 흔들리지 않는 깊이 있는 투자 인사이트를 제공합니다.",
    author: "FreshJinyong",
    profile: "https://techcapitallab.com/",
    ogImage: "default-og.png",
    lang: "ko",
    timezone: "Asia/Seoul",
    dir: "ltr",
    googleVerification: "WpEBFaYHlClGwYnha029U-rCj1iUMpkrvkB5STprTJM",
  },
  posts: {
    perPage: 6,
    perIndex: 6,
    scheduledPostMargin: 15 * 60 * 1000,
  },
  features: {
    lightAndDarkMode: true,
    dynamicOgImage: true,
    showArchives: true,
    showBackButton: true,
    editPost: {
      enabled: false,
    },
    search: "pagefind",
  },
  socials: [],
  shareLinks: [
    { name: "x",        url: "https://x.com/intent/post?url=" },
    { name: "telegram", url: "https://t.me/share/url?url=" },
    { name: "facebook", url: "https://www.facebook.com/sharer.php?u=" },
    { name: "mail",     url: "mailto:?subject=See%20this%20post&body=" },
  ],
});
