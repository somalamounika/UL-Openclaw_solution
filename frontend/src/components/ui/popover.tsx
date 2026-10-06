import * as RadixPopover from '@radix-ui/react-popover'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

export function Popover({
  trigger,
  children,
  align = 'start',
  side = 'bottom',
  className,
  open,
  onOpenChange,
}: {
  trigger: ReactNode
  children: ReactNode
  align?: 'start' | 'center' | 'end'
  side?: 'top' | 'right' | 'bottom' | 'left'
  className?: string
  open?: boolean
  onOpenChange?: (open: boolean) => void
}) {
  return (
    <RadixPopover.Root open={open} onOpenChange={onOpenChange}>
      <RadixPopover.Trigger asChild>{trigger}</RadixPopover.Trigger>
      <RadixPopover.Portal>
        <RadixPopover.Content
          align={align}
          side={side}
          sideOffset={6}
          collisionPadding={8}
          className={cn(
            'z-[85] rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] p-1.5 shadow-xl animate-fade-in',
            className,
          )}
        >
          {children}
        </RadixPopover.Content>
      </RadixPopover.Portal>
    </RadixPopover.Root>
  )
}
