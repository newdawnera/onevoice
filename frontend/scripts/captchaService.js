import { runtimeConfig } from "./runtimeConfig.js";

const HCAPTCHA_SCRIPT_URL =
  "https://js.hcaptcha.com/1/api.js?render=explicit";

let hcaptchaLoadPromise;

function getLoadedApi() {
  const api = window.hcaptcha;
  return api && typeof api.render === "function" ? api : null;
}

function loadHCaptcha() {
  const loadedApi = getLoadedApi();
  if (loadedApi) return Promise.resolve(loadedApi);
  if (hcaptchaLoadPromise) return hcaptchaLoadPromise;

  hcaptchaLoadPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = HCAPTCHA_SCRIPT_URL;
    script.async = true;
    script.defer = true;
    script.dataset.allyHcaptcha = "true";

    script.addEventListener(
      "load",
      () => {
        const api = getLoadedApi();
        if (api) {
          resolve(api);
          return;
        }
        reject(new Error("hCaptcha API did not initialize."));
      },
      { once: true }
    );
    script.addEventListener(
      "error",
      () => reject(new Error("hCaptcha API could not be loaded.")),
      { once: true }
    );

    document.head.appendChild(script);
  });

  return hcaptchaLoadPromise;
}

function preferredWidgetSize() {
  return window.matchMedia?.("(max-width: 380px)").matches
    ? "compact"
    : "normal";
}

export function createCaptchaWidget({ container, statusElement, tokenInput }) {
  if (!container || !tokenInput) {
    throw new Error("CAPTCHA controls are unavailable.");
  }

  let api;
  let mountPromise;
  let widgetId;

  function setStatus(message = "") {
    if (statusElement) statusElement.textContent = message;
  }

  function clearToken(message = "") {
    tokenInput.value = "";
    setStatus(message);
  }

  async function mount() {
    if (mountPromise) return mountPromise;

    mountPromise = (async () => {
      api = await loadHCaptcha();
      widgetId = api.render(container, {
        sitekey: runtimeConfig.captchaSiteKey,
        size: preferredWidgetSize(),
        theme: "dark",
        callback(token) {
          tokenInput.value = typeof token === "string" ? token : "";
          setStatus("");
        },
        "expired-callback"() {
          clearToken("Security check expired. Please complete it again.");
        },
        "chalexpired-callback"() {
          clearToken("Security check expired. Please complete it again.");
        },
        "error-callback"() {
          clearToken("Security check failed to load. Please try again.");
        },
      });
      return widgetId;
    })();

    try {
      return await mountPromise;
    } catch (error) {
      mountPromise = undefined;
      clearToken(
        "Security check is unavailable. Check your connection and refresh the page."
      );
      throw error;
    }
  }

  function getToken() {
    return tokenInput.value.trim() || undefined;
  }

  function reset() {
    clearToken();
    if (api && widgetId !== undefined) api.reset(widgetId);
  }

  return Object.freeze({ getToken, mount, reset });
}

export function isCaptchaFailure(error) {
  const code = typeof error?.code === "string" ? error.code : "";
  const message = typeof error?.message === "string" ? error.message : "";
  return code === "captcha_failed" || /captcha/i.test(message);
}
