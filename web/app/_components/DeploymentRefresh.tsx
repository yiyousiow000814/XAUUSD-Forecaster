"use client";

import { useEffect } from "react";
import { DASHBOARD_REFRESH_INTERVALS } from "../_lib/dashboard-refresh";

function scriptFingerprint(documentRoot: Document): string {
  return Array.from(documentRoot.scripts)
    .map((script) => script.src)
    .filter((source) => source.includes("/_next/static/") && source.endsWith(".js"))
    .map((source) => new URL(source, window.location.origin).pathname)
    .sort()
    .join("|");
}

export default function DeploymentRefresh() {
  useEffect(() => {
    if (navigator.webdriver) return;
    const loadedFingerprint = scriptFingerprint(document);
    if (!loadedFingerprint) return;

    let checking = false;
    const checkDeployment = async () => {
      if (checking || document.visibilityState !== "visible") return;
      checking = true;
      try {
        const response = await fetch(`${window.location.pathname}?__deployment_check=${Date.now()}`, {
          cache: "no-store",
          headers: { Accept: "text/html" },
        });
        if (!response.ok) return;
        const nextDocument = new DOMParser().parseFromString(await response.text(), "text/html");
        const nextFingerprint = scriptFingerprint(nextDocument);
        if (nextFingerprint && nextFingerprint !== loadedFingerprint) window.location.reload();
      } catch {
        // A transient network failure must not disturb the dashboard.
      } finally {
        checking = false;
      }
    };

    const timer = window.setInterval(checkDeployment, DASHBOARD_REFRESH_INTERVALS.deployment);
    window.addEventListener("focus", checkDeployment);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", checkDeployment);
    };
  }, []);

  return null;
}
