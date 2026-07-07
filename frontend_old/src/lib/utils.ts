import { type ClassValue, clsx } from "clsx"
import { decode as punydecode } from "punycode"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * 將 Punycode 編碼名稱（xn--...）解碼為 Unicode 顯示名稱。
 * 支援多 label hostname（如 xn--p8s957b.xn--fiq228cy8f3p4a）。
 */
export function decodeName(name: string | null | undefined): string {
  if (!name) return ""
  try {
    return name
      .split(".")
      .map((label) =>
        label.toLowerCase().startsWith("xn--")
          ? punydecode(label.slice(4))
          : label,
      )
      .join(".")
  } catch {
    return name
  }
}
