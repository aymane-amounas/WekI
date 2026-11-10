"use strict";
(function () {
  const KEY = "wk-theme";
  const THEMES = ["auto", "light", "dark"];

  const VECTOR = { auto: "skin-theme-clientpref-os", light: "skin-theme-clientpref-day", dark: "skin-theme-clientpref-night" };

  function stored() {
    try {
      const value = localStorage.getItem(KEY);
      return THEMES.includes(value) ? value : "auto";
    } catch {
      return "auto";
    }
  }

  function apply(theme) {
    const root = document.documentElement;
    if (theme === "auto") delete root.dataset.theme;
    else root.dataset.theme = theme;
    root.classList.remove(...Object.values(VECTOR));
    root.classList.add(VECTOR[theme]);
  }

  function label(button, theme) {
    button.textContent = `Theme: ${theme}`;
    button.setAttribute("aria-label", `Theme: ${theme}. Switch theme`);
  }

  let current = stored();
  apply(current);

  document.addEventListener("DOMContentLoaded", () => {
    for (const button of document.querySelectorAll("[data-theme-toggle]")) {
      label(button, current);
      button.hidden = false;
      button.addEventListener("click", () => {
        current = THEMES[(THEMES.indexOf(current) + 1) % THEMES.length];
        try {
          localStorage.setItem(KEY, current);
        } catch {
        }
        apply(current);
        document.querySelectorAll("[data-theme-toggle]").forEach((b) => label(b, current));
      });
    }
  });
})();
