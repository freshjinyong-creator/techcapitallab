import type { APIRoute } from "astro";
import { getCollection } from "astro:content";
import { getSortedPosts } from "@/utils/getSortedPosts";
import { getPostUrl } from "@/utils/getPostPaths";
import config from "@/config";

export const GET: APIRoute = async () => {
  const posts = await getCollection("posts");
  const sortedPosts = getSortedPosts(posts);
  const recentPosts = sortedPosts.slice(0, 30); // Top 30 recent posts

  const lines = [
    `# ${config.site.title}`,
    "",
    `> ${config.site.description.replace(/\n/g, " ")}`,
    "",
    "## 사이트 핵심 정보",
    `- [홈페이지](${config.site.url}): 돈테크랩 메인 포털`,
    `- [소개(About)](${new URL("/pages/about/", config.site.url).href}): 돈테크랩 리서치 철학 및 데이터 분석 기준`,
    `- [카테고리: 경제 기초](${new URL("/category/economy-basics/", config.site.url).href}): 거시경제 및 금융 기초 가이드`,
    `- [카테고리: 매일 주요 뉴스](${new URL("/category/news-macro/", config.site.url).href}): 글로벌 매크로 및 시장 주요 이슈 분석`,
    `- [카테고리: 증권사 리포트 읽기](${new URL("/category/report-lab/", config.site.url).href}): 국내외 증권사 및 애널리스트 산업·기업 리포트 분석`,
    `- [카테고리: 차트 및 기술 분석](${new URL("/category/chart-analysis/", config.site.url).href}): 퀀트 지표 및 기술적 분석`,
    "",
    "## 주요 분석 포스트 목록 (최신순)",
    ...recentPosts.map(({ data, id, filePath }) => {
      const url = new URL(getPostUrl(id, filePath, config.site.lang), config.site.url).href;
      const desc = data.description ? `: ${data.description.replace(/\n/g, " ")}` : "";
      return `- [${data.title}](${url})${desc}`;
    }),
    "",
    "## 데이터 및 인용 정책",
    "- 출처: 한국거래소(KRX), 금융감독원 전자공시시스템(DART), 국내외 주요 증권사 공식 리서치 리포트 기반 자체 데이터 재해석",
    "- 갱신 주기: 매 영업일 실시간 및 마감 분석 데이터 갱신",
    `- 인용 시 표기: 돈테크랩 (${config.site.title}, ${config.site.url})`,
    "",
  ];

  return new Response(lines.join("\n"), {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
    },
  });
};
