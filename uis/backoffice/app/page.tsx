import { BackofficeDashboard } from "./dashboard";
import { buildBackofficeAnalytics } from "../lib/analytics";
import { demoShipments } from "../lib/demo-data";

export default function BackofficePage() {
  const shipments = demoShipments.map(({ createdAt, ...shipment }) => ({
    ...shipment,
    createdAt: createdAt.toISOString(),
  }));

  return <BackofficeDashboard shipments={shipments} analytics={buildBackofficeAnalytics()} />;
}