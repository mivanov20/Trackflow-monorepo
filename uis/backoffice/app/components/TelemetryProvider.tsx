"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { useReportWebVitals } from "next/web-vitals";
import { enterRoute, installFlowListeners, leavePage } from "../../lib/telemetry-flows";
import { installErrorCapture, recordWebVital, reportPageLoad } from "../../lib/telemetry-errors";
import { startTelemetry } from "../../lib/telemetry";

/**
 * Starts the capture system once for the whole backoffice: page views, flows and forms, uncaught errors and
 * Web Vitals. Pages and components never track anything themselves; this is mounted once in the root layout.
 */
export function TelemetryProvider() {
  const pathname = usePathname();

  useEffect(() => {
    const stopFlows = installFlowListeners();
    const stopErrors = installErrorCapture();
    // Registered before the service's own listeners, so the page-load and flow events are queued first
    // and leave in the same beacon batch when the tab is hidden or closed.
    const onHide = () => {
      reportPageLoad();
      leavePage();
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") onHide();
    };
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pagehide", onHide);
    const stopService = startTelemetry();
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pagehide", onHide);
      stopService();
      stopErrors();
      stopFlows();
    };
  }, []);

  useEffect(() => {
    enterRoute(pathname, window.location.search);
  }, [pathname]);

  useReportWebVitals(recordWebVital);

  return null;
}
