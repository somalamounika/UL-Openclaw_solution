import * as RadixTooltip from '@radix-ui/react-tooltip'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

export const TooltipProvider = RadixTooltip.Provider

export function Tooltip({
  content,
  children,
  side = 'top',
  className,
}: {
  content: ReactNode
  children: ReactNode
  side?: 'top' | 'right' | 'bottom' | 'left'
  className?: string
}) {
  if (!content) return <>{children}</>
  return (
    <RadixTooltip.Root delayDuration={350}>
      <RadixTooltip.Trigger asChild>{children}</RadixTooltip.Trigger>
      <RadixTooltip.Portal>
        <RadixTooltip.Content
          side={side}
          sideOffset={6}
          collisionPadding={8}
          className={cn(
            'z-[80] max-w-xs rounded-md border border-[var(--border-strong)] bg-[var(--surface-2)]',
            'px-2.5 py-1.5 text-xs text-fg shadow-lg animate-fade-in',
            className,
          )}
        >
          {content}
          <RadixTooltip.Arrow className="fill-[var(--surface-2)]" />
        </RadixTooltip.Content>
      </RadixTooltip.Portal>
    </RadixTooltip.Root>
  )
}
