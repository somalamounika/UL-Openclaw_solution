import * as RadixScrollArea from '@radix-ui/react-scroll-area'
import { forwardRef, type ReactNode } from 'react'
import { cn } from '@/lib/utils'

export const ScrollArea = forwardRef<
  HTMLDivElement,
  { children: ReactNode; className?: string; viewportClassName?: string }
>(({ children, className, viewportClassName }, ref) => (
  <RadixScrollArea.Root className={cn('relative overflow-hidden', className)} type="hover">
    <RadixScrollArea.Viewport ref={ref} className={cn('h-full w-full', viewportClassName)}>
      {children}
    </RadixScrollArea.Viewport>
    <RadixScrollArea.Scrollbar
      orientation="vertical"
      className="flex w-2 touch-none select-none p-0.5 transition-colors"
    >
      <RadixScrollArea.Thumb className="flex-1 rounded-full bg-[var(--border-strong)]" />
    </RadixScrollArea.Scrollbar>
    <RadixScrollArea.Corner />
  </RadixScrollArea.Root>
))
ScrollArea.displayName = 'ScrollArea'
