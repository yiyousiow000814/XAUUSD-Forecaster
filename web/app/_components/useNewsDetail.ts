"use client";
import { useCallback, useEffect, useState } from "react";
import { loadNewsDetail, readNewsDetail, type NewsDetailTarget } from "../_lib/news-detail-loader";

export function useNewsDetail(target: NewsDetailTarget | null) {
  const identity = JSON.stringify(target);
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{identity: string; payload: Record<string, unknown> | null; failed: boolean} | null>(null);
  const cached = target ? readNewsDetail(target) : null;
  useEffect(() => {
    const request = JSON.parse(identity) as NewsDetailTarget | null;
    if (!request) return;
    let cancelled = false;
    void loadNewsDetail(request).then(value => {
      if (!cancelled) setResult({identity, payload: value.payload, failed: false});
    }).catch(() => {
      if (!cancelled) setResult({identity, payload: null, failed: true});
    });
    return () => { cancelled = true; };
  }, [identity, attempt]);
  const retry = useCallback(() => { setResult(null); setAttempt(value => value + 1); }, []);
  const current = result?.identity === identity ? result : null;
  return { payload: cached?.payload ?? current?.payload ?? null, failed: current?.failed ?? false, retry };
}
