import { Link } from "@tanstack/react-router"
import { Plus } from "lucide-react"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"

const CreateContainer = () => {
  const { t } = useTranslation(["resources"])

  return (
    <Button asChild>
      <Link to="/resources-create">
        <Plus className="mr-2 h-4 w-4" />
        {t("resources:create.title")}
      </Link>
    </Button>
  )
}

export default CreateContainer
