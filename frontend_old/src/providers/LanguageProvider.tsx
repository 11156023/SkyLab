import { createContext, useContext, useEffect, useState } from "react"
import { useTranslation } from "react-i18next"

export type Language = "zh-TW" | "en" | "ja"

type LanguageProviderProps = {
  children: React.ReactNode
  defaultLanguage?: Language
  storageKey?: string
}

type LanguageProviderState = {
  language: Language
  setLanguage: (lang: Language) => void
}

const initialState: LanguageProviderState = {
  language: "zh-TW",
  setLanguage: () => null,
}

const LanguageProviderContext =
  createContext<LanguageProviderState>(initialState)

export function LanguageProvider({
  children,
  defaultLanguage = "zh-TW",
  storageKey = "SkyLab-language",
  ...props
}: LanguageProviderProps) {
  const { i18n } = useTranslation()

  // Detect system language or use stored language
  const getInitialLanguage = (): Language => {
    const storedLang = localStorage.getItem(storageKey) as Language | null
    if (storedLang && ["zh-TW", "en", "ja"].includes(storedLang)) {
      return storedLang
    }

    // Detect system language
    const browserLang = navigator.language
    const langMap: Record<string, Language> = {
      "zh-TW": "zh-TW",
      "zh-Hant": "zh-TW",
      "zh-HK": "zh-TW",
      "zh-MO": "zh-TW",
      en: "en",
      "en-US": "en",
      "en-GB": "en",
      ja: "ja",
      "ja-JP": "ja",
    }

    return langMap[browserLang] || defaultLanguage
  }

  const [language, setLanguageState] = useState<Language>(getInitialLanguage)

  // Initialize i18n language
  useEffect(() => {
    i18n.changeLanguage(language)
  }, [language, i18n])

  const setLanguage = (newLanguage: Language) => {
    localStorage.setItem(storageKey, newLanguage)
    setLanguageState(newLanguage)
    i18n.changeLanguage(newLanguage)
  }

  const value = {
    language,
    setLanguage,
  }

  return (
    <LanguageProviderContext.Provider {...props} value={value}>
      {children}
    </LanguageProviderContext.Provider>
  )
}

export const useLanguage = () => {
  const context = useContext(LanguageProviderContext)

  if (context === undefined)
    throw new Error("useLanguage must be used within a LanguageProvider")

  return context
}
