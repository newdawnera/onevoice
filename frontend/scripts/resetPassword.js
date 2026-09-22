import {
  processAuthRedirect,
  signOut,
  updateRecoveredPassword,
} from "./authService.js";

const form = document.getElementById("reset-password-form");
const status = document.getElementById("reset-status");
const button = document.getElementById("update-password-button");
const password = document.getElementById("new-password");
const confirmation = document.getElementById("confirm-new-password");

async function prepareRecovery() {
  try {
    const session = await processAuthRedirect();
    if (!session) throw new Error("No recovery session");
    status.textContent = "Enter and confirm your new password.";
    form.hidden = false;
  } catch {
    status.textContent =
      "This recovery link is invalid, expired, or has already been used. Request a new link from the sign-in page.";
    status.className = "message error";
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (button.disabled) return;
  if (password.value !== confirmation.value) {
    status.textContent = "Passwords do not match.";
    status.className = "message error";
    return;
  }
  if (password.value.length < 8) {
    status.textContent = "Use a password with at least 8 characters.";
    status.className = "message error";
    return;
  }

  button.disabled = true;
  button.textContent = "Updating…";
  try {
    const { error } = await updateRecoveredPassword(password.value);
    if (error) throw error;
    form.reset();
    form.hidden = true;
    await signOut();
    status.textContent = "Password updated. Please sign in with your new password.";
    status.className = "message success";
    window.setTimeout(() => window.location.replace("login.html"), 1200);
  } catch {
    status.textContent =
      "The password could not be updated. The recovery link may have expired; please request another link.";
    status.className = "message error";
  } finally {
    button.disabled = false;
    button.textContent = "Update password";
  }
});

prepareRecovery();
