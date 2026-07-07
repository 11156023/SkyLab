import { Link } from "@tanstack/react-router"

import { cn } from "@/lib/utils"

interface LogoProps {
  variant?: "full" | "icon" | "responsive"
  className?: string
  asLink?: boolean
}

const ICON_SRC = "/assets/images/favicon.png"

export function Logo({
  variant = "full",
  className,
  asLink = true,
}: LogoProps) {
  const renderBrand = (sizeClass: string, extraClass = "") => (
    <span
      className={cn("inline-flex items-center gap-2 font-semibold", extraClass)}
    >
      <img
        src={ICON_SRC}
        alt="SkyLab"
        className={cn(sizeClass, "object-contain")}
      />
      {variant !== "icon" && <span className="text-base">SkyLab</span>}
    </span>
  )

  const content =
    variant === "responsive" ? (
      <>
        <span className={cn("group-data-[collapsible=icon]:hidden", className)}>
          {renderBrand("h-6 w-6")}
        </span>
        <span
          className={cn(
            "hidden group-data-[collapsible=icon]:inline-flex",
            className,
          )}
        >
          <img src={ICON_SRC} alt="SkyLab" className="size-5 object-contain" />
        </span>
      </>
    ) : (
      renderBrand(variant === "full" ? "h-6 w-6" : "size-5", className)
    )

  if (!asLink) {
    return content
  }

  return <Link to="/">{content}</Link>
}
