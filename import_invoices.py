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
Settings and API credentials are read from settings.yaml.

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
import datetime
import time
import argparse
import logging
from contextlib import contextmanager
from collections.abc import Generator
from pathlib import Path
from logging.handlers import RotatingFileHandler
import yaml
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.panel import Panel
from rich.table import Table
from rich.columns import Columns

class LexwareSettings:
    def __init__(self, api_key, api_url, posting_category, retry_count=3):
        self.api_key = api_key
        self.api_url = api_url
        self.posting_category = posting_category
        self.retry_count = retry_count

class PretixSettings:
    def __init__(self, resource_url, api_token):
        self.resource_url = resource_url
        self.api_token = api_token

class TextSettings:
    def __init__(self, voucher, cancellation, reference, giftcard):
        self.voucher = voucher
        self.cancellation = cancellation
        self.reference = reference
        self.giftcard = giftcard

class Settings:
    def __init__(self, settings):

        self.import_date = datetime.date.today() - datetime.timedelta(days=1)
        self.test_mode = False
        self.logging_enabled = False
        self.logfile_name = 'pretix_to_lexware.log'

        try:
            self.pretix = PretixSettings(
                resource_url=settings['api_settings']['pretix']['resource_url'],
                api_token=settings['api_settings']['pretix']['api_token']
            )
            self.lexware = LexwareSettings(
                api_key=settings['api_settings']['lexware']['api_key'],
                api_url=settings['api_settings']['lexware']['api_url'],
                posting_category=settings['api_settings']['lexware']['posting_category'],
                retry_count=settings['api_settings']['lexware']['retry_count'] if settings['api_settings']['lexware']['retry_count'] else 3
            )

        except KeyError as e:
            raise KeyError(f"Missing required setting: {e}. Please check your settings.yaml file.")
        
        self.texts = TextSettings(
            voucher=settings['texts']['voucher'] if settings['texts']['voucher'] else "Order %order% for event %event%\n%invoice_to_name%, %invoice_to_street%, %invoice_to_zip%, %invoice_to_city%, %invoice_to_country%",
            cancellation=settings['texts']['cancellation'] if settings['texts']['cancellation'] else "Cancellation ",
            reference=settings['texts']['reference'] if settings['texts']['reference'] else "\nReference: %refers%",
            giftcard=settings['texts']['giftcard'] if settings['texts']['giftcard'] else "Reversal of gift card payment for order %order%"
        )

    def set_test_mode(self, active: bool):
        self.test_mode = active

    def set_logging(self, enabled: bool):
        self.logging_enabled = enabled

