const docsSections = [...document.querySelectorAll("[data-doc-section]")];
const docsSearch = document.querySelector("#docsSearch");
const themeSelect = document.querySelector("#themeSelect");

function applyDocsTheme(theme) {
  const allowed = ["graphite", "cloud", "contrast"];
  const selected = allowed.includes(theme) ? theme : "graphite";
  document.documentElement.dataset.theme = selected;
  localStorage.setItem("codex-relay-theme", selected);
  themeSelect.value = selected;
}

function filterDocs() {
  const query = docsSearch.value.trim().toLocaleLowerCase();
  let visible = 0;
  docsSections.forEach(section => {
    const haystack = `${section.dataset.keywords || ""} ${section.textContent}`.toLocaleLowerCase();
    const match = !query || haystack.includes(query);
    section.hidden = !match;
    if (match) visible += 1;
  });
  document.querySelector("#docsEmpty").hidden = visible > 0;
}

function updateActiveNavigation() {
  const visible = docsSections
    .filter(section => !section.hidden)
    .map(section => ({section, distance: Math.abs(section.getBoundingClientRect().top - 110)}))
    .sort((a, b) => a.distance - b.distance)[0]?.section;
  document.querySelectorAll(".docs-sidebar a").forEach(link => {
    link.classList.toggle("active", visible && link.hash === `#${visible.id}`);
  });
}

applyDocsTheme(localStorage.getItem("codex-relay-theme") || "graphite");
docsSearch.addEventListener("input", filterDocs);
themeSelect.addEventListener("change", event => applyDocsTheme(event.target.value));
document.addEventListener("scroll", updateActiveNavigation, {passive: true});
updateActiveNavigation();
