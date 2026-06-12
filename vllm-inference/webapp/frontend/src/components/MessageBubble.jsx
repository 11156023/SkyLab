import { User, Sparkles, Loader2, FileText, Video, ChevronDown, ChevronRight } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkBreaks from 'remark-breaks'
import rehypeHighlight from 'rehype-highlight'
import 'highlight.js/styles/github.css' // Changed to github.css for light theme
import { useState } from 'react'

// 只允許本地 blob 物件 URL 或 base64 圖片/影片 data URL，其他一律拒絕
const sanitizeMediaUrl = (url) => {
  if (typeof url !== 'string') return null
  if (/^blob:/.test(url)) return url
  if (/^data:(?:image|video)\/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=]*$/i.test(url)) return url
  return null
}

const MessageBubble = ({ type, text, image, document, video, isStreaming }) => {
  const isUser = type === 'user'
  const [isThinkingExpanded, setIsThinkingExpanded] = useState(false)

  // 解析 thinking 標籤（僅用於 AI 回應）
  const parseThinkingContent = (content) => {
    if (!content || isUser) {
      return [{ type: 'output', content }]
    }

    // 檢查是否有結束標籤
    const endThinkIndex = content.indexOf('</think>')
    
    if (endThinkIndex !== -1) {
      // 有結束標籤，分割思考與回答
      let thinkingPart = content.slice(0, endThinkIndex)
      const responsePart = content.slice(endThinkIndex + '</think>'.length)

      // 清理思考部分的開始標籤（如果有的話）
      const startThinkIndex = thinkingPart.indexOf('<think>')
      if (startThinkIndex !== -1) {
        thinkingPart = thinkingPart.slice(startThinkIndex + '<think>'.length)
      }

      const parts = []
      if (thinkingPart.trim()) {
        parts.push({ type: 'thinking', content: thinkingPart.trim() })
      }
      if (responsePart.trim()) {
        parts.push({ type: 'output', content: responsePart.trim() })
      }
      return parts.length > 0 ? parts : [{ type: 'output', content: '' }]
    }

    // 沒有結束標籤，處理開始標籤與預設情況
    let thinkingPart = content
    const startThinkIndex = thinkingPart.indexOf('<think>')
    if (startThinkIndex !== -1) {
      thinkingPart = thinkingPart.slice(startThinkIndex + '<think>'.length)
    }

    // 根據您的需求：模型一開始輸出就是 thinking 內容，預設將所有內容放入思考折疊區塊
    return [{ type: 'thinking', content: thinkingPart.trim() }]
  }

  const contentParts = parseThinkingContent(text)

  if (isUser) {
    return (
      <div className="flex justify-end w-full animate-in">
        <div className="bg-gray-100 text-gray-900 rounded-3xl rounded-tr-sm px-5 py-3 max-w-[85%] sm:max-w-[75%] break-words shadow-sm">
          {/* 圖片 */}
          {image && (
            <div className="mb-3">
              <img
                src={sanitizeMediaUrl(image)}
                alt="Uploaded"
                className="max-w-full max-h-64 object-contain rounded-xl"
              />
            </div>
          )}

          {/* 影片 */}
          {video && (
            <div className="mb-3 relative">
              <video
                src={sanitizeMediaUrl(video)}
                controls
                className="max-w-full max-h-64 rounded-xl object-contain bg-black/10"
                playsInline
              />
              <div className="absolute top-2 left-2 flex items-center gap-1.5 px-2 py-1 bg-black/60 backdrop-blur-sm rounded-lg">
                <Video className="w-3.5 h-3.5 text-white" />
                <span className="text-white text-xs font-medium">影片</span>
              </div>
            </div>
          )}

          {/* 文件 */}
          {document && (
            <div className="mb-3 flex items-center gap-3 px-3 py-2 bg-white rounded-xl border border-gray-200">
              <FileText className="w-5 h-5 text-gray-500 shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-gray-800 font-medium text-sm truncate" title={document}>
                  {document}
                </p>
                <p className="text-gray-500 text-xs mt-0.5">文件檔案</p>
              </div>
            </div>
          )}

          {/* 文字 */}
          <div className="whitespace-pre-wrap text-base leading-relaxed">
            {text}
          </div>
        </div>
      </div>
    )
  }

  // Assistant Message
  return (
    <div className="flex gap-4 w-full animate-in">
      {/* AI 頭像 */}
      <div className="w-8 h-8 rounded-full border border-gray-200 bg-white flex items-center justify-center shrink-0 shadow-sm mt-1">
        <Sparkles className="w-4 h-4 text-gray-700" />
      </div>

      {/* 訊息內容 */}
      <div className="flex-1 min-w-0 text-base leading-relaxed text-gray-800">
        <div className="space-y-4 font-sans">
          {contentParts.map((part, idx) => (
            part.type === 'thinking' ? (
              // Thinking 部分：可折疊
              <div 
                key={idx} 
                className="text-gray-500 text-sm border border-gray-100 rounded-xl bg-gray-50/50 overflow-hidden"
              >
                <div 
                  className="font-medium text-xs text-gray-500 flex items-center gap-2 cursor-pointer select-none hover:bg-gray-100/50 px-4 py-2.5 transition-colors"
                  onClick={() => setIsThinkingExpanded(!isThinkingExpanded)}
                >
                  {isStreaming && idx === contentParts.length - 1 ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0 text-gray-400" />
                  ) : isThinkingExpanded ? (
                    <ChevronDown className="w-3.5 h-3.5 shrink-0 text-gray-400" />
                  ) : (
                    <ChevronRight className="w-3.5 h-3.5 shrink-0 text-gray-400" />
                  )}
                  <span className="uppercase tracking-wider">
                    {isStreaming && idx === contentParts.length - 1 ? '思考中...' : '思考過程'}
                  </span>
                </div>
                
                {isThinkingExpanded && (
                  <div className="px-4 pb-3 pt-1 whitespace-pre-wrap font-mono text-[13px] leading-relaxed text-gray-500 border-t border-gray-100/60">
                    {part.content}
                  </div>
                )}
              </div>
            ) : part.content ? (
              // 輸出部分：Markdown 渲染
              <div key={idx} className="prose-custom">
                <ReactMarkdown 
                  remarkPlugins={[remarkGfm, remarkBreaks]}
                  rehypePlugins={[rehypeHighlight]}
                  components={{
                    // 自定義樣式來符合現代簡約標準
                    p: ({node, ...props}) => <p className="mb-4 last:mb-0" {...props} />,
                    h1: ({node, ...props}) => <h1 className="text-2xl font-semibold mb-4 mt-6 first:mt-0 text-gray-900" {...props} />,
                    h2: ({node, ...props}) => <h2 className="text-xl font-semibold mb-3 mt-5 first:mt-0 text-gray-900" {...props} />,
                    h3: ({node, ...props}) => <h3 className="text-lg font-medium mb-2 mt-4 first:mt-0 text-gray-900" {...props} />,
                    ul: ({node, ...props}) => <ul className="list-disc list-outside mb-4 ml-5 space-y-1.5" {...props} />,
                    ol: ({node, ...props}) => <ol className="list-decimal list-outside mb-4 ml-5 space-y-1.5" {...props} />,
                    li: ({node, ...props}) => <li className="pl-1" {...props} />,
                    code: ({node, inline, ...props}) => 
                      inline ? (
                        <code className="bg-gray-100 text-gray-800 px-1.5 py-0.5 rounded text-[14px] font-mono" {...props} />
                      ) : (
                        <code className="block bg-gray-50 border border-gray-200 rounded-xl p-4 overflow-x-auto text-[14px] my-4 font-mono leading-normal shadow-sm" {...props} />
                      ),
                    pre: ({node, ...props}) => <pre className="my-0 overflow-visible" {...props} />,
                    blockquote: ({node, ...props}) => (
                      <blockquote className="border-l-[3px] border-gray-300 pl-4 text-gray-600 my-4" {...props} />
                    ),
                    table: ({node, ...props}) => (
                      <div className="overflow-x-auto my-4 border border-gray-200 rounded-lg">
                        <table className="min-w-full text-sm text-left divide-y divide-gray-200" {...props} />
                      </div>
                    ),
                    thead: ({node, ...props}) => <thead className="bg-gray-50" {...props} />,
                    th: ({node, ...props}) => <th className="px-4 py-3 font-medium text-gray-900 border-b border-gray-200" {...props} />,
                    td: ({node, ...props}) => <td className="px-4 py-3 border-b border-gray-100" {...props} />,
                    a: ({node, ...props}) => <a className="text-blue-600 hover:underline" {...props} />,
                  }}
                >
                  {part.content}
                </ReactMarkdown>
              </div>
            ) : null
          ))}
        </div>
        
        {/* 游標 */}
        {isStreaming && (
          <span className="inline-block w-2 h-4 ml-1 align-middle bg-gray-400 animate-pulse"></span>
        )}

        {/* 載入指示器 */}
        {isStreaming && !text && (
          <div className="flex items-center gap-2 text-gray-400 mt-2">
            <Loader2 className="w-4 h-4 animate-spin" />
            <span className="text-sm">思考中...</span>
          </div>
        )}
      </div>
    </div>
  )
}

export default MessageBubble
