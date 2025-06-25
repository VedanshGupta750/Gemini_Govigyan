import json
import psycopg2
import psycopg2.extensions
import traceback
import os
from PIL import Image
import google.generativeai as genai
from dotenv import load_dotenv
import fitz # PyMuPDF
from io import BytesIO
from flask import Flask, request, jsonify
from flask_cors import CORS # Import CORS
import re # Make sure 're' module is imported for regex operations

# Initialize Flask app
app = Flask(__ON_RENDER_STARTUP__) # Use __name__ instead of 'ON_RENDER_STARTUP' for Flask app initialization

# Enable CORS for all routes and all origins (for development)
CORS(app)

# Load environment variables
load_dotenv()

# --- Database Functions ---
def create_database_if_not_exists(dbname, user, password, host, port):
    """
    Connects to the default 'postgres' database and creates the target database
    if it does not already exist. Uses SSL mode 'require'.
    """
    try:
        print("[DB_SETUP] Connecting to default 'postgres' database...")
        # Ensure sslmode='require' for Render.com PostgreSQL connections
        with psycopg2.connect(dbname='postgres', user=user, password=password, host=host, port=port, sslmode='require') as conn:
            # Set isolation level to allow DDL commands outside a transaction block
            conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
            print("[DB_SETUP] Connected successfully.")
            
            with conn.cursor() as cur:
                print("[DB_SETUP] Checking if database exists...")

                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
                exists = cur.fetchone()
                if not exists:
                    print(f"[DB_SETUP] Database '{dbname}' does not exist. Creating now...")
                    # Using f-string for CREATE DATABASE is common; ensure dbname is trusted.
                    cur.execute(f'CREATE DATABASE "{dbname}";')
                    print(f"[DB_SETUP] Database '{dbname}' created successfully.")
                else:
                    print(f"[DB_SETUP] Database '{dbname}' already exists. Skipping creation.")
        print("[DB_SETUP] Connection to default database closed.") # 'with' statement handles closing
    except psycopg2.Error as e:
        print(f"[ERROR] PostgreSQL Error during DB creation check/create: {e}")
        traceback.print_exc()
    except Exception as e:
        print("[ERROR] General error creating database:")
        traceback.print_exc()

def insert_data_into_postgres(data_list, dbname, user, password, host, port, table_name):
    """
    Connects to the target database, ensures the table exists, and inserts data.
    Uses SSL mode 'require'.
    """
    try:
        print("[DB_INSERT] Connecting to target database...")
        # Ensure sslmode='require' for Render.com PostgreSQL connections
        with psycopg2.connect(dbname=dbname, user=user, password=password, host=host, port=port, sslmode='require') as conn:
            print("[DB_INSERT] Connected to target database.")
            
            with conn.cursor() as cur:
                print(f"[DB_INSERT] Ensuring table '{table_name}' exists...")

                create_table_query = f"""
                CREATE TABLE IF NOT EXISTS "{table_name}" (
                    "Entry_ID" SERIAL PRIMARY KEY,
                    "DATE" TEXT,
                    "PARTICULARS" TEXT,
                    "Voucher_BillNo" TEXT,
                    "RECEIPTS_Quantity" INTEGER,
                    "RECEIPTS_Amount" REAL,
                    "ISSUED_Quantity" INTEGER,
                    "ISSUED_Amount" REAL,
                    "BALANCE_Quantity" INTEGER,
                    "BALANCE_Amount" REAL
                );
                """
                cur.execute(create_table_query)
                conn.commit() # Commit table creation if it was executed
                print(f"[DB_INSERT] Table '{table_name}' ensured.")

                if not data_list:
                    print("[DB_INSERT] No data provided to insert. Skipping insertion.")
                    return

                print(f"[DB_INSERT] Inserting {len(data_list)} records into '{table_name}'...")
                insert_count = 0
                for i, record in enumerate(data_list, start=1):
                    # Use .get() with a default of None to handle missing keys gracefully
                    date_val = record.get("DATE")
                    particulars_val = record.get("PARTICULARS")
                    voucher_billno_val = record.get("Voucher_BillNo")
                    receipts_qty = record.get("RECEIPTS_Quantity")
                    receipts_amt = record.get("RECEIPTS_Amount")
                    issued_qty = record.get("ISSUED_Quantity")
                    issued_amt = record.get("ISSUED_Amount")
                    balance_qty = record.get("BALANCE_Quantity")
                    balance_amt = record.get("BALANCE_Amount")

                    # Handle cases where particulars_val might be None
                    print(f"[DB_INSERT.{i}] Inserting record: Date='{date_val}', Particulars='{(particulars_val[:30] + '...') if particulars_val else ''}'")

                    sql = f"""
                        INSERT INTO "{table_name}" (
                            "DATE", "PARTICULARS", "Voucher_BillNo",
                            "RECEIPTS_Quantity", "RECEIPTS_Amount",
                            "ISSUED_Quantity", "ISSUED_Amount",
                            "BALANCE_Quantity", "BALANCE_Amount"
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """
                    try:
                        cur.execute(sql, (
                            date_val, particulars_val, voucher_billno_val,
                            receipts_qty, receipts_amt,
                            issued_qty, issued_amt,
                            balance_qty, balance_amt
                        ))
                        insert_count += 1
                    except psycopg2.Error as insert_err:
                        print(f"[ERROR] Failed to insert record {i}: {record}")
                        print(f"        PostgreSQL Error: {insert_err}")
                        conn.rollback() # Rollback the transaction on error
                
                conn.commit() # Commit all successfully inserted records
                print(f"[DB_INSERT] {insert_count} records processed and committed.")
                
                cur.execute(f'SELECT COUNT(*) FROM "{table_name}"')
                total_rows = cur.fetchone()[0]
                print(f"[DB_INSERT] Total rows in table '{table_name}': {total_rows}")

        print("[DB_INSERT] Connection to target database closed.") # 'with' statement handles closing
    except psycopg2.Error as e:
        print(f"[ERROR] PostgreSQL Error during data insertion setup/connection: {e}")
        traceback.print_exc()
        # 'with' statement handles rollback on exception, but explicit rollback in the loop is good.
    except Exception as e:
        print("[ERROR] General error during data insertion process:")
        traceback.print_exc()

