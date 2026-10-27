import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  // Research runs web search plus a local LLM and can take several minutes.
  experimental: {
    proxyTimeout: 600_000,
  },
  async rewrites() {
    const apiBaseUrl = process.env.UVM_API_INTERNAL_URL ?? "http://127.0.0.1:8000";
    return [
      {
        source: "/api/backend/:path*",
        destination: `${apiBaseUrl}/api/v1/:path*`,
      },
    ];
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
