import { useEffect, useState } from 'react'

const OFFER_WORDS = Array.from({ length: 16 }, () => 'OFFER')

export function OfferTicker() {
  const [reduced, setReduced] = useState(false)

  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    const sync = () => setReduced(media.matches)
    sync()
    media.addEventListener('change', sync)
    return () => media.removeEventListener('change', sync)
  }, [])

  function offerRun(prefix: string) {
    return (
      <div className="ticker-run">
        {OFFER_WORDS.map((word, index) => (
          <span key={`${prefix}-${word}-${index}`}>{word}</span>
        ))}
      </div>
    )
  }

  return (
    <>
      <div className="ticker ticker-top" aria-hidden="true">
        <div className={`ticker-track ticker-left${reduced ? ' is-static' : ''}`}>
          {offerRun('top-a')}
          {offerRun('top-b')}
        </div>
      </div>
      <div className="ticker ticker-bottom" aria-hidden="true">
        <div className={`ticker-track ticker-right${reduced ? ' is-static' : ''}`}>
          {offerRun('bottom-a')}
          {offerRun('bottom-b')}
        </div>
      </div>
    </>
  )
}
