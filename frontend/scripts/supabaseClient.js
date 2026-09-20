import { createClient } from "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.116.0/+esm";
import { runtimeConfig } from "./runtimeConfig.js";

const AUTH_STORAGE_KEY = "ally.supabase.auth";
const PERSISTENCE_KEY = "ally.auth.persistence";
const LOCAL_MODE = "local";
const SESSION_MODE = "session";

let persistenceMode =
  localStorage.getItem(PERSISTENCE_KEY) === LOCAL_MODE
    ? LOCAL_MODE
    : SESSION_MODE;

function isPkceVerifierKey(key) {
  return key === `${AUTH_STORAGE_KEY}-code-verifier`;
}

function matchingKeys(storage) {
  const keys = [];
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index);
    if (key && (key === AUTH_STORAGE_KEY || key.startsWith(`${AUTH_STORAGE_KEY}-`))) {
      keys.push(key);
    }
  }
  return keys;
}

function removeAuthKeys(storage) {
  matchingKeys(storage).forEach((key) => storage.removeItem(key));
}

export function setAuthPersistenceMode(rememberUser) {
  persistenceMode = rememberUser ? LOCAL_MODE : SESSION_MODE;
  if (persistenceMode === LOCAL_MODE) {
    removeAuthKeys(sessionStorage);
    sessionStorage.removeItem(PERSISTENCE_KEY);
    localStorage.setItem(PERSISTENCE_KEY, LOCAL_MODE);
  } else {
    removeAuthKeys(localStorage);
    localStorage.removeItem(PERSISTENCE_KEY);
    sessionStorage.setItem(PERSISTENCE_KEY, SESSION_MODE);
  }
}

export function clearTransientAuthStorage() {
  for (const storage of [localStorage, sessionStorage]) {
    matchingKeys(storage)
      .filter((key) => key !== AUTH_STORAGE_KEY)
      .forEach((key) => storage.removeItem(key));
  }
}

const controlledStorage = {
  getItem(key) {
    if (isPkceVerifierKey(key)) return localStorage.getItem(key);
    const storage = persistenceMode === LOCAL_MODE ? localStorage : sessionStorage;
    return storage.getItem(key);
  },
  setItem(key, value) {
    if (isPkceVerifierKey(key)) {
      sessionStorage.removeItem(key);
      localStorage.setItem(key, value);
      return;
    }
    const selected = persistenceMode === LOCAL_MODE ? localStorage : sessionStorage;
    const stale = persistenceMode === LOCAL_MODE ? sessionStorage : localStorage;
    stale.removeItem(key);
    selected.setItem(key, value);
  },
  removeItem(key) {
    localStorage.removeItem(key);
    sessionStorage.removeItem(key);
  },
};

export const supabase = createClient(
  runtimeConfig.supabaseUrl,
  runtimeConfig.supabasePublishableKey,
  {
    auth: {
      flowType: "pkce",
      autoRefreshToken: true,
      persistSession: true,
      detectSessionInUrl: true,
      storage: controlledStorage,
      storageKey: AUTH_STORAGE_KEY,
    },
  }
);
