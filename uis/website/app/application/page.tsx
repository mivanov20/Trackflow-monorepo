import type { Metadata } from "next";
import { ApplicationForm } from "./application-form";

export const metadata: Metadata = {
  title: "TrackFlow | Client Application Form",
  description: "Apply to onboard your brand with TrackFlow logistics operations.",
};

export default function ApplicationPage() {
  return <ApplicationForm />;
}