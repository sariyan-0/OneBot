const path = require("path");
require("dotenv").config({ path: path.resolve(__dirname, "..", ".env") });

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  experimental: {
    serverActions: {
      bodySizeLimit: "8mb",
    },
  },
};

module.exports = nextConfig;
