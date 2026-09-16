import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router'
import accountAvatar from '../assets/account-avatar.svg'
import { KeyPanel } from './KeyPanel'
import { ResumePanel } from './ResumePanel'
import { useSession } from '../stores/SessionProvider'

export function AccountMenu() {
  const navigate = useNavigate()
  const { signOut } = useSession()
  const rootRef = useRef<HTMLDivElement>(null)
  const [menuOpen, setMenuOpen] = useState(false)
  const [sheet, setSheet] = useState<null | 'resume' | 'key'>(null)
  const [signingOut, setSigningOut] = useState(false)

  useEffect(() => {
    if (!menuOpen) {
      return
    }
    function onPointerDown(event: PointerEvent) {
      const target = event.target
      if (!(target instanceof Node)) {
        return
      }
      if (rootRef.current?.contains(target)) {
        return
      }
      setMenuOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [menuOpen])

  function openSheet(next: 'resume' | 'key') {
    setMenuOpen(false)
    setSheet(next)
  }

  async function onExit() {
    if (signingOut) {
      return
    }
    setSigningOut(true)
    setMenuOpen(false)
    setSheet(null)
    try {
      await signOut()
      navigate('/', { replace: true })
    } finally {
      setSigningOut(false)
    }
  }

  return (
    <div className="account" id="account" ref={rootRef}>
      <button
        className="avatar-btn"
        type="button"
        id="avatar-btn"
        aria-label="账号"
        aria-expanded={menuOpen}
        aria-haspopup="menu"
        onClick={() => setMenuOpen((open) => !open)}
      >
        <img src={accountAvatar} alt="账号" width={28} height={28} />
      </button>
      <div className={`account-menu${menuOpen ? ' is-on' : ''}`} id="account-menu" role="menu">
        <button type="button" id="menu-resume" role="menuitem" onClick={() => openSheet('resume')}>
          我的简历
        </button>
        <button type="button" id="menu-key" role="menuitem" onClick={() => openSheet('key')}>
          我的key
        </button>
        <button
          type="button"
          className="exit"
          id="menu-exit"
          role="menuitem"
          disabled={signingOut}
          onClick={() => void onExit()}
        >
          退出
        </button>
      </div>
      {createPortal(
        <>
          <ResumePanel open={sheet === 'resume'} onClose={() => setSheet(null)} />
          <KeyPanel open={sheet === 'key'} onClose={() => setSheet(null)} />
        </>,
        document.body,
      )}
    </div>
  )
}
