export type Language = "en" | "es";

export interface ContentCardData {
  eyebrow?: string;
  title: string;
  description: string;
  tone: "mint" | "sun" | "plain";
}

export interface SnapshotStatData {
  label: string;
  value: string;
}

export interface WebsiteCopy {
  tagline: string;
  navFeatures: string;
  navHow: string;
  navWhy: string;
  navApply: string;
  mobileApply: string;
  heroBadge: string;
  heroTitle: string;
  heroText: string;
  heroStart: string;
  heroHow: string;
  teamMembers: string;
  warehouseSites: string;
  carrierPartners: string;
  featuresTitle: string;
  featuresText: string;
  features: ContentCardData[];
  howTitle: string;
  steps: ContentCardData[];
  whyTitle: string;
  reasons: ContentCardData[];
  snapshotTitle: string;
  snapshotText: string;
  stats: SnapshotStatData[];
  snapshots: ContentCardData[];
  contactTitle: string;
  contactEmail: string;
  contactLocation: string;
  footerApply: string;
  footerTagline: string;
  footerRights: string;
}

const english: WebsiteCopy = {
  tagline: "End-to-end logistics",
  navFeatures: "Features",
  navHow: "How it works",
  navWhy: "Why TrackFlow",
  navApply: "Apply now",
  mobileApply: "Apply",
  heroBadge: "Integrated logistics for growing e-commerce brands",
  heroTitle: "Your parcels move. Your team focuses on growth.",
  heroText:
    "TrackFlow runs your entire logistics operation, from storage and pick-pack to carrier delivery and returns, so your brand can focus on selling while we handle the physical complexity.",
  heroStart: "Start application",
  heroHow: "See how it works",
  teamMembers: "Team members",
  warehouseSites: "Warehouse sites",
  carrierPartners: "Carrier partners",
  featuresTitle: "Core capabilities",
  featuresText:
    "TrackFlow Tech modernizes operations with integrated logistics systems for warehouses, carriers, returns, and customer support.",
  features: [
    { title: "Unified inventory visibility", description: "One real-time view of stock across every warehouse site to eliminate blind spots and stockouts.", tone: "plain" },
    { title: "Smart carrier orchestration", description: "Select the right carrier by destination, urgency, and weight with explainable recommendations.", tone: "mint" },
    { title: "Automated returns flow", description: "Rules-driven approvals and AI-assisted product condition checks reduce delays and manual workload.", tone: "sun" },
  ],
  howTitle: "How it works",
  steps: [
    { eyebrow: "Step 01", title: "Connect your channels", description: "Share order sources and priorities. We map your SKUs, fulfillment rules, and SLAs.", tone: "mint" },
    { eyebrow: "Step 02", title: "Operate in real time", description: "Inventory, shipment status, incidents, and returns are visible through centralized dashboards.", tone: "sun" },
    { eyebrow: "Step 03", title: "Scale with confidence", description: "Automated reporting, alerting, and customer support improve speed, margin, and retention.", tone: "plain" },
  ],
  whyTitle: "Built for real-world logistics complexity",
  reasons: [
    { title: "Complex operations, connected", description: "Multiple warehouse systems, carrier diversity, and multilingual workflows connected through one operating model.", tone: "sun" },
    { title: "Data that drives decisions", description: "Executive dashboards and automated weekly reporting provide fresher insight than manual spreadsheet cycles.", tone: "mint" },
  ],
  snapshotTitle: "Company snapshot from TrackFlow briefing",
  snapshotText: "Facts below come from the internal TrackFlow briefing and summarize the current company context.",
  stats: [
    { label: "Founded", value: "2009" },
    { label: "Annual revenue", value: "~EUR 9M" },
    { label: "Warehouse sites", value: "2 sites" },
    { label: "Team size", value: "~130 people" },
  ],
  snapshots: [
    { title: "Leadership and structure", description: "CEO Thomas Harry leads TrackFlow, while Andrés Kim heads its technology team.", tone: "mint" },
    { title: "Current operational gaps", description: "Inventory is fragmented across warehouses, carrier operations are manual, and reporting is assembled by hand.", tone: "sun" },
  ],
  contactTitle: "Contact",
  contactEmail: "Email: growth@trackflow.example",
  contactLocation: "Distributed teams, connected operations",
  footerApply: "Apply to onboard your brand",
  footerTagline: "TrackFlow Tech | Last-mile delivery and warehouse management",
  footerRights: "© 2026 TrackFlow. All rights reserved.",
};

