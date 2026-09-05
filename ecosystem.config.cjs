const path = require("path");

const ROOT_DIR = __dirname;
const SHARED_DATA = path.join(ROOT_DIR, "jax-shared", "data");
const LOGS_DIR = path.join(SHARED_DATA, "logs");

// Load environment variables from .env
let envConfig = {};
try {
  const result = require("dotenv").config({ path: path.join(ROOT_DIR, ".env") });
  envConfig = result.parsed || {};
} catch (e) {}

const apps = [
  {
    name: "jax-whatsapp-monitor",
    script: "monitor.mjs",
    cwd: path.join(ROOT_DIR, "jax-whatsapp-monitor"),
    interpreter: "node",
    watch: false,
    autorestart: true,
    max_restarts: 20,
    min_uptime: "10s",
    restart_delay: 3000,
    max_memory_restart: "200M",
    kill_timeout: 30000,
    env: {
      ...envConfig,
      NODE_ENV: "production",
      API_PORT: "9095",
      SQLITE_DB_PATH: path.join(SHARED_DATA, "prospects.db"),
      AUTH_DIR: path.join(ROOT_DIR, "jax-whatsapp-monitor", "auth_info_monitor")
    },
    error_file: path.join(LOGS_DIR, "pm2-monitor-error.log"),
    out_file: path.join(LOGS_DIR, "pm2-monitor-out.log"),
    log_date_format: "YYYY-MM-DD HH:mm:ss Z",
    merge_logs: true,
    log_type: "json"
  },
  {
    name: "jax-whatsapp",
    script: "bot.mjs",
    cwd: path.join(ROOT_DIR, "jax-whatsapp-agent"),
    interpreter: "node",
    watch: false,
    autorestart: true,
    max_restarts: 20,
    min_uptime: "10s",
    restart_delay: 3000,
    max_memory_restart: "250M",
    kill_timeout: 30000,
    env: {
      ...envConfig,
      NODE_ENV: "production",
      SQLITE_DB_PATH: path.join(SHARED_DATA, "prospects.db")
    },
    error_file: path.join(LOGS_DIR, "pm2-whatsapp-error.log"),
    out_file: path.join(LOGS_DIR, "pm2-whatsapp-out.log"),
    log_date_format: "YYYY-MM-DD HH:mm:ss Z",
    merge_logs: true,
    log_type: "json"
  }
];

if (process.env.TELEGRAM_BOT_TOKEN) {
  apps.push({
    name: "jax-telegram",
    script: "bot.mjs",
    cwd: path.join(ROOT_DIR, "jax-telegram-agent"),
    interpreter: "node",
    watch: false,
      autorestart: true,
      max_restarts: 20,
      min_uptime: "10s",
      restart_delay: 3000,
      max_memory_restart: "220M",
      kill_timeout: 30000,
      env: {
        NODE_ENV: "production",
        HEALTH_PORT: "9090"
      },
      error_file: path.join(LOGS_DIR, "pm2-telegram-error.log"),
      out_file: path.join(LOGS_DIR, "pm2-telegram-out.log"),
      log_date_format: "YYYY-MM-DD HH:mm:ss Z",
      merge_logs: true,
      log_type: "json"
    });
}

apps.push({
  name: "jax-watchdog",
  script: "watchdog.mjs",
  cwd: path.join(ROOT_DIR, "jax-shared"),
  interpreter: "node",
  watch: false,
  autorestart: true,
  max_restarts: 50,
  min_uptime: "10s",
  restart_delay: 5000,
  max_memory_restart: "64M",
  env: {
    NODE_ENV: "production"
  },
  error_file: path.join(LOGS_DIR, "pm2-watchdog-error.log"),
  out_file: path.join(LOGS_DIR, "pm2-watchdog-out.log"),
  log_date_format: "YYYY-MM-DD HH:mm:ss Z",
  merge_logs: true,
  log_type: "json"
});

module.exports = { apps };
