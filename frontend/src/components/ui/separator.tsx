import * as RadixSeparator from '@radix-ui/react-separator'
import { cn } from '@/lib/utils'

export function Separator({ className, ...props }: RadixSeparator.SeparatorProps) {
  return (
    <RadixSeparator.Root
      className={cn('bg-[var(--border)] data-[orientation=horizontal]:h-px data-[orientation=vertical]:w-px', className)}
      {...props}
    />
  )
}
