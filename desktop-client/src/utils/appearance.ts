import { ref, watch } from "vue";

function saved(key: string, fallback: string) {
  try {
    return localStorage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
}
export const theme = ref(
  saved("skylab.theme", "dark") === "light" ? "light" : "dark"
);
export const resourceView = ref(
  saved("skylab.resource-view", "grid") === "list" ? "list" : "grid"
);
watch(
  theme,
  value => {
    document.documentElement.dataset.theme = value;
    document.documentElement.classList.toggle("dark", value === "dark");
    try {
      localStorage.setItem("skylab.theme", value);
    } catch {
      /* Session preference still applies. */
    }
  },
  { immediate: true }
);
watch(resourceView, value => {
  try {
    localStorage.setItem("skylab.resource-view", value);
  } catch {
    /* Session preference still applies. */
  }
});
export const toggleTheme = () => {
  theme.value = theme.value === "dark" ? "light" : "dark";
};
