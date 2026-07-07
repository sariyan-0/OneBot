import fs from "fs/promises";
import path from "path";

const nextDir = path.resolve(process.cwd(), ".next");

try {
  await fs.rm(nextDir, { recursive: true, force: true });
} catch {
  // Ignore cleanup errors; Next will recreate the directory on demand.
}
