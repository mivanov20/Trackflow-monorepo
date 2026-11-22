"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { BackofficeAnalytics } from "../lib/analytics";
import { clearSession } from "../lib/auth";
import type { SampleShipment } from "../lib/demo-data";
import { RfpLiveFeed } from "./components/RfpLiveFeed";

type DisplayShipment = Omit<SampleShipment, "createdAt"> & { createdAt: string };

interface BackofficeDashboardProps {
  shipments: DisplayShipment[];
  analytics: BackofficeAnalytics;
}

function statusClass(status: string): string {
  if (status === "In transit") return "transit";
  if (status === "Assigned" || status === "Pending") return "handoff";
  if (status === "Failed") return "exception";
  return "delivered";
}

function formatMoney(amount: number): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(amount);
}

function M2Results({ analytics }: { analytics: BackofficeAnalytics }) {
  return (
    <section className="m2-section" aria-labelledby="m2-title">
      <div className="m2-heading">
        <div><p className="eyebrow">MILESTONE 2 · LIVE COMPUTATIONS</p><h2 id="m2-title">Operations algorithms</h2></div>
        <span className="m2-source">Imported from <code>src/utils</code></span>
      </div>
      <div className="m2-grid">
        <article className="m2-card m2-card-highlight">
          <span className="m2-label">INVENTORY VALUE</span>
          <strong>{formatMoney(analytics.inventoryValue)}</strong>
          <p>Across {analytics.categories.length} product categories</p>
        </article>
        <article className="m2-card">
          <span className="m2-label">LOW STOCK ALERTS</span>
          <strong>{analytics.lowStockProducts.length.toString().padStart(2, "0")}</strong>
          <ul>{analytics.lowStockProducts.map((product) => <li key={product}>{product}</li>)}</ul>
        </article>
        <article className="m2-card">
          <span className="m2-label">CARRIER RECOMMENDATION</span>
          <strong className="m2-result-title">{analytics.carrierRecommendation}</strong>
          <p>{analytics.recommendedCost === null ? "No eligible carrier" : `${formatMoney(analytics.recommendedCost)} · score ${analytics.recommendedScore}`}</p>
        </article>
        <article className="m2-card">
          <span className="m2-label">AVERAGE SHIPMENT DISTANCE</span>
          <strong>{analytics.averageDistance.toLocaleString("en-US")} <small>km</small></strong>
          <p>Calculated across the sample shipments</p>
        </article>
        <article className="m2-card">
          <span className="m2-label">SHIPMENT STATUS GROUPS</span>
          <div className="m2-count-list">{analytics.shipmentGroups.filter((group) => group.count > 0).map((group) => <span key={group.status}>{group.status}<b>{group.count}</b></span>)}</div>
        </article>
        <article className="m2-card">
          <span className="m2-label">MOST USED CARRIERS</span>
          <div className="m2-count-list">{analytics.topCarriers.map((carrier) => <span key={carrier.carrier}>{carrier.carrier}<b>{carrier.count}</b></span>)}</div>
          <p>Highest reliability: {analytics.mostReliableCarrier}</p>
        </article>
        <article className="m2-card">
          <span className="m2-label">LOOKUP & FILTER CHECKS</span>
          <p>SKU lookup: <b>{analytics.foundProduct}</b></p>
          <p>Shipment lookup: <b>{analytics.foundShipment}</b></p>
          <p>Binary weight search: <b>{analytics.productWeightSearch}</b></p>
          <p>Zaragoza stock: <b>{analytics.zaragozaStockCount} products</b></p>
          <p>Electronics filter: <b>{analytics.electronicsCount} products</b></p>
          <p>Lowest stock: <b>{analytics.lowestStockSku}</b></p>
        </article>
        <article className="m2-card">
          <span className="m2-label">RECORD VALIDATION</span>
          <strong className="m2-result-title">{analytics.validationSummary}</strong>
          {analytics.validationDetails.map((detail) => <p key={detail}>{detail}</p>)}
        </article>
        <article className="m2-card m2-card-wide">
          <span className="m2-label">PRODUCT CATEGORY COUNTS</span>
          <div className="m2-count-list m2-category-list">{analytics.categoryCounts.map((category) => <span key={category.category}>{category.category}<b>{category.count}</b></span>)}</div>
        </article>
      </div>
    </section>
  );
}

