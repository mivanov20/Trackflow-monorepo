import type { NextConfig } from "next";

// Dirección de la API vista desde ESTE servidor Next. En Docker es el nombre del servicio de Compose
// (http://api:8000), nunca localhost: un contenedor no llega a otro por localhost.
const apiInternalUrl = process.env.API_INTERNAL_URL?.trim().replace(/\/$/, "");

const nextConfig: NextConfig = {
  async rewrites() {
    // El navegador llama a /backend/...; este servidor reenvía la petición a la API por su nombre de servicio.
    // Solo se activa si API_INTERNAL_URL está definido (en Docker), así fuera de Docker nada cambia:
    // el navegador sigue usando NEXT_PUBLIC_INVENTORY_API_URL directamente.
    return apiInternalUrl ? [{ source: "/backend/:path*", destination: `${apiInternalUrl}/:path*` }] : [];
  },
  experimental: {
    // El análisis de un archivo grande puede tardar más que los 30 s por defecto del proxy.
    proxyTimeout: 600_000,
    // Next corta por defecto a 10 MB el cuerpo de las peticiones que reenvía: un CSV mayor llegaría
    // truncado a la API, que se quedaría esperando el resto. El límite real lo impone la API
    // (ANALYSIS_MAX_UPLOAD_MB, 250 MB por defecto); este solo debe ser mayor.
    middlewareClientMaxBodySize: "300mb",
  },
};

export default nextConfig;
