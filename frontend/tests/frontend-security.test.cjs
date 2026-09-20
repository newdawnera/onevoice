const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const scriptsDirectory = path.resolve(__dirname, "..", "scripts");

class MemoryStorage {
  constructor() {
    this.values = new Map();
  }

  get length() {
    return this.values.size;
  }

  key(index) {
    return [...this.values.keys()][index] ?? null;
  }

  getItem(key) {
    return this.values.has(key) ? this.values.get(key) : null;
  }

  setItem(key, value) {
    this.values.set(String(key), String(value));
  }

  removeItem(key) {
    this.values.delete(key);
  }
}

function createContext(overrides = {}) {
  return vm.createContext({
    AbortController,
    FormData,
    Headers,
    Response,
    URL,
    clearTimeout,
    console: { error() {}, log() {}, warn() {} },
    queueMicrotask,
    setTimeout,
    ...overrides,
  });
}

function syntheticModule(context, exports) {
  return new vm.SyntheticModule(
    Object.keys(exports),
    function initialize() {
      for (const [name, value] of Object.entries(exports)) {
        this.setExport(name, value);
      }
    },
    { context }
  );
}

async function loadScript(filename, context, imports) {
  const source = fs.readFileSync(path.join(scriptsDirectory, filename), "utf8");
  const module = new vm.SourceTextModule(source, {
    context,
    identifier: filename,
  });
  await module.link(async (specifier) => {
    if (!(specifier in imports)) {
      throw new Error(`Unexpected import: ${specifier}`);
    }
    return syntheticModule(context, imports[specifier]);
  });
  await module.evaluate();
  return module.namespace;
}

async function loadSupabaseClient() {
  const localStorage = new MemoryStorage();
  const sessionStorage = new MemoryStorage();
  let clientOptions;
  const context = createContext({ localStorage, sessionStorage });
  const namespace = await loadScript("supabaseClient.js", context, {
    "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.116.0/+esm": {
      createClient(_url, _key, options) {
        clientOptions = options;
        return { marker: "client" };
      },
    },
    "./runtimeConfig.js": {
      runtimeConfig: {
        supabasePublishableKey: "sb_publishable_test",
        supabaseUrl: "https://project.supabase.co",
      },
    },
  });
  return { clientOptions, localStorage, namespace, sessionStorage };
}

test("Supabase client uses pinned PKCE configuration and remembered storage", async () => {
  const { clientOptions, localStorage, namespace, sessionStorage } =
    await loadSupabaseClient();
  const storage = clientOptions.auth.storage;

  assert.equal(clientOptions.auth.flowType, "pkce");
  assert.equal(clientOptions.auth.autoRefreshToken, true);
  assert.equal(clientOptions.auth.persistSession, true);
  assert.equal(clientOptions.auth.detectSessionInUrl, true);

  sessionStorage.setItem("ally.supabase.auth", "stale");
  namespace.setAuthPersistenceMode(true);
  storage.setItem("ally.supabase.auth", "remembered");
  assert.equal(localStorage.getItem("ally.supabase.auth"), "remembered");
  assert.equal(sessionStorage.getItem("ally.supabase.auth"), null);

  storage.setItem("ally.supabase.auth-code-verifier", "temporary");
  assert.equal(
    localStorage.getItem("ally.supabase.auth-code-verifier"),
    "temporary"
  );
  namespace.clearTransientAuthStorage();
  assert.equal(localStorage.getItem("ally.supabase.auth"), "remembered");
  assert.equal(localStorage.getItem("ally.supabase.auth-code-verifier"), null);
});

test("session-only mode removes remembered tokens and does not copy them", async () => {
  const { clientOptions, localStorage, namespace, sessionStorage } =
    await loadSupabaseClient();
  const storage = clientOptions.auth.storage;

  namespace.setAuthPersistenceMode(true);
  storage.setItem("ally.supabase.auth", "remembered");
  namespace.setAuthPersistenceMode(false);
  storage.setItem("ally.supabase.auth", "session-only");

  assert.equal(localStorage.getItem("ally.supabase.auth"), null);
  assert.equal(sessionStorage.getItem("ally.supabase.auth"), "session-only");
  assert.equal(new MemoryStorage().getItem("ally.supabase.auth"), null);
});

