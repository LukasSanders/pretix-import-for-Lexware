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
    """
    Provides a data container object for all settings referring to the Lexware API.
    Container object will be handled by the Settings class and is not intended to be used directly.

    Attributes:
        api_key (str): The API key for authenticating with the Lexware API.
        api_url (str): The base URL for the Lexware API.
        posting_category (str): The posting category to be used for vouchers.
        retry_count (int): The number of times to retry API requests in case of failure.
    """

    def __init__(self, api_key, api_url, posting_category, retry_count=3):
        self.api_key = api_key
        self.api_url = api_url
        self.posting_category = posting_category
        self.retry_count = retry_count

class PretixSettings:
    """
    Provides a data container object for all settings referring to the pretix API.
    Container object will be handled by the Settings class and is not intended to be used directly.

    Attributes:
        resource_url (str): The base URL for the pretix API.
        api_token (str): The API token for authenticating with the pretix API.
    """

    def __init__(self, resource_url, api_token):
        """
        Constructor method for the settings container.

        Parameters
        ----------
        resource_url : str
            The base URL for the pretix API.
        api_token : str
            The API token for authenticating with the pretix API.
        """

        self.resource_url = resource_url
        self.api_token = api_token

class TextSettings:
    """
    Provides a data container object for all settings used for voucher text generation.
    Container object will be handled by the Settings class and is not intended to be used directly.

    Attributes
    ----------
        voucher (str): The text template for voucher generation.
        cancellation (str): The text template for cancellation messages.
        reference (str): The text template for reference information.
        giftcard (str): The text template for gift card related messages.
    """

    def __init__(self, voucher, cancellation, reference, giftcard):
        self.voucher = voucher
        self.cancellation = cancellation
        self.reference = reference
        self.giftcard = giftcard

class Settings:
    """
    Provides a data container object for all settings used in the invoice import process.
    Uses the PretixSettings, LexwareSettings, and TextSettings classes to encapsulate specific settings.

    Attributes
    ----------
        import_date (datetime.date): The date for which invoices will be imported.
        test_mode (bool): Flag indicating whether the script is running in test mode.
        logging_enabled (bool): Flag indicating whether logging to a file is enabled.
        logfile_name (str): The name of the log file for logging output.
        pretix (PretixSettings): An instance of the PretixSettings class containing pretix API settings.
        lexware (LexwareSettings): An instance of the LexwareSettings class containing Lexware API settings.
        texts (TextSettings): An instance of the TextSettings class containing text templates for voucher generation
        request_timeout (int): The timeout duration for API requests in seconds.

    Methods
    -------
        set_test_mode(active: bool): Sets the test mode flag.
        set_logging(enabled: bool): Sets the logging enabled flag.
    """

    def __init__(self, settings):
        """
        Constructor method for the settings container.

        Parameters
        ----------
        settings : Dictionary
            Dictionary containing the settings loaded from the settings.yaml file. The dictionary is expected
            to have the following structure:
            {
                "api_settings": {
                    "pretix": {
                        "resource_url": "https://pretix.example.com/api/",
                        "api_token": "your_pretix_api_token"
                    },
                    "lexware": {
                        "api_key": "your_lexware_api_key",
                        "api_url": "https://lexware.example.com/api/",
                        "posting_category": "your_posting_category",
                        "retry_count": 3
                    }
                },
                "texts": {
                    "voucher": "Your voucher text %with% %placeholders% here",
                    "cancellation": "Your cancellation text %with% %placeholders% here",
                    "reference": "Your reference text %with% %placeholders% here",
                    "giftcard": "Your gift card text %with% %placeholders% here"
                }
            }

        Raises
        ------
        KeyError
            The dictionary is missing required settings. The error message will indicate which setting is missing.
            Due to a missing setting in the YAML file or incorrect keys.
        """

        self.import_date = datetime.date.today() - datetime.timedelta(days=1)
        self.test_mode = False
        self.logging_enabled = False
        self.logfile_name = 'pretix_to_lexware.log'
        self.request_timeout = 30

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
        """Sets the test mode flag."""
        self.test_mode = active

    def set_logging(self, enabled: bool):
        """Sets the logging enabled flag."""
        self.logging_enabled = enabled

