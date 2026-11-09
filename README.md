# pretix Import for Lexware
Import invoices from a pretix ticket shop to Lexware. 

## Summary
Import all ticket sales from pretix and post them as vouchers to Lexware. The script imports invoices for 
a specified date which can be passed as a command-line argument (default: yesterday). 
In Lexware, a voucher (not an actual invoice) will be created using the collective contact. All lines of 
the invoice will be passed as voucher items, so different tax rates will be handled correctly. 
Cancellation invoices will also be imported as sales credit notes. 
If a gift card is applied, an additional voucher will be created to reverse the voucher purchase (see below). 
All vouchers will be creating using the same posting category which is specified as a constant in the script. 

## Setup & Use
The script relies on the following environment variables to be set:
- PRETIX_RESOURCE_URL: The base URL of the pretix API (e.g https://pretix.host.example/api/v1/organizers/[org]/events/[event])
- PRETIX_API_KEY: The API key for the pretix API.
- LEXWARE_API_URL: The base URL of the Lexware API (e.g. https://api.lexware.io/v1).
- LEXWARE_API_KEY: The API key for the Lexware API.
- LEXWARE_RETRY_COUNT: Number of times the script should retry creating a voucher in case the request is rejected due to the [rate limit](https://developers.lexware.io/docs/#api-rate-limits)
The script uses dotenv, so a .env-file in the same directory will be sufficient. A sample .env file is supplied within the project files. 

There are four more environment variables for adjusting the [voucher text](https://developers.lexware.io/docs/#vouchers-endpoint-voucher-properties) (remark): 
- VOUCHER_TEXT: The general voucher text which is added to all requests. 
- VOUCHER_ADDT_CANCELATION: Additional text which will be added *in front of* the general text in case the invoice is a credit note/cancelation. 
- VOUCHER_ADDT_REFERENCE: Additional text which will be added *at the end of* the general text in case the invoice refers to another document. 
These will be overwritten with default values if they are not present. Placeholders which refer to properties of the [pretix invoice endpoint](https://docs.pretix.eu/dev/api/resources/invoices.html) can be used. For more information, please read the respective section below.
- VOUCHER_TEXT_GIFTCARD: The voucher text for sales credit notes (see [Lexware API documentation for further explanation on voucher types](https://developers.lexware.io/docs/#vouchers-endpoint-voucher-properties)) to reverse a gift card purchase if a gift card is applied in a relevant order. 
This will also be overwritten with a default value if it is not present. Placeholders which refer to properties of the [pretix order endpoint](https://docs.pretix.eu/dev/api/resources/orders.html) can be used. For more information, please read the respective section below.

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

The script will use [Rich](https://github.com/Textualize/rich) for output to the console. If the script is run in background mode or if a log is necessary for any other reason, logging to a file in the same directory can be activated using the command line argument -l resp. --logging. The logging file will be created and edited using RotatingFileHandler. 

## API References
For further information on the pretix and Lexware APIs, please refer to the following documentation:
- https://developers.lexware.io/docs/
- https://docs.pretix.eu/dev/api/index.html

## Gift Cards 
Gift cards can be sold via pretix shops. Gift cards are multi-purpose vouchers and will thus always have a 0 % VAT rate (according to German tax law). 
For gift card purchases, regular invoices are issued which can also be imported using this script along with ticket or merchandise orders. 
When using a gift card during the order process, the invoice will state the full amount and the invoice object received through the API will have no 
reference to the applied voucher. In the [order object](https://docs.pretix.eu/dev/api/resources/orders.html), the gift card application is listed in the 
*payments* property though. 
To record the gift card application correctly in the accounts, in addition to the invoice, a sales credit note is posted over the used amount and 
with a VAT rate of 0 %. The script checks if the application date matches the import date. 

## Voucher Texts 
All vouchers (see [endpoint documentation](https://developers.lexware.io/docs/#vouchers-endpoint)) created in lexware will get a descriptive text (property "remark"). This text should give accountants, controllers or auditors a sufficient description of the nature and origin of the voucher resp. accounting document. It is also used for allocating bank transfers and it is searchable, so it is generally a good idea to add customer information or other reference numbers like the order number for clearing purposes. 

The voucher text will be created according to the following three environment variables: 
- VOUCHER_TEXT: The general voucher text which is added to all requests. 
- VOUCHER_ADDT_CANCELATION: Additional text which will be added *in front of* the general text in case the invoice is a credit note/cancelation. 
- VOUCHER_ADDT_REFERENCE: Additional text which will be added *at the end of* the general text in case the invoice refers to another document.
- VOUCHER_TEXT_GIFTCARD: The voucher text for sales credit notes to reverse a gift card purchase if a gift card is applied in a relevant order. 
They have default values and can include *placeholders* (see below). It is possible to include line breaks. 

# Default Voucher Texts
- VOUCHER_TEXT: 'Order %order% for event %event%,\n %invoice_to_name%, %invoice_to_street%, %invoice_to_zipcode% %invoice_to_city%, %invoice_to_country%' which includes the order number as well as the customer address 
- VOUCHER_ADDT_CANCELATION: 'Cancelation '
- VOUCHER_ADDT_REFERENCE: 'Reference: %refers%' which includes the reference document number
- VOUCHER_TEXT_GIFTCARD: 'Usage of a gift voucher for Order No. %code%'

Thus, the default voucher text for a cancellation (which refers to the cancelled invoice) would be for example: 

```
Cancelation
Order A1B2C3 for event TestCon 2027,
John Doe, 12 Testing Crescent, AB3 C4D Testham, GB
Reference: TESTCON2027-2026-000123
```

# Placeholders 
All properties of the [invoice endpoint](https://docs.pretix.eu/dev/api/resources/invoices.html) of the pretix API can be used as placeholders for regular invoices and sales credit notes as long as they are direct child properties and have the type *String* or are at least convertible to a string value. To add a property, the name in percent signs (e.g. *%invoice_to%*) can be added to the text. 
For the voucher text of *sales credit notes referring to giftcard applications*, the properties of the [pretix order endpoint](https://docs.pretix.eu/dev/api/resources/orders.html) can be used. 

Please mind: 
1. It is not possible to add nested properties or paths like *%lines/description% or anything in that direction. The respective functions simply uses the placeholder as a key to read the respective value from the invoice JSON object.
2. When the property is technically not convertible to a string value, the script will fail. Make sure to only use string values or values which can be converted to a string.
3. When the property is technically convertible to a string, but the conversion leads to an undesirable output (e.g. using boolean, datetime or array/list values), it is not recommended to use that property.
4. It is not possible to apply formatting or anything in that direction.
5. The name of the property has to be written correctly including proper case. Please look the respective key names up in the [API documentation](https://docs.pretix.eu/dev/api/resources/invoices.html). 
