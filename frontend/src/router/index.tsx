import { Navigate, Outlet, Route, Routes } from 'react-router'
import ApplicationsPage from '../pages/ApplicationsPage'
import ChatPage from '../pages/ChatPage'
import KnowledgePage from '../pages/KnowledgePage'
import OnboardingPage from '../pages/OnboardingPage'
import { useSession } from '../stores/SessionProvider'

function RequireAuth() {
  const { status } = useSession()
  if (status === 'loading') {
    return (
      <p className="restore-status" role="status">
        正在接上…
      </p>
    )
  }
  return status === 'authenticated' ? <Outlet /> : <Navigate to="/" replace />
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<OnboardingPage />} />
      <Route element={<RequireAuth />}>
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/apps" element={<ApplicationsPage />} />
        <Route path="/jingyan" element={<KnowledgePage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
