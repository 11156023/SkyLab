import { ref, watch } from "vue";

function saved(key: string, fallback: string) {
  try {
    return localStorage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
}
/* 預設亮色，與 web 端一致；使用者切過深色才記住 */
export const theme = ref(
  saved("skylab.theme", "light") === "dark" ? "dark" : "light"
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
