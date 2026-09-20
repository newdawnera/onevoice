import { initializeApp } from "./appLogic.js";

function renderMigrationBoundary() {
  document.querySelectorAll("#create-task-btn").forEach((button) => {
    button.disabled = true;
    button.setAttribute("aria-disabled", "true");
    button.title = "Action items return after the data migration";
  });
  const search = document.getElementById("search-input");
  if (search) {
    search.disabled = true;
    search.placeholder = "Action-item search returns after data migration";
  }
  document.querySelectorAll(".filter-btn").forEach((button) => {
    button.disabled = true;
  });

  const tableBody = document.getElementById("action-items-tbody");
  if (!tableBody) return;
  const row = document.createElement("tr");
  const cell = document.createElement("td");
  cell.colSpan = 6;
  cell.className = "p-8 text-center text-amber-800 bg-amber-50";
  cell.textContent =
    "Action items and reminders are temporarily unavailable until Firestore data is migrated to Supabase. No browser-supplied task data is being trusted during this transition.";
  row.appendChild(cell);
  tableBody.replaceChildren(row);
}

initializeApp(() => {
  renderMigrationBoundary();
});
