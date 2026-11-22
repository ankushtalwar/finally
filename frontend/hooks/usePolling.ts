"use client";

import { useEffect, useRef } from "react";

/** Run `fn` now and every `ms` while mounted. The latest `fn` is always used. */
export function usePolling(fn: () => void, ms: number) {
  const ref = useRef(fn);
  useEffect(() => {
    ref.current = fn;
  }, [fn]);
  useEffect(() => {
    ref.current();
    const id = setInterval(() => ref.current(), ms);
    return () => clearInterval(id);
  }, [ms]);
}
