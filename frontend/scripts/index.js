VANTA.HALO({
  el: "#vanta-canvas",
  mouseControls: true,
  touchControls: true,
  gyroControls: false,
  minHeight: 200.0,
  minWidth: 200.0,
  amplitudeFactor: 3.0,
  size: 0.5,
  backgroundColor: 0x111827,
  baseColor: 0x6366f1,
});

document.addEventListener("DOMContentLoaded", function () {
  const welcomeMessages = [
    "Welcome to Ally, your AI Meeting Wizard!",
    "The Only Tool That Closes The Loop Between Notes & Action",
    "From Role-based Summaries to trackable Action Items - Ally got you covered.",
    "Effortlessly transform your meeting recordings and notes into summaries, action items, and insights.",
    "Upload a file, record or type notes to get started.",
  ];

  let messageIndex = 0;
  const welcomeElement = document.getElementById("welcome-message");

  if (welcomeElement) {
    welcomeElement.textContent = welcomeMessages[messageIndex];
  }

  const changeWelcomeMessage = () => {
    messageIndex = (messageIndex + 1) % welcomeMessages.length;
    if (welcomeElement) {
      welcomeElement.textContent = welcomeMessages[messageIndex];
    }
  };

  setInterval(changeWelcomeMessage, 5000);
});

tailwind.config = {
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "sans-serif"],
      },
      colors: {
        "ally-dark": "#111827",
        "ally-card": "#1f2937",
        "ally-border": "#374151",
        "ally-purple": "#8b5cf6",
        "ally-blue": "#3b82f6",
        "ally-green": "#22c55e",
        "ally-red": "#ef4444",
        "ally-gold": "#facc15",
      },
    },
  },
};

document.addEventListener("DOMContentLoaded", () => {
  const showButton = document.getElementById("show-how-it-works");
  const closeButton = document.getElementById("close-panel-btn");
  const panel = document.getElementById("how-it-works-panel");
  const body = document.body;

  const openPanel = (e) => {
    e.preventDefault();
    panel.classList.add("is-open");
    body.classList.add("body-no-scroll");
  };

  const closePanel = () => {
    panel.classList.remove("is-open");
    body.classList.remove("body-no-scroll");
  };

  showButton.addEventListener("click", openPanel);
  closeButton.addEventListener("click", closePanel);

  document.addEventListener("click", (e) => {
    if (
      panel.classList.contains("is-open") &&
      !panel.contains(e.target) &&
      !e.target.closest("#show-how-it-works")
    ) {
      closePanel();
    }
  });
});
