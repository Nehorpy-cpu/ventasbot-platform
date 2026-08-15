"""Contratos HTTP validados."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .models import ConversationStatus, DeliveryStatus, InvoiceStatus, MessageDirection, OrderStatus, PaymentStatus, Role, TenantStatus


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
    phone: str
    role: Role
    active: bool


class UserCreate(BaseModel):
    email: str
    name: str = Field(min_length=2, max_length=160)
    phone: str = Field(default="", max_length=32)
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


class CatalogImportInput(BaseModel):
    source: str = Field(pattern=r"^(META|WEB|WHATSAPP_BUSINESS|CSV|API)$")
    products: list[ProductCreate] = Field(min_length=1, max_length=5000)
    deactivate_missing: bool = False


class CatalogImportOutput(BaseModel):
    source: str
    created: int
    updated: int
    deactivated: int
    total_received: int


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
    checkout_url: str | None = None


class InvoiceIssueInput(BaseModel):
    document_number: str = Field(min_length=1, max_length=80)
    external_id: str = Field(default="", max_length=160)
    kude_url: str = Field(default="", max_length=500, pattern=r"^https://|^$")


class InvoiceOutput(ORMModel):
    id: str
    tenant_id: str
    order_id: str
    status: InvoiceStatus
    customer_name: str
    tax_id: str
    email: str
    amount: int
    provider: str
    document_number: str
    external_id: str
    kude_url: str


class PaymentMethodConfigInput(BaseModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,39}$")
    display_name: str = Field(min_length=2, max_length=100)
    enabled: bool = False
    instructions: str = Field(default="", max_length=4000)
    api_url: str = Field(default="", max_length=500)
    public_key_env: str = Field(default="", pattern=r"^[A-Z][A-Z0-9_]*$|^$")
    private_key_env: str = Field(default="", pattern=r"^[A-Z][A-Z0-9_]*$|^$")
    commerce_code: str = Field(default="", pattern=r"^[0-9]{1,40}$|^$")
    branch_code: str = Field(default="", pattern=r"^[0-9]{1,40}$|^$")
    return_url: str = Field(default="", max_length=500)
    cancel_url: str = Field(default="", max_length=500)


class PaymentMethodConfigOutput(PaymentMethodConfigInput, ORMModel):
    id: str
    tenant_id: str


class CheckoutInput(BaseModel):
    method: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,39}$")
    idempotency_key: str = Field(min_length=8, max_length=160)


class CheckoutOutput(BaseModel):
    payment_id: str
    method: str
    status: PaymentStatus
    checkout_url: str | None = None
    instructions: str = ""


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


class WhatsappIntegrationInput(BaseModel):
    phone_number_id: str = Field(min_length=3, max_length=80)
    waba_id: str = Field(default="", max_length=80)
    display_phone: str = Field(default="", max_length=40)
    access_token_env: str = Field(default="", pattern=r"^[A-Z][A-Z0-9_]*$|^$")
    graph_version: str = Field(default="v21.0", pattern=r"^v[0-9]+\.[0-9]+$")
    active: bool = False


class WhatsappIntegrationOutput(WhatsappIntegrationInput, ORMModel):
    id: str
    tenant_id: str


class ConversationOutput(ORMModel):
    id: str
    tenant_id: str
    customer_id: str
    status: ConversationStatus
    assigned_user_id: str | None
    bot_state: str
    unread_count: int


class MessageOutput(ORMModel):
    id: str
    conversation_id: str
    external_id: str | None
    direction: MessageDirection
    message_type: str
    text: str


class ConversationAssign(BaseModel):
    assigned_user_id: str | None = None
    status: ConversationStatus


class SendMessageInput(BaseModel):
    text: str = Field(min_length=1, max_length=4096)
