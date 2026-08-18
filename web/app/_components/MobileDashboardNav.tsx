"use client";

import {
  DASHBOARD_GLOBAL_DESTINATIONS,
  type DashboardGlobalDestinationId,
} from "./DashboardNavigation";
import { useDashboardNavigation } from "./DashboardNavigation";

export default function MobileDashboardNav({
  activeDestination,
}: {
  activeDestination: DashboardGlobalDestinationId;
}) {
  const navigation = useDashboardNavigation();
  const currentHref = DASHBOARD_GLOBAL_DESTINATIONS.find(
    destination => destination.id === activeDestination,
  )?.href ?? "/";

  return <div className="mobile-dashboard-nav">
    <label>
      <span>主要区域</span>
      <select
        aria-label="切换主要区域"
        value={currentHref}
        onChange={event => {
          const href = event.currentTarget.value;
          if (navigation) void navigation.navigate(href);
          else window.location.assign(href);
        }}
      >
        {DASHBOARD_GLOBAL_DESTINATIONS.map(destination => (
          <option key={destination.id} value={destination.href}>{destination.label}</option>
        ))}
      </select>
    </label>
  </div>;
}
