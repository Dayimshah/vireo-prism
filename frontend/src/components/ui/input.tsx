import * as React from 'react'

import { cn } from '@/lib/utils'

const Input = React.forwardRef<HTMLInputElement, React.ComponentProps<'input'>>(
  ({ className, type, ...props }, ref) => (
    <input
      ref={ref}
      type={type}
      className={cn(
        'flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50',
        // The window picker uses native `<input type="date">` rather than a JS calendar —
        // it clamps with `min`/`max`, is keyboard-accessible for free, and drops a
        // dependency. Its one rough edge is the calendar icon, which Chromium renders as a
        // dark glyph that disappears against a dark field; `invert` in dark mode fixes it.
        // Firefox and Safari ignore this pseudo-element, which is why it is only a filter
        // and never layout.
        '[&::-webkit-calendar-picker-indicator]:cursor-pointer [&::-webkit-calendar-picker-indicator]:opacity-60 [&::-webkit-calendar-picker-indicator]:hover:opacity-100 dark:[&::-webkit-calendar-picker-indicator]:invert',
        className,
      )}
      {...props}
    />
  ),
)
Input.displayName = 'Input'

export { Input }
