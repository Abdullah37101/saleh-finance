const buttons = document.querySelectorAll(".btn:not(.btn-outline-danger)");
const navbar = document.getElementById("nav_bar");

function toggleMode() {
  document.body.getAttribute("data-bs-theme")
    ? document.body.removeAttribute("data-bs-theme")
    : document.body.setAttribute("data-bs-theme", "dark");
    
  buttons.forEach(element => element.classList.toggle("btn-outline-dark"));
}

if (!window.matchMedia("(prefers-color-scheme: dark)").matches)
  toggleMode();

document.getElementById("themeSwitch").addEventListener("click", toggleMode);
