import { forwardRef, type ButtonHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'outline'
type Size = 'sm' | 'md' | 'icon' | 'xs'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
}

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-accent text-[var(--accent-fg)] hover:brightness-110 disabled:hover:brightness-100',
  secondary: 'bg-[var(--surface-3)] text-fg hover:bg-[var(--surface-2)] border border-[var(--border)]',
  ghost: 'text-fg-muted hover:text-fg hover:bg-[var(--surface-3)]',
  outline: 'border border-[var(--border-strong)] text-fg hover:bg-[var(--surface-3)]',
  danger: 'bg-[var(--danger)] text-white hover:brightness-110',
}

const SIZES: Record<Size, string> = {
  xs: 'h-6 px-2 text-2xs gap-1',
  sm: 'h-7 px-2.5 text-xs gap-1.5',
  md: 'h-9 px-3.5 text-sm gap-2',
  icon: 'h-7 w-7 justify-center',
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = 'secondary', size = 'md', ...props }, ref) => (
    <button
      ref={ref}
      className={cn(
        'inline-flex items-center rounded-md font-medium transition-colors',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-1 focus-visible:ring-offset-[var(--bg)]',
        'disabled:cursor-not-allowed disabled:opacity-45',
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...props}
    />
  ),
)
Button.displayName = 'Button'