async function loadAuthService(authOverrides = {}, windowOverrides = {}) {
  const calls = { cleanup: 0, persistence: [], signup: [] };
  const auth = {
    exchangeCodeForSession: async () => ({ data: { session: null }, error: null }),
    getSession: async () => ({ data: { session: null }, error: null }),
    getUser: async () => ({ data: { user: null }, error: null }),
    onAuthStateChange: () => ({ data: { subscription: { unsubscribe() {} } } }),
    refreshSession: async () => ({ data: { session: null }, error: null }),
    resetPasswordForEmail: async () => ({ data: {}, error: null }),
    signInWithPassword: async () => ({ data: {}, error: null }),
    signOut: async () => ({ error: null }),
    signUp: async (options) => {
      calls.signup.push(options);
      return { data: { session: null }, error: null };
    },
    updateUser: async () => ({ data: {}, error: null }),
    ...authOverrides,
  };
  const historyCalls = [];
  const window = {
    history: {
      replaceState(...args) {
        historyCalls.push(args);
      },
    },
    location: {
      href: "https://app.example/auth-callback.html",
      pathname: "/auth-callback.html",
    },
    ...windowOverrides,
  };
  const context = createContext({ document: { title: "Ally" }, window });
  const namespace = await loadScript("authService.js", context, {
    "./runtimeConfig.js": {
      runtimeConfig: {
        captchaSiteKey: "",
        publicFrontendBaseUrl: "https://app.example",
      },
    },
    "./supabaseClient.js": {
      clearTransientAuthStorage() {
        calls.cleanup += 1;
      },
      setAuthPersistenceMode(value) {
        calls.persistence.push(value);
      },
      supabase: {
        auth,
        from: () => ({
          select: () => ({
            eq: () => ({ maybeSingle: async () => ({ data: null, error: null }) }),
          }),
        }),
      },
    },
  });
  return { calls, historyCalls, namespace };
}

test("signup is session-only and sends only display metadata plus fixed callback", async () => {
  const { calls, namespace } = await loadAuthService();
  await namespace.signUp({
    captchaToken: "captcha",
    displayName: "Alice",
    email: "alice@example.com",
    password: "not-altered",
  });

  assert.deepEqual(calls.persistence, [false]);
  assert.equal(calls.signup[0].password, "not-altered");
  assert.equal(calls.signup[0].options.emailRedirectTo, "https://app.example/auth-callback.html");
  assert.deepEqual(
    JSON.parse(JSON.stringify(calls.signup[0].options.data)),
    { display_name: "Alice" }
  );
  assert.equal("role" in calls.signup[0].options.data, false);
});

test("callback exchanges a code once, removes it from the URL, and clears PKCE state", async () => {
  let exchanges = 0;
  const session = { access_token: "not-logged" };
  const { calls, historyCalls, namespace } = await loadAuthService(
    {
      exchangeCodeForSession: async () => {
        exchanges += 1;
        return { data: { session }, error: null };
      },
    },
    {
      location: {
        href: "https://app.example/auth-callback.html?code=sensitive",
        pathname: "/auth-callback.html",
      },
    }
  );

  assert.equal(await namespace.processAuthRedirect(), session);
  assert.equal(exchanges, 1);
  assert.equal(historyCalls[0][2], "/auth-callback.html");
  assert.equal(calls.cleanup, 1);
});

test("invalid callbacks still clear transient PKCE state", async () => {
  const { calls, namespace } = await loadAuthService(
    {
      exchangeCodeForSession: async () => ({
        data: {},
        error: new Error("invalid"),
      }),
    },
    {
      location: {
        href: "https://app.example/auth-callback.html?code=invalid",
        pathname: "/auth-callback.html",
      },
    }
  );

  await assert.rejects(namespace.processAuthRedirect());
  assert.equal(calls.cleanup, 1);
});

test("password recovery always uses the fixed same-origin reset page", async () => {
  let request;
  const { namespace } = await loadAuthService({
    resetPasswordForEmail: async (...args) => {
      request = args;
      return { data: {}, error: null };
    },
  });
  await namespace.requestPasswordReset("alice@example.com", "captcha");
  assert.equal(request[1].redirectTo, "https://app.example/reset-password.html");
  assert.equal(request[1].captchaToken, "captcha");
});

async function loadApiClient({ session, refresh, responses }) {
  const calls = { fetch: [], refresh: 0, signout: 0 };
  const redirects = [];
  const context = createContext({
    fetch: async (url, options) => {
      calls.fetch.push({ options, url });
      return responses.shift();
    },
    window: {
      location: {
        assign(value) {
          redirects.push(value);
        },
        pathname: "/home.html",
      },
    },
  });
  const namespace = await loadScript("apiClient.js", context, {
    "./authService.js": {
      getCurrentSession: async () => session,
      refreshSession: async () => {
        calls.refresh += 1;
        if (refresh instanceof Error) throw refresh;
        return refresh;
      },
      signOut: async () => {
        calls.signout += 1;
      },
    },
    "./runtimeConfig.js": {
      runtimeConfig: { apiBaseUrl: "https://api.example" },
    },
  });
  return { calls, namespace, redirects };
}

test("API client sends bearer token, preserves abort signal, and leaves FormData boundary to fetch", async () => {
  const response = new Response(null, { status: 204 });
  const { calls, namespace } = await loadApiClient({
    refresh: null,
    responses: [response],
    session: { access_token: "first-token" },
  });
  const body = new FormData();
  body.append("file", "value");
  const controller = new AbortController();
  await namespace.authenticatedFetch("/upload-document/", {
    body,
    headers: { "Content-Type": "incorrect/manual" },
    method: "POST",
    signal: controller.signal,
  });

  assert.equal(calls.fetch.length, 1);
  assert.equal(calls.fetch[0].options.headers.get("Authorization"), "Bearer first-token");
  assert.equal(calls.fetch[0].options.headers.has("Content-Type"), false);
  assert.equal(calls.fetch[0].options.signal, controller.signal);
});

