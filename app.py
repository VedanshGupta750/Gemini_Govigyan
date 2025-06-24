from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials
import os
from datetime import datetime
import json
import logging
import google.generativeai as genai
from dotenv import load_dotenv

# Load environment variables from a .env file
load_dotenv()

app = Flask(__name__)
# Configure CORS to allow requests from your frontend
CORS(app, resources={r"/*": {"origins": ["http://localhost:3000", "https://your-deployed-frontend.com"],
                             "methods": ["GET", "POST", "OPTIONS"],
                             "allow_headers": ["Content-Type"]}})

# Configure logging to write to 'app.log' file
logging.basicConfig(level=logging.INFO, filename='app.log', format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Database connection parameters from environment variables
DB_PARAMS = {
    'dbname': os.getenv('DB_NAME', 'your_db'),
    'user': os.getenv('DB_USER', 'your_user'),
    'password': os.getenv('DB_PASSWORD', 'your_password'),
    'host': os.getenv('DB_HOST', 'your_host'),
    'port': os.getenv('DB_PORT', '5432')
}

# Google Sheets API setup
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
CREDS_FILE = os.getenv('GOOGLE_CREDS', 'credentials.json') # Path to your service account credentials file

try:
    # Initialize Google Sheets service using service account credentials
    creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
    sheets_service = build('sheets', 'v4', credentials=creds)
    logger.info("Google Sheets API initialized successfully.")
except Exception as e:
    logger.error(f"Google Sheets API setup failed: {e}")
    # Re-raise the exception to stop the application if a critical service fails
    raise

# Gemini 2.0 Flash setup
try:
    # Configure Gemini API with the API key from environment variables
    genai.configure(api_key=os.getenv('GEMINI_API_KEY'))
    gemini_model = genai.GenerativeModel('gemini-2.0-flash')
    logger.info("Gemini API initialized successfully.")
except Exception as e:
    logger.error(f"Gemini API setup failed: {e}")
    # Re-raise the exception to stop the application if a critical service fails
    raise

def get_db_connection():
    """Establishes and returns a new PostgreSQL database connection."""
    try:
        conn = psycopg2.connect(**DB_PARAMS)
        logger.info("Database connection established.")
        return conn
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise # Propagate the exception to the caller

# Existing upload route (preserved and modified for StockBook schema)
@app.route('/upload', methods=['POST', 'OPTIONS'])
def upload_files():
    """
    Handles file uploads, processes them (placeholder for Gemini logic),
    inserts data into the StockBook table, and syncs to Google Sheets.
    """
    if request.method == 'OPTIONS':
        # Handle CORS preflight request
        return '', 200
    try:
        if 'files' not in request.files:
            logger.warning("No files in request")
            return jsonify({'error': 'No files uploaded'}), 400
        files = request.files.getlist('files')
        if not files or all(f.filename == '' for f in files):
            logger.warning("Empty file list or no valid files")
            return jsonify({'error': 'No valid files uploaded'}), 400

        data_entries = []
        for file in files:
            # Placeholder for Gemini logic. In a real scenario, this would extract data from the file.
            gemini_result = {}  # Replace with your original Gemini logic
            entry = {
                'Date': datetime.now().strftime('%Y-%m-%d'),
                'Particulars': gemini_result.get('description', 'Processed File'),
                'VoucherBillNo': gemini_result.get('bill_no', 'N/A'),
                'ReceiptQuantity': gemini_result.get('quantity', 0),
                'ReceiptAmount': float(gemini_result.get('amount', 0.0)),
                'IssuedQuantity': 0,
                'IssuedAmount': 0.0,
                'BalanceQuantity': gemini_result.get('quantity', 0),
                'BalanceAmount': float(gemini_result.get('amount', 0.0))
            }
            data_entries.append(entry)

        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor) # Use RealDictCursor for dictionary-like rows

        # Insert data into StockBook table
        for entry in data_entries:
            cur.execute("""
                INSERT INTO StockBook (Date, Particulars, VoucherBillNo, ReceiptQuantity, ReceiptAmount,
                                     IssuedQuantity, IssuedAmount, BalanceQuantity, BalanceAmount)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING TransactionID; -- Return the auto-generated TransactionID
            """, (
                entry['Date'], entry['Particulars'], entry['VoucherBillNo'],
                entry['ReceiptQuantity'], entry['ReceiptAmount'],
                entry['IssuedQuantity'], entry['IssuedAmount'],
                entry['BalanceQuantity'], entry['BalanceAmount']
            ))
            # Fetch the returned TransactionID and add it to the entry dictionary
            entry['TransactionID'] = cur.fetchone()['transactionid'] # Column names returned by psycopg2 are lowercase
        conn.commit() # Commit the transaction to save changes to the database

        # Sync data to Google Sheets
        spreadsheet_id = os.getenv('SPREADSHEET_ID', 'your_spreadsheet_id')
        values = [[e['TransactionID'], e['Date'], e['Particulars'], e['VoucherBillNo'],
                   e['ReceiptQuantity'], e['ReceiptAmount'], e['IssuedQuantity'],
                   e['IssuedAmount'], e['BalanceQuantity'], e['BalanceAmount']]
                  for e in data_entries]
        sheets_service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range='A1', # Append to the first sheet, starting at A1
            valueInputOption='RAW', # Interpret input data as raw values
            body={'values': values}
        ).execute()

        cur.close()
        conn.close()
        logger.info(f"Uploaded {len(files)} files successfully via /upload and synced to Google Sheets.")
        return jsonify({'message': 'Files processed and synced'}), 200

    except Exception as e:
        logger.error(f"Upload error: {e}", exc_info=True) # Log full traceback
        # Rollback in case of error
        if 'conn' in locals() and conn:
            conn.rollback()
        return jsonify({'error': str(e)}), 500

