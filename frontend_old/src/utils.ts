import { AxiosError } from "axios"
import type { ApiError } from "./client"

function extractErrorMessage(err: unknown): string {
  if (err instanceof AxiosError) {
    return err.message
  }

  const apiError = err as ApiError
  const body = apiError.body as
    | { detail?: string | Array<{ msg?: string }> }
    | undefined
  const errDetail = body?.detail
  if (Array.isArray(errDetail) && errDetail.length > 0) {
    return errDetail[0]?.msg ?? "Something went wrong."
  }
  return typeof errDetail === "string" ? errDetail : "Something went wrong."
}

export const handleError = function (
  this: (msg: string) => void,
  err: unknown,
) {
  const errorMessage = extractErrorMessage(err)
  this(errorMessage)
}

export const getInitials = (name: string): string => {
  return name
    .split(" ")
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase()
}
