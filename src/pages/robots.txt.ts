import type { APIRoute } from "astro";

const getRobotsTxt = (sitemapURL: URL, siteURL: URL) => `
User-agent: *
Allow: /
Disallow: /cdn-cgi/
Disallow: /tags/
Disallow: /tags/*
Disallow: /search
Disallow: /search/
Disallow: /rss.xml
Disallow: /rss.xml/

# AI Search & Answer Engine Crawlers (AEO / GEO Optimization)
User-agent: GPTBot
Allow: /
User-agent: OAI-SearchBot
Allow: /
User-agent: ChatGPT-User
Allow: /
User-agent: ClaudeBot
Allow: /
User-agent: Claude-SearchBot
Allow: /
User-agent: Claude-User
Allow: /
User-agent: PerplexityBot
Allow: /
User-agent: Perplexity-User
Allow: /
User-agent: Yeti
Allow: /

Sitemap: ${sitemapURL.href}
`;

export const GET: APIRoute = ({ site }) => {
  const sitemapURL = new URL("sitemap-index.xml", site);
  return new Response(getRobotsTxt(sitemapURL, site ? new URL(site) : new URL("https://techcapitallab.com/")), {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, max-age=14400",
    },
  });
};
