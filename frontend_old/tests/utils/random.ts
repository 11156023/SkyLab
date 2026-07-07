import { randomUUID } from "node:crypto"

const randomHex = () => randomUUID().replace(/-/g, "")

export const randomEmail = () =>
  `test_${randomHex().substring(0, 8)}@example.com`

export const randomTeamName = () => `Team ${randomHex().substring(0, 8)}`

export const randomPassword = () => randomHex()

export const slugify = (text: string) =>
  text
    .toLowerCase()
    .replace(/\s+/g, "-")
    .replace(/[^\w-]+/g, "")

export const randomItemTitle = () => `Item ${randomHex().substring(0, 8)}`

export const randomItemDescription = () =>
  `Description ${randomHex().substring(0, 8)}`
