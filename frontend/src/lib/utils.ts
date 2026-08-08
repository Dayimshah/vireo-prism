import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/**
 * Merge class names, resolving Tailwind conflicts in favour of the last one.
 *
 * `clsx` flattens conditionals and arrays; `twMerge` then removes earlier classes that
 * the later ones override. Both are needed: `clsx('p-2', 'p-4')` yields `"p-2 p-4"`, and
 * which of the two wins is then decided by stylesheet order rather than by the caller —
 * so a component's `className` prop could not reliably override its own defaults.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}
