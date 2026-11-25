// Μικρές βελτιώσεις UI (η εφαρμογή δουλεύει και χωρίς JS). Χωρίς inline scripts λόγω CSP.
document.addEventListener("DOMContentLoaded", () => {
  const okColor = (c) => /^#[0-9a-fA-F]{6}$/.test(c || "");
  // Χρώμα μαθήματος ως CSS variable (--c)
  document.querySelectorAll("[data-color]").forEach((el) => {
    if (okColor(el.dataset.color)) el.style.setProperty("--c", el.dataset.color);
  });
  // Μπάρες και δαχτυλίδια προόδου
  document.querySelectorAll("[data-pct]").forEach((el) => {
    const pct = Math.max(0, Math.min(100, Number(el.dataset.pct) || 0));
    if (el.classList.contains("ring")) el.style.setProperty("--p", pct);
    else requestAnimationFrame(() => { el.style.width = pct + "%"; });
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
  // Αποφυγή διπλού submit (χωρίς να χάνεται η τιμή του κουμπιού που πατήθηκε)
  document.querySelectorAll("form").forEach((f) => f.addEventListener("submit", (e) => {
    if (e.defaultPrevented) return;
    setTimeout(() => f.querySelectorAll("button[type=submit]").forEach((b) => { b.disabled = true; }), 0);
  }));
  // Άνοιγμα της σημερινής ημέρας στο πλάνο
  const today = document.querySelector(".trow.is-today");
  if (today && !location.hash) today.scrollIntoView({ block: "center" });
});
