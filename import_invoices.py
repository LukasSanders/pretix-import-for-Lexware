import requests
import json
import os
import datetime
import logging
import sys
import time

PSTNG_CATEGORY_ID_SALES = "8f8664a8-fd86-11e1-a21f-0800200c9a66"

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

handler = logging.StreamHandler(sys.stdout) 
handler.setLevel(logging.INFO)

formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)

logger.addHandler(handler)

def main():

    import_date = datetime.date(2026, 10, 9) # TODO: Change to yesterday's date or import parameter for production

    transactions = get_transactions(import_date)

    if len(transactions) == 0:
        logger.info(f"No transactions received for {import_date}. Leaving program.")
        return

    relevant_orders = set()

    for transaction in transactions:
        relevant_orders.add(transaction["order"])

    logger.info(f"Fetching invoices for {len(relevant_orders)} orders...")

    invoices_to_import = []

    for order in relevant_orders:
        invoices = get_invoices(order)
        for invoice in invoices:
            if invoice["date"] == import_date.strftime("%Y-%m-%d"):
                invoices_to_import.append(invoice)
                logger.info(f"Invoice {invoice['number']} for order {order} noted for import")
            else: 
                logger.info(f"Invoice {invoice['number']} for order {order} created on {invoice['date']}")

    if len(invoices_to_import) == 0:
        logger.info(f"No invoices to import for {import_date}. Leaving program.")
        return
    else:
        logger.info(f"Importing {len(invoices_to_import)} invoices...")

    total_invoices = len(invoices_to_import)
    import_failed = 0
    import_successful = 0

    for invoice in invoices_to_import:
        logger.info(f"Importing invoice {invoice['number']} for order {invoice['order']}...")

        attempts = 0
        while (attempts <= int(os.environ.get('LEXWARE_RETRY_COUNT'))):  # type: ignore

            attempts += 1
            result = create_voucher(convert_invoice(invoice))

            if result.ok: 
                # Document has been created, exit loop
                logger.info(f"Invoice {invoice['number']} imported successfully.")
                import_successful += 1
                break
            elif result.status_code == 429:
                # Rate limit exceeded, wait and try again 
                time.sleep(3)
                logger.warning(f"API rate limit exceeded (status {result.status_code}). Will try again {int(os.environ.get('LEXWARE_RETRY_COUNT')) - attempts} more time(s).") # type: ignore
                continue
            else: 
                # Document creation failed for a different reason, exit loop 
                logger.error(f"Failed to import invoice {invoice['number']}: {result.status_code} {result.text}")
                import_failed += 1
                break

    logger.info(f"Import summary for {import_date}: {import_successful} successful, {import_failed} failed out of {total_invoices} total.")

def get_transactions(date: datetime.date):

    url = os.environ.get('PRETIX_RESOURCE_URL') + "/invoices/?created_since=" + date.strftime("%Y-%m-%d") + "T00:00:00Z" # type: ignore
    headers = { "Authorization": "Token " + os.environ.get('PRETIX_API_KEY') } # type: ignore

    response = requests.request("GET", url, headers=headers, data={})

    if response.status_code != 200:
        logger.error(f"Failed to fetch transactions: {response.status_code} - {response.text}")
        return []
    else:
        logger.info(f"Successfully fetched {json.loads(response.text)["count"]} transactions for {date}")

    return(json.loads(response.text)["results"])

def get_invoices(order: str):

    url = os.environ.get('PRETIX_RESOURCE_URL') + "/invoices/?order=" + order # type: ignore
    headers = { "Authorization": "Token " + os.environ.get('PRETIX_API_KEY') } # type: ignore

    response = requests.request("GET", url, headers=headers, data={})

    return(json.loads(response.text)["results"])

def convert_invoice(invoice):

    items = []
    total_gross = 0.0
    total_tax = 0.0
    
    for item in invoice["lines"]:

        tax_amount = round(float(item['tax_value'].replace("-", "")), 2)  # Remove negative sign if present
        gross_amount = round(float(item['gross_value'].replace("-", "")), 2)  # Remove negative sign if present

        total_gross += gross_amount
        total_tax += tax_amount

        items.append({
            "amount": gross_amount,
            "taxAmount": tax_amount,
            "taxRatePercent": int(round(float(item['tax_rate']), 0)),
            "categoryId": PSTNG_CATEGORY_ID_SALES,
        })

    return json.dumps({
        "type": "salesinvoice" if invoice["is_cancellation"] == False else "salescreditnote",
        "voucherNumber": invoice["number"],
        "voucherDate": invoice["date"],
        "shippingDate": invoice["date"],
        "dueDate": invoice["date"],     
        "totalGrossAmount": round(total_gross, 2),
        "totalTaxAmount": round(total_tax, 2),
        "taxType": "gross",
        "useCollectiveContact": True,
        "remark": f"Bestellung {invoice['order']} zu Veranstaltung {invoice['event']}\n{invoice['invoice_to_name']}, {invoice['invoice_to_street']}, {invoice['invoice_to_zipcode']} {invoice['invoice_to_city']}, {invoice['invoice_to_country']}{f"\nReferenzbelegnummer: {invoice['refers']}" if invoice["refers"] != None else ""}",
        "voucherItems": items
    })

def create_voucher(voucher):

    headers = {
        'Authorization': 'Bearer ' + os.environ.get('LEXWARE_API_KEY'),  # type: ignore
        'Accept': 'application/json',
        'Content-Type': 'application/json'
        }

    response = requests.request("POST", os.environ.get('LEXWARE_API_URL'), headers=headers, data=voucher)  # type: ignore

    return response

if __name__ == '__main__':
    main()