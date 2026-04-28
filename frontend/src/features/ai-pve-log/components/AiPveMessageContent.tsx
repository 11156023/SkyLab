import {
  AlertTriangle,
  Bot,
  Check,
  MessageSquare,
  Send,
  Wrench,
  X,
} from "lucide-react"
import { type FormEvent, useMemo, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Textarea } from "@/components/ui/textarea"
import { AiPveLogService, type ToolCallRecord } from "@/features/ai-pve-log/api"
import useCustomToast from "@/hooks/useCustomToast"

type LocalMessage = {
  role: "user" | "assistant"
  content: string
  tools?: ToolCallRecord[]
}

/** 清除 Qwen3 殘留的 tool call 標記，避免原始標記顯示在對話框中 */
function sanitizeContent(text: string): string {
  return (
    text
      // <|tool_call>call:func{...}<tool_call|> 或 <|tool_call|>...<|/tool_call|>
      .replace(/<\|?tool_call\|?>[\s\S]*?<\|?\/?tool_call\|?>/g, "")
      // 無結尾標記：<|tool_call>call:func{...}
      .replace(/<\|?tool_call\|?>\s*call:[a-zA-Z0-9_]+\s*\{[\s\S]+\}/g, "")
      // <think>...</think>
      .replace(/<think>[\s\S]*?<\/think>/g, "")
      // 殘留的特殊 token 如 <|"|> <|endoftext|>
      .replace(/<\|[^>]*\|>/g, "")
      .trim()
  )
}

/**
 * AI PVE Message Content Block - extracted from Page
 */
