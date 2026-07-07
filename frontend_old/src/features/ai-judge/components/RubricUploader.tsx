/**
 * RubricUploader - Drag and drop file upload component for rubric documents
 */

import { FileText, Upload, X } from "lucide-react"
import { useCallback, useState } from "react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

type RubricUploaderProps = {
  onUpload: (file: File) => void
  isLoading?: boolean
  accept?: string
}

export function RubricUploader({
  onUpload,
  isLoading = false,
  accept = ".docx,.pdf",
}: RubricUploaderProps) {
  const [isDragging, setIsDragging] = useState(false)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const acceptedExtensions = accept
    .split(",")
    .map((ext) => ext.trim().replace(".", "").toLowerCase())

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragging(true)
  }, [])

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragging(false)
  }, [])

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      e.stopPropagation()
      setIsDragging(false)

      const files = e.dataTransfer.files
      if (files.length > 0) {
        const file = files[0]
        const ext = file.name.split(".").pop()?.toLowerCase()
        if (ext && acceptedExtensions.includes(ext)) {
          setSelectedFile(file)
        }
      }
    },
    [acceptedExtensions],
  )

  const handleFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files
      if (files && files.length > 0) {
        setSelectedFile(files[0])
      }
    },
    [],
  )

  const handleUpload = useCallback(() => {
    if (selectedFile) {
      onUpload(selectedFile)
    }
  }, [selectedFile, onUpload])

  const handleClear = useCallback(() => {
    setSelectedFile(null)
  }, [])

  return (
    <div className="space-y-4">
      {/* biome-ignore lint/a11y/noStaticElementInteractions: drag-and-drop zone requires static div with drag event handlers */}
      <div
        className={cn(
          "relative rounded-xl border-2 border-dashed p-8 transition-all",
          "flex flex-col items-center justify-center gap-4",
          isDragging
            ? "border-primary bg-primary/5"
            : "border-muted-foreground/25 hover:border-muted-foreground/50",
          isLoading && "pointer-events-none opacity-50",
        )}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        <input
          type="file"
          accept={accept}
          onChange={handleFileSelect}
          className="absolute inset-0 cursor-pointer opacity-0"
          disabled={isLoading}
        />

        {selectedFile ? (
          <div className="flex items-center gap-3">
            <FileText className="h-10 w-10 text-primary" />
            <div className="text-left">
              <p className="font-medium">{selectedFile.name}</p>
              <p className="text-sm text-muted-foreground">
                {(selectedFile.size / 1024 / 1024).toFixed(2)} MB
              </p>
            </div>
            <Button
              variant="ghost"
              size="icon"
              className="ml-2"
              onClick={(e) => {
                e.stopPropagation()
                handleClear()
              }}
              disabled={isLoading}
            >
              <X className="h-4 w-4" />
            </Button>
          </div>
        ) : (
          <>
            <Upload className="h-10 w-10 text-muted-foreground" />
            <div className="text-center">
              <p className="font-medium">拖放情境評估表文件到這裡</p>
              <p className="text-sm text-muted-foreground">
                或點擊選擇檔案（支援 .docx、.pdf）
              </p>
            </div>
          </>
        )}
      </div>

      {selectedFile && (
        <Button onClick={handleUpload} disabled={isLoading} className="w-full">
          {isLoading ? (
            <>
              <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
              AI 分析中...
            </>
          ) : (
            <>
              <Upload className="mr-2 h-4 w-4" />
              上傳並分析
            </>
          )}
        </Button>
      )}
    </div>
  )
}
