import { useGraphStore } from '@/state/graphStore'
import { Dialog } from '@/components/ui/dialog'

const SHORTCUTS: Array<[string, string]> = [
  ['/', 'Focus graph search'],
  ['F', 'Focus mode on selection'],
  ['Esc', 'Pop focus / clear selection'],
  ['E', 'Expand selection'],
  ['C', 'Collapse selection'],
  ['Space (hold)', 'Fisheye lens — wheel resizes it'],
  ['+ / −', 'Zoom in / out'],
  ['0', 'Fit to screen'],
  ['[ / ]', 'Toggle left / right rail'],
  ['Backspace', 'Clear focus to overview'],
  ['Double-click node', 'Expand / collapse'],
  ['Alt+click node', 'Expand 2 hops'],
  ['Shift+click node', 'Add to selection'],
  ['Right-click node', 'Expand by type'],
  ['?', 'This sheet'],
]

export function ShortcutSheet() {
  const open = useGraphStore((s) => s.shortcutsOpen)
  const setOpen = useGraphStore((s) => s.setShortcutsOpen)

  return (
    <Dialog open={open} onOpenChange={setOpen} title="Keyboard shortcuts">
      <div className="grid grid-cols-2 gap-x-6 gap-y-1.5">
        {SHORTCUTS.map(([key, action]) => (
          <div key={key} className="flex items-baseline justify-between gap-3 text-xs">
            <span className="text-fg-muted">{action}</span>
            <kbd className="shrink-0 rounded border border-[var(--border-strong)] bg-[var(--surface-3)] px-1.5 py-0.5 font-mono text-[10px] text-fg">
              {key}
            </kbd>
          </div>
        ))}
      </div>
    </Dialog>
  )
}