const spanish: WebsiteCopy = {
  tagline: "Logistica integral",
  navFeatures: "Funciones",
  navHow: "Como funciona",
  navWhy: "Por que TrackFlow",
  navApply: "Aplicar ahora",
  mobileApply: "Aplicar",
  heroBadge: "Logistica integrada para marcas de comercio electronico",
  heroTitle: "Tus paquetes se mueven. Tu equipo se enfoca en crecer.",
  heroText: "TrackFlow gestiona toda tu operacion logistica, desde almacenamiento y pick-pack hasta entrega con carriers y devoluciones, para que tu marca se enfoque en vender.",
  heroStart: "Iniciar solicitud",
  heroHow: "Ver como funciona",
  teamMembers: "Miembros del equipo",
  warehouseSites: "Sitios de almacen",
  carrierPartners: "Carriers aliados",
  featuresTitle: "Capacidades clave",
  featuresText: "TrackFlow Tech moderniza operaciones con sistemas integrados para almacenes, carriers, devoluciones y atencion al cliente.",
  features: [
    { title: "Visibilidad unificada de inventario", description: "Una vista en tiempo real del stock en cada almacen para eliminar puntos ciegos y quiebres de stock.", tone: "plain" },
    { title: "Orquestacion inteligente de carriers", description: "Selecciona el carrier ideal segun destino, urgencia y peso con recomendaciones explicables.", tone: "mint" },
    { title: "Flujo automatizado de devoluciones", description: "Aprobaciones por reglas e inspeccion asistida por IA para reducir retrasos y carga manual.", tone: "sun" },
  ],
  howTitle: "Como funciona",
  steps: [
    { eyebrow: "Paso 01", title: "Conecta tus canales", description: "Comparte fuentes de pedidos y prioridades. Mapeamos tus SKU, reglas de fulfillment y SLA.", tone: "mint" },
    { eyebrow: "Paso 02", title: "Opera en tiempo real", description: "Inventario, estado de envios, incidencias y devoluciones visibles en tableros centralizados.", tone: "sun" },
    { eyebrow: "Paso 03", title: "Escala con confianza", description: "Reportes, alertas y soporte automatizado mejoran velocidad, margen y retencion.", tone: "plain" },
  ],
  whyTitle: "Disenado para la complejidad logistica real",
  reasons: [
    { title: "Operaciones complejas, conectadas", description: "Varios sistemas de almacen, diversidad de carriers y flujos multilingues conectados en un solo modelo operativo.", tone: "sun" },
    { title: "Datos para decidir mejor", description: "Tableros ejecutivos y reportes semanales automaticos con informacion mas fresca que los procesos manuales.", tone: "mint" },
  ],
  snapshotTitle: "Resumen de empresa segun briefing de TrackFlow",
  snapshotText: "Los datos de abajo vienen del briefing interno de TrackFlow y resumen el contexto actual de la empresa.",
  stats: [
    { label: "Fundada", value: "2009" },
    { label: "Ingresos anuales", value: "~EUR 9M" },
    { label: "Sitios de almacen", value: "2 sitios" },
    { label: "Tamano del equipo", value: "~130 personas" },
  ],
  snapshots: [
    { title: "Liderazgo y estructura", description: "El CEO Thomas Harry lidera TrackFlow y Andrés Kim dirige el equipo de tecnologia.", tone: "mint" },
    { title: "Brechas operativas actuales", description: "El inventario esta fragmentado entre almacenes, la operacion con carriers es manual y los reportes se arman a mano.", tone: "sun" },
  ],
  contactTitle: "Contacto",
  contactEmail: "Correo: growth@trackflow.example",
  contactLocation: "Equipos distribuidos, operaciones conectadas",
  footerApply: "Aplica para integrar tu marca",
  footerTagline: "TrackFlow Tech | Gestion de ultima milla y almacenes",
  footerRights: "© 2026 TrackFlow. All rights reserved.",
};

export const websiteCopy: Record<Language, WebsiteCopy> = { en: english, es: spanish };