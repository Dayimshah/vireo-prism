import { Monitor, Moon, Sun } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useTheme, type ThemePreference } from '@/state/theme'

/**
 * Light, dark, or follow the system.
 *
 * Three options rather than a two-way switch. `system` is a distinct preference, not a
 * starting value: a reader whose OS switches to dark in the evening expects the dashboard to
 * follow, and a plain toggle has nowhere to record that intent — once tapped, it is pinned
 * forever.
 *
 * The icon shows the theme in effect, so `system` displays a sun or a moon depending on what
 * the OS currently reports. The menu is where the preference itself is visible; showing a
 * monitor glyph in the topbar would tell a reader how the theme is decided rather than what
 * it is.
 */

const OPTIONS: readonly { value: ThemePreference; label: string }[] = [
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
  { value: 'system', label: 'System' },
]

export function ThemeToggle() {
  const { preference, resolved, setPreference } = useTheme()

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          // Names the current state, not the action. A reader using a screen reader needs to
          // know what the theme is; "Toggle theme" tells them only that a control exists.
          aria-label={`Theme: ${preference}. Change theme`}
        >
          {resolved === 'dark' ? <Moon /> : <Sun />}
        </Button>
      </DropdownMenuTrigger>

      <DropdownMenuContent align="end" className="w-44">
        <DropdownMenuLabel>Appearance</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuRadioGroup
          value={preference}
          // Radix hands back a plain `string`; the preference is a union of three. The cast
          // is safe because every item below is one of those three, and widening the setter
          // instead would let a typo elsewhere store an unusable value.
          onValueChange={(value) => setPreference(value as ThemePreference)}
        >
          {OPTIONS.map((option) => (
            <DropdownMenuRadioItem key={option.value} value={option.value} className="gap-2">
              {option.value === 'light' && <Sun className="size-3.5" />}
              {option.value === 'dark' && <Moon className="size-3.5" />}
              {option.value === 'system' && <Monitor className="size-3.5" />}
              <span>{option.label}</span>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
