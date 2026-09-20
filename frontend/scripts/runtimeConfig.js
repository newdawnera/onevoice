const isLocalDevelopment = ["localhost", "127.0.0.1"].includes(
  window.location.hostname
);

const config = Object.freeze({
  supabaseUrl: "https://fvltzantpynlbodwczez.supabase.co",
  supabasePublishableKey:
    "sb_publishable_JJKzenn_JNo62jETbJY9gQ_d6O3KB25",
  apiBaseUrl: isLocalDevelopment
    ? "http://localhost:8000"
    : "https://ally-back.onrender.com",
  publicFrontendBaseUrl: isLocalDevelopment
    ? window.location.origin
    : "https://ally-vimd.onrender.com",
  captchaSiteKey: "",
});

function isPlaceholder(value) {
  return !value || /your-|placeholder|example\.com/i.test(value);
}

export function getRuntimeConfig() {
  if (
    isPlaceholder(config.supabaseUrl) ||
    isPlaceholder(config.supabasePublishableKey) ||
    isPlaceholder(config.apiBaseUrl) ||
    isPlaceholder(config.publicFrontendBaseUrl)
  ) {
    throw new Error("Application configuration is unavailable.");
  }
  for (const key of ["supabaseUrl", "apiBaseUrl", "publicFrontendBaseUrl"]) {
    const parsed = new URL(config[key]);
    if (!isLocalDevelopment && parsed.protocol !== "https:") {
      throw new Error("Application configuration is unavailable.");
    }
  }
  if (!config.supabasePublishableKey.startsWith("sb_publishable_")) {
    throw new Error("Application configuration is unavailable.");
  }
  return config;
}

export const runtimeConfig = getRuntimeConfig();
