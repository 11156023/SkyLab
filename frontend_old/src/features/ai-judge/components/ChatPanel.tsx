/**
 * ChatPanel - Chat interface for refining rubric with AI
 */

import { Bot, Send, Sparkles, User } from "lucide-react"
import { useCallback, useRef, useState } from "react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

import type { ChatMessage } from "../api"

type ChatPanelProps = {
  messages: ChatMessage[]
  onSendMessage: (message: string, isRefine?: boolean) => void
  isLoading?: boolean
  disabled?: boolean
}

export function ChatPanel({
  messages,
  onSendMessage,
  isLoading = false,
  disabled = false,
}: ChatPanelProps) {
  const [input, setInput] = useState("")
  const messagesEndRef = useRef<HTMLDivElement>(null)

  const handleSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault()
      if (input.trim() && !isLoading) {
        onSendMessage(input.trim())
        setInput("")
      }
    },
    [input, isLoading, onSendMessage],
  )

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault()
        if (input.trim() && !isLoading) {
          onSendMessage(input.trim())
          setInput("")
        }
      }
    },
    [input, isLoading, onSendMessage],
  )

  const handleRefine = useCallback(() => {
    if (!isLoading) {
      onSendMessage("請幫我審核並潤飾目前的情境評估表", true)
    }
  }, [isLoading, onSendMessage])

  return (
    <div className="flex h-full flex-col">
      {/* Messages */}
      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        {messages.length === 0 ? (
          <div className="flex h-full items-center justify-center">
            <div className="text-center text-muted-foreground">
              <Bot className="mx-auto mb-2 h-8 w-8" />
              <p>與 AI 對話來精煉你的情境評估表</p>
              <p className="mt-1 text-sm">
                可以詢問修改建議，或直接下達調整指令
              </p>
            </div>
          </div>
        ) : (
          messages.map((msg, i) => (
            <div
              key={i}
              className={cn(
                "flex gap-3",
                msg.role === "user" ? "justify-end" : "justify-start",
              )}
            >
              {msg.role === "assistant" && (
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10">
                  <Bot className="h-4 w-4 text-primary" />
                </div>
              )}
              <div
                className={cn(
                  "max-w-[80%] rounded-xl px-4 py-2",
                  msg.role === "user"
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted",
                )}
              >
                <p className="whitespace-pre-wrap text-sm">{msg.content}</p>
              </div>
              {msg.role === "user" && (
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted">
                  <User className="h-4 w-4" />
                </div>
              )}
            </div>
          ))
        )}

        {isLoading && (
          <div className="flex gap-3">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10">
              <Bot className="h-4 w-4 text-primary" />
            </div>
            <div className="rounded-xl bg-muted px-4 py-2">
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 animate-bounce rounded-full bg-primary [animation-delay:-0.3s]" />
                <span className="h-2 w-2 animate-bounce rounded-full bg-primary [animation-delay:-0.15s]" />
                <span className="h-2 w-2 animate-bounce rounded-full bg-primary" />
              </div>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="border-t p-4">
        <div className="mb-2 flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleRefine}
            disabled={disabled || isLoading}
          >
            <Sparkles className="mr-1 h-3 w-3" />
            全表潤飾
          </Button>
        </div>
        <form onSubmit={handleSubmit} className="flex gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="輸入訊息... (Shift+Enter 換行)"
            rows={1}
            disabled={disabled || isLoading}
            className={cn(
              "flex-1 resize-none rounded-xl border bg-background px-4 py-2 text-sm",
              "focus:outline-none focus:ring-2 focus:ring-primary",
              "disabled:cursor-not-allowed disabled:opacity-50",
            )}
          />
          <Button
            type="submit"
            size="icon"
            disabled={disabled || isLoading || !input.trim()}
          >
            <Send className="h-4 w-4" />
          </Button>
        </form>
        <p className="mt-2 text-xs text-muted-foreground">
          提示：詢問問題不會修改評估表，需明確指令（如「幫我改」「新增」）才會執行變更
        </p>
      </div>
    </div>
  )
}
