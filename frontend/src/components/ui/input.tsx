import { forwardRef, type InputHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        'h-8 w-full rounded-md border border-[var(--border)] bg-[var(--surface-3)] px-2.5 text-sm text-fg',
        'placeholder:text-fg-subtle',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:border-transparent',
        className,
      )}
      {...props}
    />
  ),
)
Input.displayName = 'Input'
