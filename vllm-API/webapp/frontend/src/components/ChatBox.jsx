import { useState, useRef, useEffect } from 'react'
import { Send, ImageIcon, X, Loader2, Upload, Paperclip, FileText, Video } from 'lucide-react'
import MessageBubble from './MessageBubble'

// 只允許本地 blob 物件 URL 或 base64 圖片/影片 data URL，其他一律拒絕
const sanitizeMediaUrl = (url) => {
  if (typeof url !== 'string') return null
  if (/^blob:/.test(url)) return url
  if (/^data:(?:image|video)\/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=]*$/i.test(url)) return url
  return null
}

const ChatBox = ({ modelInfo, globalSelectedModel }) => {
  const [message, setMessage] = useState('')
  const [image, setImage] = useState(null)
  const [imagePreview, setImagePreview] = useState(null)
  const [document, setDocument] = useState(null)
  const [documentName, setDocumentName] = useState(null)
  const [video, setVideo] = useState(null)
  const [videoPreview, setVideoPreview] = useState(null)
  const [isStreaming, setIsStreaming] = useState(false)
  const [currentResponse, setCurrentResponse] = useState('')
  const [lastUserMessage, setLastUserMessage] = useState(null)
  const [isDragging, setIsDragging] = useState(false)
  
  // 配置參數（從後端獲取，避免硬編碼）
  const [config, setConfig] = useState({
    default_max_tokens: 2048,
    default_temperature: 0.7,
    document_max_tokens: 4096,
    vision_temperature: 0.75,
  })
  
  const fileInputRef = useRef(null)
  const documentInputRef = useRef(null)
  const videoInputRef = useRef(null)
  const responseEndRef = useRef(null)
  const dropZoneRef = useRef(null)

  // 獲取配置參數
  useEffect(() => {
    fetch('/api/config')
      .then(res => res.json())
      .then(data => setConfig(data))
      .catch(err => console.error('獲取配置失敗:', err))
  }, [])

  // 自動滾動到回應底部
  useEffect(() => {
    if (currentResponse) {
      responseEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [currentResponse])

  // 處理圖片選擇
  const handleImageSelect = (e) => {
    const file = e.target.files?.[0]
    if (file && file.type.startsWith('image/')) {
      setImage(file)
      const reader = new FileReader()
      reader.onload = (e) => setImagePreview(sanitizeMediaUrl(e.target.result))
      reader.readAsDataURL(file)
    }
  }

  // 處理影片選擇
  const handleVideoSelect = (file) => {
    if (!file || !file.type.startsWith('video/')) return
    // 清除圖片和文件（三種媒體互斥）
    setImage(null)
    setImagePreview(null)
    setDocument(null)
    setDocumentName(null)
    setVideo(file)
    setVideoPreview(sanitizeMediaUrl(URL.createObjectURL(file)))
  }

  // 處理文件選擇
  const handleDocumentSelect = (e) => {
    const file = e.target.files?.[0]
    if (file) {
      const validExtensions = ['.docx', '.pdf', '.txt', '.md']
      const fileExt = file.name.toLowerCase().substring(file.name.lastIndexOf('.'))
      
      if (validExtensions.includes(fileExt)) {
        setDocument(file)
        setDocumentName(file.name)
        // 清除圖片（文件和圖片互斥）
        setImage(null)
        setImagePreview(null)
      } else {
        alert('不支援的文件格式！請上傳 DOCX、PDF、TXT 或 MD 文件。')
      }
    }
  }

  // 處理拖放
  const handleDragEnter = (e) => {
    e.preventDefault()
    e.stopPropagation()
    if (!isStreaming) {
      setIsDragging(true)
    }
  }

  const handleDragLeave = (e) => {
    e.preventDefault()
    e.stopPropagation()
    // 檢查是否真的離開了拖放區域
    if (!dropZoneRef.current?.contains(e.relatedTarget)) {
      setIsDragging(false)
    }
  }

  const handleDragOver = (e) => {
    e.preventDefault()
    e.stopPropagation()
  }

  const handleDrop = (e) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragging(false)

    if (isStreaming) return

    const files = e.dataTransfer.files
    if (files.length > 0) {
      const file = files[0]
      const fileName = file.name.toLowerCase()
      
      // 檢查是否為影片
      if (file.type.startsWith('video/') && modelInfo.is_image_capable) {
        handleVideoSelect(file)
      }
      // 檢查是否為圖片
      else if (file.type.startsWith('image/') && modelInfo.is_image_capable) {
        setImage(file)
        const reader = new FileReader()
        reader.onload = (e) => setImagePreview(sanitizeMediaUrl(e.target.result))
        reader.readAsDataURL(file)
        // 清除文件與影片
        setDocument(null)
        setDocumentName(null)
        removeVideo()
      }
      // 檢查是否為文件
      else if (fileName.endsWith('.docx') || fileName.endsWith('.pdf') || 
               fileName.endsWith('.txt') || fileName.endsWith('.md')) {
        setDocument(file)
        setDocumentName(file.name)
        // 清除圖片與影片
        setImage(null)
        setImagePreview(null)
        removeVideo()
      }
    }
  }

  // 移除影片
  const removeVideo = () => {
    if (videoPreview) {
      URL.revokeObjectURL(videoPreview)
    }
    setVideo(null)
    setVideoPreview(null)
    if (videoInputRef.current) {
      videoInputRef.current.value = ''
    }
  }

  // 移除圖片
  const removeImage = () => {
    setImage(null)
    setImagePreview(null)
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  // 移除文件
  const removeDocument = () => {
    setDocument(null)
    setDocumentName(null)
    if (documentInputRef.current) {
      documentInputRef.current.value = ''
    }
  }

  // 處理流式回應的輔助函數
  const processStream = async (reader) => {
    const decoder = new TextDecoder()
    let buffer = ''
    let inReasoning = false

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      
      // 最後一行可能是不完整的，保留到下一次處理
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue

        let data = line.slice(6).trim()
        if (!data) continue
          
        if (data === '[DONE]') {
          if (inReasoning) {
             setCurrentResponse(prev => prev + '\n</think>\n')
          }
          setIsStreaming(false)
          return
        }

        if (data.startsWith('[ERROR]')) {
          throw new Error(data.slice(8))
        }

        if (data.startsWith('[INFO]')) {
          try {
            const info = JSON.parse(data.slice(7))
            const header = `> 影片: ${info.duration}s \u00b7 ${info.frames}幀 \u00b7 ${info.chunks}段\n\n`
            setCurrentResponse(prev => prev + header)
          } catch (e) {
            // ignore parse errors
          }
          continue
        }

        if (data.startsWith('[STATS]')) {
          try {
            const stats = JSON.parse(data.slice(8))
            const footer = `\n\n> 統計: 耗時 ${stats.time}s \u00b7 生成 ${stats.completion_tokens} tokens \u00b7 速度 ${stats.tps} tokens/s\n\n`
            setCurrentResponse(prev => prev + footer)
          } catch (e) {
            // ignore parse errors
          }
          continue
        }

        if (data.startsWith('{')) {
          try {
            const obj = JSON.parse(data)
            const delta = obj?.choices?.[0]?.delta
            if (delta) {
              let textToAppend = ''
              
              // 處理推理內容 (DeepSeek-R1 / vLLM reasoning_content)
              if (delta.reasoning_content) {
                if (!inReasoning) {
                  inReasoning = true
                  textToAppend += '<think>\n'
                }
                textToAppend += delta.reasoning_content
              } else if (delta.content !== undefined && delta.content !== null && delta.content !== '') {
                if (inReasoning) {
                  inReasoning = false
                  textToAppend += '\n</think>\n'
                }
                textToAppend += delta.content
              }

              if (textToAppend) {
                setCurrentResponse(prev => prev + textToAppend)
              }
            }
            
            if (obj?.usage) {
              const stats = obj.usage
              const footer = `\n\n> 統計: prompt ${stats.prompt_tokens} \u00b7 completion ${stats.completion_tokens} \u00b7 total ${stats.total_tokens}\n\n`
              setCurrentResponse(prev => prev + footer)
            }
          } catch (e) {
            // 如果解析失敗，不要把完整的 JSON 字串當作一般文字印出來
            console.warn("JSON parse error for SSE data:", data)
          }
        }
      }
    }
  }

  // 發送訊息（流式）
  const sendMessage = async () => {
    if (!message.trim() && !image && !document) return
    if (isStreaming) return

    // 保存用戶訊息
    setLastUserMessage({
      text: message,
      image: imagePreview,
      document: documentName,
      video: videoPreview,
    })

    // 清空輸入（立即清除預覽）
    const userMessage = message
    const userImage = image
    const userDocument = document
    const userVideo = video
    setMessage('')
    removeImage()  // 立即清除圖片
    removeDocument() // 立即清除文件
    removeVideo()  // 立即清除影片
    setIsStreaming(true)
    setCurrentResponse('')

    try {
      let endpoint

      if (userDocument) {
        // 文件模式 - 使用 FormData
        endpoint = '/api/chat/document/stream'
        const formData = new FormData()
        formData.append('message', userMessage || '請分析這份文件的內容')
        formData.append('document', userDocument)
        formData.append('model', globalSelectedModel || modelInfo?.default_model || '')
        formData.append('max_tokens', String(config.document_max_tokens))
        formData.append('temperature', String(config.default_temperature))

        const response = await fetch(endpoint, {
          method: 'POST',
          body: formData,
        })

        if (!response.ok) {
          throw new Error('請求失敗')
        }

        // 處理 SSE 流
        await processStream(response.body.getReader())
      } else if (userVideo) {
        // 影片模式 - 使用 FormData
        endpoint = '/api/chat/video/stream'
        const formData = new FormData()
        formData.append('message', userMessage || '請分析這段影片的內容')
        formData.append('video', userVideo)
        formData.append('model', globalSelectedModel || modelInfo?.default_model || '')
        formData.append('max_tokens', String(config.default_max_tokens))
        formData.append('temperature', String(config.vision_temperature ?? config.default_temperature))

        const response = await fetch(endpoint, {
          method: 'POST',
          body: formData,
        })

        if (!response.ok) {
          throw new Error('影片請求失敗')
        }

        // 處理 SSE 流
        await processStream(response.body.getReader())
      } else if (userImage) {
        // 圖片模式 - 使用 FormData
        endpoint = '/api/chat/vision/stream'
        const formData = new FormData()
        formData.append('message', userMessage)
        formData.append('image', userImage)
        formData.append('model', globalSelectedModel || modelInfo?.default_model || '')
        formData.append('max_tokens', String(config.default_max_tokens))
        formData.append('temperature', String(config.vision_temperature))

        const response = await fetch(endpoint, {
          method: 'POST',
          body: formData,
        })

        if (!response.ok) {
          throw new Error('請求失敗')
        }

        // 處理 SSE 流
        await processStream(response.body.getReader())
      } else {
        // 純文字模式
        endpoint = '/api/chat/stream'
        
        const response = await fetch(endpoint, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            message: userMessage,
            model: globalSelectedModel || modelInfo?.default_model,
            max_tokens: config.default_max_tokens,
            temperature: config.default_temperature,
          }),
        })

        if (!response.ok) {
          throw new Error('請求失敗')
        }

        // 處理 SSE 流
        await processStream(response.body.getReader())
      }
    } catch (error) {
      console.error('發送失敗:', error)
      setCurrentResponse('發生錯誤: ' + error.message)
      setIsStreaming(false)
    }
  }

  // 新對話
  const newChat = () => {
    setLastUserMessage(null)
    setCurrentResponse('')
    setMessage('')
    removeImage()
    removeDocument()
    removeVideo()
  }

  // Enter 發送
  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  return (
    <div 
      ref={dropZoneRef}
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
      className={`flex flex-col h-full relative transition-colors duration-200 ${
        isDragging ? 'bg-gray-50' : 'bg-white'
      }`}
    >
      {/* 拖放提示覆蓋層 */}
      {isDragging && (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-white/90 backdrop-blur-sm">
          <div className="text-center space-y-4">
            <div className="w-16 h-16 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-4">
              <Upload className="w-8 h-8 text-gray-400" />
            </div>
            <p className="text-xl font-medium text-gray-900">放開以上傳檔案</p>
            <p className="text-sm text-gray-500">支援圖片, 影片, 與文件檔案</p>
          </div>
        </div>
      )}

      {/* 對話顯示區 */}
      <div className="flex-1 overflow-y-auto custom-scrollbar w-full">
        <div className="max-w-5xl mx-auto px-4 pt-8 pb-40 space-y-8">
          {!lastUserMessage && !currentResponse && (
            <div className="flex items-center justify-center mt-32 text-gray-400">
              <div className="text-center space-y-4 max-w-sm">
                <div className="w-12 h-12 bg-gray-50 border border-gray-100 rounded-2xl flex flex-col items-center justify-center mx-auto mb-6 shadow-sm">
                   <Loader2 className="w-6 h-6 animate-spin text-gray-300" />
                </div>
                <p className="text-lg font-medium text-gray-700">準備就緒</p>
                <p className="text-sm text-gray-500 leading-relaxed">
                  描述您的問題或任務。{modelInfo.is_image_capable && '您也可以上傳或拖放文件、圖片與影片進行分析。'}
                </p>
              </div>
            </div>
          )}

          {lastUserMessage && (
            <MessageBubble
              type="user"
              text={lastUserMessage.text}
              image={lastUserMessage.image}
              document={lastUserMessage.document}
              video={lastUserMessage.video}
            />
          )}

          {currentResponse && (
            <MessageBubble
              type="assistant"
              text={currentResponse}
              isStreaming={isStreaming}
            />
          )}
          
          <div ref={responseEndRef} className="h-4" />
        </div>
      </div>

      {/* 輸入區 (固定於底部) */}
      <div className="absolute bottom-0 left-0 w-full bg-gradient-to-t from-white via-white/95 to-transparent pt-10 pb-6 px-4 shrink-0 pointer-events-none z-10">
        <div className="max-w-4xl mx-auto w-full flex flex-col pointer-events-auto">
          <div className="w-full bg-gray-50 border border-gray-200 rounded-3xl p-3 focus-within:ring-1 focus-within:ring-gray-300 focus-within:bg-white transition-all shadow-lg">
          {/* 預覽區域 */}
          {(videoPreview || imagePreview || documentName) && (
            <div className="flex gap-3 mb-3 px-2 pt-1 overflow-x-auto custom-scrollbar">
              {/* 影片預覽 */}
              {videoPreview && (
                <div className="relative inline-block group shrink-0">
                  <div className="relative rounded-xl overflow-hidden border border-gray-200 w-24 h-24 bg-black">
                    <video
                      src={videoPreview}
                      className="w-full h-full object-cover opacity-80"
                      muted
                      playsInline
                    />
                    <div className="absolute inset-0 flex items-center justify-center">
                      <Video className="w-6 h-6 text-white opacity-70" />
                    </div>
                  </div>
                  <button
                    onClick={removeVideo}
                    className="absolute -top-2 -right-2 p-1 bg-white border border-gray-200 rounded-full text-gray-500 hover:text-gray-900 shadow-sm"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              )}

              {/* 圖片預覽 */}
              {imagePreview && (
                <div className="relative inline-block group shrink-0">
                  <div className="relative rounded-xl overflow-hidden border border-gray-200 w-24 h-24 bg-gray-100">
                    <img
                      src={imagePreview}
                      alt="Preview"
                      className="w-full h-full object-cover"
                    />
                  </div>
                  <button
                    onClick={removeImage}
                    className="absolute -top-2 -right-2 p-1 bg-white border border-gray-200 rounded-full text-gray-500 hover:text-gray-900 shadow-sm"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              )}

              {/* 文件預覽 */}
              {documentName && (
                <div className="relative inline-block group shrink-0">
                  <div className="relative rounded-xl border border-gray-200 bg-white p-3 flex flex-col items-center justify-center w-24 h-24 shadow-sm text-center">
                    <FileText className="w-6 h-6 text-gray-400 mb-2" />
                    <span className="text-xs text-gray-600 font-medium truncate w-full px-1" title={documentName}>
                      {documentName}
                    </span>
                  </div>
                  <button
                    onClick={removeDocument}
                    className="absolute -top-2 -right-2 p-1 bg-white border border-gray-200 rounded-full text-gray-500 hover:text-gray-900 shadow-sm"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              )}
            </div>
          )}

          {/* 輸入框主體 */}
          <textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyPress={handleKeyPress}
            placeholder={
              video ? '描述你想了解這段影片的哪些內容...' : 
              image ? "描述你想了解的內容..." : 
              document ? "請問關於這份文件的問題..." : 
              "發送訊息..."
            }
            className="w-full bg-transparent text-gray-900 placeholder-gray-500 resize-none focus:outline-none text-[15px] leading-relaxed px-3 py-1 mb-2 max-h-32 custom-scrollbar overflow-y-auto"
            rows={Math.min(5, Math.max(1, message.split('\n').length))}
            disabled={isStreaming}
          />
          
          <div className="flex items-center justify-between px-2 pb-1">
            <div className="flex items-center gap-1.5">
              {/* 上傳附件選單 (簡化版) */}
              <input
                ref={documentInputRef}
                type="file"
                accept=".docx,.pdf,.txt,.md"
                onChange={handleDocumentSelect}
                className="hidden"
                disabled={isStreaming}
              />
              <button
                onClick={() => documentInputRef.current?.click()}
                disabled={isStreaming}
                className="p-1.5 text-gray-400 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors disabled:opacity-50"
                title="上傳文件"
              >
                <Paperclip className="w-5 h-5" />
              </button>

              {modelInfo.is_image_capable && (
                <>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/*"
                    onChange={handleImageSelect}
                    className="hidden"
                    disabled={isStreaming}
                  />
                  <button
                    onClick={() => fileInputRef.current?.click()}
                    disabled={isStreaming}
                    className="p-1.5 text-gray-400 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors disabled:opacity-50"
                    title="上傳圖片"
                  >
                    <ImageIcon className="w-5 h-5" />
                  </button>

                  <input
                    ref={videoInputRef}
                    type="file"
                    accept="video/*"
                    onChange={(e) => handleVideoSelect(e.target.files?.[0])}
                    className="hidden"
                    disabled={isStreaming}
                  />
                  <button
                    onClick={() => videoInputRef.current?.click()}
                    disabled={isStreaming}
                    className="p-1.5 text-gray-400 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors disabled:opacity-50"
                    title="上傳影片"
                  >
                    <Video className="w-5 h-5" />
                  </button>
                </>
              )}
            </div>

            {/* 發送與新對話區塊 */}
            <div className="flex items-center gap-2">
              {currentResponse && !isStreaming && (
                <button
                  onClick={newChat}
                  className="px-3 py-1.5 text-sm font-medium text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors shadow-sm"
                >
                  新對話
                </button>
              )}
              
              <button
                onClick={sendMessage}
                disabled={(!message.trim() && !image && !document && !video) || isStreaming}
                className="p-1.5 bg-gray-900 text-white rounded-xl hover:bg-gray-800 disabled:bg-gray-200 disabled:text-gray-400 transition-colors"
                title="發送"
              >
                {isStreaming ? (
                  <Loader2 className="w-5 h-5 animate-spin" />
                ) : (
                  <Send className="w-5 h-5" />
                )}
              </button>
            </div>
          </div>
        </div>
          
        {/* 底部資訊 */}
        <div className="mt-3 text-center text-gray-400 text-xs w-full font-medium">
          Powered by vLLM • 單次對話模式{globalSelectedModel ? ` • ${globalSelectedModel}` : ''}
        </div>
      </div>
    </div>
  </div>
)
}

export default ChatBox
