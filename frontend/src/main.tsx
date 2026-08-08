import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from './App'
import './index.css'

/**
 * Mount the app.
 *
 * `StrictMode` is on, and it double-invokes effects in development
 * -------------------------------------------------------------
 * Deliberately kept. It is what surfaces an effect that is not idempotent, and this app has
 * two places where that matters: the theme provider's `matchMedia` listener and the search
 * palette's debounce timer. Both clean up after themselves, and StrictMode is the only thing
 * that would have proven it.
 *
 * It does not double-fire requests — react-query dedupes by key, so a second mount reads the
 * cache rather than the network.
 *
 * The root element is checked rather than asserted
 * ----------------------------------------------
 * `createRoot(document.getElementById('root')!)` is the common idiom, and its failure mode is
 * a blank page with `null is not an object` in the console. If `index.html` and this file
 * ever disagree about the id, an explicit message costs one line and says which file to look
 * at.
 */
const container = document.getElementById('root')

if (!container) {
  throw new Error(
    'No #root element found. index.html must contain <div id="root"></div> for the app to mount.',
  )
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
