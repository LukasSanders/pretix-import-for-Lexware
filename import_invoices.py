"""Import invoices from pretix to Lexware for a specified date.

Summary
-------
Import all ticket sales from pretix and post them as vouchers to Lexware. The script imports invoices for 
a specified date which can be passed as a command-line argument (default: yesterday). 
In Lexware, a voucher (not an actual invoice) will be created using the collective contact. All lines of 
the invoice will be passed as voucher items, so different tax rates will be handled correctly. 
Cancellation invoices will also be imported as sales credit notes. 
All vouchers will be creating using the same posting category which is specified as a constant in the script. 

Setup & Use
-----------
The script relies on the following environment variables to be set:
- PRETIX_RESOURCE_URL: The base URL of the pretix API (e.g https://pretix.pony-events.eu/api/v1/organizers/[org]/events/[event])
- PRETIX_API_KEY: The API key for the pretix API.
- LEXWARE_API_URL: The base URL of the Lexware API (e.g. https://api.lexware.io/v1).
- LEXWARE_API_KEY: The API key for the Lexware API.

The Lexware API URL remains static, the pretix API URL is event-specific and must be set accordingly. 
You will need to run this script multiple times for each event if you want to handle multiple events.
The API keys for both APIs must be generated in the respective systems. For more information on URLs and on how to 
generate the API keys, please refer to the respective API documentation (see below).

As the script imports invoices for a whole day, it should not be run for the current day. A failsafe for 
duplicate imports is not yet implemented. 

The script can be called with the following command line arguments:
-d, --date DATE        Date for which to import invoices (format: YYYY-MM-DD).
-t, --test             Run in test mode without actually creating invoices.
-l, --logging          Create a log file in the same directory. 

If the import is handled manually, pass the date (format: YYYY-MM-DD) for which to import invoices. The argument
may be omitted if invoices for yesterday are to be imported. If the script is run automatically (e.g. via cron job),
it is advisable to omit the date argument and run the script daily, as it will then always import invoices for the 
previous day.

For testing purposes, the script can be run in test mode, which will not create any vouchers in Lexware. Test mode
can be activated passing the respective command line argument (-t, --test). 
In test mode, the script will still fetch and convert the invoices, just not call the Lexware API. 

API Reference
-------------
For further information on the pretix and Lexware APIs, please refer to the following documentation:
- https://developers.lexware.io/docs/
- https://docs.pretix.eu/dev/api/index.html

Copyright (c) 2026 Lukas Sanders.  All rights reserved.

Developed by:   Lukas Sanders
                https://github.com/LukasSanders

Redistribution and use in source and binary forms, with or without modification, are permitted provided that 
the following conditions are met:

1.  Redistributions of source code must retain the above copyright notice, this list of conditions and the following 
    disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following 
    disclaimer in the documentation and/or other materials provided with the distribution.
3.  All advertising materials mentioning features or use of this software must display the following acknowledgement: 
    This product includes software developed by Lukas Sanders.
4.  Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote products 
    derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY LUKAS SANDERS AS IS AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, 
THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL 
LUKAS SANDERS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, 
BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) 
HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR 
OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

import requests
import json
import os
import datetime
import time
import argparse
import logging
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress
from rich.panel import Panel
from rich.table import Table
from rich.columns import Columns

# Posting category ID for sales invoices in Lexware (sale of goods/services).
# Adjust this id if necessary, list can be retrieved via the posting-categories endpoint of the Lexware API.
PSTNG_CATEGORY_ID_SALES = '8f8664a8-fd86-11e1-a21f-0800200c9a66'

CONSOLE = Console()
LOGGER = LOGGER = logging.getLogger(__name__)
IMPORT_DATE = datetime.date.today() - datetime.timedelta(days=1)
TEST_MODE = False
LOGGING_ENABLED = False
LOGFILE_NAME = 'pretix_to_lexware.log'

load_dotenv()

def main():
    """Main function to handle the invoice import. 

    Console arguments (via argparse):
    -d, --date DATE        Date for which to import invoices (format: YYYY-MM-DD). Defaults to yesterday if not provided.
    -t, --test             Run in test mode without actually creating invoices.
    -l, --logging          Create a log file in the same directory. 
    """

    # Set up and retrieve command line arguments.
    parser = argparse.ArgumentParser(description='Import invoices from pretix to Lexware for a specified date.')
    parser.add_argument('-d', '--date', 
                        type=lambda s: datetime.datetime.strptime(s, '%Y-%m-%d'), 
                        help='Date for which to import invoices (format: YYYY-MM-DD). Defaults to yesterday if not provided.')
    parser.add_argument('-t', '--test', action='store_true', help='Run in test mode without actually creating invoices.')
    parser.add_argument('-l', '--logging', action='store_true', help='Output log to a file in the same directory.')
    args = parser.parse_args()

    if args.date:
        global IMPORT_DATE
        IMPORT_DATE = args.date.date() 

    if args.test:
        global TEST_MODE
        TEST_MODE = args.test 

    if args.logging:
        global LOGGING_ENABLED
        LOGGING_ENABLED = args.logging

    # Set up logging to file if requested. 
    if LOGGING_ENABLED:
        LOGGER.setLevel(logging.DEBUG)

        file_handler = RotatingFileHandler(LOGFILE_NAME, maxBytes=1024*1024, backupCount=5)
        file_handler.setLevel(logging.DEBUG)

        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)

        LOGGER.addHandler(file_handler)

    if TEST_MODE:
        CONSOLE.print(Panel('Running in test mode. No invoices will be created.', title='TEST MODE', style='bold yellow'))
        log('Running in test mode. No invoices will be created.', logging.WARNING)

    # Get transactions for the specified date from pretix. The invoice endpoint has no date filter, 
    # but a filter for order numbers. Therefore, we first fetch all transactions for the specified date, 
    # filter out unique order numbers and then fetch all invoices for those orders. The list of invoices 
    # is then filtered by the specified date to get only the relevant invoices created on that date.
    with CONSOLE.status(f'Fetching transactions for {IMPORT_DATE}...'):
        transactions = get_transactions(IMPORT_DATE)

    # If no transactions are found, leave the program.
    if len(transactions) == 0:
        CONSOLE.log(f'No transactions received for {IMPORT_DATE}. Leaving program.', style='bold yellow')
        log(f'No transactions received for {IMPORT_DATE}. Leaving program.', logging.INFO)
        return

    # There can be more than one transaction per order on a single day, so we need to filter out duplicates.
    # The unique list of orders is necessary to fetch the invoices for each order. 
    relevant_orders_unique = set()

    for transaction in transactions:
        relevant_orders_unique.add(transaction['order'])

    relevant_orders = list(relevant_orders_unique)

    with Progress() as prg_get_invoices:
        task_get_invoice = prg_get_invoices.add_task(f'Fetching invoices for {len(relevant_orders)} orders...', total=len(relevant_orders))
        log(f'Fetching invoices for {len(relevant_orders)} orders...', logging.INFO)
        
        invoices_to_import = []

        tbl_fetched_invoices = Table(title='Fetched Invoices')
        tbl_fetched_invoices.add_column('Invoice Number', style='cyan', no_wrap=True)
        tbl_fetched_invoices.add_column('Order', no_wrap=True)
        tbl_fetched_invoices.add_column('Invoice Date', no_wrap=True)
        tbl_fetched_invoices.add_column('Status', no_wrap=True)

        while not prg_get_invoices.finished:
            order = relevant_orders.pop(0)
            invoices = get_invoices(order)
            for invoice in invoices:
                if invoice['date'] == IMPORT_DATE.strftime('%Y-%m-%d'):
                    invoices_to_import.append(invoice)
                    tbl_fetched_invoices.add_row(invoice['number'], order, IMPORT_DATE.strftime('%Y-%m-%d'), '[bold green]to be imported')
                    log(f'Invoice {invoice["number"]} for order {order} noted for import', logging.INFO)
                else: 
                    tbl_fetched_invoices.add_row(invoice['number'], order, IMPORT_DATE.strftime('%Y-%m-%d'), '[bold yellow]no import')
                    log(f'Invoice {invoice["number"]} for order {order} created on {invoice["date"]}, no import', logging.INFO)

            prg_get_invoices.update(task_get_invoice, advance=1)

    CONSOLE.print(tbl_fetched_invoices)

    # Leave program if there are no invoices to import. 
    if len(invoices_to_import) == 0:
        CONSOLE.log(f'No invoices to import for {IMPORT_DATE}. Leaving program.', style='bold yellow')
        log(f'No invoices to import for {IMPORT_DATE}. Leaving program.', logging.INFO)
        return

    total_invoices = len(invoices_to_import)
    import_failed = 0
    import_successful = 0

    tbl_imp_invoices = Table(title='Imported Invoices')
    tbl_imp_invoices.add_column('Invoice Number', style='cyan', no_wrap=True)
    tbl_imp_invoices.add_column('Order', no_wrap=True)
    tbl_imp_invoices.add_column('Invoice Date', no_wrap=True)
    tbl_imp_invoices.add_column('Status', no_wrap=True)

    with Progress() as prg_post_invoices:
        task_post_invoice = prg_post_invoices.add_task(f'Importing {total_invoices} invoices...', total=total_invoices)
        log(f'Importing {total_invoices} invoices...', logging.INFO)

        while not prg_post_invoices.finished:
            invoice = invoices_to_import.pop(0)  # Get the next invoice to import

            # The Lexware API has a rate limit of 2 requests per second. If the limit is exceeded, the API returns a 429 status code.
            # We will retry the request in that case, up to a maximum number of attempts defined in the environment variable 
            # LEXWARE_RETRY_COUNT, and wait 2 seconds.
            attempts = 0

            while (attempts <= int(os.environ.get('LEXWARE_RETRY_COUNT'))):  # type: ignore

                attempts += 1

                # In test mode, the Lexware API will not be called and the payload will be logged to the console for inspection
                # if requested. 
                if TEST_MODE: 
                    tbl_imp_invoices.add_row(invoice['number'], order, IMPORT_DATE.strftime('%Y-%m-%d'), '[bold green]no import (test mode)')
                    import_successful += 1
                    prg_post_invoices.update(task_post_invoice, advance=1)
                    log(f'Invoice {invoice["number"]} not imported (test mode).', logging.INFO)
                    break

                result = create_voucher(convert_invoice(invoice))

                if result.ok: 
                    # Document has been created, exit loop.
                    tbl_imp_invoices.add_row(invoice['number'], order, IMPORT_DATE.strftime('%Y-%m-%d'), '[bold green]successful')
                    log(f'Invoice {invoice["number"]} imported successfully.', logging.INFO)
                    import_successful += 1
                    prg_post_invoices.update(task_post_invoice, advance=1)
                    break
                elif result.status_code == 429:
                    # Rate limit exceeded, wait and try again 
                    time.sleep(2)
                    CONSOLE.log(f'API rate limit exceeded (status {result.status_code}). Will try again {int(os.environ.get("LEXWARE_RETRY_COUNT")) - attempts} more time(s).', style='bold yellow') # type: ignore
                    log(f'API rate limit exceeded (status {result.status_code}). Will try again {int(os.environ.get("LEXWARE_RETRY_COUNT")) - attempts} more time(s).', logging.WARNING) # type: ignore
                    continue
                else: 
                    # Document creation failed for a different reason, exit loop.
                    tbl_imp_invoices.add_row(invoice['number'], order, IMPORT_DATE.strftime('%Y-%m-%d'), '[bold red]failed')
                    log(f'Failed to import invoice {invoice["number"]}: {result.status_code} {result.text}', logging.ERROR)
                    import_failed += 1
                    prg_post_invoices.update(task_post_invoice, advance=1)
                    break

    CONSOLE.print(tbl_imp_invoices)

    CONSOLE.log(f'Import summary for {IMPORT_DATE}:', style='bold')

    stats = [   Panel(f'{import_successful}', title='Successful', style='bold green'),
                Panel(f'{import_failed}', title='Failed', style='bold red'),
                Panel(f'{total_invoices}', title='Total', style='bold')]

    CONSOLE.log(Columns(stats))

    log(f'Import summary for {IMPORT_DATE}: {import_successful} successful, {import_failed} failed out of {total_invoices} total.', logging.INFO)

def get_transactions(date: datetime.date):
    """Fetches transactions from the pretix API for a given date.

    Keyword arguments:
    date -- the date for which to fetch transactions
    """

    url = os.environ.get('PRETIX_RESOURCE_URL') + '/transactions/?created_since=' + date.strftime('%Y-%m-%d') + 'T00:00:00Z' # type: ignore
    headers = {'Authorization': 'Token ' + os.environ.get('PRETIX_API_KEY')} # type: ignore

    response = requests.request('GET', url, headers=headers, data={})

    if response.status_code != 200:
        CONSOLE.log(f'[bold red]Failed to fetch transactions: {response.status_code} - {response.text}')
        log(f'[bold red]Failed to fetch transactions: {response.status_code} - {response.text}', logging.ERROR)
        return []
    else:
        CONSOLE.log(f'[bold green]Successfully fetched {json.loads(response.text)["count"]} transactions for {date}')
        log(f'[bold green]Successfully fetched {json.loads(response.text)["count"]} transactions for {date}', logging.INFO)
        return(json.loads(response.text)['results'])

def get_invoices(order: str):
    """Fetches invoices from the pretix API for a given order.

    Keyword arguments:
    order -- the order for which to fetch invoices
    """

    url = os.environ.get('PRETIX_RESOURCE_URL') + '/invoices/?order=' + order # type: ignore
    headers = {'Authorization': 'Token ' + os.environ.get('PRETIX_API_KEY')} # type: ignore

    response = requests.request('GET', url, headers=headers, data={})

    return(json.loads(response.text)['results'])

def convert_invoice(invoice):
    """Converts a pretix invoice to the format required by the Lexware API.

    Keyword arguments:
    invoice -- the pretix invoice to convert (JSON object), see API documentation for details on the JSON structure
    """

    items = []
    total_gross = 0.0
    total_tax = 0.0
    
    for item in invoice['lines']:

        tax_amount = round(float(item['tax_value'].replace('-', '')), 2)  # Remove negative sign if present
        gross_amount = round(float(item['gross_value'].replace('-', '')), 2)  # Remove negative sign if present

        total_gross += gross_amount
        total_tax += tax_amount

        items.append({
            'amount': gross_amount,
            'taxAmount': tax_amount,
            'taxRatePercent': int(round(float(item['tax_rate']), 0)),
            'categoryId': PSTNG_CATEGORY_ID_SALES,
        })

    cancellation_prefix = 'Storno ' if invoice['is_cancellation'] == True else ''
    reference = f'\nReferenzbelegnummer: {invoice["refers"]}' if invoice['refers'] is not None else ''

    return json.dumps({
        'type': 'salesinvoice' if invoice['is_cancellation'] == False else 'salescreditnote',
        'voucherNumber': invoice['number'],
        'voucherDate': invoice['date'],
        'shippingDate': invoice['date'],
        'dueDate': invoice['date'],     
        'totalGrossAmount': round(total_gross, 2),
        'totalTaxAmount': round(total_tax, 2),
        'taxType': 'gross',
        'useCollectiveContact': True,
        'remark': f'{cancellation_prefix}Bestellung {invoice["order"]} zu Veranstaltung {invoice["event"]}\n{invoice["invoice_to_name"]}, {invoice["invoice_to_street"]}, {invoice["invoice_to_zipcode"]} {invoice["invoice_to_city"]}, {invoice["invoice_to_country"]}{reference}',
        'voucherItems': items
    })

def create_voucher(voucher):
    """Creates a voucher in the Lexware API.

    Keyword arguments:
    voucher -- the voucher data to create (JSON object), see Lexware API documentation for details on the JSON structure
    """

    headers = {
        'Authorization': 'Bearer ' + os.environ.get('LEXWARE_API_KEY'),  # type: ignore
        'Accept': 'application/json',
        'Content-Type': 'application/json'
        }

    response = requests.request('POST', os.environ.get('LEXWARE_API_URL') + '/vouchers', headers=headers, data=voucher)  # type: ignore

    return response

def log(message, level):

    if not LOGGING_ENABLED:
        return

    match level:
        case logging.DEBUG:
            LOGGER.debug(message)
        case logging.INFO:
            LOGGER.info(message)
        case logging.WARNING:
            LOGGER.warning(message)
        case logging.ERROR:
            LOGGER.error(message)

if __name__ == '__main__':
    main()