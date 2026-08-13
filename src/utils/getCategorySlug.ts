import { slugifyStr } from "./slugify";

export const CATEGORY_MAP: Record<
  string,
  { name: string; slug: string; desc: string }
> = {
  "1. 경제 기초 지식": {
    name: "1. 경제 기초 지식 (Economy Basics)",
    slug: "economy-basics",
    desc: "기초 경제 원리와 개념을 알기 쉽게 정리한 Evergreen 지식 창고입니다.",
  },
  "2. 뉴스 속 경제": {
    name: "2. 뉴스 속 경제 (News & Macro)",
    slug: "news-macro",
    desc: "매일 쏟아지는 글로벌 경제 뉴스 속 맥락과 거시 경제 흐름을 해석합니다.",
  },
  "3. 증권사 리포트 읽기": {
    name: "3. 증권사 리포트 읽기 (Report Lab)",
    slug: "report-lab",
    desc: "증권사 산업 및 기업 리포트를 수치와 팩트 중심으로 심층 분석합니다.",
  },
};

export const getCategorySlug = (categoryName: string): string => {
  return CATEGORY_MAP[categoryName]?.slug || slugifyStr(categoryName);
};

export const getCategoryFromSlug = (slug: string) => {
  const found = Object.entries(CATEGORY_MAP).find(([_, v]) => v.slug === slug);
  if (found) {
    return { key: found[0], ...found[1] };
  }
  return null;
};
