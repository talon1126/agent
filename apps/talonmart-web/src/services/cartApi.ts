import { apiClient } from '@/services/apiClient'
import type {
  AddCartItemRequest,
  AddCartItemResponse,
  CartResponse,
  RemoveCartItemResponse,
} from '@/types/cart'

export const CART_USER_ID = 1

export async function fetchCart(userId = CART_USER_ID): Promise<CartResponse> {
  const response = await apiClient.get<CartResponse>('/cart', {
    params: { user_id: userId },
  })

  return response.data
}

export async function addCartItem(payload: AddCartItemRequest): Promise<AddCartItemResponse> {
  const response = await apiClient.post<AddCartItemResponse>('/cart', payload)

  return response.data
}

export async function removeCartItem(
  itemId: string,
  userId = CART_USER_ID,
): Promise<RemoveCartItemResponse> {
  const response = await apiClient.delete<RemoveCartItemResponse>('/cart', {
    params: { user_id: userId, item_id: itemId },
  })

  return response.data
}
