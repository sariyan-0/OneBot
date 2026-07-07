const os = require("os");
const { execSync } = require("child_process");

function safeExec(cmd) {
  try {
    return execSync(cmd, { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  } catch {
    return "";
  }
}

function getSystemSnapshot() {
  const total = os.totalmem();
  const free = os.freemem();
  const used = total - free;
  const load = os.loadavg();
  const df = safeExec("df -k . | tail -1");
  const dfParts = df.split(/\s+/).filter(Boolean);
  const diskUsed = Number(dfParts[2] || 0) * 1024;
  const diskTotal = Number(dfParts[1] || 0) * 1024;

  return {
    hostname: os.hostname(),
    platform: `${os.type()} ${os.release()}`,
    uptime: os.uptime(),
    load,
    cpuCount: os.cpus().length,
    memory: { used, total },
    disk: { used: diskUsed, total: diskTotal },
    process: process.memoryUsage(),
  };
}

module.exports = { getSystemSnapshot };
