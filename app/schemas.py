"""Contratos HTTP validados."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .models import DeliveryStatus, OrderStatus, PaymentStatus, Role, TenantStatus


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LoginInput(BaseModel):
    email: str
    password: str
    tenant_slug: str | None = None


class TokenOutput(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TenantCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,98}[a-z0-9]$")
    owner_name: str = Field(min_length=2, max_length=160)
    owner_email: str
    owner_password: str = Field(min_length=8)
    is_demo: bool = False


class TenantOutput(ORMModel):
    id: str
    name: str
    slug: str
    status: TenantStatus
    is_demo: bool
    timezone: str
    currency: str


class UserOutput(ORMModel):
    id: str
    tenant_id: str | None
    email: str
    name: str
    role: Role
    active: bool


class UserCreate(BaseModel):
    email: str
    name: str = Field(min_length=2, max_length=160)
    password: str = Field(min_length=8)
    role: Role


class ProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    price: int = Field(ge=0)
    stock: int = Field(ge=0)
    active: bool = True
    image_url: str | None = None


class ProductOutput(ProductCreate, ORMModel):
    id: str
    tenant_id: str


class CustomerInput(BaseModel):
    phone: str = Field(min_length=8, max_length=32)
    name: str = ""
    email: str | None = None
    tax_id: str | None = None
    address: str = ""
    latitude: str | None = None
    longitude: str | None = None


class CustomerOutput(CustomerInput, ORMModel):
    id: str
    tenant_id: str


class OrderItemInput(BaseModel):
    product_id: str
    quantity: int = Field(gt=0, le=1000)


class OrderCreate(BaseModel):
    customer: CustomerInput
    items: list[OrderItemInput] = Field(min_length=1)
    discount: int = Field(default=0, ge=0)
    shipping: int = Field(default=0, ge=0)
    payment_method: str = "CASH_ON_DELIVERY"
    address: str = ""
    latitude: str | None = None
    longitude: str | None = None
    requested_slot: str | None = None
    notes: str = ""
    source: str = "admin"


class OrderItemOutput(ORMModel):
    id: str
    product_id: str
    product_name: str
    unit_price: int
    quantity: int
    subtotal: int


class OrderOutput(ORMModel):
    id: str
    tenant_id: str
    customer_id: str
    status: OrderStatus
    subtotal: int
    discount: int
    shipping: int
    total: int
    payment_method: str
    address: str
    requested_slot: str | None
    notes: str
    source: str
    items: list[OrderItemOutput]


class StatusChange(BaseModel):
    status: OrderStatus


class PaymentCreate(BaseModel):
    provider: str
    status: PaymentStatus = PaymentStatus.PENDING
    amount: int = Field(gt=0)
    external_id: str | None = None
    idempotency_key: str = Field(min_length=8, max_length=160)


class PaymentOutput(ORMModel):
    id: str
    tenant_id: str
    order_id: str
    provider: str
    status: PaymentStatus
    amount: int
    external_id: str | None


class DeliveryAssign(BaseModel):
    driver_id: str


class DeliveryUpdate(BaseModel):
    status: DeliveryStatus
    latitude: str | None = None
    longitude: str | None = None
    proof_note: str | None = None


class DeliveryOutput(ORMModel):
    id: str
    tenant_id: str
    order_id: str
    driver_id: str | None
    status: DeliveryStatus
    tracking_token: str
    current_latitude: str | None
    current_longitude: str | None
    proof_note: str | None
