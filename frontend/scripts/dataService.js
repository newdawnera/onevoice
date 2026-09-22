import { getCurrentUser } from "./authService.js";
import { supabase } from "./supabaseClient.js";

const MEETING_PAGE_SIZE = 20;
const MAX_MEETING_PAGE_SIZE = 50;
const MAX_ACTION_LIST_SIZE = 500;
const ALLOWED_STATUSES = new Set([
  "not_started",
  "in_progress",
  "completed",
]);
const STATUS_LABELS = Object.freeze({
  not_started: "Not Started",
  in_progress: "In Progress",
  completed: "Completed",
});
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

let meetingChannel = null;
let actionChannel = null;

export class DataServiceError extends Error {
  constructor(operation, kind = "request_failed") {
    super(`The ${operation} operation could not be completed.`);
    this.name = "DataServiceError";
    this.kind = kind;
  }
}

function fail(operation, kind) {
  throw new DataServiceError(operation, kind);
}

function requiredText(value, maximum, operation, field) {
  const normalized = typeof value === "string" ? value.trim() : "";
  if (!normalized || normalized.length > maximum) {
    fail(operation, `invalid_${field}`);
  }
  return normalized;
}

function optionalText(value, maximum, operation, field) {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value !== "string") fail(operation, `invalid_${field}`);
  const normalized = value.trim();
  if (!normalized) return null;
  if (normalized.length > maximum) fail(operation, `invalid_${field}`);
  return normalized;
}

function dateOnly(value, operation, field) {
  const normalized = optionalText(value, 10, operation, field);
  if (!normalized) return null;
  if (!DATE_PATTERN.test(normalized)) fail(operation, `invalid_${field}`);
  const [year, month, day] = normalized.split("-").map(Number);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  if (
    parsed.getUTCFullYear() !== year ||
    parsed.getUTCMonth() !== month - 1 ||
    parsed.getUTCDate() !== day
  ) {
    fail(operation, `invalid_${field}`);
  }
  return normalized;
}

function validateDateOrder(startDate, deadline, operation) {
  if (startDate && deadline && deadline < startDate) {
    fail(operation, "invalid_date_order");
  }
}

function normalizeEmail(value, operation) {
  const email = optionalText(value, 320, operation, "assignee_email");
  if (email && !EMAIL_PATTERN.test(email)) {
    fail(operation, "invalid_assignee_email");
  }
  return email;
}

function requireUuid(value, operation, field) {
  if (typeof value !== "string" || !UUID_PATTERN.test(value)) {
    fail(operation, `invalid_${field}`);
  }
  return value;
}

async function verifiedUser(operation) {
  try {
    const user = await getCurrentUser();
    if (!user?.id) fail(operation, "not_authenticated");
    return user;
  } catch (error) {
    if (error instanceof DataServiceError) throw error;
    fail(operation, "not_authenticated");
  }
}

function unwrap(result, operation) {
  if (result?.error) fail(operation);
  return result?.data;
}

function normalizeActionFields(action, operation, { includeStatus = true } = {}) {
  const title = requiredText(action?.title, 500, operation, "title");
  const assignee =
    optionalText(action?.assignee, 200, operation, "assignee") || "Unassigned";
  const assigneeEmail = normalizeEmail(action?.assigneeEmail, operation);
  const startDate = dateOnly(action?.startDate, operation, "start_date");
  const deadline = dateOnly(action?.deadline, operation, "deadline");
  validateDateOrder(startDate, deadline, operation);

  const normalized = { assignee, assigneeEmail, deadline, startDate, title };
  if (includeStatus) {
    if (!ALLOWED_STATUSES.has(action?.status)) {
      fail(operation, "invalid_status");
    }
    normalized.status = action.status;
  }
  return normalized;
}

export function statusLabel(status) {
  return STATUS_LABELS[status] || "Unknown";
}

export function sourceLabel(source) {
  return source === "ai_generated" ? "AI-generated" : "Manual";
}

export function isDateOverdue(deadline, status, today = new Date()) {
  if (!deadline || status === "completed" || !DATE_PATTERN.test(deadline)) {
    return false;
  }
  const year = today.getFullYear();
  const month = String(today.getMonth() + 1).padStart(2, "0");
  const day = String(today.getDate()).padStart(2, "0");
  return deadline < `${year}-${month}-${day}`;
}

