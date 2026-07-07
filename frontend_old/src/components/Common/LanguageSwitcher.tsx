import { Languages } from "lucide-react"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import { type Language, useLanguage } from "@/providers/LanguageProvider"

const languageOptions: { value: Language; label: string; flag: string }[] = [
  { value: "zh-TW", label: "繁體中文", flag: "🇹🇼" },
  { value: "en", label: "English", flag: "🇬🇧" },
  { value: "ja", label: "日本語", flag: "🇯🇵" },
]

export function SidebarLanguageSwitcher() {
  const { language, setLanguage } = useLanguage()

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu modal={false}>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton
              tooltip="語言 / Language"
              className="data-[state=open]:bg-sidebar-accent data-[state=open]:text-sidebar-accent-foreground"
            >
              <Languages className="size-4" />
              <span>語言 / Language</span>
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            className="w-[--radix-dropdown-menu-trigger-width] min-w-56 rounded-lg"
            side="top"
            align="end"
            sideOffset={4}
          >
            {languageOptions.map((option) => (
              <DropdownMenuItem
                key={option.value}
                onClick={() => setLanguage(option.value)}
                className="gap-2"
              >
                <span>{option.flag}</span>
                <span>{option.label}</span>
                {language === option.value && (
                  <span className="ml-auto">✓</span>
                )}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}

// Standalone version for use outside sidebar
export function LanguageSwitcher() {
  const { language, setLanguage } = useLanguage()

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="inline-flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium hover:bg-accent hover:text-accent-foreground"
        >
          <Languages className="size-4" />
          <span>
            {languageOptions.find((opt) => opt.value === language)?.label}
          </span>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {languageOptions.map((option) => (
          <DropdownMenuItem
            key={option.value}
            onClick={() => setLanguage(option.value)}
            className="gap-2"
          >
            <span>{option.flag}</span>
            <span>{option.label}</span>
            {language === option.value && <span className="ml-auto">✓</span>}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