# --- Helper Function to Process Different Input Types ---
def process_input_files(input_files):
    """Processes local file paths (not used for Flask uploads, kept for reference)."""
    image_paths = []
    for file_path in input_files:
        if file_path.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tiff')):
            if os.path.exists(file_path):
                image_paths.append(file_path)
                print(f"[IMAGE] Added image: {file_path}")
            else:
                print(f"[ERROR] Image file not found: {file_path}")
        elif file_path.lower().endswith('.pdf'):
            if os.path.exists(file_path):
                print(f"[PDF] Processing PDF {file_path}...")
                try:
                    doc = fitz.open(file_path)
                    for page_num in range(len(doc)):
                        page = doc.load_page(page_num)
                        pix = page.get_pixmap()
                        img_data = pix.tobytes("jpeg")
                        img = Image.open(BytesIO(img_data))
                        # Note: For local files, this saves to current directory.
                        # For uploads, it saves to /tmp.
                        output_path = f"temp_pdf_page_{page_num}.jpg" 
                        img.save(output_path, 'JPEG')
                        image_paths.append(output_path)
                        print(f"  - Converted page {page_num} to {output_path}")
                    doc.close()
                except Exception as e:
                    print(f"[ERROR] Failed to process PDF {file_path}: {e}")
                    traceback.print_exc()
            else:
                print(f"[ERROR] PDF file not found: {file_path}")
        else:
            print(f"[WARNING] Skipping unsupported file type: {file_path}")
    return image_paths

# --- Modified to Process Uploaded Files ---
def process_uploaded_files(uploaded_files):
    """Saves uploaded files to /tmp and converts PDFs to images."""
    image_paths = []
    for file in uploaded_files:
        if file and file.filename: # Check for non-empty filename
            filename = file.filename
            # Use a more unique temp name to avoid conflicts if multiple files have same name
            temp_filename = f"{os.urandom(8).hex()}_{filename}" 
            file_path = os.path.join("/tmp", temp_filename) # Use os.path.join for path construction

            if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tiff')):
                try:
                    file.save(file_path)
                    image_paths.append(file_path)
                    print(f"[IMAGE] Saved uploaded image: {file_path}")
                except Exception as e:
                    print(f"[ERROR] Failed to save uploaded image {filename}: {e}")
                    traceback.print_exc()
            elif filename.lower().endswith('.pdf'):
                try:
                    file.save(file_path)
                    print(f"[PDF] Processing uploaded PDF {file_path}...")
                    doc = fitz.open(file_path)
                    for page_num in range(len(doc)):
                        page = doc.load_page(page_num)
                        pix = page.get_pixmap()
                        img_data = pix.tobytes("jpeg")
                        img = Image.open(BytesIO(img_data))
                        pdf_output_path = os.path.join("/tmp", f"{os.urandom(8).hex()}_temp_pdf_page_{page_num}.jpg")
                        img.save(pdf_output_path, 'JPEG')
                        image_paths.append(pdf_output_path)
                        print(f"  - Converted page {page_num} to {pdf_output_path}")
                    doc.close()
                    os.remove(file_path) # Remove original PDF after conversion
                    print(f"[CLEANUP] Removed original uploaded PDF: {file_path}")
                except Exception as e:
                    print(f"[ERROR] Failed to process uploaded PDF {filename}: {e}")
                    traceback.print_exc()
            else:
                print(f"[WARNING] Skipping unsupported file type: {filename}")
        else:
            print("[WARNING] Received an empty file part.")
    return image_paths

