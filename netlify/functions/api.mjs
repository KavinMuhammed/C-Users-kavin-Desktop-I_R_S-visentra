// Handles /api/* on Netlify. The VisionTra API (FastAPI + PyTorch) runs on a separate host;
// set VISIONTRA_API_URL (e.g. https://api.example.com) to forward requests to it.
// Without it, every call gets the API's JSON error shape so the UI shows a clear message.
const HOP = new Set(["host", "connection", "content-length", "transfer-encoding", "accept-encoding"]);

export default async (req) => {
  const upstream = (Netlify.env.get("VISIONTRA_API_URL") || "").replace(/\/+$/, "");
  if (!upstream) {
    return Response.json(
      { error: { code: "SERVICE_UNAVAILABLE", message: "The VisionTra server is not connected to this site yet. Inspections, sign-in and reports need the backend to be running." } },
      { status: 503 },
    );
  }
  const url = new URL(req.url);
  const headers = new Headers();
  for (const [k, v] of req.headers) if (!HOP.has(k.toLowerCase())) headers.set(k, v);
  headers.set("X-Forwarded-Host", url.host);
  headers.set("X-Forwarded-Proto", url.protocol.replace(":", ""));
  try {
    const res = await fetch(upstream + url.pathname + url.search, {
      method: req.method,
      headers,
      body: ["GET", "HEAD"].includes(req.method) ? undefined : await req.arrayBuffer(),
      redirect: "manual",
    });
    const out = new Headers(res.headers);
    out.delete("content-encoding");
    out.delete("content-length");
    return new Response(res.body, { status: res.status, headers: out });
  } catch {
    return Response.json({ error: { code: "SERVICE_UNAVAILABLE", message: "Can't reach the VisionTra server. Check that it is running, then try again." } }, { status: 502 });
  }
};

export const config = { path: "/api/*" };
