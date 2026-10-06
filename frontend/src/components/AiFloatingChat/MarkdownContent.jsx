import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

/* 站內路徑（/my-resources）才可以點；「//evil.com」是協定相對網址，等於外站 */
export function isInternalHref(href) {
  return typeof href === "string" && href.startsWith("/") && !href.startsWith("//");
}

/* 模型的回覆可能被使用者輸入或畫面資料帶歪（prompt injection），所以輸出端也要收：
   - 圖片一律不顯示：![](https://外站/?資料) 一渲染瀏覽器就會自己送出請求，
     是把對話內容偷帶出去最常見的管道。
   - 外部連結不給點：只顯示文字與網址，避免被引導到釣魚頁。站內路徑照常可點。
   - 原始 HTML 與 javascript: 連結由 rehype-sanitize 擋掉。 */
function SafeLink({ href, children }) {
  if (isInternalHref(href)) return <a href={href}>{children}</a>;
  const label = typeof children === "string" ? children : null;
  return (
    <span>
      {children}
      {href && label !== href ? ` (${href})` : null}
    </span>
  );
}

const COMPONENTS = { a: SafeLink };

/**
 * AI 回覆的 markdown 渲染。獨立成檔讓 AiFloatingChat 以 lazy 載入：
 * 浮動聊天掛在每一頁的 layout 上，micromark／remark 一起進入口 chunk
 * 會讓所有人登入時多下載數百 KB，而多數人根本沒打開聊天。
 */
export default function MarkdownContent({ children }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeSanitize]}
      disallowedElements={["img"]}
      components={COMPONENTS}
    >
      {children}
    </ReactMarkdown>
  );
}