# --- Gemini Interaction Function (Multimodal) ---
def extract_json_from_images_with_gemini(image_paths, api_key):
    """
    Sends images to Gemini API with a prompt to extract JSON data.
    """
    print("[GEMINI] Configuring Gemini...")
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash-latest')
        print(f"[GEMINI] Using model: {model.model_name}")
        print("[GEMINI] Gemini configured successfully.")
    except Exception as config_err:
        print(f"[ERROR] Failed to configure Gemini: {config_err}")
        traceback.print_exc()
        return None

    # --- Load Images ---
    image_objects = []
    print(f"[GEMINI] Loading {len(image_paths)} images...")
    for img_path in image_paths:
        try:
            print(f"  - Loading: {img_path}")
            img = Image.open(img_path)
            image_objects.append(img)
        except Exception as img_err:
            print(f"[ERROR] Failed to load image {img_path}: {img_err}")
            traceback.print_exc()
            # Decide if you want to continue with other images or fail immediately
            return None # Fail immediately if an image can't be loaded

    if not image_objects:
        print("[ERROR] No images were successfully loaded.")
        return None

    print("[GEMINI] Images loaded successfully.")

    prompt_text = """
    You are an assistant that analyzes images of handwritten or printed Stock Book pages and extracts transaction information into JSON format. Return ONLY the raw JSON list with no additional text, comments, or formatting. Do not include markdown (e.g., ```json) or any explanatory text before or after the JSON.

    Look at the following image(s) carefully. It contains rows detailing stock transactions. For each distinct transaction row you can identify in the image, extract the following fields:
    - "DATE": The date of the transaction.
    - "PARTICULARS": The description or details of the transaction, including any voucher or bill number if clearly associated within the 'Particulars' column. If there's a separate 'Voucher Bill No.' column, prioritize that.
    - "Voucher_BillNo": The voucher or bill number, if there is a separate column for it. If it's integrated into 'Particulars' and there's no separate column, try to extract it, otherwise use null or an empty string.
    - "RECEIPTS_Quantity": The quantity received (under the 'RECEIPTS' or 'आवक माल' section). If empty or not applicable, use null.
    - "RECEIPTS_Amount": The amount/value received (under the 'RECEIPTS' or 'आवक माल' section). If empty or not applicable, use null.
    - "ISSUED_Quantity": The quantity issued (under the 'ISSUED' or 'जावक माल' section). If empty or not applicable, use null.
    - "ISSUED_Amount": The amount/value issued (under the 'ISSUED' or 'जावक माल' section). If empty or not applicable, use null.
    - "BALANCE_Quantity": The quantity remaining in balance (under the 'BALANCE' or 'बची संख्या' section).
    - "BALANCE_Amount": The amount/value remaining in balance (under the 'BALANCE' or 'बची संख्या' section).

    Format the output as a JSON list of objects. Each object represents one transaction row found in the image. The JSON keys MUST be exactly: "DATE", "PARTICULARS", "Voucher_BillNo", "RECEIPTS_Quantity", "RECEIPTS_Amount", "ISSUED_Quantity", "ISSUED_Amount", "BALANCE_Quantity", "BALANCE_Amount".

    Example output (no extra text):
    [{"DATE": "11/01/14", "PARTICULARS": "प्रारंभिक शेष", "Voucher_BillNo": null, "RECEIPTS_Quantity": 33, "RECEIPTS_Amount": 6930.00, "ISSUED_Quantity": null, "ISSUED_Amount": null, "BALANCE_Quantity": 33, "BALANCE_Amount": 6930.00}]

    If a value is not present in the image for a specific field in a row, use null for numeric fields and an empty string or null for text fields where appropriate. Ensure all keys are present in each JSON object.
    """

    request_payload = [prompt_text] + image_objects

    print("[GEMINI] Sending prompt and images to Gemini API...")
    raw_json_text = "" # Initialize raw_json_text
    try:
        response = model.generate_content(request_payload)
        print("[GEMINI] Received response from API.")

        raw_json_text = response.text.strip() # Strip any leading/trailing whitespace
        print(f"[GEMINI] Raw response text (full): {raw_json_text}") # Log full response for debugging

        # Try to clean the response by removing any non-JSON text
        if raw_json_text.startswith('```json') and raw_json_text.endswith('```'):
            raw_json_text = raw_json_text[len('```json'):-len('```')].strip()
        elif raw_json_text.startswith('[') or raw_json_text.startswith('{'):
            pass # Already looks like JSON, proceed
        else:
            # Look for a JSON array or object pattern within the text.
            # Using re.DOTALL to allow '.' to match newlines.
            # This regex is more robust for finding JSON that might be embedded in other text.
            json_match = re.search(r'(\[.*?\]|\{.*?\})', raw_json_text, re.DOTALL)
            if json_match:
                raw_json_text = json_match.group(1).strip() # Capture group 1
                print(f"[GEMINI] Extracted potential JSON from response: {raw_json_text}")
            else:
                print("[ERROR] No valid JSON structure found in response after initial checks.")
                print(f"        Raw Text: {raw_json_text}")
                return None

        if not raw_json_text:
            print("[ERROR] Empty (or unparseable) response from Gemini.")
            return None

        print("[GEMINI] Attempting to parse JSON...")
        parsed_data = json.loads(raw_json_text)
        print("[GEMINI] JSON parsed successfully.")

        if isinstance(parsed_data, list):
            print(f"[GEMINI] Extracted {len(parsed_data)} records.")
            return parsed_data
        elif isinstance(parsed_data, dict):
            print("[WARNING] Gemini returned a single JSON object, wrapping it in a list.")
            return [parsed_data]
        else:
            print(f"[ERROR] Gemini output was not a JSON list or object. Type: {type(parsed_data)}")
            return None

    except json.JSONDecodeError as json_err:
        print(f"[ERROR] Failed to decode JSON response from Gemini: {json_err}")
        print(f"        Problematic JSON Text (first 500 chars): {raw_json_text[:500]}...") # Limit output
        traceback.print_exc()
        return None
    except Exception as e:
        print(f"[ERROR] Error during Gemini API call or processing: {e}")
        traceback.print_exc()
        if 'response' in locals() and hasattr(response, 'prompt_feedback'):
            print(f"        Prompt Feedback: {response.prompt_feedback}")
        if 'response' in locals() and hasattr(response, 'candidates') and response.candidates:
            print(f"        Finish Reason: {response.candidates[0].finish_reason}")
            print(f"        Safety Ratings: {response.candidates[0].safety_ratings}")
        return None

