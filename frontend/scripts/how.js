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
  let lines = [];
  const isLg = () => window.innerWidth >= 1024;
  const isMd = () => window.innerWidth >= 768 && window.innerWidth < 1024;
  const isSm = () => window.innerWidth < 768;

  const lineOptions = {
    color: "rgba(107, 114, 128, 0.7)",
    size: 3,
    path: "fluid",
    dash: { animation: true, len: 6, gap: 8 },
    endPlug: "arrow3",
    endPlugSize: 1.5,
  };

  function drawLines() {
    lines.forEach((line) => line.remove());
    lines = [];
    const connections = [
      { start: "step-1", end: "step-2" },
      { start: "step-2", end: "step-3" },
      { start: "step-3", end: "step-4" },
      { start: "step-4", end: "step-5" },
      { start: "step-5", end: "step-6" },
    ];
    connections.forEach((conn) => {
      const startEl = document.getElementById(conn.start);
      const endEl = document.getElementById(conn.end);
      if (startEl && endEl) {
        try {
          let startSocket = "right",
            endSocket = "left";
          if (isSm()) {
            startSocket = "bottom";
            endSocket = "top";
          } else if (isMd() && ["step-2", "step-4"].includes(conn.start)) {
            startSocket = "bottom";
            endSocket = "top";
          } else if (isLg() && conn.start === "step-3") {
            startSocket = "bottom";
            endSocket = "top";
          }
          lines.push(
            new LeaderLine(startEl, endEl, {
              ...lineOptions,
              startSocket,
              endSocket,
            })
          );
        } catch (e) {
          console.error("Could not draw line:", e);
        }
      }
    });
  }
  setTimeout(drawLines, 100);
  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(drawLines, 250);
  });
});
