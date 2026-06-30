import { useState } from 'react'
import { Info } from 'lucide-react'

interface InfoTooltipProps {
  text: string
  width?: number
}

export default function InfoTooltip({ text, width = 260 }: InfoTooltipProps) {
  const [show, setShow] = useState(false)

  return (
    <span
      className="relative inline-flex items-center"
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
    >
      <Info className="w-3.5 h-3.5 text-[#64748b] hover:text-[#94a3b8] cursor-help" />
      {show && (
        <div
          className="absolute z-50 left-1/2 -translate-x-1/2 bottom-full mb-2 p-3 bg-[#1a2035] border border-[#1e2d40] rounded-lg shadow-xl text-[11px] text-[#94a3b8] leading-relaxed whitespace-pre-line"
          style={{ width }}
        >
          {text}
        </div>
      )}
    </span>
  )
}