export function BackofficeDashboard({ shipments, analytics }: BackofficeDashboardProps) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [warehouse, setWarehouse] = useState("all");
  const [status, setStatus] = useState("all");
  const [updatedAt, setUpdatedAt] = useState("--:--");

  useEffect(() => {
    function updateTimestamp() {
      setUpdatedAt(new Intl.DateTimeFormat("en", { hour: "2-digit", minute: "2-digit" }).format(new Date()));
    }
    updateTimestamp();
    function handleShortcut(event: KeyboardEvent) {
      const target = event.target;
      if (event.key === "/" && target instanceof HTMLElement && !["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName)) {
        event.preventDefault();
        document.querySelector<HTMLInputElement>("#searchInput")?.focus();
      }
    }
    document.addEventListener("keydown", handleShortcut);
    return () => document.removeEventListener("keydown", handleShortcut);
  }, []);

  const visibleShipments = shipments.filter((shipment) => {
    const normalizedQuery = query.trim().toLowerCase();
    const matchesQuery = [shipment.id, shipment.brand, shipment.destination.city, shipment.destination.country, shipment.carrier ?? ""]
      .some((value) => value.toLowerCase().includes(normalizedQuery));
    return matchesQuery && (warehouse === "all" || shipment.origin === warehouse) && (status === "all" || shipment.status === status);
  });
  const transitCount = visibleShipments.filter((shipment) => shipment.status === "In transit").length;
  const handoffCount = visibleShipments.filter((shipment) => shipment.status === "Assigned" || shipment.status === "Pending").length;

  return (
    <div className="workspace">
      <aside className="sidebar" aria-label="Workspace navigation">
        <a className="brand" href="#main" aria-label="TrackFlow operations desk"><span className="brand-mark" aria-hidden="true">T</span><span>trackflow<span className="brand-sub">OPERATIONS</span></span></a>
        <div className="workspace-switcher"><span className="switcher-mark" aria-hidden="true">O</span><span><strong>Operations</strong><small>Internal workspace</small></span><span className="switcher-caret" aria-hidden="true">⌄</span></div>
        <p className="nav-caption">WORKSPACE</p>
        <nav className="primary-nav">
          <a className="nav-item selected" href="#overview" aria-current="page"><span className="nav-glyph" aria-hidden="true">▦</span>Overview</a>
          <a className="nav-item" href="#shipments"><span className="nav-glyph" aria-hidden="true">↗</span>Shipments<span className="nav-count">{shipments.length.toString().padStart(2, "0")}</span></a>
          <a className="nav-item" href="#m2-title"><span className="nav-glyph" aria-hidden="true">⌖</span>M2 analytics</a>
          <Link className="nav-item" href="/backoffice/inventory/products"><span className="nav-glyph" aria-hidden="true">▤</span>Inventory</Link>
          <Link className="nav-item" href="/backoffice/rfp"><span className="nav-glyph" aria-hidden="true">✉</span>RFP intake</Link>
          <Link className="nav-item" href="/backoffice/incidents"><span className="nav-glyph" aria-hidden="true">!</span>Incidents</Link>
          <Link className="nav-item" href="/backoffice/incidents/analyze"><span className="nav-glyph" aria-hidden="true">≣</span>Analyze incidents</Link>
          <Link className="nav-item" href="/account/profile"><span className="nav-glyph" aria-hidden="true">☺</span>My account</Link>
        </nav>
        <div className="sidebar-bottom"><button className="nav-item nav-signout" type="button" onClick={() => { clearSession(); router.replace("/login"); }}><span className="nav-glyph" aria-hidden="true">⎋</span>Sign out</button><div className="connection-state"><span></span>DEMO MODE</div><p>Illustrative data<br />No live systems connected</p><a href="http://localhost:3000/">Public website <span aria-hidden="true">↗</span></a></div>
      </aside>

      <main id="main" className="main-area">
        <header className="topbar"><div className="breadcrumbs"><span>Operations</span><span aria-hidden="true">/</span><strong>Overview</strong></div><div className="topbar-meta"><span className="demo-tag"><span></span>DEMO DATA</span><span className="avatar" aria-label="Demo user">TF</span></div></header>
        <div className="content-wrap">
          <section className="page-heading" id="overview">
            <div><p className="eyebrow">OPERATIONS / OVERVIEW</p><h1>Operations desk</h1><p className="page-subtitle">A sample view across warehouse handoffs and active shipments.</p></div>
            <button className="refresh-button" type="button" title="Update the local demo timestamp" onClick={() => setUpdatedAt(new Intl.DateTimeFormat("en", { hour: "2-digit", minute: "2-digit" }).format(new Date()))}><span aria-hidden="true">↻</span> Refresh view</button>
          </section>
          <section className="demo-notice" aria-label="Demo data notice"><span className="notice-mark" aria-hidden="true">i</span><p><strong>Illustrative workspace.</strong> These example records are local to this page and are not connected to TrackFlow systems.</p><span className="updated-label">UPDATED <time>{updatedAt}</time></span></section>

          <section className="overview-grid" aria-label="Sample shipment summary">
            <article className="summary-card"><div className="summary-top"><span>EXAMPLE SHIPMENTS</span><span className="summary-symbol">↗</span></div><strong>{visibleShipments.length.toString().padStart(2, "0")}</strong><p>Rows in this sample view</p></article>
            <article className="summary-card"><div className="summary-top"><span>IN TRANSIT</span><span className="summary-symbol green">⇢</span></div><strong>{transitCount.toString().padStart(2, "0")}</strong><p>Illustrative status count</p></article>
            <article className="summary-card"><div className="summary-top"><span>NEEDS HANDOFF</span><span className="summary-symbol orange">◷</span></div><strong>{handoffCount.toString().padStart(2, "0")}</strong><p>Illustrative status count</p></article>
            <article className="summary-card location-card"><div className="summary-top"><span>WAREHOUSE SITES</span><span className="summary-symbol">⌖</span></div><strong>02</strong><p>Example warehouse sites</p></article>
          </section>

          <RfpLiveFeed />

          <section className="shipment-section" id="shipments" aria-labelledby="shipment-title">
            <div className="section-title-row"><div><p className="eyebrow">SAMPLE OPERATIONS <span className="demo-inline">DEMO</span></p><h2 id="shipment-title">Shipment activity</h2></div><a className="text-link" href="#shipment-title">View all <span aria-hidden="true">→</span></a></div>
            <div className="toolbar">
              <label className="search-field"><span aria-hidden="true">⌕</span><span className="visually-hidden">Search shipments</span><input id="searchInput" type="search" placeholder="Search order, destination or carrier" autoComplete="off" value={query} onChange={(event) => setQuery(event.target.value)} /><kbd>/</kbd></label>
              <div className="filter-group"><label className="visually-hidden" htmlFor="locationFilter">Filter by warehouse site</label><select id="locationFilter" value={warehouse} onChange={(event) => setWarehouse(event.target.value)}><option value="all">All warehouse sites</option><option value="Los Angeles">Los Angeles</option><option value="Zaragoza">Zaragoza</option></select><label className="visually-hidden" htmlFor="statusFilter">Filter by status</label><select id="statusFilter" value={status} onChange={(event) => setStatus(event.target.value)}><option value="all">All statuses</option><option value="Pending">Pending</option><option value="Assigned">Assigned</option><option value="In transit">In transit</option><option value="Delivered">Delivered</option><option value="Failed">Failed</option></select></div>
            </div>
            <div className="table-wrap"><table><thead><tr><th scope="col">ORDER</th><th scope="col">DESTINATION</th><th scope="col">CARRIER</th><th scope="col">WAREHOUSE</th><th scope="col">STATUS</th><th scope="col">CREATED</th><th><span className="visually-hidden">Details</span></th></tr></thead>
              <tbody>{visibleShipments.map((shipment) => <tr key={shipment.id}><td><strong className="order-id">{shipment.id}</strong><span className="order-brand">{shipment.brand}</span></td><td className="destination">{shipment.destination.city}, {shipment.destination.country}</td><td className="carrier-cell">{shipment.carrier ?? "Unassigned"}</td><td><span className="warehouse-tag">{shipment.origin}</span></td><td><span className={`status ${statusClass(shipment.status)}`}>{shipment.status}</span></td><td className="time-cell">{new Date(shipment.createdAt).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}</td><td className="row-more">···</td></tr>)}</tbody>
              </table>{visibleShipments.length === 0 && <p className="empty-state">No sample shipments match those filters.</p>}</div>
            <div className="table-footer"><span aria-live="polite">Showing {visibleShipments.length} of {shipments.length} sample shipments</span><span>Sample records · no live data</span></div>
          </section>

          <M2Results analytics={analytics} />
          <footer className="page-footer"><span>TRACKFLOW OPERATIONS</span><span>Internal workspace · Demo view</span></footer>
        </div>
      </main>
    </div>
  );
}