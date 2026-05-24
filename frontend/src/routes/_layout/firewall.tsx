import { createFileRoute } from "@tanstack/react-router"

import { FirewallTopology } from "@/components/Firewall"

export const Route = createFileRoute("/_layout/firewall")({
  component: FirewallPage,
  head: () => ({
    meta: [
      {
        title: "防火牆管理 - SkyLab",
      },
    ],
  }),
})

function FirewallPage() {
  return (
    <div className="w-full h-full">
      <FirewallTopology />
    </div>
  )
}
