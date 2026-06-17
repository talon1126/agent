import { apiClient } from '@/services/apiClient'
import type {
  CreateWarehouseOrderRequest,
  DeliveryAddressResponse,
  WarehouseOrderResponse,
} from '@/types/checkout'

export async function fetchDeliveryAddresses(userId: number): Promise<DeliveryAddressResponse> {
  // Checkout must query addresses by user id so one shopper never sees another shopper's address.
  const response = await apiClient.get<DeliveryAddressResponse>('/delivery_addresses', {
    params: { user_id: userId },
  })

  return response.data
}

export async function createWarehouseOrder(
  payload: CreateWarehouseOrderRequest,
): Promise<WarehouseOrderResponse> {
  // Checkout delegates warehouse selection and fulfillment review creation to the warehouse API.
  const response = await apiClient.post<WarehouseOrderResponse>('/warehouse/orders', payload)

  return response.data
}
