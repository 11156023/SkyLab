import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"

import {
  type Body_login_login_access_token as AccessToken,
  type LdapLoginRequest,
  LoginService,
  type UserRegister,
  UsersService,
} from "@/client"
import { currentUserQueryOptions } from "@/features/auth/queries"
import { queryKeys } from "@/lib/queryKeys"
import { AuthSessionService } from "@/services/authSession"
import { isTokenExpiredOrNearExpiry } from "@/services/openApiAuth"
import { handleError } from "@/utils"
import useCustomToast from "./useCustomToast"

const isLoggedIn = () => {
  const token = localStorage.getItem("access_token")
  if (!token) return false
  return !isTokenExpiredOrNearExpiry(token, Date.now(), 0)
}

const useAuth = (options?: {
  /** Return true to prevent automatic navigation to "/" after login */
  onLoginSuccess?: () => Promise<boolean | undefined> | boolean | undefined
}) => {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showErrorToast } = useCustomToast()

  const { data: user } = useQuery({
    ...currentUserQueryOptions(),
    enabled: isLoggedIn(),
  })

  const signUpMutation = useMutation({
    mutationFn: (data: UserRegister) =>
      UsersService.registerUser({ requestBody: data }),
    onSuccess: () => {
      navigate({ to: "/login" })
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.users.all })
    },
  })

  const login = async (data: AccessToken) => {
    const response = await LoginService.loginAccessToken({
      formData: data,
    })
    AuthSessionService.setTokens(response)
  }

  const afterLogin = async () => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.auth.currentUser,
    })
    if (options?.onLoginSuccess) {
      try {
        const preventNavigate = await options.onLoginSuccess()
        if (preventNavigate === true) return
      } catch (error) {
        handleError.call(showErrorToast, error)
        return
      }
    }
    navigate({ to: "/" })
  }

  const loginMutation = useMutation({
    mutationFn: login,
    onSuccess: afterLogin,
    onError: handleError.bind(showErrorToast),
  })

  const googleLoginMutation = useMutation({
    mutationFn: (idToken: string) =>
      AuthSessionService.loginWithGoogle(idToken),
    onSuccess: afterLogin,
    onError: handleError.bind(showErrorToast),
  })

  const ldapLoginMutation = useMutation({
    mutationFn: async (data: LdapLoginRequest) => {
      const response = await LoginService.loginLdap({ requestBody: data })
      AuthSessionService.setTokens(response)
    },
    onSuccess: afterLogin,
    onError: handleError.bind(showErrorToast),
  })

  const logout = () => {
    // Fire-and-forget: revoke server-side blacklist entry. We don't await
    // because the user expects logout to be instant; if the request fails
    // (e.g. offline), the token_version mechanism + natural expiry still
    // protect the session.
    void AuthSessionService.revokeTokens()
    AuthSessionService.clearTokens()
    queryClient.setQueryData(queryKeys.auth.currentUser, null)
    navigate({ to: "/login" })
  }

  return {
    signUpMutation,
    loginMutation,
    googleLoginMutation,
    ldapLoginMutation,
    logout,
    user,
  }
}

export { isLoggedIn }
export default useAuth
