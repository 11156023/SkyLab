import { Bot, X } from "lucide-react"
import { useState } from "react"

import { AiNavigatorPanel } from "@/components/AiNavigator/AiNavigatorPanel"
import { Button } from "@/components/ui/button"

export function GlobalAiNavigator() {
  const [open, setOpen] = useState(false)

  if (import.meta.env.MODE === "test") {
    return null
  }

  return (
    <>
      {open ? (
        <section className="fixed bottom-23 right-4 z-30 w-[min(26rem,calc(100vw-1rem))] rounded-xl border border-border/70 bg-background/95 p-3 shadow-2xl backdrop-blur-sm md:bottom-24 md:right-6 md:w-[24rem]">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-xs text-muted-foreground">AI 導覽助手</p>
            <Button
              type="button"
              size="icon"
              variant="ghost"
              className="h-7 w-7 rounded-full"
              aria-label="關閉 AI 導覽"
              onClick={() => setOpen(false)}
            >
              <X className="h-4 w-4" />
            </Button>
          </div>
          <div className="max-h-[70vh] overflow-y-auto pr-1">
            <AiNavigatorPanel />
          </div>
        </section>
      ) : null}

      <Button
        type="button"
        size="icon"
        aria-label={open ? "關閉 AI 導覽" : "打開 AI 導覽"}
        data-testid="global-ai-nav-trigger"
        onClick={() => setOpen((value) => !value)}
        className="fixed bottom-24 right-6 z-30 h-13 w-13 rounded-full border border-primary/30 bg-primary text-primary-foreground shadow-lg hover:bg-primary/90 md:bottom-8 md:right-8"
      >
        <Bot className="h-5 w-5" />
      </Button>
    </>
  )
}
