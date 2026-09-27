"use client";

import { HamburgerMenuIcon, Cross2Icon } from "@radix-ui/react-icons";
import { useEffect, useRef, type ReactNode } from "react";

export default function MobileDashboardNav({ children }: { children: ReactNode }) {
  const menu = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const element = menu.current;
    if (!element) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        element.open = false;
        element.querySelector("summary")?.focus();
      }
    };
    const closeOnNavigation = (event: MouseEvent) => {
      if (event.target instanceof Element && event.target.closest("a,button")) element.open = false;
    };
    element.addEventListener("keydown", closeOnEscape);
    element.addEventListener("click", closeOnNavigation);
    return () => {
      element.removeEventListener("keydown", closeOnEscape);
      element.removeEventListener("click", closeOnNavigation);
    };
  }, []);
  return <details className="public-mobile-menu" ref={menu}>
    <summary aria-label="打开或关闭导航"><HamburgerMenuIcon className="menu-open-icon" aria-hidden="true" /><Cross2Icon className="menu-close-icon" aria-hidden="true" /></summary>
    <div className="public-menu-content">{children}</div>
  </details>;
}
