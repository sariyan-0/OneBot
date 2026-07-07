const path = require("path");
require("dotenv").config({ path: path.resolve(__dirname, "..", ".env") });

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  experimental: {
    serverActions: {
      bodySizeLimit: "256mb",
    },
  },
};

module.exports = nextConfig;
