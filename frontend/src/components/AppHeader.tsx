import { useLocation, useNavigate } from 'react-router'
import { AccountMenu } from './AccountMenu'
import { useSession } from '../stores/SessionProvider'

export function AppHeader() {
  const location = useLocation()
  const navigate = useNavigate()
  const { status, requestChatAccess, requestAppsAccess } = useSession()
  const path = location.pathname
  const onWelcome = path === '/'
  const onChat = path === '/chat'
  const onJingyan = path === '/jingyan'
  const onApps = path === '/apps'
  const entered = status === 'authenticated'

  return (
    <header className="nav">
      <div className="tabs">
        {entered ? (
          <AccountMenu />
        ) : (
          <button
            className={`tab${onWelcome ? ' is-on' : ''}`}
            type="button"
            onClick={() => navigate('/')}
          >
            初次见面
          </button>
        )}
        <button
          className={`tab${onChat ? ' is-on' : ''}`}
          type="button"
          onClick={() => {
            if (requestChatAccess()) {
              navigate('/chat')
            }
          }}
        >
          日常对话
        </button>
      </div>
      <div className="tabs tabs-right">
        <button
          className={`tab${onJingyan ? ' is-on' : ''}`}
          type="button"
          onClick={() => {
            if (requestAppsAccess()) {
              navigate('/jingyan')
            }
          }}
        >
          我的面经
        </button>
        <button
          className={`tab${onApps ? ' is-on' : ''}`}
          type="button"
          onClick={() => {
            if (requestAppsAccess()) {
              navigate('/apps')
            }
          }}
        >
          我的投递
        </button>
      </div>
    </header>
  )
}
