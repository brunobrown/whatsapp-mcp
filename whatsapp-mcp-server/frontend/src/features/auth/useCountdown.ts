import { useEffect, useState } from "react";

/** Counts a wait down by the second. Returns the seconds left and a way to start one. */
export function useCountdown(): [number, (seconds: number) => void] {
  const [until, setUntil] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (until === 0) return;
    const timer = setInterval(() => {
      const current = Date.now();
      setNow(current);
      if (current >= until) clearInterval(timer);
    }, 1000);
    return () => {
      clearInterval(timer);
    };
  }, [until]);
  const start = (seconds: number): void => {
    const current = Date.now();
    setNow(current);
    setUntil(current + seconds * 1000);
  };
  return [Math.max(0, Math.ceil((until - now) / 1000)), start];
}
