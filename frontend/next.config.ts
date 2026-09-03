import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["127.0.0.1"],
  distDir: process.env.RIFFLOOM_NEXT_DIST_DIR ?? ".next",
  output: "standalone",
};

export default nextConfig;
