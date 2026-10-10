import { computed, ref, watch } from "vue";

function saved(key: string, fallback: string) {
  try {
    return localStorage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
}

function persist(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* Session preference still applies. */
  }
}

/* 明暗模式：淺色／深色／系統，預設跟隨系統（同 web 的 THEME_DEFAULTS.mode） */
export type ThemePreference = "light" | "dark" | "system";

const storedTheme = saved("skylab.theme", "system");
export const theme = ref<ThemePreference>(
  storedTheme === "light" || storedTheme === "dark" ? storedTheme : "system"
);

/* Windows 的明暗設定：Electron 沒設 nativeTheme.themeSource，prefers-color-scheme 直接反映系統，切換時即時跟著變 */
const systemDarkQuery = window.matchMedia("(prefers-color-scheme: dark)");
const systemTheme = ref<"light" | "dark">(
  systemDarkQuery.matches ? "dark" : "light"
);
systemDarkQuery.addEventListener("change", event => {
  systemTheme.value = event.matches ? "dark" : "light";
});

/** 實際套用的明暗（系統模式時換算成當下的系統設定） */
export const resolvedTheme = computed(() =>
  theme.value === "system" ? systemTheme.value : theme.value
);

watch(
  resolvedTheme,
  value => {
    /* body.dark：web 的深色色票；html.dark：Element Plus 的深色變數；
       data-theme：preload 載入畫面（Vue 還沒掛上前就要知道明暗） */
    document.body.classList.toggle("dark", value === "dark");
    document.documentElement.classList.toggle("dark", value === "dark");
    document.documentElement.dataset.theme = value;
  },
  { immediate: true }
);
watch(theme, value => persist("skylab.theme", value));

/** 首頁的切換鈕：依「現在看到的」明暗切到另一邊，切了就是固定模式、不再跟隨系統 */
export const toggleTheme = () => {
  theme.value = resolvedTheme.value === "dark" ? "light" : "dark";
};

export const resourceView = ref<"grid" | "list">(
  saved("skylab.resource-view", "grid") === "list" ? "list" : "grid"
);
watch(resourceView, value => persist("skylab.resource-view", value));
