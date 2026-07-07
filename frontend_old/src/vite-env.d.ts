/// <reference types="vite/client" />

declare module "punycode" {
  export function decode(string: string): string
  export function encode(string: string): string
  export function toUnicode(domain: string): string
  export function toASCII(domain: string): string
}

interface ImportMetaEnv {
  readonly VITE_API_URL: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
