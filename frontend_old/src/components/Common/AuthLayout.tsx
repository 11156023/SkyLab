import { Appearance } from "@/components/Common/Appearance"
import { Background3D } from "@/components/Common/Background3D"
import { useTheme } from "@/components/theme-provider"

interface AuthLayoutProps {
  children: React.ReactNode
}

export function AuthLayout({ children }: AuthLayoutProps) {
  const { resolvedTheme } = useTheme()
  const isDark = resolvedTheme === "dark"

  return (
    <div className="auth-background relative min-h-svh flex items-center justify-center p-4 overflow-hidden">
      <Background3D isDark={isDark} />
      <div className="absolute top-4 right-4 z-10">
        <Appearance />
      </div>
      <div className="glass-card relative z-10 w-full max-w-sm rounded-2xl px-8 py-10">
        {children}
      </div>
    </div>
  )
}
