// The text menu (Aa), shared by the library (index.html) and the reader (read.html): text
// and interface sizes, the font, and the theme.
{
  const sizeMenu = document.getElementById("size-menu");

  // Theme: automatic (the system's choice), light or dark, remembered per browser. Each
  // page applies the stored theme before it draws (a script in its <head>).
  const systemDark = window.matchMedia?.("(prefers-color-scheme: dark)");
  function showTheme() {
    const mode = document.documentElement.dataset.theme ?? "auto";
    for (const b of sizeMenu.querySelectorAll(".theme-choice")) b.setAttribute("aria-pressed", String(b.dataset.theme === mode));
    // The phone's status bar (installed app) takes the page's background colour.
    const dark = mode === "dark" || (mode === "auto" && !!systemDark?.matches);
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", dark ? "#1d1a16" : "#fdf6e3");
  }
  systemDark?.addEventListener?.("change", showTheme);
  sizeMenu.addEventListener("click", (e) => {
    const choice = e.target.closest(".theme-choice");
    if (!choice) return;
    const mode = choice.dataset.theme;
    if (mode === "auto") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = mode;
    try { localStorage.setItem("theme", mode); } catch {}
    showTheme();
  });
  showTheme();

  // Text size menu (Aa): the reader's text (verses, card, scholia) and the interface (the
  // bars; all of the library), each a step smaller or larger, remembered per browser.
  // Each page's <head> applies the stored sizes before it draws; a change fires "sizechange".
  const SIZES = [0.8, 0.9, 1, 1.1, 1.2, 1.35, 1.5, 1.7];
  const sizePicker = document.getElementById("size-picker");
  const sizeButton = document.getElementById("size-button");

  function sizeOf(row) {
    const v = parseFloat(document.documentElement.style.getPropertyValue(row.dataset.prop)) || 1;
    // The nearest step, in case the stored value isn't one.
    return SIZES.reduce((a, b) => (Math.abs(b - v) < Math.abs(a - v) ? b : a));
  }
  function showSizes() {
    for (const row of sizeMenu.querySelectorAll(".size-row")) {
      const v = sizeOf(row);
      row.querySelector(".size-value").textContent = `${Math.round(v * 100)}%`;
      row.querySelector('[data-step="-1"]').disabled = v === SIZES[0];
      row.querySelector('[data-step="1"]').disabled = v === SIZES.at(-1);
    }
  }
  sizeMenu.addEventListener("click", (e) => {
    const step = e.target.closest(".size-step");
    if (!step) return;
    const row = step.closest(".size-row");
    const i = SIZES.indexOf(sizeOf(row)) + Number(step.dataset.step);
    const v = SIZES[Math.min(Math.max(i, 0), SIZES.length - 1)];
    document.documentElement.style.setProperty(row.dataset.prop, v);
    try { localStorage.setItem(row.dataset.key, String(v)); } catch {}
    showSizes();
    document.dispatchEvent(new Event("sizechange"));
  });
  // The font for the Greek: Gentium Book Plus (the default), EB Garamond or GFS Didot.
  function showFont() {
    const font = document.documentElement.dataset.font ?? "gentium";
    for (const b of sizeMenu.querySelectorAll(".font-choice")) b.setAttribute("aria-pressed", String(b.dataset.font === font));
  }
  sizeMenu.addEventListener("click", (e) => {
    const choice = e.target.closest(".font-choice");
    if (!choice) return;
    const font = choice.dataset.font;
    if (font === "gentium") delete document.documentElement.dataset.font;
    else document.documentElement.dataset.font = font;
    try { localStorage.setItem("font", font); } catch {}
    showFont();
    document.dispatchEvent(new Event("sizechange"));
    // A web font is drawn once it has loaded: refit again then.
    document.fonts?.ready.then(() => document.dispatchEvent(new Event("sizechange")));
  });
  function closeSizes() {
    if (sizeMenu.hidden) return;
    sizeMenu.hidden = true;
    sizeButton.setAttribute("aria-expanded", "false");
  }
  sizeButton.addEventListener("click", () => {
    if (!sizeMenu.hidden) return closeSizes();
    showSizes();
    showFont();
    sizeMenu.hidden = false;
    // Keep it on screen: it hangs from the button's right edge, so on a narrow screen
    // move it right (into the space beside the button) rather than off the left edge.
    sizeMenu.style.right = "";
    const r = sizeMenu.getBoundingClientRect();
    const shift = Math.min(Math.max(0, 8 - r.left), Math.max(0, innerWidth - 8 - r.right));
    const zoom = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--ui-scale")) || 1;
    if (shift) sizeMenu.style.right = `${-shift / zoom}px`;
    sizeButton.setAttribute("aria-expanded", "true");
  });
  document.addEventListener("pointerdown", (e) => { if (!sizePicker.contains(e.target)) closeSizes(); });
  sizePicker.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !sizeMenu.hidden) { e.preventDefault(); closeSizes(); sizeButton.focus(); }
  });
}