class Connector:
    def __init__(
        self,
        import_date: datetime.date,
        test_mode: bool,
        logging_enabled: bool,
        settings_location: str,
    ):
        self.console = Console()
        self.logger = logging.getLogger(__name__)
        self.import_date = import_date
        self.transactions: list[dict] = []
        self.orders: list[str] = []
        self.invoices: list[dict] = []
        self.vouchers: list[dict] = []
        self.import_successful: int = 0
        self.import_failed: int = 0
        self._active_progress: Progress | None = None

        with Path(settings_location).open(encoding='utf-8') as settings_file:
            self.settings = Settings(yaml.safe_load(settings_file))
        self.settings.set_logging(logging_enabled)
        self.settings.set_test_mode(test_mode)

    def out(self, message, style=None, level=logging.INFO):
        console = (
            self._active_progress.console
            if self._active_progress is not None
            else self.console
        )
        console.log(message, style=style)
        if not self.settings.logging_enabled:
            return

        match level:
            case logging.DEBUG:
                self.logger.debug(message)
            case logging.INFO:
                self.logger.info(message)
            case logging.WARNING:
                self.logger.warning(message)
            case logging.ERROR:
                self.logger.error(message)

    @contextmanager
    def progress(
        self,
        **kwargs,
    ) -> Generator[Progress, None, None]:
        """Run a progress indicator and route output through its console."""
        progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=None),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=self.console,
            expand=True,
            refresh_per_second=10,
            **kwargs,
        )
        self._active_progress = progress
        try:
            with progress as active_progress:
                yield active_progress
        finally:
            self._active_progress = None

    def configure_logging(self):
        if not self.settings.logging_enabled:
            return

        self.logger.setLevel(logging.DEBUG)
        file_handler = RotatingFileHandler(
            self.settings.logfile_name, maxBytes=1024 * 1024, backupCount=5
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        )
        self.logger.addHandler(file_handler)

    def run(self):
        if not self.settings.pretix.resource_url or not self.settings.pretix.api_token:
            self.out(
                'Pretix API settings are missing or incomplete in the settings file. '
                'Please provide the resource URL and API token.',
                style='bold red',
                level=logging.ERROR,
            )
            return

        if (
            not self.settings.lexware.api_url
            or not self.settings.lexware.api_key
            or not self.settings.lexware.posting_category
            or not self.settings.lexware.retry_count
        ):
            self.out(
                'Lexware API settings are missing or incomplete in the settings file. '
                'Please provide the API URL and API key.',
                style='bold red',
                level=logging.ERROR,
            )
            return

        if self.import_date >= datetime.date.today():
            self.out(
                f'Specified date {self.import_date} is today or in the future. '
                'Please specify a past date.',
                style='bold red',
                level=logging.ERROR,
            )
            return

        try:
            self.configure_logging()
        except OSError as error:
            self.out(
                f'Failed to set up logging to file: {error}. '
                'Please check file permissions and available disk space.',
                style='bold red',
                level=logging.ERROR,
            )
            return

        if self.settings.test_mode:
            message = 'Running in test mode. No invoices will be created.'
            self.console.print(Panel(message, title='TEST MODE', style='bold yellow'))
            self.out(message, style='bold yellow', level=logging.WARNING)

        with self.console.status(f'Fetching transactions for {self.import_date}...'):
            self.transactions = self.get_transactions(self.import_date)

        if not self.transactions:
            self.out(
                f'No transactions received for {self.import_date}. Leaving program.',
                style='bold yellow',
            )
            return

        self.orders = list(dict.fromkeys(
            transaction['order'] for transaction in self.transactions
        ))
        self.fetch_invoices()

        if not self.invoices:
            self.out(
                f'No invoices to import for {self.import_date}. Leaving program.',
                style='bold yellow',
            )
            return

        self.convert_invoices()
        self.add_giftcard_vouchers()
        self.post_vouchers()

    def get_transactions(self, date: datetime.date):
        """Fetch transactions from pretix for a given date."""
        url = (
            self.settings.pretix.resource_url
            + '/transactions/?created_since='
            + date.strftime('%Y-%m-%d')
            + 'T00:00:00Z'
        )
        headers = {'Authorization': 'Token ' + self.settings.pretix.api_token}
        response = requests.request('GET', url, headers=headers, data={})

        if response.status_code != 200:
            self.out(
                f'Failed to fetch transactions: {response.status_code} - {response.text}',
                style='bold red',
                level=logging.ERROR,
            )
            return []

        result = response.json()
        self.out(
            f'Successfully fetched {result["count"]} transactions for {date}',
            style='bold green',
        )
        return result['results']

    def get_invoices(self, order: str):
        """Fetch invoices from pretix for a given order."""
        url = self.settings.pretix.resource_url + '/invoices/?order=' + order
        headers = {'Authorization': 'Token ' + self.settings.pretix.api_token}
        response = requests.request('GET', url, headers=headers, data={})
        return response.json()['results']

    def get_order(self, order: str):
        """Fetch an order from pretix."""
        url = self.settings.pretix.resource_url + '/orders/' + order
        headers = {'Authorization': 'Token ' + self.settings.pretix.api_token}
        response = requests.request('GET', url, headers=headers, data={})
        return response.json()

    def fetch_invoices(self):
        with self.progress() as progress:
            message = f'Fetching invoices for {len(self.orders)} orders...'
            task = progress.add_task(message, total=len(self.orders))
            self.out(message)

            table = Table(title='Fetched Invoices')
            table.add_column('Invoice Number', style='cyan', no_wrap=True)
            table.add_column('Order', no_wrap=True)
            table.add_column('Invoice Date', no_wrap=True)
            table.add_column('Status', no_wrap=True)

            for order in self.orders:
                for invoice in self.get_invoices(order):
                    if invoice['date'] == self.import_date.strftime('%Y-%m-%d'):
                        self.invoices.append(invoice)
                        status = '[bold green]to be imported'
                        self.out(
                            f'Invoice {invoice["number"]} for order {order} noted for import'
                        )
                    else:
                        status = '[bold yellow]no import'
                        self.out(
                            f'Invoice {invoice["number"]} for order {order} created '
                            f'on {invoice["date"]}, no import'
                        )
                    table.add_row(
                        invoice['number'],
                        order,
                        invoice['date'],
                        status,
                    )
                progress.update(task, advance=1)

        self.console.print(table)

    def replace_placeholders(self, text: str, data: dict):
        """Replace placeholders that match keys in a pretix object."""
        for key, value in data.items():
            text = text.replace(f'%{key}%', str(value))
        return text

    def convert_invoice(self, invoice: dict):
        """Convert a pretix invoice to a Lexware voucher."""
        items = []
        total_gross = 0.0
        total_tax = 0.0

        for item in invoice['lines']:
            tax_amount = round(float(item['tax_value'].replace('-', '')), 2)
            gross_amount = round(float(item['gross_value'].replace('-', '')), 2)
            total_gross += gross_amount
            total_tax += tax_amount
            items.append({
                'amount': gross_amount,
                'taxAmount': tax_amount,
                'taxRatePercent': int(round(float(item['tax_rate']), 0)),
                'categoryId': self.settings.lexware.posting_category,
            })

        voucher_text = self.replace_placeholders(self.settings.texts.voucher, invoice)
        cancellation_prefix = (
            self.replace_placeholders(self.settings.texts.cancellation, invoice)
            if invoice['is_cancellation']
            else ''
        )
        reference = (
            self.replace_placeholders(self.settings.texts.reference, invoice)
            if invoice['refers'] is not None
            else ''
        )

        return {
            'type': 'salescreditnote' if invoice['is_cancellation'] else 'salesinvoice',
            'voucherNumber': invoice['number'],
            'voucherDate': invoice['date'],
            'shippingDate': invoice['date'],
            'dueDate': invoice['date'],
            'totalGrossAmount': round(total_gross, 2),
            'totalTaxAmount': round(total_tax, 2),
            'taxType': 'gross',
            'useCollectiveContact': True,
            'remark': f'{cancellation_prefix}{voucher_text}{reference}',
            'voucherItems': items,
        }

    def convert_invoices(self):
        message = f'Converting {len(self.invoices)} invoices to vouchers...'
        with self.progress() as progress:
            task = progress.add_task(message, total=len(self.invoices))
            self.out(message, level=logging.INFO)
            for invoice in self.invoices:
                self.vouchers.append(self.convert_invoice(invoice))
                progress.update(task, advance=1)

    def create_giftcard_voucher(self, order: dict, payment: dict):
        """Create a voucher to reverse a gift card payment."""
        amount = round(float(payment['amount'].replace('-', '')), 2)
        voucher_date = self.import_date.strftime('%Y-%m-%d')
        return {
            'type': 'salescreditnote',
            'voucherNumber': f'GIFT-{order["code"]}-{payment["local_id"]:03d}',
            'voucherDate': voucher_date,
            'shippingDate': voucher_date,
            'dueDate': voucher_date,
            'totalGrossAmount': amount,
            'totalTaxAmount': 0.0,
            'taxType': 'gross',
            'useCollectiveContact': True,
            'remark': self.replace_placeholders(self.settings.texts.giftcard, order),
            'voucherItems': [{
                'amount': amount,
                'taxAmount': 0.0,
                'taxRatePercent': 0,
                'categoryId': self.settings.lexware.posting_category,
            }],
        }

    def add_giftcard_vouchers(self):
        message = f'Checking {len(self.orders)} orders for used gift cards...'
        with self.progress() as progress:
            task = progress.add_task(message, total=len(self.orders))
            self.out(message, level=logging.INFO)
            for order in self.orders:
                order_details = self.get_order(order)
                for payment in order_details.get('payments', []):
                    if payment.get('provider') != 'giftcard':
                        continue
                    payment_date = payment.get('payment_date')
                    if (
                        payment_date
                        and datetime.datetime.fromisoformat(payment_date).date()
                        == self.import_date
                    ):
                        self.vouchers.append(
                            self.create_giftcard_voucher(order_details, payment)
                        )
                        self.out(
                            f'Gift card payment {payment["local_id"]} for order '
                            f'{order} noted for reversal.'
                        )
                progress.update(task, advance=1)

    def create_voucher(self, voucher: dict):
        """Create a voucher in the Lexware API."""
        headers = {
            'Authorization': 'Bearer ' + self.settings.lexware.api_key,
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        }
        return requests.request(
            'POST',
            self.settings.lexware.api_url + '/vouchers',
            headers=headers,
            data=json.dumps(voucher),
        )

    def post_vouchers(self):
        total_vouchers = len(self.vouchers)
        table = Table(title='Imported Invoices')
        table.add_column('Invoice Number', style='cyan', no_wrap=True)
        table.add_column('Type', no_wrap=True)
        table.add_column('Invoice Date', no_wrap=True)
        table.add_column('Gross Amount', no_wrap=True, justify='right')
        table.add_column('Tax Amount', no_wrap=True, justify='right')
        table.add_column('Status', no_wrap=True)

        with self.progress() as progress:
            message = f'Importing {total_vouchers} invoices...'
            task = progress.add_task(message, total=total_vouchers)
            self.out(message)

            for voucher in self.vouchers:
                status = '[bold red]failed'
                if self.settings.test_mode:
                    status = '[bold green]no import (test mode)'
                    self.import_successful += 1
                    self.out(
                        f'Invoice {voucher["voucherNumber"]} not imported (test mode).'
                    )
                else:
                    for attempt in range(self.settings.lexware.retry_count + 1):
                        result = self.create_voucher(voucher)
                        if result.ok:
                            status = '[bold green]successful'
                            self.import_successful += 1
                            self.out(
                                f'Invoice {voucher["voucherNumber"]} imported successfully.'
                            )
                            break
                        if result.status_code == 429 and attempt < self.settings.lexware.retry_count:
                            time.sleep(2)
                            remaining = self.settings.lexware.retry_count - attempt
                            message = (
                                f'API rate limit exceeded (status {result.status_code}). '
                                f'Will try again {remaining} more time(s).'
                            )
                            self.out(message, style='bold yellow', level=logging.WARNING)
                            continue

                        self.import_failed += 1
                        self.out(
                            f'Failed to import invoice {voucher["voucherNumber"]}: '
                            f'{result.status_code} {result.text}',
                            level=logging.ERROR,
                        )
                        break

                table.add_row(
                    voucher['voucherNumber'],
                    voucher['type'],
                    self.import_date.strftime('%Y-%m-%d'),
                    f'{voucher["totalGrossAmount"]:.2f}',
                    f'{voucher["totalTaxAmount"]:.2f}',
                    status,
                )
                progress.update(task, advance=1)

        self.console.print(table)
        self.console.log(f'Import summary for {self.import_date}:', style='bold')
        stats = [
            Panel(f'{self.import_successful}', title='Successful', style='bold green'),
            Panel(f'{self.import_failed}', title='Failed', style='bold red'),
            Panel(f'{total_vouchers}', title='Total', style='bold'),
        ]
        self.console.log(Columns(stats))
        self.out(
            f'Import summary for {self.import_date}: {self.import_successful} '
            f'successful, {self.import_failed} failed out of {total_vouchers} total.'
        )


