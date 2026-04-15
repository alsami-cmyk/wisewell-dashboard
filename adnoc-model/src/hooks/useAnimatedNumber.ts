import { useState, useEffect, useRef } from 'react'

/**
 * Animates from the previous value to `target` using an ease-out cubic curve.
 * Returns the current interpolated value for display.
 */
export function useAnimatedNumber(target: number, duration = 380): number {
  const [current, setCurrent] = useState(target)
  const fromRef  = useRef(target)
  const frameRef = useRef(0)

  useEffect(() => {
    const from = fromRef.current
    if (from === target) return

    const start = performance.now()

    const tick = (now: number) => {
      const t = Math.min((now - start) / duration, 1)
      const eased = 1 - Math.pow(1 - t, 3)          // ease-out cubic
      setCurrent(from + (target - from) * eased)
      if (t < 1) {
        frameRef.current = requestAnimationFrame(tick)
      } else {
        fromRef.current = target
      }
    }

    cancelAnimationFrame(frameRef.current)
    frameRef.current = requestAnimationFrame(tick)

    return () => cancelAnimationFrame(frameRef.current)
  }, [target, duration])

  return current
}
