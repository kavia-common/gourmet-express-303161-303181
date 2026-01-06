from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class PaymentIntentCreateRequest(BaseModel):
    order_id: UUID = Field(..., description="Order id to create a payment intent for.")
    amount_cents: int = Field(..., ge=0, description="Expected total amount in cents (client-calculated).")


class PaymentIntentCreateResponse(BaseModel):
    order_id: UUID = Field(..., description="Order id the payment intent is for.")
    amount_cents: int = Field(..., ge=0, description="Validated total amount in cents.")
    currency: str = Field(..., min_length=1, description="Currency code for the payment.")
    client_secret: str = Field(
        ...,
        description=(
            "Fake client secret string for the simulated payment provider. "
            "In Stripe this would be the PaymentIntent client_secret."
        ),
    )
    provider: str = Field(
        "stub",
        description="Payment provider identifier. For now: 'stub'. Later: 'stripe'.",
    )


class PaymentConfirmRequest(BaseModel):
    order_id: UUID = Field(..., description="Order id to confirm payment for.")
    client_secret: str = Field(..., min_length=1, description="Client secret returned from /payments/intent.")


class PaymentConfirmResponse(BaseModel):
    order_id: UUID = Field(..., description="Order id that was confirmed.")
    status: str = Field(..., description="Payment status. For now: 'succeeded'.")
    order_status: str = Field(..., description="Updated order status after confirmation (e.g. PAID).")
    provider: str = Field(
        "stub",
        description="Payment provider identifier. For now: 'stub'. Later: 'stripe'.",
    )
