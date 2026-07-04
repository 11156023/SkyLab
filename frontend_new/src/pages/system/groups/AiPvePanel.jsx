import { useEffect, useRef, useState } from "react";
import styles from "./AiPvePanel.module.scss";
import MIcon from "../../../components/MIcon";
import { useToast } from "../../../hooks/useToast";
import { AiPveLogService } from "../../../services/aiPveLog";

/** 清除 Qwen3 殘留的 tool call 標記，避免原始標記顯示在對話框中 */
function sanitizeContent(text) {
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
  );
}

export default function AiPvePanel({ groupId }) {
  const toast = useToast();
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [messages, setMessages] = useState([
    {
      role: "assistant",
      content:
        "我是 AI-PVE 助手。你可以詢問節點資源、VM/LXC 狀態、儲存空間使用率等資訊。",
    },
  ]);
  const [chatHistory, setChatHistory] = useState([]);
  const [pendingTool, setPendingTool] = useState(null); // { token, command, reason }
  const [pendingCommand, setPendingCommand] = useState("");
  const logEndRef = useRef(null);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isSending, pendingTool]);

  const canSend = input.trim().length > 0 && !isSending && !pendingTool;

  function handleChatResponse(response) {
    if (response.error) toast.error(response.error);
    setChatHistory(response.messages || []);

    setMessages((prev) => [
      ...prev,
      {
        role: "assistant",
        content: response.reply || response.error || "指令執行完畢",
        tools: response.tools_called,
      },
    ]);

    if (response.needs_confirmation) {
      const sshTool = response.tools_called?.find(
        (t) => t.name === "ssh_exec" && t.result?.pending,
      );
      if (sshTool?.result?.confirm_token) {
        const command = sshTool.args?.command || "";
        setPendingTool({
          token: sshTool.result.confirm_token,
          command,
          reason: sshTool.args?.reason || "執行系統指令",
        });
        setPendingCommand(command);
      }
    }
  }

  async function handleSubmit(event) {
    event.preventDefault();
    const message = input.trim();
    if (!message || isSending || pendingTool) return;

    setInput("");
    setIsSending(true);
    setMessages((prev) => [...prev, { role: "user", content: message }]);

    const newHistory = [...chatHistory];
    if (newHistory.length > 0) {
      newHistory.push({ role: "user", content: message });
    }

    try {
      const response = await AiPveLogService.chat(
        newHistory.length > 0
          ? { messages: newHistory, group_id: groupId }
          : { message, group_id: groupId },
      );
      handleChatResponse(response);
    } catch (err) {
      const detail = err?.message ?? "AI-PVE 對話失敗";
      toast.error(detail);
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `發生錯誤：${detail}` },
      ]);
    } finally {
      setIsSending(false);
    }
  }

  async function handleConfirm(approved) {
    if (!pendingTool) return;
    const command = pendingCommand.trim();
    if (approved && !command) {
      toast.error("請先輸入要執行的指令");
      return;
    }
    setIsSending(true);

    try {
      const res = await AiPveLogService.confirmSsh({
        token: pendingTool.token,
        approved,
        command: approved ? command : undefined,
      });

      const currentToken = pendingTool.token;
      setPendingTool(null);
      setPendingCommand("");

      if (!approved) {
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: "已取消執行指令。" },
        ]);
        setIsSending(false);
        return;
      }

      // 把對話紀錄中 pending 的 tool 結果換成實際執行結果，再請 AI 續答
      const updatedHistory = [...chatHistory];
      const targetIdx = updatedHistory.findIndex(
        (m) =>
          m.role === "tool" &&
          typeof m.content === "string" &&
          m.content.includes(currentToken),
      );
      if (targetIdx !== -1) {
        updatedHistory[targetIdx] = {
          ...updatedHistory[targetIdx],
          content: JSON.stringify(res),
        };
      }

      const chatRes = await AiPveLogService.chat({
        messages: updatedHistory,
        group_id: groupId,
      });
      handleChatResponse(chatRes);
      setIsSending(false);
    } catch (err) {
      toast.error(err?.message ?? "確認失敗");
      setIsSending(false);
    }
  }

  return (
    <div className={styles.panel}>
      <div className={styles.panelHeading}>
        <h2 className={styles.panelTitle}>
          <MIcon name="smart_toy" size={20} />
          AI-PVE 訊息
        </h2>
        <p className={styles.panelDesc}>
          針對當前 PVE 環境快速提問，取得 VM/LXC 與節點運行建議
        </p>
      </div>

      <div className={styles.chatCard}>
        <div className={styles.chatCardHead}>
          <MIcon name="comment" size={18} />
          對話記錄
        </div>

        <div className={styles.chatLog}>
          {messages.map((msg, index) => (
            <div
              key={`${msg.role}-${index}`}
              className={`${styles.msg} ${msg.role === "user" ? styles.msg_user : styles.msg_assistant}`}
            >
              <div className={styles.msgHead}>
                <MIcon name={msg.role === "assistant" ? "smart_toy" : "person"} size={16} />
                <span>{msg.role === "assistant" ? "AI-PVE" : "你"}</span>
              </div>
              <p className={styles.msgContent}>{sanitizeContent(msg.content)}</p>
              {msg.tools && msg.tools.length > 0 && (
                <div className={styles.toolRow}>
                  <span className={styles.toolLabel}>
                    <MIcon name="terminal" size={14} />
                    系統呼叫：
                  </span>
                  {msg.tools.map((tool, toolIndex) => (
                    <span key={`${tool.name}-${toolIndex}`} className={styles.toolBadge}>
                      {tool.name}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}

          {pendingTool && (
            <div className={styles.pendingBox}>
              <div className={styles.pendingHead}>
                <MIcon name="warning" size={18} />
                AI 請求執行安全指令
              </div>
              <p className={styles.pendingReason}>
                <strong>目的：</strong>
                {pendingTool.reason}
              </p>
              <textarea
                value={pendingCommand}
                onChange={(event) => setPendingCommand(event.target.value)}
                placeholder="可在此修改後再允許執行"
                disabled={isSending}
              />
              <p className={styles.pendingHint}>
                為保護伺服器安全，請確認指令內容後再允許執行。
              </p>
              <div className={styles.pendingActions}>
                <button
                  type="button"
                  className={styles.btnAllow}
                  onClick={() => handleConfirm(true)}
                  disabled={isSending || pendingCommand.trim().length === 0}
                >
                  <MIcon name="check" size={16} />
                  允許執行
                </button>
                <button
                  type="button"
                  className={styles.btnSecondary}
                  onClick={() => handleConfirm(false)}
                  disabled={isSending}
                >
                  <MIcon name="close" size={16} />
                  拒絕
                </button>
              </div>
            </div>
          )}

          {isSending && (
            <div className={styles.thinking}>
              <span className={styles.pulse} />
              AI-PVE 思考中...
            </div>
          )}
          <div ref={logEndRef} />
        </div>

        <form className={styles.composer} onSubmit={handleSubmit}>
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="例如：幫我列出目前 CPU 使用率最高的 5 台 VM，並附上節點名稱"
            disabled={isSending}
          />
          <div className={styles.composerActions}>
            <button type="submit" className={styles.btnPrimary} disabled={!canSend}>
              <MIcon name="send" size={16} />
              發送訊息
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
