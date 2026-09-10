export const onRequest = async (context: any) => {
  const url = new URL(context.request.url);
  const path = url.searchParams.get("path");
  const vid = url.searchParams.get("vid");

  const corsHeaders = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  };

  if (context.request.method === "OPTIONS") {
    return new Response(null, { headers: corsHeaders });
  }

  if (!path || !path.startsWith("/posts/") || path === "/posts/" || path === "/posts") {
    return new Response(JSON.stringify({ error: "Invalid path" }), {
      status: 400,
      headers: corsHeaders,
    });
  }

  // Normalize path (strip trailing slash if any)
  const normalizedPath = path.length > 1 && path.endsWith("/") ? path.slice(0, -1) : path;

  const kv = context.env ? context.env.PV_KV : null;
  if (!kv) {
    // If KV is not bound yet, return fallback mock response
    return new Response(
      JSON.stringify({
        pv: 1,
        incremented: false,
        notice: "PV_KV binding not configured yet in Cloudflare Pages.",
      }),
      { status: 200, headers: corsHeaders }
    );
  }

  const countKey = `pv:${normalizedPath}`;
  const lockKey = vid ? `lock:${normalizedPath}:${vid}` : null;

  try {
    let isLocked = false;
    if (lockKey) {
      const lockVal = await kv.get(lockKey);
      if (lockVal) {
        isLocked = true;
      }
    }

    const currentPvStr = await kv.get(countKey);
    let currentPv = currentPvStr ? parseInt(currentPvStr, 10) : 0;
    if (isNaN(currentPv)) currentPv = 0;

    let incremented = false;

    if (!isLocked) {
      currentPv += 1;
      await kv.put(countKey, String(currentPv));
      if (lockKey) {
        // Lock for 24 hours = 86400 seconds
        await kv.put(lockKey, "1", { expirationTtl: 86400 });
      }
      incremented = true;
    }

    return new Response(
      JSON.stringify({
        pv: currentPv,
        incremented,
      }),
      {
        status: 200,
        headers: corsHeaders,
      }
    );
  } catch (err: any) {
    return new Response(
      JSON.stringify({ error: err?.message || "KV Storage Error" }),
      {
        status: 500,
        headers: corsHeaders,
      }
    );
  }
};