# Updated upload-flash route with detailed error logging and StockBook schema alignment
@app.route('/upload-flash', methods=['POST', 'OPTIONS'])
def upload_files_flash():
    """
    Handles file uploads, processes them with Gemini 2.0 Flash,
    inserts data into the StockBook table, and syncs to Google Sheets.
    """
    if request.method == 'OPTIONS':
        # Handle CORS preflight request
        return '', 200
    try:
        if 'files' not in request.files:
            logger.warning("No files in request")
            return jsonify({'error': 'No files uploaded'}), 400
        files = request.files.getlist('files')
        if not files or all(f.filename == '' for f in files):
            logger.warning("Empty file list or no valid files")
            return jsonify({'error': 'No valid files uploaded'}), 400

        logger.info(f"Processing {len(files)} files with Gemini 2.0 Flash")
        data_entries = []
        for file in files:
            try:
                # Read file content and prepare for Gemini API
                file_content = file.read()
                logger.debug(f"Processing file: {file.filename}, size: {len(file_content)} bytes, mimetype: {file.mimetype}")

                # Call Gemini API to extract data
                response = gemini_model.generate_content([
                    {"mime_type": file.mimetype, "data": file_content},
                    {"text": "Extract financial data: description, bill number, quantity, amount. Respond as a JSON object with keys 'description', 'bill_no', 'quantity', 'amount'."}
                ])
                gemini_result_text = response.text
                logger.debug(f"Gemini raw result: {gemini_result_text}")

                # Parse Gemini's JSON response
                try:
                    gemini_data = json.loads(gemini_result_text)
                except json.JSONDecodeError:
                    logger.error(f"Failed to parse Gemini JSON: {gemini_result_text}")
                    # Fallback if Gemini doesn't return perfect JSON
                    gemini_data = {
                        'description': gemini_result_text, # Use the raw text as description
                        'bill_no': 'N/A',
                        'quantity': 0,
                        'amount': 0.0
                    }

                # Map Gemini data to StockBook schema
                entry = {
                    'Date': datetime.now().strftime('%Y-%m-%d'),
                    'Particulars': gemini_data.get('description', 'Processed File'),
                    'VoucherBillNo': gemini_data.get('bill_no', 'N/A'),
                    'ReceiptQuantity': float(gemini_data.get('quantity', 0)), # Ensure float for DECIMAL type
                    'ReceiptAmount': float(gemini_data.get('amount', 0.0)),
                    'IssuedQuantity': 0.0, # Default to 0.0 for quantities, 0.0 for amounts
                    'IssuedAmount': 0.0,
                    'BalanceQuantity': float(gemini_data.get('quantity', 0)),
                    'BalanceAmount': float(gemini_data.get('amount', 0.0))
                }
                data_entries.append(entry)
            except Exception as e:
                logger.error(f"Gemini processing failed for {file.filename}: {e}", exc_info=True)
                # Decide whether to raise or continue; here, re-raise to fail the whole request
                raise

        logger.info("Inserting into PostgreSQL StockBook table.")
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        for entry in data_entries:
            cur.execute("""
                INSERT INTO StockBook (Date, Particulars, VoucherBillNo, ReceiptQuantity, ReceiptAmount,
                                     IssuedQuantity, IssuedAmount, BalanceQuantity, BalanceAmount)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING TransactionID;
            """, (
                entry['Date'], entry['Particulars'], entry['VoucherBillNo'],
                entry['ReceiptQuantity'], entry['ReceiptAmount'],
                entry['IssuedQuantity'], entry['IssuedAmount'],
                entry['BalanceQuantity'], entry['BalanceAmount']
            ))
            entry['TransactionID'] = cur.fetchone()['transactionid'] # psycopg2 returns column names in lowercase
        conn.commit()

        logger.info("Syncing to Google Sheets.")
        spreadsheet_id = os.getenv('SPREADSHEET_ID', 'your_spreadsheet_id')
        values = [[e['TransactionID'], e['Date'], e['Particulars'], e['VoucherBillNo'],
                   e['ReceiptQuantity'], e['ReceiptAmount'], e['IssuedQuantity'],
                   e['IssuedAmount'], e['BalanceQuantity'], e['BalanceAmount']]
                  for e in data_entries]

        # Append data to the Google Sheet
        sheets_service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range='A1',
            valueInputOption='RAW',
            body={'values': values}
        ).execute()

        cur.close()
        conn.close()
        sheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit" # Link for editing the sheet
        logger.info(f"Upload successful, sheet URL: {sheet_url}")
        return jsonify({'message': 'Files processed and synced to Google Sheet', 'sheet_url': sheet_url}), 200

    except Exception as e:
        logger.error(f"Upload-flash error: {e}", exc_info=True)
        if 'conn' in locals() and conn:
            conn.rollback() # Ensure rollback on error
        return jsonify({'error': f"Failed to process files: {str(e)}"}), 500