class Connector:
    """
    Import, convert, and post pretix invoices to Lexware.

    The connector loads the YAML settings, coordinates API requests, converts pretix
    invoice data into Lexware voucher data, handles gift-card reversals, and reports
    progress and results in the CLI.

    Attributes
    ----------
    console : rich.console.Console
        Console used for CLI output.
    logger : logging.Logger
        Logger used for optional file logging.
    settings : Settings
        API, text, and runtime settings loaded from the YAML file.
    import_date : datetime.date
        Date for which transactions and invoices are imported.
    transactions : list[dict]
        Transactions fetched from pretix for the import date.
    orders : list[str]
        Unique order codes referenced by the fetched transactions.
    invoices : list[dict]
        Invoices whose invoice date matches the import date.
    vouchers : list[dict]
        Lexware voucher objects prepared for posting.
    import_successful : int
        Number of vouchers posted successfully or counted as successful in test mode.
    import_failed : int
        Number of vouchers that could not be posted.

    Methods
    -------
    out(message, style, level)
        Print a message to the CLI and optionally write it to the log.
    progress(**kwargs)
        Create a full-width Rich progress context for CLI operations.
    configure_logging()
        Configure the rotating file logger when logging is enabled.
    run()
        Validate settings and run the complete invoice import workflow.
    get_transactions(date)
        Fetch pretix transactions created on the specified date.
    get_invoices(order)
        Fetch pretix invoices associated with an order.
    get_order(order)
        Fetch an order and its payments from pretix.
    fetch_invoices()
        Fetch and filter invoices for the configured import date.
    replace_placeholders(text, data)
        Replace invoice or order placeholder values in configured text.
    convert_invoice(invoice)
        Convert one pretix invoice into a Lexware voucher object.
    convert_invoices()
        Convert all fetched invoices into Lexware voucher objects.
    create_giftcard_voucher(order, payment)
        Create a Lexware credit note for a gift-card payment.
    add_giftcard_vouchers()
        Add gift-card reversal vouchers for applicable orders.
    create_voucher(voucher)
        Post one voucher object to the Lexware API.
    post_vouchers()
        Post all prepared vouchers and print the final import summary.
    """

    def __init__(
        self,
        import_date: datetime.date,
        test_mode: bool,
        logging_enabled: bool,
        settings_location: str,
    ):
        """
        Constructor method for the Connector class. Initializes the connector with the specified import date, test mode,
        logging settings, and settings file location. Loads the settings from the YAML file and prepares the console
        and logger for output.

        Parameters
        ----------
        import_date : datetime.date
            Date for which invoices should be imported.
        test_mode : bool
            If true, convert and report vouchers without posting them to Lexware.
        logging_enabled : bool
            If true, enable log output to the configured rotating log file.
        settings_location : str
            Path to the YAML settings file.

        Raises
        ------
        FileNotFoundError
            If the settings file does not exist.
        yaml.YAMLError
            If the settings file cannot be parsed.
        KeyError
            If a required settings key is missing or has an incorrect key name.
        """

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

        # Load settings from the YAML file and initialize the Settings object
        with Path(settings_location).open(encoding='utf-8') as settings_file:
            self.settings = Settings(yaml.safe_load(settings_file))
        self.settings.set_logging(logging_enabled)
        self.settings.set_test_mode(test_mode)

    def out(self, message, style=None, level=logging.INFO):
        """
        Outputs a message to the CLI. If logging is enabled, the same message is also
        logged. While a Rich Progress Indicator is running, it's own console is used
        to avoid duplicate rendering.

        Parameters
        ----------
        message : str
            The message to be printed to the console and logged.
        style : str or Style, optional
            The style to be applied to the message, by default None. Please see the
            Rich documentation for available styles (https://rich.readthedocs.io/en/stable/style.html).
            You may use a style definition or a Style object.
        level : int, optional
            The logging level for the message, by default logging.INFO. Attributes from the logging module
            should be used (e.g., logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR).
        """

        # Determine which console to use: the active progress console if a progress indicator is running, otherwise the main console.
        console = (
            self._active_progress.console
            if self._active_progress is not None
            else self.console
        )
        console.log(message, style=style)

        # Abort logging if logging is not enabled in the settings.
        if not self.settings.logging_enabled:
            return

        # Use the appropriate logging method based on the specified logging level.
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
        """
        Run a progress indicator and route output through its console.

        Parameters
        ----------
        **kwargs : dict
            Additional keyword arguments to be passed to the Rich Progress constructor.

        Yields
        ------
        rich.progress.Progress
            The active progress indicator.

        Raises
        ------
        TypeError
            If an unsupported keyword argument is passed to Rich Progress.
        ValueError
            If a progress configuration value is invalid.
        """

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
        """
        Configures the rotating file logger if logging is enabled. The log file will be created in the same directory
        as the script, with a maximum size of 1 MB and up to 5 backup files. The log format includes the timestamp,
        logger name, log level, and message. If the log file cannot be created or written to, an OSError will be raised.

        Raises
        ------
        OSError
            If the log file or rotating file handler cannot be created.
        """

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
        """
        Execute the complete invoice import workflow.

        Validates the API and date settings, fetches transactions and invoices,
        converts them to Lexware vouchers, adds gift-card reversals, and posts
        the vouchers.

        Returns
        -------
        None
            The method prints results and exits early if validation or input checks fail.

        Raises
        ------
        KeyError
            If an API response or invoice lacks a required key.
        TypeError
            If a required invoice or payment value has an incompatible type.
        AttributeError
            If a required payment or text value does not support the expected operation.
        ValueError
            If an invoice, gift-card payment, or date value cannot be converted.
        """

        """"""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
        """ Phase 0: Initial checks and preparations.                              """
        """"""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""        

        # Check if the necessary API settings are present in the settings file. 
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

        # Importing for a future date or today is not allowed. Future dates will return
        # no transactions; if running for the same day, some transactions may be missed. 
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

        # Indicate if running in test mode. 
        if self.settings.test_mode:
            message = 'Running in test mode. No invoices will be created.'
            self.console.print(Panel(message, title='TEST MODE', style='bold yellow'))
            self.out(message, style='bold yellow', level=logging.WARNING)

        """"""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
        """ Phase 1: Fetch transactions for the specified import date.             """
        """"""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

        with self.console.status(f'Fetching transactions for {self.import_date}...'):
            self.transactions = self.get_transactions(self.import_date)

        if not self.transactions:
            self.out(
                f'No transactions received for {self.import_date}. Leaving program.',
                style='bold yellow',
            )
            return

        """"""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
        """ Phase 2: Fetch invoices for all orders referenced by the transactions. """
        """"""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

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

        """"""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
        """ Phase 3: Convert invoices and add reversals for gift-card payments.    """
        """"""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

        self.convert_invoices()
        self.add_giftcard_vouchers()

        """"""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
        """ Phase 4: Post vouchers to Lexware.                                     """
        """"""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

        self.post_vouchers()

    def get_transactions(self, date: datetime.date):
        """
        Fetch transactions from pretix for a given date. See pretix API documentation for 
        details on the transactions endpoint.

        Parameters
        ----------
        date : datetime.date
            Date for which transactions are requested.

        Returns
        -------
        list[dict]
            Transaction objects returned by pretix, or an empty list on API failure.

        """
        url = (
            self.settings.pretix.resource_url
            + '/transactions/?created_since='
            + date.strftime('%Y-%m-%d')
            + 'T00:00:00Z'
        )
        headers = {'Authorization': 'Token ' + self.settings.pretix.api_token}
        try:
            response = requests.request(
                'GET',
                url,
                headers=headers,
                data={},
                timeout=self.settings.request_timeout,
            )
        except requests.RequestException as error:
            self.out(
                f'Failed to fetch transactions: {error}',
                style='bold red',
                level=logging.ERROR,
            )
            return []

        if response.status_code != 200:
            self.out(
                f'Failed to fetch transactions: {response.status_code} - {response.text}',
                style='bold red',
                level=logging.ERROR,
            )
            return []

        try:
            result = response.json()
            count = result['count']
            transactions = result['results']
        except (json.JSONDecodeError, requests.exceptions.JSONDecodeError, KeyError) as error:
            self.out(
                f'Invalid transaction response from pretix: {error}',
                style='bold red',
                level=logging.ERROR,
            )
            return []

        self.out(
            f'Successfully fetched {count} transactions for {date}',
            style='bold green',
        )
        return transactions

    def get_invoices(self, order: str):
        """
        Fetch invoices from pretix for a given order. See pretix API documentation for 
        more details on the invoices endpoint.

        Parameters
        ----------
        order : str
            Pretix order code used to filter the invoices.

        Returns
        -------
        list[dict]
            Invoice objects associated with the order.

        """
        url = self.settings.pretix.resource_url + '/invoices/?order=' + order
        headers = {'Authorization': 'Token ' + self.settings.pretix.api_token}
        try:
            response = requests.request(
                'GET',
                url,
                headers=headers,
                data={},
                timeout=self.settings.request_timeout,
            )
            response.raise_for_status()
            return response.json()['results']
        except requests.RequestException as error:
            self.out(
                f'Failed to fetch invoices for order {order}: {error}',
                style='bold red',
                level=logging.ERROR,
            )
        except (json.JSONDecodeError, requests.exceptions.JSONDecodeError, KeyError) as error:
            self.out(
                f'Invalid invoice response for order {order}: {error}',
                style='bold red',
                level=logging.ERROR,
            )
        return []

    def get_order(self, order: str):
        """
        Fetch an order from pretix. Used to check the order object for gift-card payments. 
        See pretix API documentation for more details on the orders endpoint.

        Parameters
        ----------
        order : str
            Pretix order code to fetch.

        Returns
        -------
        dict
            Order data, including payments and order properties.

        """
        url = self.settings.pretix.resource_url + '/orders/' + order
        headers = {'Authorization': 'Token ' + self.settings.pretix.api_token}
        try:
            response = requests.request(
                'GET',
                url,
                headers=headers,
                data={},
                timeout=self.settings.request_timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as error:
            self.out(
                f'Failed to fetch order {order}: {error}',
                style='bold red',
                level=logging.ERROR,
            )
        except (json.JSONDecodeError, requests.exceptions.JSONDecodeError) as error:
            self.out(
                f'Invalid order response for order {order}: {error}',
                style='bold red',
                level=logging.ERROR,
            )
        return {}

    def fetch_invoices(self):
        """
        Fetch invoices for the unique orders and filter them by the import date.
        See pretix API documentation for details on the invoices endpoint.

        Matching invoices are stored in `invoices`; fetched but unmatched invoices
        are reported in a results table.

        Raises
        -------
        KeyError
            If an invoice lacks `date` or `number`.
        """
        with self.progress() as progress:
            message = f'Fetching invoices for {len(self.orders)} orders...'
            task = progress.add_task(message, total=len(self.orders))
            self.out(message)

            # Add table output for better usability. 
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
        """
        Replace placeholders in a template text from the settings file for voucher generation
        with values from a pretix object. Please see the documentation on text placeholders. 
        The placeholder name has to exactly match the key in the pretix object, including case sensitivity.

        Parameters
        ----------
        text : str
            Text containing placeholders in `%key%` notation.
        data : dict
            Pretix invoice or order data used as placeholder values.

        Returns
        -------
        str
            Text with matching placeholders replaced.

        Raises
        ------
        AttributeError
            If `text` or a placeholder value is not suitable for string replacement.
        TypeError
            If `data` is not an iterable mapping or contains incompatible values.
        """
        for key, value in data.items():
            text = text.replace(f'%{key}%', str(value))
        return text

    def convert_invoice(self, invoice: dict):
        """
        Convert a pretix invoice to a Lexware voucher. Please see the respective API documentation
        for more information on the invoice and voucher objects. 
        The method calculates a total gross and tax amount and thus avoids rounding errors resulting
        from different rounding methods between pretix and Lexware.

        Parameters
        ----------
        invoice : dict
            Pretix invoice data containing header fields and invoice lines.

        Returns
        -------
        dict
            Lexware voucher object ready for posting.

        Raises
        ------
        KeyError
            If a required invoice or invoice-line field is missing.
        AttributeError
            If a numeric value does not provide string replacement behavior.
        TypeError
            If an invoice or line value has an incompatible type.
        ValueError
            If a numeric gross amount, tax amount, or tax rate cannot be converted.
        """

        # First, convert all pretix invoice lines to voucher items. While doing this, 
        # calculate the total gross and tax amounts. 
        # All amounts are rounded to two decimal places. 
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

        # Generate the voucher text, cancellation prefix, and reference text from the configured templates.
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
        """
        Convert all fetched invoices into Lexware voucher objects.

        The resulting objects are appended to `vouchers`.

        Raises
        ------
        KeyError
            If an invoice or invoice-line field is missing.
        TypeError
            If an invoice or numeric value has an incompatible type.
        ValueError
            If an invoice numeric value cannot be converted.
        """
        message = f'Converting {len(self.invoices)} invoices to vouchers...'
        with self.progress() as progress:
            task = progress.add_task(message, total=len(self.invoices))
            self.out(message, level=logging.INFO)
            for invoice in self.invoices:
                self.vouchers.append(self.convert_invoice(invoice))
                progress.update(task, advance=1)

    def create_giftcard_voucher(self, order: dict, payment: dict):
        """
        Create a Lexware credit note for a gift-card payment. When a gift-card is used, the payment is reversed in Lexware 
        by creating a credit note with the same amount as the gift-card payment (not the full gift-card value). 

        Parameters
        ----------
        order : dict
            Pretix order containing the order code.
        payment : dict
            Gift-card payment containing its amount and local ID.

        Returns
        -------
        dict
            Lexware sales credit-note voucher.

        Raises
        ------
        KeyError
            If the order code or payment amount/local ID is missing.
        AttributeError
            If the payment amount is not a string.
        ValueError
            If the gift-card payment amount cannot be converted to a number.
        """
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
        """
        Add reversal vouchers for gift-card payments made on the import date.

        The method checks every relevant order and, if found, creates a reversal 
        credit note for all used gift cards.

        Raises
        ------
        KeyError
            If required order or gift-card payment fields are missing.
        AttributeError
            If the gift-card payment amount is not a string.
        ValueError
            If a payment date or gift-card amount has an invalid format.
        """
        message = f'Checking {len(self.orders)} orders for used gift cards...'
        with self.progress() as progress:
            task = progress.add_task(message, total=len(self.orders))
            self.out(message, level=logging.INFO)
            for order in self.orders:
                order_details = self.get_order(order)
                for payment in order_details.get('payments', []):
                    # If a gift card is used, 'giftcard' is given as the payment provider. 
                    if payment.get('provider') != 'giftcard':
                        continue
                    # The payment date (date of when the gift card was used) has to be the same 
                    # as the import date as an order may contain earlier or later transactions. 
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
        """
        Post a voucher to the Lexware API.

        Parameters
        ----------
        voucher : dict
            Lexware voucher object to post.

        Returns
        -------
        requests.Response
            HTTP response returned by the Lexware API.

        Raises
        ------
        requests.RequestException
            If the Lexware API request fails due to a network or HTTP client error.
        """

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
            timeout=self.settings.request_timeout,
        )

    def post_vouchers(self):
        """
        Post all prepared vouchers and print the final import summary.

        Successful HTTP responses, test-mode skips, HTTP 429 retries, and failed
        requests update `import_successful` or `import_failed` and are shown in a
        CLI results table.

        Raises
        ------
        KeyError
            If a prepared voucher lacks a required display or amount field.
        """

        total_vouchers = len(self.vouchers)

        # Table output for convenient overview. 
        table = Table(title='Imported Invoices')
        table.add_column('Invoice Number', style='cyan', no_wrap=True)
        table.add_column('Type', no_wrap=True)
        table.add_column('Invoice Date', no_wrap=True)
        table.add_column('Gross Amount', no_wrap=True, justify='right')     # amounts right-alined 
        table.add_column('Tax Amount', no_wrap=True, justify='right')       # amounts right-alined
        table.add_column('Status', no_wrap=True)

        with self.progress() as progress:
            message = f'Importing {total_vouchers} invoices...'
            task = progress.add_task(message, total=total_vouchers)
            self.out(message)

            for voucher in self.vouchers:
                status = '[bold red]failed'
                # Posting to Lexware is omitted in test mode. 
                if self.settings.test_mode:
                    status = '[bold green]no import (test mode)'
                    self.import_successful += 1
                    self.out(
                        f'Invoice {voucher["voucherNumber"]} not imported (test mode).'
                    )
                else:
                    # A HTTP error with status 429 may occur if the API rate limit is exceeded. 
                    # In that case, the script will wait for two seconds and retry for a specified
                    # number of times (given in the settings file).
                    for attempt in range(self.settings.lexware.retry_count + 1):
                        try:
                            result = self.create_voucher(voucher)
                        except requests.RequestException as error:
                            status = '[bold red]failed'
                            self.import_failed += 1
                            self.out(
                                f'Failed to import invoice {voucher["voucherNumber"]}: '
                                f'{error}',
                                level=logging.ERROR,
                            )
                            break
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
                    self.import_date.strftime('%Y-%m-%d'),  # format to YYYY-MM-DD
                    f'{voucher["totalGrossAmount"]:.2f}',   # format to decimal, two decimal places
                    f'{voucher["totalTaxAmount"]:.2f}',     # format to decimal, two decimal places 
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

    try:
        connector.run()
    except requests.RequestException as error:
        Console().log(
            f'API request failed: {error}',
            style='bold red',
        )
        logging.getLogger(__name__).error('API request failed: %s', error)
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError, ValueError) as error:
        Console().log(
            f'Invalid API or invoice data: {error}',
            style='bold red',
        )
        logging.getLogger(__name__).error('Invalid API or invoice data: %s', error)
    except OSError as error:
        Console().log(
            f'File operation failed: {error}',
            style='bold red',
        )
        logging.getLogger(__name__).error('File operation failed: %s', error)


if __name__ == '__main__':
    main()