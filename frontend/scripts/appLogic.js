import { auth, db } from "./firebaseInit.js";
import {
  onAuthStateChanged,
  signOut,
} from "https://www.gstatic.com/firebasejs/11.9.1/firebase-auth.js";

function initializeSidebar() {
  const menuButton = document.getElementById("menu-button");
  const mobileMenu = document.getElementById("mobile-menu");
  const overlay = document.getElementById("overlay");

  if (menuButton) {
    menuButton.addEventListener("click", (e) => {
      e.stopPropagation();
      mobileMenu.classList.toggle("-translate-x-full");
      overlay.classList.toggle("hidden");
    });
  }

  if (overlay) {
    overlay.addEventListener("click", () => {
      mobileMenu.classList.add("-translate-x-full");
      overlay.classList.add("hidden");
    });
  }
}

function initializeLogout() {
  const logoutBtn = document.getElementById("logout-btn");
  if (logoutBtn) {
    logoutBtn.addEventListener("click", (e) => {
      e.preventDefault();
      signOut(auth)
        .then(() => {
          console.log("Sign-out successful, redirecting.");
          window.location.href = "index.html";
        })
        .catch((error) => {
          console.error("Sign-out failed:", error);
        });
    });
  }
}

export function initializeApp(pageSpecificLogic) {
  initializeSidebar();
  initializeLogout();

  const welcomeMessage = document.getElementById("welcome-message");

  onAuthStateChanged(auth, (user) => {
    if (user) {
      const userName = user.displayName || user.email;
      if (welcomeMessage) {
        welcomeMessage.innerHTML = `<strong>Welcome, <span style="text-transform: uppercase;">${userName}</span></strong>`;
      }

      if (pageSpecificLogic) {
        pageSpecificLogic(user, db);
      }
    } else {
      console.log("No user found, redirecting to login.");
      window.location.href = "login.html";
    }
  });
}
