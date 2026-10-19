# pretix Import for Lexware
Import invoices from a pretix ticket shop to Lexware. 

## Summary
Import all ticket sales from pretix and post them as vouchers to Lexware. The script imports invoices for 
a specified date which can be passed as a command-line argument (default: yesterday). 
In Lexware, a voucher (not an actual invoice) will be created using the collective contact. All lines of 
the invoice will be passed as voucher items, so different tax rates will be handled correctly. 
Cancellation invoices will also be imported as sales credit notes. 
All vouchers will be creating using the same posting category which is specified as a constant in the script. 

## Setup & Use
The script relies on the following environment variables to be set:
- PRETIX_RESOURCE_URL: The base URL of the pretix API (e.g https://pretix.host.example/api/v1/organizers/[org]/events/[event])
- PRETIX_API_KEY: The API key for the pretix API.
- LEXWARE_API_URL: The base URL of the Lexware API (e.g. https://api.lexware.io/v1).
- LEXWARE_API_KEY: The API key for the Lexware API.
The script uses dotenv, so a .env-file in the same directory will be sufficient. 

The Lexware API URL remains static, the pretix API URL is event-specific and must be set accordingly. 
You will need to run this script multiple times for each event if you want to handle multiple events.
The API keys for both APIs must be generated in the respective systems. For more information on URLs and on how to 
generate the API keys, please refer to the respective API documentation (see below).

As the script imports invoices for a whole day, it should not be run for the current day. A failsafe for 
duplicate imports is not yet implemented. 

The script can be called with the following command line arguments:
-d, --date DATE        Date for which to import invoices (format: YYYY-MM-DD).
-t, --test             Run in test mode without actually creating invoices.
-i, --inspect          Dump payload for Lexware API to the console for inspection. Only works in test mode.

If the import is handled manually, pass the date (format: YYYY-MM-DD) for which to import invoices. The argument
may be omitted if invoices for yesterday are to be imported. If the script is run automatically (e.g. via cron job),
it is advisable to omit the date argument and run the script daily, as it will then always import invoices for the 
previous day.

For testing purposes, the script can be run in test mode, which will not create any vouchers in Lexware. Test mode
can be activated passing the respective command line argument (-t, --test). If necessary, pass the argument -i, --inspect 
to inspect the payload for the Lexware API. The payload that would have been sent to create
the invoice will then be printed to the console. 
In test mode, the script will still fetch and convert the invoices, just not call the Lexware API. 

## API References
For further information on the pretix and Lexware APIs, please refer to the following documentation:
- https://developers.lexware.io/docs/
- https://docs.pretix.eu/dev/api/index.html