def main():
    """Parse command-line options and run the invoice import."""
    parser = argparse.ArgumentParser(
        description='Import invoices from pretix to Lexware for a specified date.'
    )
    parser.add_argument(
        '-d',
        '--date',
        type=lambda value: datetime.datetime.strptime(value, '%Y-%m-%d').date(),
        help='Date for which to import invoices (format: YYYY-MM-DD). Defaults to yesterday.',
    )
    parser.add_argument(
        '-t', '--test', action='store_true',
        help='Run in test mode without actually creating invoices.',
    )
    parser.add_argument(
        '-l', '--logging', action='store_true',
        help='Output log to a file in the same directory.',
    )
    parser.add_argument(
        '-s', '--settings-file', default='./settings.yaml',
        help='Path to the settings file if different from the default location (default: ./settings.yaml).',
    )
    args = parser.parse_args()
    import_date = args.date or datetime.date.today() - datetime.timedelta(days=1)

    try:
        connector = Connector(
            import_date,
            args.test,
            args.logging,
            args.settings_file,
        )
    except FileNotFoundError:
        Console().log(
            f'Settings file not found under {args.settings_file}. '
            'Please ensure the settings file exists and is accessible.',
            style='bold red',
        )
        logging.getLogger(__name__).error('Settings file not found: %s', args.settings_file)
        return
    except yaml.YAMLError as error:
        Console().log(
            f'Error parsing the settings file: {error}. '
            'Please ensure the settings file is valid YAML.',
            style='bold red',
        )
        logging.getLogger(__name__).error('Error parsing settings file: %s', error)
        return
    except KeyError as error:
        Console().log(str(error), style='bold red')
        logging.getLogger(__name__).error('Missing required setting: %s', error)
        return

    connector.run()


if __name__ == '__main__':
    main()