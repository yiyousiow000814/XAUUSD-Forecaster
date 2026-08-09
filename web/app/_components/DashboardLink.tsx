"use client";

import { useCallback, useEffect, useState, type MouseEvent, type ReactNode } from "react";
import { useRouter } from "next/navigation";

type DashboardLinkProps = {
  children: ReactNode;
  className?: string;
  href: string;
  replace?: boolean;
};

export default function DashboardLink({ children, className, href, replace = false }: DashboardLinkProps) {
  const router = useRouter();
  const [navigating, setNavigating] = useState(false);
  const prefetch = useCallback(() => router.prefetch(href), [href, router]);

  useEffect(() => {
    prefetch();
  }, [prefetch]);

  const navigate = (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (replace) router.replace(href);
    else router.push(href);
    setNavigating(true);
  };

  return <a
    aria-busy={navigating || undefined}
    className={[className, navigating ? "is-navigating" : ""].filter(Boolean).join(" ")}
    href={href}
    onClick={navigate}
    onFocus={prefetch}
    onPointerEnter={prefetch}
  >
    {children}
  </a>;
}
