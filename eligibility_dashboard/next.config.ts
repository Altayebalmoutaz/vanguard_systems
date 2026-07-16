import type { NextConfig } from "next";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(import.meta.url);
const turbopackRoot = path.dirname(require.resolve("./package.json"));

const nextConfig: NextConfig = {
  // Required for the production Docker image (copies .next/standalone).
  output: "standalone",
  turbopack: {
    root: turbopackRoot,
  },
};

export default nextConfig;
