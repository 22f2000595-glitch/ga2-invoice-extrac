
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
    text = text.replace("\u00a0", " ").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    content = "\n".join(lines)

    # Vendor: explicit labels first, then the planted company name.
    vendor = None

    m = re.search(
        r"(?i)\b(?:vendor(?:\s+name)?|supplier(?:\s+name)?|"
        r"merchant|issued\s+by|billed\s+by)\s*[:=\-]\s*"
        r"(.+?)(?=\s*(?:\||;|\n|"
        r"\b(?:invoice\s*(?:no|number|date)|"
        r"currency|total\s+due|amount\s+due|due\s+date)\b\s*[:=])|$)",
        content,
    )
    if m:
        vendor = m.group(1).strip(" \t,;|")

    if not vendor:
        m = re.search(
            r"(?im)^\s*([A-Za-z0-9][A-Za-z0-9&.,'()\- ]{1,100}?"
            r"\b(?:Industries|Incorporated|Corporation|Corp\.?|"
            r"Limited|Ltd\.?|LLC|Inc\.?))\s*$",
            content,
        )
        if m:
            vendor = m.group(1).strip()

    if not vendor:
        # Handle the common randomized Acme-xxxx Industries Ltd. format.
        m = re.search(
            r"\b([A-Za-z][A-Za-z0-9&.'()\- ]{1,90}?"
            r"\b(?:Industries|Limited|Ltd\.?|Corporation|Corp\.?|LLC|Inc\.?))",
            content,
            re.I,
        )
        if m:
            vendor = m.group(1).strip()

    # Currency code anywhere in the invoice takes priority over symbols.
    currency_match = re.search(r"(?i)\b(USD|EUR|GBP)\b", content)
    if currency_match:
        currency = currency_match.group(1).upper()
    else:
        symbol_match = re.search(r"[$€£]", content)
        currency = (
            {"$": "USD", "€": "EUR", "£": "GBP"}[symbol_match.group()]
            if symbol_match else None
        )

    # Prefer labeled totals. Accept amount/currency in either order.
    amount = None
    number = r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)"
    labeled_patterns = [
        r"(?i)\b(?:total\s+amount\s+due|total\s+due|amount\s+due|"
        r"balance\s+due|grand\s+total|total\s+payable|"
        r"invoice\s+total|total|amount)\b\s*[:=\-]?\s*"
        r"(?:(?:USD|EUR|GBP)\s*)?[$€£]?\s*" + number,
        r"(?i)\b(?:total\s+amount\s+due|total\s+due|amount\s+due|"
        r"balance\s+due|grand\s+total|total\s+payable|"
        r"invoice\s+total|total|amount)\b\s*[:=\-]?\s*"
        r"[$€£]?\s*" + number + r"\s*(?:USD|EUR|GBP)\b",
    ]

    for pattern in labeled_patterns:
        m = re.search(pattern, content)
        if m:
            amount = float(m.group(1).replace(",", ""))
            break

    if amount is None:
        # Currency-code or currency-symbol amounts.
        m = re.search(
            r"(?i)\b(?:USD|EUR|GBP)\s*[$€£]?\s*" + number,
            content,
        )
        if not m:
            m = re.search(r"[$€£]\s*" + number, content)
        if m:
            amount = float(m.group(1).replace(",", ""))

    # Prefer due dates; do not mistake an invoice issue date for the due date.
    date_formats = [
        r"\b(20\d{2}-\d{2}-\d{2})\b",
        r"\b(\d{1,2}/\d{1,2}/20\d{2})\b",
        r"\b(\d{1,2}-\d{1,2}-20\d{2})\b",
    ]

    def find_date(fragment):
        for pattern in date_formats:
            for match in re.finditer(pattern, fragment):
                raw = match.group(1)
                try:
                    if "-" in raw and raw[:4].isdigit():
                        parsed = date.fromisoformat(raw)
                    elif "/" in raw:
                        a, b, y = raw.split("/")
                        # Interpret slash dates as MM/DD/YYYY.
                        parsed = date(int(y), int(a), int(b))
                    else:
                        a, b, y = raw.split("-")
                        parsed = date(int(y), int(b), int(a))
                    return parsed.isoformat()
                except ValueError:
                    continue
        return None

    due_date = None
    for match in re.finditer(
        r"(?i)\b(?:payment\s+)?due(?:\s+date)?\b\s*[:=\-]?\s*"
        r"([^;\n|]{1,50})",
        content,
    ):
        due_date = find_date(match.group(1))
        if due_date:
            break

    if not due_date:
        for match in re.finditer(
            r"(?i)\b(?:payable\s+by|due\s+by)\b\s*[:=\-]?\s*"
            r"([^;\n|]{1,50})",
            content,
        ):
            due_date = find_date(match.group(1))
            if due_date:
                break

    if not due_date:
        due_date = find_date(content)

    if not vendor or amount is None or not currency or not due_date:
        raise ValueError(
            f"Missing fields: vendor={bool(vendor)}, "
            f"amount={amount is not None}, currency={bool(currency)}, "
            f"date={bool(due_date)}"
        )

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
    except (ValueError, TypeError, OverflowError) as exc:
        return JSONResponse(
            status_code=422,
            content={"detail": str(exc)},
        )
