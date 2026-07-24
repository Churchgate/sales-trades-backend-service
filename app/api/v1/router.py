from fastapi import APIRouter

from app.api.v1.endpoints import admin, analytics, auth, bookings, campaigns, trade

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(analytics.router)
api_router.include_router(bookings.router)
api_router.include_router(campaigns.router)
api_router.include_router(trade.router)
