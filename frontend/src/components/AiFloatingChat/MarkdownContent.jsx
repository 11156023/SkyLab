import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

/**
 * AI 回覆的 markdown 渲染。獨立成檔讓 AiFloatingChat 以 lazy 載入：
 * 浮動聊天掛在每一頁的 layout 上，micromark／remark 一起進入口 chunk
 * 會讓所有人登入時多下載數百 KB，而多數人根本沒打開聊天。
 */
export default function MarkdownContent({ children }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>
      {children}
    </ReactMarkdown>
  );
}
