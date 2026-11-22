/** 认证扩展：POST /api/v1/auth/login、POST /api/v1/auth/register（计划 §9 #7） */
import { request } from './client'
import type { AuthToken, AuthUser, LoginRequest, RegisterRequest } from './types'

export function login(payload: LoginRequest): Promise<AuthToken> {
  return request<AuthToken>({ url: '/auth/login', method: 'post', data: payload })
}

export function register(payload: RegisterRequest): Promise<AuthUser> {
  return request<AuthUser>({ url: '/auth/register', method: 'post', data: payload })
}
