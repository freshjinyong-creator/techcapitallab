import type { APIRoute } from "astro";
import { getCollection } from "astro:content";
import { fontData, experimental_getFontFileURL } from "astro:assets";
import satori from "satori";
import sharp from "sharp";
import { getFontPathByWeight } from "@/utils/getFontPathByWeight";
import { getPostSlug } from "@/utils/getPostPaths";
import config from "@/config";

export async function getStaticPaths() {
  if (!config.features.dynamicOgImage) {
    return [];
  }

  const posts = await getCollection("posts").then(p =>
    p.filter(({ data }) => !data.draft && !data.ogImage)
  );

  return posts.map(post => ({
    params: { slug: getPostSlug(post.id, post.filePath) },
    props: post,
  }));
}

export const GET: APIRoute = async ({ props, url }) => {
  if (!config.features.dynamicOgImage) {
    return new Response(null, { status: 404, statusText: "Not found" });
  }

  const fonts = fontData["--font-google-sans-code"];
  const regularFontPath = getFontPathByWeight(fonts, 400);
  const boldFontPath = getFontPathByWeight(fonts, 700);

  const krFonts = fontData["--font-noto-sans-kr"];
  const krRegularFontPath = getFontPathByWeight(krFonts, 400);
  const krBoldFontPath = getFontPathByWeight(krFonts, 700);

  if (regularFontPath === undefined || boldFontPath === undefined) {
    throw new Error("Cannot find the base font path.");
  }

  const [regularData, boldData, krRegularData, krBoldData] = await Promise.all([
    fetch(experimental_getFontFileURL(regularFontPath, url)).then(res => res.arrayBuffer()),
    fetch(experimental_getFontFileURL(boldFontPath, url)).then(res => res.arrayBuffer()),
    krRegularFontPath ? fetch(experimental_getFontFileURL(krRegularFontPath, url)).then(res => res.arrayBuffer()) : null,
    krBoldFontPath ? fetch(experimental_getFontFileURL(krBoldFontPath, url)).then(res => res.arrayBuffer()) : null,
  ]);

  const svg = await satori(
    {
      type: "div",
      props: {
        style: {
          background: "#0f172a",
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontFamily: '"Noto Sans KR", "Google Sans Code", sans-serif',
        },
        children: [
          {
            type: "div",
            props: {
              style: {
                border: "2px solid #334155",
                background: "#1e293b",
                borderRadius: "16px",
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
                padding: "3rem",
                width: "90%",
                height: "82%",
                boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.5)",
              },
              children: [
                {
                  type: "div",
                  props: {
                    style: {
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      width: "100%",
                    },
                    children: [
                      {
                        type: "div",
                        props: {
                          style: {
                            background: "rgba(16, 185, 129, 0.15)",
                            color: "#34d399",
                            border: "1px solid rgba(16, 185, 129, 0.3)",
                            borderRadius: "8px",
                            padding: "6px 16px",
                            fontSize: 20,
                            fontWeight: "bold",
                          },
                          children: props.data.category || "FINANCIAL RESEARCH",
                        },
                      },
                      {
                        type: "div",
                        props: {
                          style: {
                            color: "#94a3b8",
                            fontSize: 22,
                            fontWeight: "bold",
                            letterSpacing: "0.05em",
                          },
                          children: config.site.title,
                        },
                      },
                    ],
                  },
                },
                {
                  type: "p",
                  props: {
                    style: {
                      fontSize: 52,
                      fontWeight: "bold",
                      color: "#f8fafc",
                      lineHeight: "1.3",
                      maxHeight: "60%",
                      overflow: "hidden",
                      margin: "1rem 0",
                    },
                    children: props.data.title,
                  },
                },
                {
                  type: "div",
                  props: {
                    style: {
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      width: "100%",
                      borderTop: "1px solid #334155",
                      paddingTop: "1rem",
                      color: "#94a3b8",
                      fontSize: 20,
                    },
                    children: [
                      {
                        type: "span",
                        props: {
                          children: "TechCapitalLab Financial Insights",
                        },
                      },
                      {
                        type: "span",
                        props: {
                          style: { color: "#38bdf8", fontWeight: "bold" },
                          children: "techcapitallab.com",
                        },
                      },
                    ],
                  },
                },
              ],
            },
          },
        ],
      },
    },
    {
      width: 1200,
      height: 630,
      embedFont: true,
      fonts: [
        {
          name: "Google Sans Code",
          data: regularData,
          weight: 400,
          style: "normal",
        },
        {
          name: "Google Sans Code",
          data: boldData,
          weight: 700,
          style: "normal",
        },
        ...(krRegularData ? [{
          name: "Noto Sans KR",
          data: krRegularData,
          weight: 400 as const,
          style: "normal" as const,
        }] : []),
        ...(krBoldData ? [{
          name: "Noto Sans KR",
          data: krBoldData,
          weight: 700 as const,
          style: "normal" as const,
        }] : []),
      ],
    }
  );

  const png = await sharp(Buffer.from(svg)).png().toBuffer();

  return new Response(new Uint8Array(png), {
    headers: {
      "Content-Type": "image/png",
      "Cache-Control": "public, max-age=31536000, immutable",
    },
  });
};