# --- API Endpoint ---
@app.route('/process', methods=['POST'])
def process_files():
    print("[API] Received /process request.")
    temp_files_to_clean = [] # List to keep track of files to remove

    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file part in the request"}), 400

        files = request.files.getlist('file') # Handle multiple files
        if not files or all(file.filename == '' for file in files):
            return jsonify({"error": "No selected files"}), 400

        # Process uploaded files and track them for cleanup
        image_paths = process_uploaded_files(files)
        temp_files_to_clean.extend(image_paths) # Add processed images (including PDF conversions)

        if not image_paths:
            return jsonify({"error": "No valid image files processed"}), 400

        # Load environment variables
        gemini_api_key = os.environ.get('GEMINI_API_KEY')
        if not gemini_api_key:
            return jsonify({"error": "GEMINI_API_KEY not set in environment"}), 500

        # Ensure these match your Render environment variables exactly
        # Using the values you provided for defaults for local testing clarity
        db_name = os.environ.get('DB_NAME', 'govigyan_qvyz') 
        db_user = os.environ.get('DB_USER', 'govigyan_user')
        db_password = os.environ.get('DB_PASSWORD', 'HKfQFPof1FXx0fFjJBTx0ZOsUniORAAkd')
        db_host = os.environ.get('DB_HOST', 'dpg-d1d9sgidbo4c73cj8r90-a') 
        db_port = int(os.environ.get('DB_PORT', 5432)) # Convert port to int

        table = 'StockBook'

        # Create database if not exists
        create_database_if_not_exists(db_name, db_user, db_password, db_host, db_port)

        # Extract data using Gemini
        extracted_data = extract_json_from_images_with_gemini(image_paths, gemini_api_key)

        if extracted_data:
            # Insert data into PostgreSQL
            insert_data_into_into_postgres(extracted_data, db_name, db_user, db_password, db_host, db_port, table) # Typo fixed
            return jsonify({"message": "Data processed and inserted successfully", "data": extracted_data}), 200
        else:
            return jsonify({"error": "Failed to extract data from images"}), 500

    except Exception as e:
        print(f"[API_ERROR] An unexpected error occurred during processing: {e}")
        traceback.print_exc()
        return jsonify({"error": f"An internal server error occurred: {e}"}), 500
    finally:
        # Clean up temporary files regardless of success or failure
        for temp_file_path in temp_files_to_clean:
            try:
                if os.path.exists(temp_file_path):
                    os.remove(temp_file_path)
                    print(f"[CLEANUP] Removed temporary file: {temp_file_path}")
            except OSError as e:
                print(f"[CLEANUP ERROR] Failed to remove {temp_file_path}: {e}")

# This part is for local development. Render's server (Gunicorn) will handle running the app.
if __name__ == '__main__':
    # Use '0.0.0.0' for host to make it accessible externally in containers/VMs
    # Use environment variable 'PORT' as provided by Render, fallback to 5000 for local.
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)
