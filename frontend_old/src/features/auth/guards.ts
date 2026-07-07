import { redirect } from "@tanstack/react-router"

import type { UserPublic } from "@/client"
import { currentUserQueryOptions } from "@/features/auth/queries"
import { queryClient } from "@/lib/queryClient"

type GuardOptions = {
  redirectTo?: string
}

async function getCurrentUser() {
  return queryClient.ensureQueryData(currentUserQueryOptions())
}

function isAdmin(user: UserPublic) {
  return user.role === "admin" || user.is_superuser
}

function isApplicationUser(user: UserPublic) {
  return user.role === "student" || user.role === "teacher" || isAdmin(user)
}

function isGroupManagerUser(user: UserPublic) {
  return user.role === "teacher" || isAdmin(user)
}

export async function requireAdminUser(options?: GuardOptions) {
  const user = await getCurrentUser()
  if (!isAdmin(user)) {
    throw redirect({ to: options?.redirectTo ?? "/" })
  }
  return user
}

export async function requireStudentUser(options?: GuardOptions) {
  const user = await getCurrentUser()
  if (user.role !== "student") {
    throw redirect({ to: options?.redirectTo ?? "/" })
  }
  return user
}

export async function requireApplicationUser(options?: GuardOptions) {
  const user = await getCurrentUser()
  if (!isApplicationUser(user)) {
    throw redirect({ to: options?.redirectTo ?? "/" })
  }
  return user
}

export async function requireGroupManagerUser(options?: GuardOptions) {
  const user = await getCurrentUser()
  if (!isGroupManagerUser(user)) {
    throw redirect({ to: options?.redirectTo ?? "/" })
  }
  return user
}