@app.route('/results', methods=['GET'])
def get_results():
    """Retrieves all data from the StockBook table."""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        # Select all columns from StockBook, ordered by TransactionID
        cur.execute("SELECT * FROM StockBook ORDER BY TransactionID")
        data = cur.fetchall()
        cur.close()
        conn.close()
        logger.info("Fetched results successfully from StockBook table.")
        return jsonify(data), 200
    except Exception as e:
        logger.error(f"Results error: {e}", exc_info=True)
        return jsonify({'error': 'Failed to load data from StockBook'}), 500

@app.route('/update', methods=['POST'])
def update_data():
    """Updates existing data in the StockBook table and syncs to Google Sheets."""
    try:
        updates = request.json # Expects a list of dictionaries, each representing a row to update
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        for update in updates:
            # Update query for StockBook table based on TransactionID
            cur.execute("""
                UPDATE StockBook
                SET Date = %s, Particulars = %s, VoucherBillNo = %s,
                    ReceiptQuantity = %s, ReceiptAmount = %s,
                    IssuedQuantity = %s, IssuedAmount = %s,
                    BalanceQuantity = %s, BalanceAmount = %s
                WHERE TransactionID = %s
            """, (
                update['Date'], update['Particulars'], update['VoucherBillNo'],
                update['ReceiptQuantity'], update['ReceiptAmount'],
                update['IssuedQuantity'], update['IssuedAmount'],
                update['BalanceQuantity'], update['BalanceAmount'],
                update['TransactionID'] # Use TransactionID for WHERE clause
            ))
        conn.commit()

        # Sync all data back to Google Sheets (clear and rewrite for simplicity in update)
        spreadsheet_id = os.getenv('SPREADSHEET_ID', 'your_spreadsheet_id')
        
        # Fetch all data after update for full sync to sheets
        cur.execute("SELECT * FROM StockBook ORDER BY TransactionID")
        all_data_after_update = cur.fetchall()

        # Prepare values for Google Sheets, ensuring headers are included for clarity
        headers = ['TransactionID', 'Date', 'Particulars', 'VoucherBillNo', 'ReceiptQuantity',
                   'ReceiptAmount', 'IssuedQuantity', 'IssuedAmount', 'BalanceQuantity', 'BalanceAmount']
        
        values_to_write = [headers] + [[d['transactionid'], d['date'].strftime('%Y-%m-%d'), d['particulars'], d['voucherbillno'],
                                         d['receiptquantity'], d['receiptamount'], d['issuedquantity'],
                                         d['issuedamount'], d['balancequantity'], d['balanceamount']]
                                        for d in all_data_after_update]

        # Clear existing data in the sheet before updating
        sheets_service.spreadsheets().values().clear(spreadsheetId=spreadsheet_id, range='A1:J').execute()
        # Update the sheet with the latest data
        sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range='A1', valueInputOption='RAW', body={'values': values_to_write}
        ).execute()

        cur.close()
        conn.close()
        logger.info("Data updated successfully in StockBook and synced to Google Sheets.")
        return jsonify({'message': 'Data updated'}), 200
    except Exception as e:
        logger.error(f"Update error: {e}", exc_info=True)
        if 'conn' in locals() and conn:
            conn.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/export-to-sheet', methods=['POST'])
