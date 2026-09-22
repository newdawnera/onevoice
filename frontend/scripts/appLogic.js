import {
  getProfileDisplayName,
  onAuthStateChange,
  requireAuthenticatedUser,
  signOut,
} from "./authService.js";
import { stopAllSubscriptions } from "./dataService.js";

function initializeSidebar() {
  const menuButton = document.getElementById("menu-button");
  const mobileMenu = document.getElementById("mobile-menu");
  const overlay = document.getElementById("overlay");

  menuButton?.addEventListener("click", (event) => {
    event.stopPropagation();
    mobileMenu?.classList.toggle("-translate-x-full");
    overlay?.classList.toggle("hidden");
  });
  overlay?.addEventListener("click", () => {
    mobileMenu?.classList.add("-translate-x-full");
    overlay.classList.add("hidden");
  });
}

function redirectToLogin() {
  if (!window.location.pathname.endsWith("/login.html")) {
    window.location.replace("login.html");
  }
}

function renderWelcome(displayName) {
  const container = document.getElementById("welcome-message");
  if (!container) return;
  container.replaceChildren();
  const strong = document.createElement("strong");
  strong.append("Welcome, ");
  const name = document.createElement("span");
  name.style.textTransform = "uppercase";
  name.textContent = displayName;
  strong.appendChild(name);
  container.appendChild(strong);
}

async function displayNameFor(user) {
  const profileName = await getProfileDisplayName(user).catch(() => null);
  const metadataName = user.user_metadata?.display_name;
  return (
    profileName ||
    (typeof metadataName === "string" ? metadataName.trim() : "") ||
    user.email ||
    "User"
  );
}

function initializeLogout(user) {
  const button = document.getElementById("logout-btn");
  if (!button) return;
  button.addEventListener("click", async (event) => {
    event.preventDefault();
    if (button.disabled) return;
    button.disabled = true;
    localStorage.removeItem(`aiMeetingWizardState_${user.id}`);
    stopAllSubscriptions();
    try {
      await signOut();
    } finally {
      window.location.replace("index.html");
    }
  });
}

export async function initializeApp(pageSpecificLogic) {
  initializeSidebar();

  let user;
  try {
    user = await requireAuthenticatedUser();
  } catch {
    redirectToLogin();
    return;
  }
  if (!user) {
    redirectToLogin();
    return;
  }

  renderWelcome(await displayNameFor(user));
  initializeLogout(user);

  if (pageSpecificLogic) {
    await pageSpecificLogic(user);
  }

  const unsubscribe = onAuthStateChange((event, session) => {
    queueMicrotask(() => {
      if (event === "SIGNED_OUT" || !session) {
        stopAllSubscriptions();
        redirectToLogin();
      } else if (event === "PASSWORD_RECOVERY") {
        window.location.replace("reset-password.html");
      }
      // INITIAL_SESSION, SIGNED_IN and TOKEN_REFRESHED require no page reset;
      // the centralized API client reads the current session for each request.
    });
  });
  window.addEventListener(
    "pagehide",
    () => {
      stopAllSubscriptions();
      unsubscribe();
    },
    { once: true }
  );
}
