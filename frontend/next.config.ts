import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  reactStrictMode: true,
  // Pin the workspace root so a stray lockfile higher up isn't picked up.
  turbopack: { root: path.join(__dirname) },
};

export default nextConfig;
