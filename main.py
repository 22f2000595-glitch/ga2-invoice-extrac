
import re
from datetime import date
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field, field_validator
from fastapi.responses import JSONResponse

app = FastAPI(title="Invoice Extraction API")


class InvoiceRequest(BaseModel):
    text: str = Field(..., min_length=1)


class InvoiceResponse(BaseModel):
    vendor: str
    amount: float
    currency: str
    date: str

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        value = value.upper()
        if not re.fullmatch(r"[A-Z]{3}", value):
            raise ValueError("Currency must be a 3-letter code")
        return value

    @field_validator("date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        date.fromisoformat(value)
        return value


def extract_invoice(text: str) -> dict[str, Any]:
    # Normalize whitespace while preserving the invoice's content.
    text = text.replace("\u00a0", " ")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    normalized = "\n".join(lines)

    # Vendor: prefer explicit vendor/supplier/merchant labels.
    vendor = None
    for line in lines:
        match = re.search(
            r"(?i)\b(?:vendor|supplier|merchant|billed\s+by|from)"
            r"\s*[:\-]\s*(.+)",
            line,
        )
        if match:
            vendor = match.group(1).strip()
            break

    # Fallback: look for a company name ending in a legal suffix.
    if not vendor:
        match = re.search(
            r"(?im)^([^\n]{2,100}?\b"
            r"(?:Industries|Inc\.?|LLC|Ltd\.?|Limited|Corporation|Corp\.?))"
            r"\s*$",
            normalized,
        )
        if match:
            vendor = match.group(1).strip()

    if not vendor:
        # Best effort for otherwise unlabeled invoice headers.
        for line in lines[:5]:
            if re.search(r"[A-Za-z]", line) and not re.search(
                r"(?i)\b(invoice|date|total|amount|due|bill to)\b", line
            ):
                vendor = line
                break

    # Currency: recognize the currency code or common symbols.
    currency = None
    currency_match = re.search(
        r"(?i)\b(USD|EUR|GBP)\b", normalized
    )
    if currency_match:
        currency = currency_match.group(1).upper()
    else:
        symbol_match = re.search(r"[$€£]", normalized)
        if symbol_match:
            currency = {"$": "USD", "€": "EUR", "£": "GBP"}[
                symbol_match.group(0)
            ]

    # Amount: prioritize total due / amount due / balance due.
    amount = None
    amount_patterns = [
        r"(?im)^\s*(?:total\s+due|amount\s+due|balance\s+due|"
        r"grand\s+total|total\s+payable)\s*[:\-]?\s*"
        r"(?:USD|EUR|GBP|[$€£])?\s*"
        r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
        r"(?i)\b(?:USD|EUR|GBP)\s*"
        r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
        r"[$€£]\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
    ]
    for pattern in amount_patterns:
        match = re.search(pattern, normalized)
        if match:
            amount = float(match.group(1).replace(",", ""))
            break

    # Due date: prioritize explicit due-date labels.
    due_date = None
    date_patterns = [
        r"(?im)^\s*(?:payment\s+)?due\s+date\s*[:\-]?\s*"
        r"(20\d{2}-\d{2}-\d{2})\b",
        r"(?i)\b(?:payable\s+by|due\s+by)\s*[:\-]?\s*"
        r"(20\d{2}-\d{2}-\d{2})\b",
        r"\b(20\d{2}-\d{2}-\d{2})\b",
    ]
    for pattern in date_patterns:
        match = re.search(pattern, normalized)
        if match:
            candidate = match.group(1)
            try:
                date.fromisoformat(candidate)
                due_date = candidate
                break
            except ValueError:
                continue

    if not vendor or amount is None or not currency or not due_date:
        raise ValueError("Could not extract all required invoice fields")

    return {
        "vendor": vendor,
        "amount": amount,
        "currency": currency,
        "date": due_date,
    }


@app.get("/")
def root():
    return {"status": "ok", "endpoint": "POST /extract"}


@app.post("/extract", response_model=InvoiceResponse)
def extract(request: InvoiceRequest):
    try:
        result = extract_invoice(request.text)
        return InvoiceResponse(**result)
    except (ValueError, TypeError):
        return JSONResponse(
            status_code=422,
            content={"detail": "Unable to extract a valid invoice"},
        )
