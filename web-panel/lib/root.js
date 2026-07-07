const path = require("path");
const fs = require("fs");

function getRootDir() {
  if (process.env.ONEBOT_ROOT) {
    return path.resolve(process.env.ONEBOT_ROOT);
  }

  const starts = [__dirname, process.cwd()];
  const markers = [
    ["main.py"],
    ["database", "models.py"],
    ["bot_data.db"],
  ];

  for (const start of starts) {
    let current = path.resolve(start);
    while (true) {
      for (const marker of markers) {
        if (fs.existsSync(path.join(current, ...marker))) {
          return current;
        }
      }
      const parent = path.dirname(current);
      if (parent === current) break;
      current = parent;
    }
  }

  return path.resolve(__dirname, "..", "..");
}

module.exports = { getRootDir };
