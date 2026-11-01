// Forwards /api/* to the VisionTra backend at VISIONTRA_API_URL, keeping the browser on one origin so the
// session and CSRF cookies keep working. Without the variable, answers in the API's own error shape.
export default async (req: Request) => {
  const base = Netlify.env.get("VISIONTRA_API_URL");
  if (!base) {
    return Response.json(
      { error: { code: "BACKEND_NOT_CONFIGURED", message: "The VisionTra server is not connected to this site yet. Set VISIONTRA_API_URL to the backend address." } },
      { status: 503 },
    );
  }
  const url = new URL(req.url);
  const target = new URL(url.pathname + url.search, base.endsWith("/") ? base : base + "/");
  const headers = new Headers(req.headers);
  headers.delete("host");
  headers.set("X-Forwarded-Host", url.host);
  headers.set("X-Forwarded-Proto", url.protocol.replace(":", ""));
  const hasBody = req.method !== "GET" && req.method !== "HEAD";
  try {
    return await fetch(target, { method: req.method, headers, body: hasBody ? req.body : undefined, redirect: "manual" });
  } catch {
    return Response.json(
      { error: { code: "NETWORK", message: "Can't reach the VisionTra server. Check that it is running, then try again." } },
      { status: 502 },
    );
  }
};

export const config = { path: "/api/*" };
