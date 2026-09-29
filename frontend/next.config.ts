import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const apiInternalUrl = process.env.API_INTERNAL_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["127.0.0.1"],
  // Docker Desktop на Windows не пробрасывает inotify-события bind-mount'а
  // в контейнер, поэтому dev-сервер переключается на polling.
  ...(process.env.WATCHPACK_POLLING === "true"
    ? { watchOptions: { pollIntervalMs: 500 } }
    : {}),
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiInternalUrl}/:path*` }];
  },
};

export default createNextIntlPlugin()(nextConfig);