def export_to_sheet():
    """
    Exports provided data to a new Google Sheet.
    This route expects the full dataset to be sent in the request body.
    """
    try:
        data = request.json # Expects a list of dictionaries representing the data to export
        
        # Create a new spreadsheet with a dynamic title
        spreadsheet = sheets_service.spreadsheets().create(
            body={'properties': {'title': f'Exported_StockBook_Results_{datetime.now().strftime("%Y%m%d_%H%M%S")}'}}
        ).execute()
        spreadsheet_id = spreadsheet['spreadsheetId']
        
        # Define headers according to StockBook schema
        headers = ['TransactionID', 'Date', 'Particulars', 'VoucherBillNo', 'ReceiptQuantity',
                   'ReceiptAmount', 'IssuedQuantity', 'IssuedAmount', 'BalanceQuantity', 'BalanceAmount']
        
        # Prepare values for the new sheet, including headers
        values = [headers] + [[d['TransactionID'], d['Date'], d['Particulars'], d['VoucherBillNo'],
                               d['ReceiptQuantity'], d['ReceiptAmount'], d['IssuedQuantity'],
                               d['IssuedAmount'], d['BalanceQuantity'], d['BalanceAmount']]
                              for d in data]
        
        # Update the new sheet with the prepared values
        sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range='A1', valueInputOption='RAW', body={'values': values}
        ).execute()

        shareable_link = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
        logger.info(f"Data exported to new sheet: {shareable_link}")
        return jsonify({'message': 'Sheet created', 'link': shareable_link}), 200
    except Exception as e:
        logger.error(f"Export error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Get port from environment variable, default to 5000
    port = int(os.environ.get("PORT", 5000))
    # Run the Flask app
    app.run(host='0.0.0.0', port=port, debug=True)
