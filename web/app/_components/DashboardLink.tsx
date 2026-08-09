"use client";

import { useCallback, useEffect, type MouseEvent, type ReactNode } from "react";
import { useRouter } from "next/navigation";

type DashboardLinkProps = {
  children: ReactNode;
  className?: string;
  href: string;
  replace?: boolean;
};

export default function DashboardLink({ children, className, href, replace = false }: DashboardLinkProps) {
  const router = useRouter();
  const prefetch = useCallback(() => router.prefetch(href), [href, router]);

  useEffect(() => {
    prefetch();
  }, [prefetch]);

  const navigate = (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    event.currentTarget.classList.add("is-navigating");
    event.currentTarget.setAttribute("aria-busy", "true");
    if (replace) router.replace(href);
    else router.push(href);
  };

  return <a
    className={className}
    href={href}
    onClick={navigate}
    onFocus={prefetch}
    onPointerEnter={prefetch}
  >
    {children}
  </a>;
}
