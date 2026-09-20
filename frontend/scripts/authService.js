import {
  clearTransientAuthStorage,
  setAuthPersistenceMode,
  supabase,
} from "./supabaseClient.js";
import { runtimeConfig } from "./runtimeConfig.js";

const allowedRedirects = Object.freeze({
  confirmation: `${runtimeConfig.publicFrontendBaseUrl}/auth-callback.html`,
  recovery: `${runtimeConfig.publicFrontendBaseUrl}/reset-password.html`,
});

function captchaOptions(captchaToken) {
  return captchaToken ? { captchaToken } : {};
}

export async function signUp({ displayName, email, password, captchaToken }) {
  setAuthPersistenceMode(false);
  return supabase.auth.signUp({
    email,
    password,
    options: {
      data: { display_name: displayName },
      emailRedirectTo: allowedRedirects.confirmation,
      ...captchaOptions(captchaToken),
    },
  });
}

export async function signIn({
  email,
  password,
  rememberUser,
  captchaToken,
}) {
  setAuthPersistenceMode(Boolean(rememberUser));
  return supabase.auth.signInWithPassword({
    email,
    password,
    options: captchaOptions(captchaToken),
  });
}

export async function signOut() {
  const result = await supabase.auth.signOut({ scope: "local" });
  clearTransientAuthStorage();
  return result;
}

export async function getCurrentSession() {
  const { data, error } = await supabase.auth.getSession();
  if (error) throw error;
  return data.session;
}

export async function getCurrentUser() {
  const { data, error } = await supabase.auth.getUser();
  if (error) throw error;
  return data.user;
}

export async function refreshSession() {
  const { data, error } = await supabase.auth.refreshSession();
  if (error) throw error;
  return data.session;
}

export async function requestPasswordReset(email, captchaToken) {
  return supabase.auth.resetPasswordForEmail(email, {
    redirectTo: allowedRedirects.recovery,
    ...captchaOptions(captchaToken),
  });
}

export async function updateRecoveredPassword(password) {
  return supabase.auth.updateUser({ password });
}

export function onAuthStateChange(callback) {
  const { data } = supabase.auth.onAuthStateChange(callback);
  return () => data.subscription.unsubscribe();
}

export async function requireAuthenticatedUser() {
  const session = await getCurrentSession();
  if (!session) return null;
  try {
    return await getCurrentUser();
  } catch {
    await signOut().catch(() => undefined);
    return null;
  }
}

export async function getProfileDisplayName(user) {
  const { data, error } = await supabase
    .from("profiles")
    .select("display_name")
    .eq("id", user.id)
    .maybeSingle();
  if (error) return null;
  return data?.display_name?.trim() || null;
}

export async function processAuthRedirect() {
  const url = new URL(window.location.href);
  const code = url.searchParams.get("code");
  window.history.replaceState({}, document.title, window.location.pathname);
  try {
    let session = await getCurrentSession();
    if (!session && code) {
      const { data, error } = await supabase.auth.exchangeCodeForSession(code);
      if (error) throw error;
      session = data.session;
    }
    return session;
  } finally {
    clearTransientAuthStorage();
  }
}

export function readCaptchaToken(form) {
  if (!runtimeConfig.captchaSiteKey) return undefined;
  const input = form.querySelector('[name="captcha-token"]');
  const token = input?.value?.trim();
  return token || undefined;
}
