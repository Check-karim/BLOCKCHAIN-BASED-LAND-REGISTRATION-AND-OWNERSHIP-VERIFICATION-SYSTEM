function bindNavigation() {
  document.querySelectorAll("[data-nav-toggle]").forEach((button) => {
    const nav = button.parentElement.querySelector("[data-nav]");
    if (!nav) return;
    button.addEventListener("click", () => {
      const open = nav.classList.toggle("is-open");
      button.setAttribute("aria-expanded", open ? "true" : "false");
    });
  });
}

function bindLocations() {
  document.querySelectorAll("select[data-province]").forEach((province) => {
    const form = province.closest("form");
    const district = form && form.querySelector("[data-district]");
    if (!district) return;
    const apply = () => {
      const selected = province.value;
      const allowAll = province.dataset.allowAll === "true";
      let keep = false;
      Array.from(district.options).forEach((option) => {
        if (!option.value) return;
        const match = (allowAll && !selected) || option.dataset.province === selected;
        option.disabled = !match;
        option.hidden = !match;
        if (match && option.value === district.value) keep = true;
      });
      if (!keep) district.value = "";
    };
    province.addEventListener("change", apply);
    apply();
  });
}

bindNavigation();
bindLocations();
