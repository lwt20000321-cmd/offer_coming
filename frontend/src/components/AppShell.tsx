import type { ReactNode } from 'react'
import { AppHeader } from './AppHeader'
import { OfferTicker } from './OfferTicker'
import { PointerWash } from './PointerWash'
import { useSession } from '../stores/SessionProvider'
import './appShell.css'

export function AppShell({ children }: { children: ReactNode }) {
  const { status } = useSession()
  const entered = status === 'authenticated'

  return (
    <>
      <PointerWash />
      <OfferTicker />
      <div className={`app-shell${entered ? ' is-in' : ''}`}>
        <AppHeader />
        {children}
      </div>
    </>
  )
}
