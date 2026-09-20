import {
  readCaptchaToken,
  requestPasswordReset,
  requireAuthenticatedUser,
  signIn,
} from "./authService.js";

const loginForm = document.getElementById("login-form");
const emailInput = document.getElementById("email");
const passwordInput = document.getElementById("password");
const rememberMeCheckbox = document.getElementById("remember-me");
const loginButton = document.getElementById("login-button");
const errorMessageDiv = document.getElementById("error-message");
const successMessageDiv = document.getElementById("success-message");
const forgotPasswordLink = document.getElementById("forgot-password-link");
const resetModal = document.getElementById("reset-modal");
const resetEmailInput = document.getElementById("reset-email-input");
const cancelResetBtn = document.getElementById("cancel-reset-btn");
const sendResetBtn = document.getElementById("send-reset-btn");
const resetErrorMessage = document.getElementById("reset-error-message");
const resetModalMessage = document.getElementById("reset-modal-message");

function setLoginPending(pending) {
  loginButton.disabled = pending;
  loginButton.textContent = pending ? "Signing In..." : "Sign In";
}

function showLoginError(message) {
  errorMessageDiv.textContent = message;
  errorMessageDiv.style.display = "block";
}

async function redirectAuthenticatedUser() {
  try {
    if (await requireAuthenticatedUser()) {
      window.location.replace("home.html");
    }
  } catch {
    showLoginError("Sign in is temporarily unavailable. Please try again later.");
  }
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (loginButton.disabled) return;

  errorMessageDiv.style.display = "none";
  successMessageDiv.classList.add("hidden");
  setLoginPending(true);

  try {
    const { data, error } = await signIn({
      email: emailInput.value.trim().toLowerCase(),
      password: passwordInput.value,
      rememberUser: rememberMeCheckbox.checked,
      captchaToken: readCaptchaToken(loginForm),
    });
    if (error) throw error;
    if (!data.session) throw new Error("No usable session");

    successMessageDiv.textContent = "Login successful. Redirecting...";
    successMessageDiv.classList.remove("hidden");
    window.setTimeout(() => window.location.replace("home.html"), 500);
  } catch (error) {
    if (error?.status === 429) {
      showLoginError("Too many sign-in attempts. Please wait and try again.");
    } else {
      showLoginError("Invalid email or password.");
    }
  } finally {
    setLoginPending(false);
  }
});

function openResetModal() {
  resetModalMessage.textContent =
    "Enter your email address to receive a password reset link.";
  resetModalMessage.classList.remove("text-green-400");
  resetEmailInput.style.display = "block";
  cancelResetBtn.style.display = "inline-flex";
  sendResetBtn.textContent = "Send Link";
  sendResetBtn.disabled = false;
  resetEmailInput.value = emailInput.value.trim();
  resetErrorMessage.classList.add("hidden");
  resetModal.classList.remove("hidden");
  resetModal.classList.add("flex");
  window.setTimeout(
    () => resetModal.querySelector(".modal-content").classList.remove("scale-95"),
    10
  );
}

function closeResetModal() {
  resetModal.querySelector(".modal-content").classList.add("scale-95");
  window.setTimeout(() => {
    resetModal.classList.add("hidden");
    resetModal.classList.remove("flex");
  }, 300);
}

forgotPasswordLink.addEventListener("click", (event) => {
  event.preventDefault();
  openResetModal();
});
cancelResetBtn.addEventListener("click", closeResetModal);
resetModal.addEventListener("click", (event) => {
  if (event.target === resetModal) closeResetModal();
});

sendResetBtn.addEventListener("click", async () => {
  if (sendResetBtn.textContent === "Close") {
    closeResetModal();
    return;
  }

  const email = resetEmailInput.value.trim().toLowerCase();
  if (!email) {
    resetErrorMessage.textContent = "Please enter an email address.";
    resetErrorMessage.classList.remove("hidden");
    return;
  }

  sendResetBtn.disabled = true;
  sendResetBtn.textContent = "Sending...";
  resetErrorMessage.classList.add("hidden");
  try {
    const { error } = await requestPasswordReset(
      email,
      readCaptchaToken(loginForm)
    );
    if (error?.status === 429) {
      resetErrorMessage.textContent =
        "Too many requests. Please wait before trying again.";
      resetErrorMessage.classList.remove("hidden");
      return;
    }
    resetModalMessage.textContent =
      "If an account exists for this email, a reset link will arrive shortly.";
    resetModalMessage.classList.add("text-green-400");
    resetEmailInput.style.display = "none";
    cancelResetBtn.style.display = "none";
    sendResetBtn.textContent = "Close";
  } catch {
    resetModalMessage.textContent =
      "If an account exists for this email, a reset link will arrive shortly.";
    resetModalMessage.classList.add("text-green-400");
    resetEmailInput.style.display = "none";
    cancelResetBtn.style.display = "none";
    sendResetBtn.textContent = "Close";
  } finally {
    sendResetBtn.disabled = false;
  }
});

redirectAuthenticatedUser();
