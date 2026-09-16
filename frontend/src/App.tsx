import { useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { AppShell } from './components/AppShell'
import { AppRoutes } from './router'
import { useSession } from './stores/SessionProvider'

export default function App() {
  const navigate = useNavigate()
  const location = useLocation()
  const { status } = useSession()

  useEffect(() => {
    if (status === 'authenticated' && location.pathname === '/') {
      navigate('/chat', { replace: true })
    }
  }, [status, location.pathname, navigate])

  return (
    <AppShell>
      <AppRoutes />
    </AppShell>
  )
}
