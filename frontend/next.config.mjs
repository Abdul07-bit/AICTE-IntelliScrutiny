import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

/** @type {import('next').NextConfig} */
const backend = process.env.BACKEND_URL || "http://localhost:8000";
// Pin the project root so Next.js never walks up to an unrelated lockfile in a parent folder.
const root = dirname(fileURLToPath(import.meta.url));

const nextConfig = {
  output: "standalone",
  outputFileTracingRoot: root,
  turbopack: { root },
  poweredByHeader: false,
  eslint: { ignoreDuringBuilds: true },
  // The browser only talks to this origin; API calls are proxied so the session cookie stays
  // first-party, httpOnly and SameSite=Strict.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
  async headers() {
    return [{
      source: "/:path*",
      headers: [
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "Referrer-Policy", value: "no-referrer" },
        { key: "X-Frame-Options", value: "SAMEORIGIN" },
      ],
    }];
  },
};

export default nextConfig;
