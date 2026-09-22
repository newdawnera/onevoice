import { authenticatedJson } from "./apiClient.js";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const RETRY_KEY_PREFIX = "ally.reminder-request.";
const retryKeys = new Map();

function requireActionId(actionItemId) {
  if (typeof actionItemId !== "string" || !UUID_PATTERN.test(actionItemId)) {
    throw new Error("The action item identifier is invalid.");
  }
  return actionItemId;
}

function storageKey(actionItemId) {
  return `${RETRY_KEY_PREFIX}${actionItemId}`;
}

function readRetryKey(actionItemId) {
  if (retryKeys.has(actionItemId)) return retryKeys.get(actionItemId);
  try {
    const stored = sessionStorage.getItem(storageKey(actionItemId));
    if (stored && UUID_PATTERN.test(stored)) {
      retryKeys.set(actionItemId, stored);
      return stored;
    }
    if (stored) sessionStorage.removeItem(storageKey(actionItemId));
  } catch {
    // Session storage can be unavailable in hardened/private browser contexts.
  }
  return null;
}

function writeRetryKey(actionItemId, key) {
  retryKeys.set(actionItemId, key);
  try {
    sessionStorage.setItem(storageKey(actionItemId), key);
  } catch {
    // The in-memory key still protects retries during this page lifetime.
  }
}

function deleteRetryKey(actionItemId) {
  retryKeys.delete(actionItemId);
  try {
    sessionStorage.removeItem(storageKey(actionItemId));
  } catch {
    // Nothing else is required when storage is unavailable.
  }
}

export async function requestManualReminder(actionItemId) {
  const id = requireActionId(actionItemId);
  const idempotencyKey = readRetryKey(id) || crypto.randomUUID();
  writeRetryKey(id, idempotencyKey);
  try {
    const result = await authenticatedJson(
      `/action-items/${encodeURIComponent(id)}/reminders`,
      {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
      }
    );
    deleteRetryKey(id);
    return result;
  } catch (error) {
    // Keep the same key for an uncertain retry. A successful response clears it,
    // so a later intentional reminder receives a fresh UUID.
    throw error;
  }
}

export function clearReminderRetryKey(actionItemId) {
  deleteRetryKey(requireActionId(actionItemId));
}