export function AiPveMessageContent({
  groupId,
}: {
  groupId: string
}) {
  const { showErrorToast } = useCustomToast()

  const [input, setInput] = useState("")
  const [isSending, setIsSending] = useState(false)
  const [messages, setMessages] = useState<LocalMessage[]>([
    {
      role: "assistant",
      content:
        "我是 AI-PVE 助手。你可以詢問節點資源、VM/LXC 狀態、儲存空間使用率等資訊。",
    },
  ])
  const [chatHistory, setChatHistory] = useState<Record<string, unknown>[]>([])
  const [pendingTool, setPendingTool] = useState<{
    token: string
    command: string
    reason: string
  } | null>(null)
  const [pendingCommand, setPendingCommand] = useState("")

  const canSend = useMemo(
    () => input.trim().length > 0 && !isSending && !pendingTool,
    [input, isSending, pendingTool],
  )

  const handleChatResponse = (response: any) => {
    if (response.error) {
      showErrorToast(response.error)
    }
    setChatHistory(response.messages || [])

    setMessages((prev) => [
      ...prev,
      {
        role: "assistant",
        content: response.reply || response.error || "指令執行完畢",
        tools: response.tools_called,
      },
    ])

    if (response.needs_confirmation) {
      const sshTool = response.tools_called?.find(
        (t: any) => t.name === "ssh_exec" && t.result?.pending,
      )
      if (sshTool?.result?.confirm_token) {
        const command = (sshTool.args.command as string) || ""
        setPendingTool({
          token: sshTool.result.confirm_token as string,
          command,
          reason: (sshTool.args.reason as string) || "執行系統指令",
        })
        setPendingCommand(command)
      }
    }
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    const message = input.trim()
    if (!message || isSending || pendingTool) return

    setInput("")
    setIsSending(true)
    setMessages((prev) => [...prev, { role: "user", content: message }])

    const newHistory = [...chatHistory]
    if (newHistory.length > 0) {
      newHistory.push({ role: "user", content: message })
    }

    try {
      const response = await AiPveLogService.chat(
        newHistory.length > 0
          ? { messages: newHistory, group_id: groupId }
          : { message, group_id: groupId },
      )
      handleChatResponse(response)
    } catch (err: any) {
      const detail = err?.body?.detail ?? err?.message ?? "AI-PVE 對話失敗"
      showErrorToast(detail)
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `發生錯誤：${detail}` },
      ])
    } finally {
      setIsSending(false)
    }
  }

  const handleConfirm = async (approved: boolean) => {
    if (!pendingTool) return
    const command = pendingCommand.trim()
    if (approved && !command) {
      showErrorToast("請先輸入要執行的指令")
      return
    }
    setIsSending(true)

    try {
      const res = await AiPveLogService.confirmSsh({
        token: pendingTool.token,
        approved,
        command: approved ? command : undefined,
      })

      const currentToken = pendingTool.token
      setPendingTool(null)
      setPendingCommand("")

      if (!approved) {
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: "已取消執行指令。" },
        ])
        setIsSending(false)
        return
      }

      // 替換對話紀錄中的 pending token
      const updatedHistory = [...chatHistory]
      const targetIdx = updatedHistory.findIndex(
        (m) =>
          m.role === "tool" &&
          typeof m.content === "string" &&
          m.content.includes(currentToken),
      )

      if (targetIdx !== -1) {
        updatedHistory[targetIdx] = {
          ...updatedHistory[targetIdx],
          content: JSON.stringify(res),
        }
      }

      const chatRes = await AiPveLogService.chat({
        messages: updatedHistory,
        group_id: groupId,
      })
      handleChatResponse(chatRes)
    } catch (err: any) {
      showErrorToast(err.message || "確認失敗")
      setIsSending(false)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold tracking-tight">AI-PVE 訊息</h2>
          <p className="text-sm text-muted-foreground mt-1">
            針對當前 PVE 環境快速提問，取得 VM/LXC 與節點運行建議
          </p>
        </div>
      </div>

      <Card className="flex h-[calc(100vh-200px)] min-h-[500px] flex-col shadow-sm border-border/50">
        <CardHeader className="border-b bg-muted/10 py-4">
          <CardTitle className="flex items-center gap-2 text-base">
            <MessageSquare className="h-5 w-5" />
            對話記錄
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-1 flex-col gap-4 p-4 lg:p-6">
          <div className="flex-1 space-y-4 overflow-y-auto rounded-lg border border-border/50 bg-muted/10 p-4 shadow-inner">
            {messages.map((msg, index) => (
              <div
                key={`${msg.role}-${index}`}
                className={`rounded-xl p-4 text-sm ${
                  msg.role === "user"
                    ? "ml-8 bg-primary/10 border border-primary/20"
                    : "mr-8 border bg-background shadow-sm"
                }`}
              >
                <div className="mb-2 flex items-center gap-2 font-medium">
                  {msg.role === "assistant" ? (
                    <Bot className="h-4 w-4 text-primary" />
                  ) : (
                    <MessageSquare className="h-4 w-4 text-muted-foreground" />
                  )}
                  <span
                    className={
                      msg.role === "assistant"
                        ? "text-primary"
                        : "text-muted-foreground"
                    }
                  >
                    {msg.role === "assistant" ? "AI-PVE" : "你"}
                  </span>
                </div>
                <p className="whitespace-pre-wrap leading-relaxed text-foreground/90">
                  {sanitizeContent(msg.content)}
                </p>
                {msg.tools && msg.tools.length > 0 && (
                  <div className="mt-4 flex flex-wrap items-center gap-2 border-t pt-3">
                    <span className="flex items-center gap-1 text-xs font-medium text-muted-foreground">
                      <Wrench className="h-3.5 w-3.5" />
                      系統呼叫：
                    </span>
                    {msg.tools.map((tool, toolIndex) => (
                      <Badge
                        key={`${tool.name}-${toolIndex}`}
                        variant="secondary"
                        className="bg-muted text-[10px] uppercase"
                      >
                        {tool.name}
                      </Badge>
                    ))}
                  </div>
                )}
              </div>
            ))}

            {pendingTool && (
              <div className="mr-8 rounded-xl border border-destructive/20 bg-destructive/5 p-5 shadow-sm">
                <div className="flex items-center gap-2 text-destructive mb-3 font-semibold">
                  <AlertTriangle className="h-5 w-5" />
                  AI 請求執行安全指令
                </div>
                <div className="mb-4 space-y-2 text-sm text-foreground/90">
                  <p>
                    <strong>目的：</strong>
                    {pendingTool.reason}
                  </p>
                  <Textarea
                    value={pendingCommand}
                    onChange={(event) => setPendingCommand(event.target.value)}
                    placeholder="可在此修改後再允許執行"
                    className="min-h-[88px] resize-y font-mono text-xs"
                    disabled={isSending}
                  />
                  <p className="text-xs text-muted-foreground mt-2">
                    為保護伺服器安全，請確認指令內容後再允許執行。
                  </p>
                </div>
                <div className="flex gap-3">
                  <Button
                    variant="default"
                    size="sm"
                    className="flex-1 bg-green-600 hover:bg-green-700"
                    onClick={() => handleConfirm(true)}
                    disabled={isSending || pendingCommand.trim().length === 0}
                  >
                    <Check className="mr-1 h-4 w-4" /> 允許執行
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="flex-1"
                    onClick={() => handleConfirm(false)}
                    disabled={isSending}
                  >
                    <X className="mr-1 h-4 w-4" /> 拒絕
                  </Button>
                </div>
              </div>
            )}

            {isSending && (
              <div className="mr-8 rounded-xl border bg-background shadow-sm p-4 text-sm text-muted-foreground flex items-center gap-3">
                <span className="flex h-2 w-2 relative">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-75" />
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-primary" />
                </span>
                AI-PVE 思考中...
              </div>
            )}
          </div>

          <form onSubmit={handleSubmit} className="flex flex-col gap-3">
            <Textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder="例如：幫我列出目前 CPU 使用率最高的 5 台 VM，並附上節點名稱"
              className="min-h-[100px] resize-none focus-visible:ring-1"
              disabled={isSending}
            />
            <div className="flex justify-end">
              <Button
                type="submit"
                disabled={!canSend}
                className="w-full sm:w-auto"
              >
                <Send className="mr-2 h-4 w-4" />
                發送訊息
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
