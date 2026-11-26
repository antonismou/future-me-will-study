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
  // Ζωντανός μετρητής χρονομέτρου
  const timers = document.querySelectorAll("[data-since]");
  if (timers.length) {
    const fmt = (sec) => {
      const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
      return (h ? h + ":" + String(m).padStart(2, "0") : String(m)) + ":" + String(s).padStart(2, "0");
    };
    const tick = () => timers.forEach((el) => {
      const t0 = Date.parse(el.dataset.since);
      if (!Number.isNaN(t0)) el.textContent = fmt(Math.max(0, Math.floor((Date.now() - t0) / 1000)));
    });
    tick();
    setInterval(tick, 1000);
  }
  // Ύψος στηλών γραφήματος
  document.querySelectorAll(".col-bar[data-h]").forEach((el) => {
    const h = Math.max(0, Math.min(100, Number(el.dataset.h) || 0));
    requestAnimationFrame(() => { el.style.height = h + "%"; if (h > 0) el.style.minHeight = "2px"; });
  });
  // Αντιγραφή link: το κουμπί εμφανίζεται μόνο όπου υπάρχει clipboard (https ή localhost)
  document.querySelectorAll("[data-select]").forEach((el) => el.addEventListener("focus", () => el.select()));
  if (navigator.clipboard) {
    document.querySelectorAll("[data-copy]").forEach((b) => {
      b.hidden = false;
      b.addEventListener("click", () => navigator.clipboard.writeText(b.dataset.copy)
        .then(() => { b.textContent = "Αντιγράφηκε"; }, () => { b.textContent = "Δεν αντιγράφηκε"; }));
    });
  }
  // Εκτύπωση
  document.querySelectorAll("[data-print]").forEach((b) => b.addEventListener("click", () => window.print()));
  // Άνοιγμα της σημερινής ημέρας στο πλάνο
  const today = document.querySelector(".trow.is-today");
  if (today && !location.hash) today.scrollIntoView({ block: "center" });
});
