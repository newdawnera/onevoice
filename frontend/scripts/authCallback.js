import { processAuthRedirect } from "./authService.js";

const status = document.getElementById("auth-callback-status");

async function completeConfirmation() {
  try {
    const session = await processAuthRedirect();
    status.textContent = session
      ? "Email confirmed. Redirecting to Ally…"
      : "Email confirmed. Redirecting to sign in…";
    status.className = "message success";
    window.setTimeout(
      () => window.location.replace(session ? "home.html" : "login.html"),
      500
    );
  } catch {
    status.textContent =
      "This confirmation link is invalid, expired, or has already been used. Please sign in or request another email.";
    status.className = "message error";
  }
}

completeConfirmation();
