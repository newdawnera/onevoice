import { initializeApp } from "./appLogic.js";
import {
  collection,
  query,
  onSnapshot,
} from "https://www.gstatic.com/firebasejs/11.9.1/firebase-firestore.js";

const historyPageLogic = (user, db) => {
  let unsubscribeFromHistory = null;
  let allHistoryItems = [];

  const elements = {
    historyList: document.getElementById("history-list"),
    loadingSpinner: document.getElementById("loading-spinner"),
    emptyState: document.getElementById("empty-state"),
    searchInput: document.getElementById("search-input"),
  };

  function applySearchFilter() {
    const searchTerm = elements.searchInput.value.toLowerCase();

    if (!searchTerm) {
      renderHistory(allHistoryItems);
      return;
    }

    const filteredItems = allHistoryItems.filter((item) => {
      const transcriptMatch = item.transcript
        ?.toLowerCase()
        .includes(searchTerm);
      const summaryMatch = item.summary?.toLowerCase().includes(searchTerm);
      const actionLogMatch = item.actionLogs?.some(
        (log) =>
          log.task?.toLowerCase().includes(searchTerm) ||
          log.assignee?.toLowerCase().includes(searchTerm)
      );
      return transcriptMatch || summaryMatch || actionLogMatch;
    });

    renderHistory(filteredItems);
  }

  function formatTimestamp(timestamp) {
    if (!timestamp || !timestamp.toDate) return "Date not available";
    return timestamp.toDate().toLocaleString("en-US", {
      year: "numeric",
      month: "long",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function renderActionLogs(logs) {
    if (!logs || logs.length === 0) {
      return '<p class="text-slate-500">No action logs for this entry.</p>';
    }
    let tableHTML = `
              <div class="w-full md:overflow-x-auto rounded-lg md:border md:border-slate-200">
                  <table class="w-full responsive-table">
                      <thead class="bg-slate-50">
                          <tr>
                              <th class="p-4 text-left text-sm font-medium text-slate-800">Task</th>
                              <th class="p-4 text-left text-sm font-medium text-slate-800">Assignee</th>
                          </tr>
                      </thead>
                      <tbody class="md:divide-y md:divide-slate-200">`;

    logs.forEach((log) => {
      tableHTML += `
                  <tr>
                      <td data-label="Task" class="p-4 text-sm text-slate-800">${
                        log.task || "N/A"
                      }</td>
                      <td data-label="Assignee" class="p-4 text-sm text-slate-600">${
                        log.assignee || "N/A"
                      }</td>
                  </tr>`;
    });

    tableHTML += "</tbody></table></div>";
    return tableHTML;
  }

  function renderHistory(historyItems) {
    elements.loadingSpinner.classList.add("hidden");
    if (historyItems.length === 0) {
      elements.emptyState.classList.remove("hidden");
      elements.historyList.innerHTML = "";
      return;
    }
    elements.emptyState.classList.add("hidden");

    elements.historyList.innerHTML = "";
    historyItems.forEach((item) => {
      const card = document.createElement("div");
      card.className =
        "bg-white rounded-lg shadow-md border border-slate-200 overflow-hidden";
      card.innerHTML = `
                    <div class="p-5">
                        <p class="text-sm text-slate-500 mb-2">${formatTimestamp(
                          item.createdAt
                        )}</p>
                        <details class="group">
                            <summary class="font-semibold text-lg cursor-pointer text-indigo-700 hover:text-indigo-800">Transcript</summary>
                            <div class="mt-2 p-4 bg-slate-50 rounded-md prose max-w-none">${
                              item.transcript || "Not available."
                            }</div>
                        </details>
                        <details class="group mt-4">
                            <summary class="font-semibold text-lg cursor-pointer text-indigo-700 hover:text-indigo-800">Summary</summary>
                            <div class="mt-2 p-4 bg-slate-50 rounded-md prose max-w-none">${
                              item.summary || "Not available."
                            }</div>
                        </details>
                        <details class="group mt-4">
                            <summary class="font-semibold text-lg cursor-pointer text-indigo-700 hover:text-indigo-800">Action Logs</summary>
                            <div class="mt-2 p-4 bg-slate-50 rounded-md prose max-w-none">${renderActionLogs(
                              item.actionLogs
                            )}</div>
                        </details>
                    </div>`;
      elements.historyList.appendChild(card);
    });
  }

  function fetchHistory() {
    elements.loadingSpinner.classList.remove("hidden");
    elements.emptyState.classList.add("hidden");

    const historyCollectionRef = collection(db, `users/${user.uid}/history`);
    const q = query(historyCollectionRef);

    if (unsubscribeFromHistory) {
      unsubscribeFromHistory();
    }

    unsubscribeFromHistory = onSnapshot(
      q,
      (querySnapshot) => {
        const historyItems = [];
        querySnapshot.forEach((doc) => {
          historyItems.push({ id: doc.id, ...doc.data() });
        });

        allHistoryItems = historyItems.sort(
          (a, b) =>
            (b.createdAt?.toMillis() || 0) - (a.createdAt?.toMillis() || 0)
        );

        applySearchFilter();
      },
      (error) => {
        console.error("Error fetching history:", error);
        elements.loadingSpinner.classList.add("hidden");
        elements.historyList.innerHTML =
          '<p class="text-red-500 text-center">Could not load history. Please try again later.</p>';
      }
    );
  }
  elements.searchInput.addEventListener("input", applySearchFilter);
  fetchHistory();
};

initializeApp(historyPageLogic);
