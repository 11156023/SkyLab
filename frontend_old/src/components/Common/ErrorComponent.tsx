import { Link } from "@tanstack/react-router"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"

const ErrorComponent = () => {
  const { t } = useTranslation("messages")

  return (
    <div
      className="app-background flex min-h-screen items-center justify-center flex-col p-4"
      data-testid="error-component"
    >
      <div className="glass-card rounded-2xl px-8 py-10 flex flex-col items-center">
        <div className="flex items-center z-10">
          <div className="flex flex-col ml-4 items-center justify-center p-4">
            <span className="text-6xl md:text-8xl font-bold leading-none mb-4">
              {t("error.title")}
            </span>
            <span className="text-2xl font-bold mb-2">Oops!</span>
          </div>
        </div>

        <p className="text-lg text-muted-foreground mb-4 text-center z-10">
          {t("error.generic")}
        </p>
        <Link to="/">
          <Button>{t("common:buttons.back")}</Button>
        </Link>
      </div>
    </div>
  )
}

export default ErrorComponent
