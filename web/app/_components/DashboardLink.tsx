"use client";

import { useCallback, useEffect, type MouseEvent, type ReactNode } from "react";
import { useDashboardNavigation } from "./DashboardNavigation";

type DashboardLinkProps = {
  children: ReactNode;
  className?: string;
  href: string;
  replace?: boolean;
};

export default function DashboardLink({ children, className, href, replace = false }: DashboardLinkProps) {
  const navigation = useDashboardNavigation();
  const prefetch = useCallback(() => navigation?.preload(href), [href, navigation]);

  useEffect(() => {
    prefetch();
  }, [prefetch]);

  const navigate = (event: MouseEvent<HTMLAnchorElement>) => {
    if (!navigation || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    event.currentTarget.classList.add("is-navigating");
    event.currentTarget.setAttribute("aria-busy", "true");
    void navigation.navigate(href, replace);
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
