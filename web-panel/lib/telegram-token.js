const { getSetting } = require("./db");

async function getTelegramBotToken() {
  const envToken = String(process.env.BOT_TOKEN || "").trim();
  const dbToken = String(await getSetting("BOT_TOKEN", "") || "").trim();
  const tokenSource = String(await getSetting("BOT_TOKEN_SOURCE", "") || "").trim().toLowerCase();

  if (dbToken && envToken && dbToken !== envToken && tokenSource !== "panel") {
    return envToken;
  }

  return dbToken || envToken;
}

module.exports = {
  getTelegramBotToken,
};
