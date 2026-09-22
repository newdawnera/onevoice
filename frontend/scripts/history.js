import { initializeApp } from "./appLogic.js";
import {
  dataLimits,
  formatEventTimestamp,
  listMeetingRecords,
  subscribeToMeetingRecords,
} from "./dataService.js";

function createElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function searchableText(record) {
  return [
    record.sourceText,
    record.summaryText,
    record.sourceFilename,
    record.emailSubject,
    ...(record.actions || []).flatMap((action) => [
      action.title,
      action.assignee,
      action.assigneeEmail,
    ]),
  ]
    .filter(Boolean)
    .join("\n")
    .toLocaleLowerCase();
}

function actionSnapshot(actions) {
  if (!actions?.length) {
    return createElement(
      "p",
      "text-slate-500",
      "No action items were extracted for this record."
    );
  }

  const wrapper = createElement(
    "div",
    "w-full md:overflow-x-auto rounded-lg md:border md:border-slate-200"
  );
  const table = createElement("table", "w-full responsive-table");
  const head = createElement("thead", "bg-slate-50");
  const headRow = document.createElement("tr");
  for (const label of ["Task", "Assignee", "Start", "Deadline"]) {
    headRow.appendChild(
      createElement(
        "th",
        "p-4 text-left text-sm font-medium text-slate-800",
        label
      )
    );
  }
  head.appendChild(headRow);
  table.appendChild(head);

  const body = createElement("tbody", "md:divide-y md:divide-slate-200");
  actions.forEach((action) => {
    const row = document.createElement("tr");
    const cells = [
      ["Task", action.title || "Untitled action"],
      ["Assignee", action.assignee || "Unassigned"],
      ["Start", action.startDate || "N/A"],
      ["Deadline", action.deadline || "N/A"],
    ];
    cells.forEach(([label, value]) => {
      const cell = createElement("td", "p-4 text-sm text-slate-700", value);
      cell.dataset.label = label;
      row.appendChild(cell);
    });
    body.appendChild(row);
  });
  table.appendChild(body);
  wrapper.appendChild(table);
  return wrapper;
}

function detailsBlock(label, content) {
  const details = createElement("details", "group mt-4");
  const summary = createElement(
    "summary",
    "font-semibold text-lg cursor-pointer text-indigo-700 hover:text-indigo-800",
    label
  );
  const body = createElement(
    "div",
    "mt-2 p-4 bg-slate-50 rounded-md whitespace-pre-wrap break-words text-slate-700",
    content || "Not available."
  );
  details.append(summary, body);
  return details;
}

