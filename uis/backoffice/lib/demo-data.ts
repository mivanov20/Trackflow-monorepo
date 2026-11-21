import type { Carrier, Product, Shipment } from "../../../src/types/models";

export interface SampleShipment extends Shipment {
  brand: string;
}

export const demoProducts: Product[] = [
  { sku: "TF-BAG-042", name: "Canvas travel bag", category: "Fashion", weightKg: 0.9, dimensions: { lengthCm: 38, widthCm: 26, heightCm: 12 }, warehouse: "Los Angeles", stockQuantity: 14, minStockThreshold: 18, unitCostUSD: 28, isFragile: false, status: "Low stock" },
  { sku: "TF-AUDIO-018", name: "Wireless headphones", category: "Electronics", weightKg: 0.6, dimensions: { lengthCm: 22, widthCm: 18, heightCm: 9 }, warehouse: "Zaragoza", stockQuantity: 42, minStockThreshold: 15, unitCostUSD: 64, isFragile: true, status: "Active" },
  { sku: "TF-SKIN-106", name: "Daily skin-care set", category: "Cosmetics", weightKg: 1.1, dimensions: { lengthCm: 24, widthCm: 20, heightCm: 10 }, warehouse: "Zaragoza", stockQuantity: 7, minStockThreshold: 12, unitCostUSD: 36, isFragile: true, status: "Low stock" },
  { sku: "TF-HOME-211", name: "Ceramic table lamp", category: "Home", weightKg: 3.4, dimensions: { lengthCm: 36, widthCm: 28, heightCm: 28 }, warehouse: "Los Angeles", stockQuantity: 23, minStockThreshold: 8, unitCostUSD: 82, isFragile: true, status: "Active" },
  { sku: "TF-TECH-319", name: "USB-C hub", category: "Electronics", weightKg: 0.3, dimensions: { lengthCm: 14, widthCm: 9, heightCm: 3 }, warehouse: "Zaragoza", stockQuantity: 0, minStockThreshold: 10, unitCostUSD: 19, isFragile: false, status: "Out of stock" },
];

export const demoShipments: SampleShipment[] = [
  { id: "TF-20481", brand: "Example brand A", sku: "TF-BAG-042", quantity: 2, origin: "Los Angeles", destination: { city: "San Diego", country: "United States", postalCode: "92101", distanceKm: 194 }, priority: "Express", declaredValueUSD: 56, carrier: "Parcel North", status: "In transit", createdAt: new Date("2026-10-14T09:10:00Z") },
  { id: "TF-20479", brand: "Example brand B", sku: "TF-SKIN-106", quantity: 1, origin: "Zaragoza", destination: { city: "Madrid", country: "Spain", postalCode: "28001", distanceKm: 316 }, priority: "Standard", declaredValueUSD: 36, carrier: null, status: "Assigned", createdAt: new Date("2026-10-14T08:45:00Z") },
  { id: "TF-20474", brand: "Example brand C", sku: "TF-AUDIO-018", quantity: 1, origin: "Zaragoza", destination: { city: "Valencia", country: "Spain", postalCode: "46001", distanceKm: 310 }, priority: "Same-day", declaredValueUSD: 64, carrier: "Iberia Express", status: "In transit", createdAt: new Date("2026-10-14T08:20:00Z") },
  { id: "TF-20469", brand: "Example brand D", sku: "TF-HOME-211", quantity: 1, origin: "Los Angeles", destination: { city: "Pasadena", country: "United States", postalCode: "91101", distanceKm: 18 }, priority: "Standard", declaredValueUSD: 82, carrier: "Parcel North", status: "Delivered", createdAt: new Date("2026-10-14T07:55:00Z") },
];

export const demoCarriers: Carrier[] = [
  { id: "carrier-north", name: "Parcel North", operatesIn: ["United States"], baseRateUSD: 7.5, ratePerKgUSD: 1.2, ratePerKmUSD: 0.018, avgDeliveryDays: 2, onTimeRate: 96, maxWeightKg: 25, handlesFragile: true, acceptsPriority: ["Standard", "Express"] },
  { id: "carrier-iberia", name: "Iberia Express", operatesIn: ["Spain"], baseRateUSD: 5.8, ratePerKgUSD: 0.95, ratePerKmUSD: 0.014, avgDeliveryDays: 1, onTimeRate: 93, maxWeightKg: 20, handlesFragile: true, acceptsPriority: ["Standard", "Express", "Same-day"] },
  { id: "carrier-global", name: "Global Parcel", operatesIn: ["United States", "Spain"], baseRateUSD: 9.2, ratePerKgUSD: 1.05, ratePerKmUSD: 0.021, avgDeliveryDays: 3, onTimeRate: 98, maxWeightKg: 30, handlesFragile: false, acceptsPriority: ["Standard", "Express"] },
];