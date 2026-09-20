import { initializeApp } from "./appLogic.js";

function renderMigrationBoundary() {
  const list = document.getElementById("history-list");
  const spinner = document.getElementById("loading-spinner");
  const emptyState = document.getElementById("empty-state");
  const search = document.getElementById("search-input");

  spinner?.classList.add("hidden");
  emptyState?.classList.add("hidden");
  if (search) {
    search.disabled = true;
    search.placeholder = "History search returns after data migration";
  }
  if (!list) return;

  const notice = document.createElement("section");
  notice.className =
    "rounded-lg border border-amber-200 bg-amber-50 p-6 text-amber-900";
  const heading = document.createElement("h2");
  heading.className = "text-lg font-semibold";
  heading.textContent = "History is temporarily unavailable";
  const body = document.createElement("p");
  body.className = "mt-2";
  body.textContent =
    "Your existing records have not yet been migrated from Firestore to Supabase. This page is protected by Supabase Auth, but record access will return in the next migration phase.";
  notice.append(heading, body);
  list.replaceChildren(notice);
}

initializeApp(() => {
  renderMigrationBoundary();
});
