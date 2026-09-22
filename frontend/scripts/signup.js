import {
  requireAuthenticatedUser,
  signUp,
} from "./authService.js";
import {
  createCaptchaWidget,
  isCaptchaFailure,
} from "./captchaService.js";

const signupForm = document.getElementById("the-signup-form");
const signupBtn = document.getElementById("signup-btn");
const errorDiv = document.getElementById("error-box");
const successDiv = document.getElementById("success-popup");
const signupCaptcha = createCaptchaWidget({
  container: document.getElementById("signup-captcha"),
  statusElement: document.getElementById("signup-captcha-status"),
  tokenInput: signupForm.querySelector('[name="captcha-token"]'),
});

function setPending(pending) {
  signupBtn.disabled = pending;
  signupBtn.textContent = pending ? "Working on it..." : "Sign Up";
}

function displayError(message) {
  errorDiv.textContent = message;
  errorDiv.style.display = "block";
}

function displaySuccess(message) {
  successDiv.textContent = message;
  successDiv.classList.remove("hidden");
}

async function redirectAuthenticatedUser() {
  try {
    if (await requireAuthenticatedUser()) {
      window.location.replace("home.html");
    }
  } catch {
    displayError("Sign up is temporarily unavailable. Please try again later.");
  }
}

signupForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (signupBtn.disabled) return;

  errorDiv.style.display = "none";
  successDiv.classList.add("hidden");

  const displayName = document.getElementById("username").value.trim();
  const email = document.getElementById("email").value.trim().toLowerCase();
  const password = document.getElementById("password").value;
  const passwordConfirmation = document.getElementById("confirm-password").value;

  if (!displayName || displayName.length > 100) {
    displayError("Please enter a display name of 100 characters or fewer.");
    return;
  }
  if (password !== passwordConfirmation) {
    displayError("Passwords do not match.");
    return;
  }
  if (password.length < 8) {
    displayError("Use a password with at least 8 characters.");
    return;
  }

  try {
    await signupCaptcha.mount();
  } catch {
    displayError(
      "Security check is unavailable. Check your connection and refresh the page."
    );
    return;
  }

  const captchaToken = signupCaptcha.getToken();
  if (!captchaToken) {
    displayError("Please complete the security check before signing up.");
    return;
  }

  setPending(true);
  try {
    const { data, error } = await signUp({
      displayName,
      email,
      password,
      captchaToken,
    });
    if (error) throw error;

    if (data.session) {
      displaySuccess("Registration successful. Redirecting...");
      window.setTimeout(() => window.location.replace("home.html"), 800);
    } else {
      displaySuccess(
        "If the address can be registered, a confirmation email will arrive shortly. Please check your inbox."
      );
      signupForm.reset();
    }
  } catch (error) {
    if (error?.status === 429) {
      displayError("Too many attempts. Please wait before trying again.");
    } else if (isCaptchaFailure(error)) {
      displayError("Security check failed or expired. Please try again.");
    } else if (error?.code === "email_address_invalid") {
      displayError("Please enter a valid email address.");
    } else {
      displayError("Sign up could not be completed. Please try again.");
    }
  } finally {
    signupCaptcha.reset();
    setPending(false);
  }
});

signupCaptcha.mount().catch(() => {
  displayError(
    "Security check is unavailable. Check your connection and refresh the page."
  );
});
redirectAuthenticatedUser();
