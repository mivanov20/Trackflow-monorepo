import {
  filterLowStockProducts,
  filterProductsByCategory,
  filterProductsByWarehouse,
  sortCarriersByReliability,
  sortProductsByStock,
} from "../../../src/utils/collections";
import {
  binarySearchProductByWeight,
  findProductBySKU,
  findShipmentById,
} from "../../../src/utils/search";
import {
  calculateAverageShipmentDistance,
  calculateShippingCost,
  calculateTotalInventoryValue,
  countProductsByCategory,
  findTopCarriers,
  groupShipmentsByStatus,
  scoreCarrierForShipment,
  selectBestCarrier,
  trackflowConstants,
} from "../../../src/utils/transformations";
import { validateCarrier, validateProduct, validateShipment } from "../../../src/utils/validations";
import { demoCarriers, demoProducts, demoShipments } from "./demo-data";

export interface BackofficeAnalytics {
  lowStockProducts: string[];
  inventoryValue: number;
  categoryCounts: Array<{ category: string; count: number }>;
  zaragozaStockCount: number;
  electronicsCount: number;
  lowestStockSku: string;
  foundProduct: string;
  productWeightSearch: string;
  foundShipment: string;
  averageDistance: number;
  shipmentGroups: Array<{ status: string; count: number }>;
  topCarriers: Array<{ carrier: string; count: number }>;
  mostReliableCarrier: string;
  carrierRecommendation: string;
  recommendedCost: number | null;
  recommendedScore: number | null;
  validationSummary: string;
  validationDetails: string[];
  categories: string[];
  statuses: string[];
}

export function buildBackofficeAnalytics(): BackofficeAnalytics {
  const sortedByWeight = [...demoProducts].sort((left, right) => left.weightKg - right.weightKg);
  const firstShipment = demoShipments[0];
  const firstProduct = findProductBySKU(demoProducts, firstShipment.sku);
  const recommendation = firstProduct ? selectBestCarrier(demoCarriers, firstShipment, firstProduct) : null;
  const recommendedScore = recommendation
    ? scoreCarrierForShipment(recommendation.carrier, firstShipment, firstProduct as NonNullable<typeof firstProduct>)
    : null;
  const validationResults = [
    validateProduct(demoProducts[0]),
    validateShipment(firstShipment),
    validateCarrier(demoCarriers[0]),
  ];
  const errors = validationResults.flatMap((result) => result.errors);
  const categories = countProductsByCategory(demoProducts);
  const groups = groupShipmentsByStatus(demoShipments);
  const topCarriers = findTopCarriers(demoShipments, 3);
  const reliableCarrier = sortCarriersByReliability(demoCarriers, "desc")[0];
  const lowestStock = sortProductsByStock(demoProducts, "asc")[0];
  const recommendationProduct = firstProduct;
  const recommendedCost = recommendation && recommendationProduct
    ? calculateShippingCost(firstShipment, recommendationProduct, recommendation.carrier)
    : null;

  return {
    lowStockProducts: filterLowStockProducts(demoProducts).map((product) => `${product.name} (${product.stockQuantity} units)`),
    inventoryValue: calculateTotalInventoryValue(demoProducts),
    categoryCounts: Object.entries(categories).map(([category, count]) => ({ category, count })),
    zaragozaStockCount: filterProductsByWarehouse(demoProducts, "Zaragoza").length,
    electronicsCount: filterProductsByCategory(demoProducts, "Electronics").length,
    lowestStockSku: lowestStock.sku,
    foundProduct: firstProduct?.name ?? "No matching SKU",
    productWeightSearch: binarySearchProductByWeight(sortedByWeight, 0.9) >= 0 ? "0.9 kg found" : "Weight not found",
    foundShipment: findShipmentById(demoShipments, "TF-20481")?.id ?? "No matching shipment",
    averageDistance: calculateAverageShipmentDistance(demoShipments),
    shipmentGroups: Object.entries(groups).map(([status, shipments]) => ({ status, count: shipments.length })),
    topCarriers,
    mostReliableCarrier: reliableCarrier?.name ?? "No carriers available",
    carrierRecommendation: recommendation?.carrier.name ?? "No eligible carrier",
    recommendedCost,
    recommendedScore,
    validationSummary: errors.length === 0 ? "3 of 3 records valid" : `${errors.length} validation issues`,
    validationDetails: errors.length === 0 ? ["Product, shipment, and carrier pass validation."] : errors,
    categories: trackflowConstants.PRODUCT_CATEGORIES,
    statuses: trackflowConstants.SHIPMENT_STATUSES,
  };
}