export function formatEventTimestamp(value, locale) {
  if (typeof value !== "string" || !value) return "Date not available";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Date not available";
  return parsed.toLocaleString(locale, {
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function mapMeetingRow(row) {
  if (!row || typeof row !== "object" || !row.id) {
    fail("meeting list", "invalid_response");
  }
  return {
    id: row.id,
    sourceType: row.source_type || "text",
    sourceFilename: row.source_filename || null,
    sourceHtml: row.source_html || null,
    sourceText: row.source_text || "",
    summaryHtml: row.summary_html || null,
    summaryText: row.summary_text || "",
    emailSubject: row.email_subject || null,
    requestedRole: row.requested_role || null,
    targetLanguage: row.target_language || null,
    aiProvider: row.ai_provider || null,
    aiModel: row.ai_model || null,
    promptVersion: row.prompt_version || null,
    actions: Array.isArray(row.extracted_actions_snapshot)
      ? row.extracted_actions_snapshot.map((action) => ({
          title: typeof action?.title === "string" ? action.title : "Untitled action",
          assignee:
            typeof action?.assignee === "string" ? action.assignee : "Unassigned",
          assigneeEmail:
            typeof action?.assigneeEmail === "string" ? action.assigneeEmail : null,
          startDate: typeof action?.startDate === "string" ? action.startDate : null,
          deadline: typeof action?.deadline === "string" ? action.deadline : null,
          evidence: typeof action?.evidence === "string" ? action.evidence : null,
          reviewStatus: ["pending", "confirmed", "rejected"].includes(
            action?.reviewStatus
          )
            ? action.reviewStatus
            : "pending",
        }))
      : [],
    createdAt: row.created_at || null,
  };
}

export function mapActionRow(row) {
  if (!row || typeof row !== "object" || !row.id) {
    fail("action list", "invalid_response");
  }
  return {
    id: row.id,
    meetingId: row.meeting_id || null,
    title: row.title || "Untitled action",
    assignee: row.assignee || "Unassigned",
    assigneeEmail: row.assignee_email || null,
    status: ALLOWED_STATUSES.has(row.status) ? row.status : "not_started",
    source: row.source === "ai_generated" ? "ai_generated" : "manual",
    reviewStatus: ["pending", "confirmed", "rejected"].includes(
      row.review_status
    )
      ? row.review_status
      : row.source === "ai_generated"
        ? "pending"
        : "confirmed",
    reviewedAt: row.reviewed_at || null,
    evidence: row.ai_evidence || row.evidence || null,
    startDate: row.start_date || null,
    deadline: row.deadline || null,
    createdAt: row.created_at || null,
  };
}

export async function getCurrentProfile() {
  const operation = "profile read";
  const user = await verifiedUser(operation);
  const data = unwrap(
    await supabase
      .from("profiles")
      .select("id, display_name, created_at, updated_at")
      .eq("id", user.id)
      .maybeSingle(),
    operation
  );
  return data || null;
}

export async function updateCurrentProfile(displayName) {
  const operation = "profile update";
  const user = await verifiedUser(operation);
  const normalized = requiredText(displayName, 100, operation, "display_name");
  const data = unwrap(
    await supabase
      .from("profiles")
      .update({ display_name: normalized })
      .eq("id", user.id)
      .select("id, display_name, created_at, updated_at")
      .maybeSingle(),
    operation
  );
  if (!data) fail(operation, "not_found");
  return data;
}

export async function listMeetingRecords({ offset = 0, limit = MEETING_PAGE_SIZE } = {}) {
  const operation = "meeting list";
  await verifiedUser(operation);
  const safeOffset = Number.isInteger(offset) && offset >= 0 ? offset : 0;
  const safeLimit =
    Number.isInteger(limit) && limit > 0
      ? Math.min(limit, MAX_MEETING_PAGE_SIZE)
      : MEETING_PAGE_SIZE;
  const data = unwrap(
    await supabase
      .from("meeting_records")
      .select(
        "id, source_type, source_filename, source_html, source_text, summary_html, summary_text, email_subject, requested_role, target_language, ai_provider, ai_model, prompt_version, extracted_actions_snapshot, created_at"
      )
      .order("created_at", { ascending: false })
      .order("id", { ascending: false })
      .range(safeOffset, safeOffset + safeLimit - 1),
    operation
  );
  if (!Array.isArray(data)) fail(operation, "invalid_response");
  return {
    records: data.map(mapMeetingRow),
    hasMore: data.length === safeLimit,
    nextOffset: safeOffset + data.length,
  };
}

export async function listActionItems() {
  const operation = "action list";
  await verifiedUser(operation);
  const data = unwrap(
    await supabase
      .from("action_items")
      .select(
        "id, meeting_id, title, assignee, assignee_email, status, source, start_date, deadline, review_status, reviewed_at, ai_evidence, created_at"
      )
      .order("created_at", { ascending: false })
      .limit(MAX_ACTION_LIST_SIZE),
    operation
  );
  if (!Array.isArray(data)) fail(operation, "invalid_response");
  return data.map(mapActionRow);
}

export async function createActionItem(action) {
  const operation = "action create";
  const user = await verifiedUser(operation);
  const normalized = normalizeActionFields(action, operation, { includeStatus: false });
  const data = unwrap(
    await supabase
      .from("action_items")
      .insert({
        user_id: user.id,
        title: normalized.title,
        assignee: normalized.assignee,
        assignee_email: normalized.assigneeEmail,
        start_date: normalized.startDate,
        deadline: normalized.deadline,
      })
      .select(
        "id, meeting_id, title, assignee, assignee_email, status, source, start_date, deadline, review_status, reviewed_at, ai_evidence, created_at"
      )
      .maybeSingle(),
    operation
  );
  if (!data) fail(operation, "not_found");
  return mapActionRow(data);
}

export async function updateActionItem(id, action) {
  const operation = "action update";
  const user = await verifiedUser(operation);
  requireUuid(id, operation, "action_id");
  const normalized = normalizeActionFields(action, operation);
  const data = unwrap(
    await supabase
      .from("action_items")
      .update({
        title: normalized.title,
        assignee: normalized.assignee,
        assignee_email: normalized.assigneeEmail,
        status: normalized.status,
        start_date: normalized.startDate,
        deadline: normalized.deadline,
      })
      .eq("id", id)
      .eq("user_id", user.id)
      .select(
        "id, meeting_id, title, assignee, assignee_email, status, source, start_date, deadline, review_status, reviewed_at, ai_evidence, created_at"
      )
      .maybeSingle(),
    operation
  );
  if (!data) fail(operation, "not_found");
  return mapActionRow(data);
}

export async function deleteActionItem(id) {
  const operation = "action delete";
  const user = await verifiedUser(operation);
  requireUuid(id, operation, "action_id");
  const data = unwrap(
    await supabase
      .from("action_items")
      .delete()
      .eq("id", id)
      .eq("user_id", user.id)
      .select("id")
      .maybeSingle(),
    operation
  );
  if (!data?.id) fail(operation, "not_found");
  return data.id;
}

function removeChannel(channel) {
  if (channel) void supabase.removeChannel(channel);
}

export function stopMeetingSubscription() {
  const channel = meetingChannel;
  meetingChannel = null;
  removeChannel(channel);
}

export function stopActionSubscription() {
  const channel = actionChannel;
  actionChannel = null;
  removeChannel(channel);
}

export function stopAllSubscriptions() {
  stopMeetingSubscription();
  stopActionSubscription();
}

export async function subscribeToMeetingRecords(onChange, onStatus = () => {}) {
  const operation = "meeting subscription";
  const user = await verifiedUser(operation);
  stopMeetingSubscription();
  const channel = supabase
    .channel(`meeting-records:${user.id}`)
    .on(
      "postgres_changes",
      {
        event: "INSERT",
        schema: "public",
        table: "meeting_records",
        filter: `user_id=eq.${user.id}`,
      },
      (payload) => {
        if (payload?.new?.user_id !== user.id) return;
        onChange(mapMeetingRow(payload.new));
      }
    )
    .subscribe(onStatus);
  meetingChannel = channel;
  return () => {
    if (meetingChannel === channel) meetingChannel = null;
    removeChannel(channel);
  };
}

export async function subscribeToActionItems(onChange, onStatus = () => {}) {
  const operation = "action subscription";
  const user = await verifiedUser(operation);
  stopActionSubscription();
  const channel = supabase
    .channel(`action-items:${user.id}`)
    .on(
      "postgres_changes",
      {
        event: "*",
        schema: "public",
        table: "action_items",
        filter: `user_id=eq.${user.id}`,
      },
      () => onChange()
    )
    .subscribe(onStatus);
  actionChannel = channel;
  return () => {
    if (actionChannel === channel) actionChannel = null;
    removeChannel(channel);
  };
}

export const dataLimits = Object.freeze({
  meetingPageSize: MEETING_PAGE_SIZE,
  maxActionListSize: MAX_ACTION_LIST_SIZE,
});
