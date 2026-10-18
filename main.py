
import re
from datetime import date

from fastapi import FastAPI
from pydantic import BaseModel, Field
from fastapi.responses import JSONResponse

app = FastAPI()


class InvoiceRequest(BaseModel):
    text: str = Field(..., min_length=1)


class InvoiceResponse(BaseModel):
    vendor: str
    amount: float
    currency: str
    date: str


def extract_invoice(text: str) -> dict:
    text = text.replace("\u00a0", " ")
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    content = "\n".join(lines)

    # Vendor
    vendor = None

    for line in lines:
        m = re.search(
            r"(?i)^\s*(?:vendor(?:\s+name)?|supplier|merchant|"
            r"billed\s+by|issued\s+by)\s*[:\-]\s*(.+?)\s*$",
            line,
        )
        if m:
            vendor = m.group(1).strip()
            break

    if not vendor:
        m = re.search(
            r"(?im)^\s*([A-Za-z0-9][^\n]{1,100}?"
            r"(?:Industries|Incorporated|Corporation|Corp\.?|"
            r"Limited|Ltd\.?|LLC|Inc\.?))\s*$",
            content,
        )
        if m:
            vendor = m.group(1).strip()

    if not vendor:
        # Common invoice header fallback.
        for line in lines[:5]:
            if (
                re.search(r"[A-Za-z]", line)
                and not re.search(
                    r"(?i)invoice|due date|invoice date|"
                    r"total|amount|currency|bill to|payment",
                    line,
                )
            ):
                vendor = line
                break

    # Currency
    currency = None
    m = re.search(r"(?i)\b(USD|EUR|GBP)\b", content)
    if m:
        currency = m.group(1).upper()
    else:
        # Symbols are fallback only.
        symbols = re.findall(r"[$€£]", content)
        if symbols:
            currency = {"$": "USD", "€": "EUR", "£": "GBP"}[
                symbols[0]
            ]

    # Amount: prefer specifically labeled totals.
    amount = None
    amount_patterns = [
        r"(?im)^\s*(?:total\s+amount\s+due|total\s+due|"
        r"amount\s+due|balance\s+due|grand\s+total|"
        r"total\s+payable|invoice\s+total|total|amount)\s*"
        r"[:=\-]?\s*(?:(?:USD|EUR|GBP)\s*)?[$€£]?\s*"
        r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)\b",
        r"(?i)\b(?:USD|EUR|GBP)\s*[$€£]?\s*"
        r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)\b",
        r"[$€£]\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\b",
    ]

    for pattern in amount_patterns:
        m = re.search(pattern, content)
        if m:
            amount = float(m.group(1).replace(",", ""))
            break

    # Due date: prioritize due-date labels over other dates.
    due_date = None
    date_patterns = [
        r"(?im)^\s*(?:payment\s+)?due\s+date\s*[:=\-]?\s*"
        r"(20\d{2}-\d{2}-\d{2})\b",
        r"(?im)^\s*(?:payment\s+)?due\s*[:=\-]?\s*"
        r"(20\d{2}-\d{2}-\d{2})\b",
        r"(?i)\b(?:payable\s+by|due\s+by)\s*[:=\-]?\s*"
        r"(20\d{2}-\d{2}-\d{2})\b",
    ]

    for pattern in date_patterns:
        m = re.search(pattern, content)
        if m:
            due_date = m.group(1)
            break

    # Fallback if no explicit due-date label exists.
    if not due_date:
        dates = re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", content)
        for candidate in dates:
            try:
                date.fromisoformat(candidate)
                due_date = candidate
                break
            except ValueError:
                continue

    if not all([vendor, amount is not None, currency, due_date]):
        raise ValueError("Missing invoice fields")

    # Validate the extracted date.
    date.fromisoformat(due_date)

    return {
        "vendor": vendor,
        "amount": amount,
        "currency": currency,
        "date": due_date,
    }


@app.get("/")
def root():
    return {"status": "ok"}


@app.post("/extract", response_model=InvoiceResponse)
def extract(request: InvoiceRequest):
    try:
        return extract_invoice(request.text)
    except (ValueError, TypeError, OverflowError):
        return JSONResponse(
            status_code=422,
            content={"detail": "Unable to extract invoice fields"},
        )
