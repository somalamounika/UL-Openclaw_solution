import type { HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

export function Badge({ className, ...props }: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-2xs font-medium',
        'bg-[var(--surface-3)] text-fg-muted border border-[var(--border)]',
        className,
      )}
      {...props}
    />
  )
}
