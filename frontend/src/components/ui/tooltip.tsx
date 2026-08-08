import * as TooltipPrimitive from '@radix-ui/react-tooltip'
import * as React from 'react'

import { cn } from '@/lib/utils'

/**
 * Tooltips.
 *
 * `TooltipProvider` is mounted once at the app root rather than per tooltip: it owns the
 * shared open/close timers, and without a common provider each tooltip runs its own delay,
 * so moving along a row of them makes every one wait afresh instead of opening
 * immediately once the first has been shown.
 */
const TooltipProvider = TooltipPrimitive.Provider
const Tooltip = TooltipPrimitive.Root
const TooltipTrigger = TooltipPrimitive.Trigger

const TooltipContent = React.forwardRef<
  React.ElementRef<typeof TooltipPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>
>(({ className, sideOffset = 4, ...props }, ref) => (
  <TooltipPrimitive.Portal>
    <TooltipPrimitive.Content
      ref={ref}
      sideOffset={sideOffset}
      // `max-w-xs` and normal wrapping: several of these carry a metric's definition from
      // the API — "Mean DAU as a percentage of rolling 28-day MAU" — and an unwrapped
      // tooltip would run the width of the screen.
      className={cn(
        'z-50 max-w-xs overflow-hidden rounded-md border bg-popover px-3 py-1.5 text-xs text-popover-foreground shadow-md animate-in fade-in-0 zoom-in-95 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95',
        className,
      )}
      {...props}
    />
  </TooltipPrimitive.Portal>
))
TooltipContent.displayName = TooltipPrimitive.Content.displayName

export { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger }
