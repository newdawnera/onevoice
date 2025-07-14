import { auth } from "./firebaseInit.js";
import {
  signInWithEmailAndPassword,
  setPersistence,
  browserLocalPersistence,
  browserSessionPersistence,
  sendPasswordResetEmail,
} from "https://www.gstatic.com/firebasejs/11.9.1/firebase-auth.js";

const loginForm = document.getElementById("login-form");
const emailInput = document.getElementById("email");
const passwordInput = document.getElementById("password");
const rememberMeCheckbox = document.getElementById("remember-me");
const loginButton = document.getElementById("login-button");
const errorMessageDiv = document.getElementById("error-message");
const successMessageDiv = document.getElementById("success-message");
const forgotPasswordLink = document.getElementById("forgot-password-link");

// Modal Elements
const resetModal = document.getElementById("reset-modal");
const resetEmailInput = document.getElementById("reset-email-input");
const cancelResetBtn = document.getElementById("cancel-reset-btn");
const sendResetBtn = document.getElementById("send-reset-btn");
const resetErrorMessage = document.getElementById("reset-error-message");
const resetModalMessage = document.getElementById("reset-modal-message");

loginForm.addEventListener("submit", async (e) => {
  e.preventDefault();

  loginButton.disabled = true;
  loginButton.textContent = "Signing In...";
  errorMessageDiv.style.display = "none";

  const email = emailInput.value;
  const password = passwordInput.value;

  try {
    const persistence = rememberMeCheckbox.checked
      ? browserLocalPersistence
      : browserSessionPersistence;
    await setPersistence(auth, persistence);

    const userCredential = await signInWithEmailAndPassword(
      auth,
      email,
      password
    );
    const user = userCredential.user;

    // console.log("Successfully logged in:", user.uid);

    showSuccess("Login successful! Redirecting...");

    setTimeout(() => {
      window.location.href = "home.html";
    }, 2000);
  } catch (error) {
    console.error("Login failed:", error);
    let friendlyMessage = "An unexpected error occurred. Please try again.";
    switch (error.code) {
      case "auth/invalid-email":
        friendlyMessage = "Please enter a valid email address.";
        break;
      case "auth/user-not-found":
      case "auth/wrong-password":
      case "auth/invalid-credential":
        friendlyMessage =
          "Invalid email or password. Please check your credentials and try again.";
        break;
      case "auth/too-many-requests":
        friendlyMessage =
          "Access to this account has been temporarily disabled due to many failed login attempts. You can immediately restore it by resetting your password or you can try again later.";
        break;
    }
    errorMessageDiv.textContent = friendlyMessage;
    errorMessageDiv.style.display = "block";

    loginButton.disabled = false;
    loginButton.textContent = "Sign In";
  }
});

function showSuccess(message) {
  successMessageDiv.textContent = message;
  successMessageDiv.classList.remove("hidden");
}

const openResetModal = () => {
  resetModalMessage.textContent =
    "Please enter your email address to receive a password reset link.";
  resetModalMessage.classList.remove("text-green-400");
  resetEmailInput.style.display = "block";
  cancelResetBtn.style.display = "inline-flex";
  sendResetBtn.textContent = "Send Link";
  sendResetBtn.disabled = false;
  resetEmailInput.value = emailInput.value;
  resetErrorMessage.classList.add("hidden");
  resetModal.classList.remove("hidden");
  resetModal.classList.add("flex");
  setTimeout(() => {
    resetModal.querySelector(".modal-content").classList.remove("scale-95");
  }, 10);
};

const closeResetModal = () => {
  resetModal.querySelector(".modal-content").classList.add("scale-95");
  setTimeout(() => {
    resetModal.classList.add("hidden");
    resetModal.classList.remove("flex");
  }, 300);
};

forgotPasswordLink.addEventListener("click", (e) => {
  e.preventDefault();
  openResetModal();
});

cancelResetBtn.addEventListener("click", closeResetModal);
resetModal.addEventListener("click", (e) => {
  if (e.target === resetModal) {
    closeResetModal();
  }
});

sendResetBtn.addEventListener("click", async () => {
  if (sendResetBtn.textContent === "Close") {
    closeResetModal();
    return;
  }

  const email = resetEmailInput.value;
  if (!email) {
    resetErrorMessage.textContent = "Please enter an email address.";
    resetErrorMessage.classList.remove("hidden");
    return;
  }

  sendResetBtn.disabled = true;
  sendResetBtn.textContent = "Sending...";
  resetErrorMessage.classList.add("hidden");

  try {
    await sendPasswordResetEmail(auth, email);
    resetModalMessage.textContent =
      "If an account exists for this email, a password reset link has been sent. Please check your inbox.";
    resetModalMessage.classList.add("text-green-400");
    resetEmailInput.style.display = "none";
    cancelResetBtn.style.display = "none";
    sendResetBtn.textContent = "Close";
    sendResetBtn.disabled = false;
  } catch (error) {
    console.error("Password reset failed:", error);
    let friendlyMessage =
      "Could not send password reset email. Please try again.";
    if (error.code === "auth/invalid-email") {
      friendlyMessage = "The email address you entered is not valid.";
    }
    resetErrorMessage.textContent = friendlyMessage;
    resetErrorMessage.classList.remove("hidden");
    sendResetBtn.disabled = false;
    sendResetBtn.textContent = "Send Link";
  }
});
