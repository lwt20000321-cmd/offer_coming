import { useEffect } from 'react'

export function PointerWash() {
  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    if (media.matches) {
      return undefined
    }

    const root = document.documentElement
    let tx = 0.42
    let ty = 0.28
    let cx = 0.42
    let cy = 0.28
    let t = 0
    let frame = 0
    let reduced = false

    const onPointer = (event: PointerEvent) => {
      tx = event.clientX / window.innerWidth
      ty = event.clientY / window.innerHeight
    }

    const onReduceChange = () => {
      reduced = media.matches
    }

    const wave = () => {
      if (!reduced) {
        t += 0.008
        cx += (tx - cx) * 0.06
        cy += (ty - cy) * 0.06
        const w1 = Math.sin(t) * 0.04
        const w2 = Math.cos(t * 0.85) * 0.05
        root.style.setProperty('--mx', String(cx + w1))
        root.style.setProperty('--my', String(cy + w2))
        root.style.setProperty('--mx2', String(1 - cx + w2))
        root.style.setProperty('--my2', String(1 - cy + w1))
      }
      frame = window.requestAnimationFrame(wave)
    }

    window.addEventListener('pointermove', onPointer)
    media.addEventListener('change', onReduceChange)
    frame = window.requestAnimationFrame(wave)

    return () => {
      window.removeEventListener('pointermove', onPointer)
      media.removeEventListener('change', onReduceChange)
      window.cancelAnimationFrame(frame)
    }
  }, [])

  return (
    <>
      <div className="wash" aria-hidden="true" />
      <div className="wash-follow" aria-hidden="true" />
    </>
  )
}