test("API client refreshes and retries exactly once after 401", async () => {
  const { calls, namespace } = await loadApiClient({
    refresh: { access_token: "second-token" },
    responses: [
      new Response(null, { status: 401 }),
      new Response("{}", { status: 200 }),
    ],
    session: { access_token: "first-token" },
  });
  const response = await namespace.authenticatedFetch("/ai-helper", {
    method: "POST",
  });

  assert.equal(response.status, 200);
  assert.equal(calls.refresh, 1);
  assert.equal(calls.fetch.length, 2);
  assert.equal(calls.fetch[1].options.headers.get("Authorization"), "Bearer second-token");
});

test("API client does not refresh on 403 and exposes 429 Retry-After safely", async () => {
  const forbidden = await loadApiClient({
    refresh: { access_token: "unused" },
    responses: [new Response(null, { status: 403 })],
    session: { access_token: "token" },
  });
  assert.equal((await forbidden.namespace.authenticatedFetch("/test")).status, 403);
  assert.equal(forbidden.calls.refresh, 0);

  const limited = await loadApiClient({
    refresh: null,
    responses: [
      new Response(JSON.stringify({ detail: "Please wait." }), {
        headers: { "Content-Type": "application/json", "Retry-After": "30" },
        status: 429,
      }),
    ],
    session: { access_token: "token" },
  });
  await assert.rejects(
    limited.namespace.authenticatedJson("/test"),
    (error) => error.status === 429 && error.retryAfter === "30"
  );
});

test("API client fails closed and redirects when a session is absent", async () => {
  const { calls, namespace, redirects } = await loadApiClient({
    refresh: null,
    responses: [],
    session: null,
  });
  await assert.rejects(namespace.authenticatedFetch("/ai-helper"));
  assert.equal(calls.fetch.length, 0);
  assert.equal(calls.signout, 1);
  assert.deepEqual(redirects, ["login.html"]);
});

class FakeElement {
  constructor() {
    this.children = [];
    this.disabled = false;
    this.listeners = {};
    this.style = {};
    this.textContent = "";
  }

  addEventListener(event, callback) {
    this.listeners[event] = callback;
  }

  append(...values) {
    this.children.push(...values);
  }

  appendChild(value) {
    this.children.push(value);
  }

  replaceChildren(...values) {
    this.children = values;
  }
}

async function loadAppLogic(user) {
  const elements = { "welcome-message": new FakeElement() };
  const redirects = [];
  const context = createContext({
    document: {
      createElement: () => new FakeElement(),
      getElementById: (id) => elements[id] ?? null,
    },
    localStorage: new MemoryStorage(),
    window: {
      addEventListener() {},
      location: {
        pathname: "/home.html",
        replace(value) {
          redirects.push(value);
        },
      },
    },
  });
  const namespace = await loadScript("appLogic.js", context, {
    "./authService.js": {
      getProfileDisplayName: async () => null,
      onAuthStateChange: () => () => undefined,
      requireAuthenticatedUser: async () => user,
      signOut: async () => undefined,
    },
  });
  return { elements, namespace, redirects };
}

test("protected-page guard redirects logged-out visitors", async () => {
  const { namespace, redirects } = await loadAppLogic(null);
  await namespace.initializeApp();
  assert.deepEqual(redirects, ["login.html"]);
});

test("display names containing HTML are assigned as text, not parsed markup", async () => {
  const malicious = '<img src=x onerror="steal()">';
  const { elements, namespace } = await loadAppLogic({
    email: "fallback@example.com",
    id: "00000000-0000-4000-8000-000000000001",
    user_metadata: { display_name: malicious },
  });
  await namespace.initializeApp();

  const strong = elements["welcome-message"].children[0];
  const name = strong.children[1];
  assert.equal(name.textContent, malicious);
  assert.equal(name.children.length, 0);
});

test("page scripts retain generic Auth/recovery errors and required safe flow hooks", () => {
  const login = fs.readFileSync(path.join(scriptsDirectory, "login.js"), "utf8");
  const signup = fs.readFileSync(path.join(scriptsDirectory, "signup.js"), "utf8");
  const callback = fs.readFileSync(path.join(scriptsDirectory, "authCallback.js"), "utf8");
  const reset = fs.readFileSync(path.join(scriptsDirectory, "resetPassword.js"), "utf8");
  const home = fs.readFileSync(path.join(scriptsDirectory, "home.js"), "utf8");

  assert.match(login, /Invalid email or password/);
  assert.match(login, /If an account exists for this email/);
  assert.match(login, /error\?\.status === 429/);
  assert.match(signup, /if \(data\.session\)/);
  assert.match(signup, /confirmation email will arrive shortly/);
  assert.doesNotMatch(signup, /send-welcome-email/);
  assert.match(callback, /invalid, expired, or has already been used/);
  assert.match(reset, /Passwords do not match/);
  assert.match(reset, /await signOut\(\)/);
  assert.match(home, /DOMPurify\.sanitize/);
  assert.match(home, /textContent/);
});