function historyCard(record) {
  const card = createElement(
    "article",
    "bg-white rounded-lg shadow-md border border-slate-200 overflow-hidden"
  );
  card.dataset.recordId = record.id;
  const content = createElement("div", "p-5");
  content.appendChild(
    createElement(
      "p",
      "text-sm text-slate-500 mb-2",
      formatEventTimestamp(record.createdAt)
    )
  );

  const sourceDescription = [
    record.sourceType ? `Source: ${record.sourceType.replaceAll("_", " ")}` : null,
    record.sourceFilename ? `File: ${record.sourceFilename}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  if (sourceDescription) {
    content.appendChild(
      createElement("p", "text-xs text-slate-500 mb-3", sourceDescription)
    );
  }

  const transcript = detailsBlock("Transcript / source", record.sourceText);
  transcript.classList.remove("mt-4");
  content.appendChild(transcript);
  content.appendChild(detailsBlock("Summary", record.summaryText));

  const actions = createElement("details", "group mt-4");
  actions.appendChild(
    createElement(
      "summary",
      "font-semibold text-lg cursor-pointer text-indigo-700 hover:text-indigo-800",
      "Action-item snapshot"
    )
  );
  const actionsBody = createElement("div", "mt-2 p-4 bg-slate-50 rounded-md");
  actionsBody.appendChild(actionSnapshot(record.actions));
  actions.appendChild(actionsBody);
  content.appendChild(actions);
  card.appendChild(content);
  return card;
}

async function historyPage() {
  const elements = {
    list: document.getElementById("history-list"),
    spinner: document.getElementById("loading-spinner"),
    empty: document.getElementById("empty-state"),
    search: document.getElementById("search-input"),
  };
  if (!elements.list || !elements.spinner || !elements.empty || !elements.search) {
    return;
  }

  const records = new Map();
  let nextOffset = 0;
  let hasMore = true;
  let loading = false;
  let unsubscribe = null;

  const controls = createElement("div", "mt-3 flex flex-wrap items-center gap-3");
  const scope = createElement(
    "p",
    "text-xs text-slate-500 flex-1",
    "Search covers the records currently loaded on this page. Load more to widen it."
  );
  const connection = createElement("p", "text-xs text-slate-500");
  connection.setAttribute("role", "status");
  connection.setAttribute("aria-live", "polite");
  const loadMore = createElement(
    "button",
    "hidden rounded-md border border-indigo-300 px-4 py-2 text-sm font-medium text-indigo-700 hover:bg-indigo-50",
    "Load more"
  );
  loadMore.type = "button";
  controls.append(scope, connection, loadMore);
  elements.list.insertAdjacentElement("afterend", controls);

  function orderedRecords() {
    return [...records.values()].sort((left, right) => {
      const byDate = String(right.createdAt || "").localeCompare(
        String(left.createdAt || "")
      );
      return byDate || String(right.id).localeCompare(String(left.id));
    });
  }

  function render() {
    const term = elements.search.value.trim().toLocaleLowerCase();
    const all = orderedRecords();
    const visible = term
      ? all.filter((record) => searchableText(record).includes(term))
      : all;

    elements.spinner.classList.add("hidden");
    elements.empty.classList.toggle("hidden", all.length !== 0);
    elements.list.replaceChildren();

    if (all.length > 0 && visible.length === 0) {
      elements.list.appendChild(
        createElement(
          "p",
          "rounded-lg border border-slate-200 bg-white p-6 text-center text-slate-500",
          "No loaded records match this search. Load more to search older records."
        )
      );
    } else {
      visible.forEach((record) => elements.list.appendChild(historyCard(record)));
    }

    loadMore.classList.toggle("hidden", !hasMore);
    loadMore.disabled = loading;
    loadMore.textContent = loading ? "Loading…" : "Load more";
  }

  function showError() {
    elements.spinner.classList.add("hidden");
    elements.empty.classList.add("hidden");
    const retry = createElement(
      "button",
      "mt-3 rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700",
      "Try again"
    );
    retry.type = "button";
    const panel = createElement(
      "section",
      "rounded-lg border border-red-200 bg-red-50 p-6 text-center text-red-800"
    );
    panel.append(
      createElement("p", "", "History could not be loaded. Check your connection and try again."),
      retry
    );
    retry.addEventListener("click", () => void loadPage(true));
    elements.list.replaceChildren(panel);
  }

  async function loadPage(reset = false) {
    if (loading) return;
    loading = true;
    if (reset) {
      records.clear();
      nextOffset = 0;
      hasMore = true;
    }
    elements.spinner.classList.toggle("hidden", nextOffset !== 0);
    loadMore.disabled = true;
    loadMore.textContent = "Loading…";
    try {
      const page = await listMeetingRecords({
        offset: nextOffset,
        limit: dataLimits.meetingPageSize,
      });
      page.records.forEach((record) => records.set(record.id, record));
      nextOffset = page.nextOffset;
      hasMore = page.hasMore;
      render();
    } catch {
      showError();
    } finally {
      loading = false;
      loadMore.disabled = false;
      loadMore.textContent = "Load more";
    }
  }

  elements.search.addEventListener("input", render);
  loadMore.addEventListener("click", () => void loadPage());
  await loadPage(true);

  try {
    unsubscribe = await subscribeToMeetingRecords(
      (record) => {
        const isNew = !records.has(record.id);
        records.set(record.id, record);
        if (isNew) nextOffset += 1;
        render();
      },
      (status) => {
        if (status === "SUBSCRIBED") {
          connection.textContent = "Live updates connected";
          connection.className = "text-xs text-emerald-700";
        } else if (["CHANNEL_ERROR", "TIMED_OUT", "CLOSED"].includes(status)) {
          connection.textContent = "Live updates disconnected — reload to check for new records";
          connection.className = "text-xs text-amber-700";
        }
      }
    );
  } catch {
    connection.textContent = "Live updates unavailable — reload to check for new records";
    connection.className = "text-xs text-amber-700";
  }

  window.addEventListener(
    "pagehide",
    () => {
      unsubscribe?.();
      unsubscribe = null;
    },
    { once: true }
  );
}

initializeApp(historyPage);
