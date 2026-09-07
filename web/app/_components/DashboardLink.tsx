"use client";

import { useCallback, useEffect, type MouseEvent, type ReactNode } from "react";
import { useDashboardNavigation } from "./DashboardNavigation";

type DashboardLinkProps = {
  ariaCurrent?: "page";
  ariaLabel?: string;
  children: ReactNode;
  className?: string;
  href: string;
  replace?: boolean;
};

export default function DashboardLink({ ariaCurrent, ariaLabel, children, className, href, replace = false }: DashboardLinkProps) {
  const navigation = useDashboardNavigation();
  const prefetch = useCallback(() => navigation?.preload(href), [href, navigation]);

  useEffect(() => {
    prefetch();
  }, [prefetch]);

  const navigate = (event: MouseEvent<HTMLAnchorElement>) => {
    if (!navigation || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    const link = event.currentTarget;
    link.classList.add("is-navigating");
    link.setAttribute("aria-busy", "true");
    void navigation.navigate(href, replace).finally(() => {
      link.classList.remove("is-navigating");
      link.removeAttribute("aria-busy");
    });
  };

  return <a
    aria-current={ariaCurrent}
    aria-label={ariaLabel}
    className={className}
    href={href}
    onClick={navigate}
    onFocus={prefetch}
    onPointerEnter={prefetch}
  >
    {children}
  </a>;
}
