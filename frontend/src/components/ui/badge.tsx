import { cva, type VariantProps } from 'class-variance-authority'
import * as React from 'react'

import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-2xs font-medium transition-colors',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-primary text-primary-foreground',
        secondary: 'border-transparent bg-secondary text-secondary-foreground',
        outline: 'text-foreground',
        muted: 'border-transparent bg-muted text-muted-foreground',
        // Direction of travel, not good/bad. Which one a metric uses is decided from the
        // API's `higher_is_better`, so churn falling is `positive`.
        positive: 'border-transparent bg-positive-muted text-positive',
        negative: 'border-transparent bg-negative-muted text-negative',
        // Neither direction. Added for the API's `degraded` health status — see the
        // `warning` token in `tailwind.config.ts` for why the other two do not fit.
        warning: 'border-transparent bg-warning-muted text-warning',
      },
    },
    defaultVariants: { variant: 'default' },
  },
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { Badge, badgeVariants }
