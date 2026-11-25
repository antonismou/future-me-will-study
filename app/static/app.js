// Μικρές βελτιώσεις UI (η εφαρμογή δουλεύει και χωρίς JS). Χωρίς inline scripts λόγω CSP.
document.addEventListener("DOMContentLoaded", () => {
  const okColor = (c) => /^#[0-9a-fA-F]{6}$/.test(c || "");
  document.querySelectorAll(".bar span[data-pct]").forEach((el) => {
    const pct = Math.max(0, Math.min(100, Number(el.dataset.pct) || 0));
    requestAnimationFrame(() => { el.style.width = pct + "%"; });
    if (okColor(el.dataset.color)) el.style.background = el.dataset.color;
  });
  document.querySelectorAll("[data-color]").forEach((el) => {
    if (!okColor(el.dataset.color) || el.matches(".bar span")) return;
    if (el.classList.contains("course-title")) el.style.borderLeftColor = el.dataset.color;
    else el.style.color = el.dataset.color;
  });
  document.querySelectorAll("form[data-confirm]").forEach((f) => {
    f.addEventListener("submit", (e) => { if (!confirm(f.dataset.confirm)) e.preventDefault(); });
  });
  const count = document.querySelector("[data-evening-count]");
  if (count) {
    const sync = () => document.querySelectorAll("[data-evening-slot]").forEach((el) => {
      el.hidden = Number(el.dataset.eveningSlot) >= Number(count.value);
    });
    count.addEventListener("change", sync);
    sync();
  }
  // Αποφυγή διπλού submit
  document.querySelectorAll("form").forEach((f) => f.addEventListener("submit", (e) => {
    if (e.defaultPrevented) return;
    setTimeout(() => f.querySelectorAll("button[type=submit]").forEach((b) => { b.disabled = true; }), 0);
  }));
});
