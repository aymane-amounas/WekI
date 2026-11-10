"use strict";

function setupSuggest(input) {
  const list = input.parentElement.querySelector(".wk-suggest");
  let items = [];
  let active = -1;
  let request = 0;

  const close = () => {
    list.hidden = true;
    active = -1;
  };

  const show = () => {
    list.replaceChildren(
      ...items.map((item, index) => {
        const li = document.createElement("li");
        const a = document.createElement("a");
        a.href = item.url;
        a.textContent = item.title;
        if (index === active) a.setAttribute("aria-current", "true");
        li.append(a);
        return li;
      }),
    );
    list.hidden = items.length === 0;
  };

  input.addEventListener("input", async () => {
    const q = input.value.trim();
    const id = ++request;
    if (!q) {
      close();
      return;
    }
    const resp = await fetch(`/suggest?q=${encodeURIComponent(q)}`);
    if (!resp.ok || id !== request) return;
    const data = await resp.json();
    items = data.map((hit) => ({
      title: hit.title,
      url: "/wiki/" + hit.path.split("/").map(encodeURIComponent).join("/"),
    }));
    active = -1;
    show();
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") return close();
    if (list.hidden) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      active = (active + step + items.length) % items.length;
      show();
    } else if (event.key === "Enter" && active >= 0) {
      event.preventDefault();
      location.href = items[active].url;
    }
  });

  input.addEventListener("blur", () => setTimeout(close, 150));
}

function setupSync(button) {
  const state = document.querySelector("[data-sync-state]");

  const refresh = async () => {
    const resp = await fetch("/status.json");
    if (!resp.ok) return;
    const status = await resp.json();
    for (const field of document.querySelectorAll("[data-field]")) {
      const value = status[field.dataset.field];
      if (value === null || value === undefined) continue;
      field.textContent = typeof value === "number" ? value.toLocaleString("en-US") : value;
    }
    button.disabled = status.running;
    state.textContent = status.running
      ? status.total
        ? `${status.phase}: ${status.done} of ${status.total}`
        : status.phase
      : "";
    if (status.running) setTimeout(refresh, 1000);
  };

  button.addEventListener("click", async () => {
    button.disabled = true;
    await fetch("/sync", { method: "POST" });
    refresh();
  });
  refresh();
}

document.querySelectorAll("[data-suggest]").forEach(setupSuggest);
const syncButton = document.querySelector("[data-sync]");
if (syncButton) setupSync(syncButton);
